"""ChatGPT Subscription catalog identities and explicit control evidence."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_CHATGPT_SUBSCRIPTION,
    as_list,
    as_mapping,
    compact_str,
    identity_str,
    stable_model_id_for,
)


vendor = VENDOR_CHATGPT_SUBSCRIPTION
_DEFAULT_PRIORITY = 10_000


def _priority_rank(raw: Mapping[str, Any]) -> int | float:
    value = raw.get("priority")
    if isinstance(value, bool):
        return _DEFAULT_PRIORITY
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return _DEFAULT_PRIORITY


def _is_hidden(raw: Mapping[str, Any]) -> bool:
    visibility = raw.get("visibility")
    return (
        isinstance(visibility, str)
        and visibility.strip().lower() in {"hide", "hidden"}
    )


def _control_value(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    token = value.strip().lower()
    # Accepted values are wire values, not capability-name aliases. Preserve
    # punctuation rather than inventing an unadvertised spelling.
    return token if re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", token) else ""


def _model_controls(raw: Mapping[str, Any]) -> tuple[mc.DeterministicControl, ...]:
    """Normalize only the native catalog's explicit control metadata."""
    levels = raw.get("supported_reasoning_levels") or raw.get("supportedReasoningEfforts")
    values: list[str] = []
    if isinstance(levels, (list, tuple)):
        for item in levels:
            if isinstance(item, Mapping):
                item = item.get("effort") or item.get("level") or item.get("name")
            value = _control_value(item)
            if value and value not in values:
                values.append(value)

    def claim(control: str, allowed: tuple[str, ...], default: Any) -> mc.DeterministicControl:
        evidence: dict[str, Any] = {"allowed_values": list(allowed)}
        normalized_default = _control_value(default)
        if normalized_default in allowed:
            evidence["default"] = normalized_default
        return mc.DeterministicControl.build(
            control=control,
            status=mc.ASSERTION_CLAIMED,
            source=mc.SOURCE_PROVIDER_READER,
            confidence=mc.CONFIDENCE_PROVIDER_REPORTED,
            evidence=evidence,
        )

    controls: list[mc.DeterministicControl] = []
    if values:
        controls.append(claim(
            mc.CONTROL_REASONING_EFFORT,
            tuple(values),
            raw.get("default_reasoning_level") or raw.get("defaultReasoningEffort"),
        ))
    if raw.get("support_verbosity") is True:
        controls.append(claim(
            mc.CONTROL_VERBOSITY, ("low", "medium", "high"), raw.get("default_verbosity"),
        ))
    return tuple(controls)


_CONTROL_FIELDS = frozenset({
    "supported_reasoning_levels", "supportedReasoningEfforts",
    "default_reasoning_level", "defaultReasoningEffort",
    "support_verbosity", "default_verbosity",
})


def select_catalog_items(
    items: tuple[Mapping[str, Any], ...],
) -> tuple[Mapping[str, Any], ...]:
    """Apply native availability/order without choosing conflicting controls."""
    sortable: list[tuple[int | float, str, int, Mapping[str, Any]]] = []
    passthrough: list[Mapping[str, Any]] = []
    controls_by_slug: dict[str, tuple[mc.DeterministicControl, ...]] = {}
    conflicting: set[str] = set()
    for position, item in enumerate(items):
        if _is_hidden(item):
            continue
        slug = identity_str(item.get("slug"))
        if not slug:
            passthrough.append(item)
            continue
        controls = _model_controls(item)
        if slug in controls_by_slug and controls != controls_by_slug[slug]:
            conflicting.add(slug)
        controls_by_slug[slug] = controls
        sortable.append((_priority_rank(item), slug, position, item))
    sortable.sort(key=lambda entry: (entry[0], entry[1], entry[2]))

    selected: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for _, slug, _, item in sortable:
        if slug not in seen:
            # Inventory remains visible; conflicting exact identities cannot
            # establish support by whichever duplicate happened to sort first.
            selected.append(
                {key: value for key, value in item.items() if key not in _CONTROL_FIELDS}
                if slug in conflicting else item
            )
            seen.add(slug)
    selected.extend(passthrough)
    return tuple(selected)


def record_from_model(
    raw: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    model_id = identity_str(raw.get("slug"))
    if not model_id:
        return None
    return ModelCapabilityRecord(
        vendor=VENDOR_CHATGPT_SUBSCRIPTION,
        model_id=model_id,
        stable_model_id=stable_model_id_for(
            VENDOR_CHATGPT_SUBSCRIPTION,
            model_id,
            endpoint_id=endpoint_id,
            base_url=base_url,
        ),
        display_name=compact_str(raw.get("display_name") or raw.get("title")) or model_id,
        capability=mc.unknown_capability(
            source=mc.SOURCE_PROVIDER_READER,
            confidence=mc.CONFIDENCE_UNKNOWN,
        ),
        deterministic_controls=_model_controls(raw),
        raw=raw,
    )


def records_from_payload(
    payload: Any,
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> tuple[ModelCapabilityRecord, ...]:
    values = as_mapping(payload).get("models")
    return tuple(
        record
        for item in select_catalog_items(
            tuple(item for item in as_list(values) if isinstance(item, Mapping))
        )
        if (record := record_from_model(item, endpoint_id=endpoint_id, base_url=base_url))
    )
