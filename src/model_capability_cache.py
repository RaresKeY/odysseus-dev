"""Bounded route/credential-scoped catalog evidence without retained secrets."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from src.model_capability_readers import ModelCapabilityRecord, records_from_payload
from src.model_capability_readers.base import stable_model_id_for
from src.provider_capability_schemas import normalize_provider_id

_CACHE_TTL_SECONDS = 300.0
_MAX_CATALOGS = 128
_lock = threading.Lock()
_catalogs: OrderedDict[str, tuple[float, tuple[ModelCapabilityRecord, ...]]] = OrderedDict()
_OPERATION_PATHS = ("/chat/completions", "/api/chat", "/api/tags", "/responses", "/models")


def scope_for_endpoint(base_url: str, headers: Mapping[str, str] | None = None) -> str:
    """Hash an exact normalized route and its complete request-header identity."""
    if not isinstance(base_url, str) or not base_url.strip():
        return ""
    try:
        parsed = urlsplit(base_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        parsed.port  # Reject malformed ports before building a cache identity.
        userinfo, separator, hostport = parsed.netloc.rpartition("@")
        netloc = userinfo + separator + hostport.lower() if separator else parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        for suffix in _OPERATION_PATHS:
            if path.endswith(suffix):
                path = path[:-len(suffix)]
                break
        normalized_url = urlunsplit((parsed.scheme, netloc, path, parsed.query, ""))
        if headers is not None and not isinstance(headers, Mapping):
            return ""
        header_items: list[tuple[str, str]] = []
        for name, value in (headers or {}).items():
            if not isinstance(name, str) or not name.strip() or not isinstance(value, str):
                return ""
            header_items.append((name.strip().lower(), value))
        identity = json.dumps(
            [normalized_url, sorted(header_items)],
            ensure_ascii=True, separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return ""
    return "catalog-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _expire(now: float) -> None:
    for key, (expires_at, _) in tuple(_catalogs.items()):
        if expires_at <= now:
            _catalogs.pop(key, None)


def remember_catalog(
    payload: Any, *, base_url: str, headers: Mapping[str, str] | None = None,
    vendor: str | None = None, endpoint_kind: str = "",
) -> tuple[ModelCapabilityRecord, ...]:
    """Replace the route snapshot, including an empty or identity-only catalog."""
    scope = scope_for_endpoint(base_url, headers)
    if not scope:
        return ()
    try:
        records = tuple(
            replace(record, raw={})
            for record in records_from_payload(
                payload, base_url=base_url, endpoint_id=scope,
                vendor=vendor, endpoint_kind=endpoint_kind,
            )
        )
    except (TypeError, ValueError, OverflowError):
        records = ()
    now = time.monotonic()
    with _lock:
        _expire(now)
        _catalogs[scope] = (now + _CACHE_TTL_SECONDS, records)
        _catalogs.move_to_end(scope)
        while len(_catalogs) > _MAX_CATALOGS:
            _catalogs.popitem(last=False)
    return records


def cached_model_record(
    model: str, *, base_url: str, headers: Mapping[str, str] | None = None,
    vendor: str | None = None,
) -> ModelCapabilityRecord | None:
    """Return one exact, fresh, vendor-consistent record, or unknown."""
    if not isinstance(model, str) or not model:
        return None
    scope = scope_for_endpoint(base_url, headers)
    if not scope:
        return None
    with _lock:
        _expire(time.monotonic())
        snapshot = _catalogs.get(scope)
        if snapshot is None:
            return None
        _catalogs.move_to_end(scope)
        matches = [record for record in snapshot[1] if record.model_id == model]
    if len(matches) != 1:
        return None
    record = matches[0]
    if vendor is not None and record.vendor != normalize_provider_id(vendor):
        return None
    expected = stable_model_id_for(record.vendor, model, endpoint_id=scope, base_url=base_url)
    return record if record.stable_model_id == expected else None


def clear() -> None:
    with _lock:
        _catalogs.clear()
