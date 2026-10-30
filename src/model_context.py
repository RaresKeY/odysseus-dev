"""
model_context.py

Query and cache model context window sizes from OpenAI-compatible APIs.
Provides token estimation for context usage tracking.
"""

from collections import OrderedDict
from collections.abc import Mapping
import ipaddress
import math
import threading
import time
import logging
import sys
from typing import Dict, List, Optional, Tuple

from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)

# Tailscale uses the CGNAT range 100.64.0.0/10, NOT all of 100.0.0.0/8.
# A bare "100." prefix would classify public addresses (e.g. AWS ranges
# under 100.x outside the CGNAT block) as local; routes/model_routes.py
# already narrows this the same way for endpoint classification.
_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _in_tailscale_range(host: str) -> bool:
    try:
        return ipaddress.ip_address(host) in _TAILSCALE_CGNAT
    except ValueError:
        return False


def _is_private_ip_literal(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in network for network in _PRIVATE_NETWORKS)


def _normalize_base_for_compare(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/models", "/completions", "/v1/messages"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    return url


def _configured_endpoint_kind(url: str) -> Optional[str]:
    """Return configured endpoint kind for a chat/base URL when available."""
    target = _normalize_base_for_compare(url)
    if not target:
        return None
    if "core.database" not in sys.modules:
        return None
    try:
        from core.database import SessionLocal, ModelEndpoint
        db = SessionLocal()
        try:
            rows = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
            matches = [ep for ep in rows if _normalize_base_for_compare(getattr(ep, "base_url", "") or "") == target]
            if len(matches) != 1:
                return None
            for ep in matches:
                base = _normalize_base_for_compare(getattr(ep, "base_url", "") or "")
                if not base:
                    continue
                if target != base and not target.startswith(base + "/"):
                    continue
                kind = (getattr(ep, "endpoint_kind", None) or "auto").strip().lower()
                if kind in ("local", "api", "proxy"):
                    return kind
                if getattr(ep, "api_key", None):
                    parsed = urlparse(base)
                    host = (parsed.hostname or "").lower()
                    path = (parsed.path or "").rstrip("/")
                    if parsed.port != 11434 and "ollama" not in host and (path.endswith("/v1") or "/openai" in path):
                        return "proxy"
                return "auto"
        finally:
            db.close()
    except Exception:
        return None


def is_local_endpoint(url: str) -> bool:
    """Check if URL points to a local/private/tailscale address."""
    kind = _configured_endpoint_kind(url)
    if kind in ("api", "proxy"):
        return False
    if kind == "local":
        return True
    try:
        host = urlparse(url).hostname or ""
        return host in _LOCAL_HOSTS or _is_private_ip_literal(host) or _in_tailscale_range(host)
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_CONTEXT = 128000
REQUEST_TIMEOUT = 5

# Known context windows for major API models (used as fallback when /models
# endpoint doesn't report context_length).
# Substring matching — use the shortest unique prefix so variants get caught.
KNOWN_CONTEXT_WINDOWS = {
    # --- Anthropic ---
    'claude-sonnet-4-5': 200000,
    'claude-sonnet-4-6': 200000,
    'claude-sonnet-4': 200000,
    'claude-opus-4': 200000,
    'claude-haiku-4': 200000,
    'claude-haiku-3-5': 200000,
    'claude-3-5-sonnet': 200000,
    'claude-3-5-haiku': 200000,
    'claude-3-opus': 200000,
    'claude-3-sonnet': 200000,
    'claude-3-haiku': 200000,

    # --- OpenAI ---
    'gpt-5': 400000,
    'gpt-4.1': 1047576,
    'gpt-4.1-mini': 1047576,
    'gpt-4.1-nano': 1047576,
    'gpt-4o': 128000,
    'gpt-4o-mini': 128000,
    'gpt-4-turbo': 128000,
    'gpt-4': 8192,
    'gpt-3.5-turbo': 16385,
    'o1': 200000,
    'o1-mini': 128000,
    'o1-pro': 200000,
    'o3': 200000,
    'o3-mini': 200000,
    'o4-mini': 200000,

    # --- DeepSeek ---
    'deepseek-chat': 64000,
    'deepseek-coder': 64000,
    'deepseek-reasoner': 64000,
    'deepseek-r1': 64000,
    'deepseek-v3': 64000,
    'deepseek-v2': 64000,
    'deepseek-v4': 64000,
    # Provider aliases used by configured Odysseus endpoints may omit the
    # generation name. Keep them out of the unknown/small-model fallback,
    # which otherwise trims multi-turn tool history to ~1K tokens.
    'deepseek-flash': 64000,

    # --- Google ---
    'gemini-2.5-pro': 1048576,
    'gemini-2.5-flash': 1048576,
    'gemini-2.0-flash': 1048576,
    'gemini-1.5-pro': 1048576,
    'gemini-1.5-flash': 1048576,
    'gemma-4': 262144,
    'gemma-3': 128000,
    'gemma-2': 8192,

    # --- Mistral ---
    'mistral-large': 128000,
    'mistral-medium': 32000,
    'mistral-small': 32000,
    'mistral-nemo': 128000,
    'mistral-7b': 32000,
    'mixtral': 32000,
    'codestral': 32000,
    'pixtral': 128000,

    # --- xAI ---
    'grok-4': 131072,
    'grok-3': 131072,
    'grok-2': 131072,

    # --- Meta / Llama ---
    'llama-4': 1048576,
    'llama-3.3': 131072,
    'llama-3.2': 131072,
    'llama-3.1': 131072,
    'llama-3': 131072,

    # --- Qwen ---
    'qwen3': 131072,
    'qwen2.5': 131072,
    'qwen2': 32768,
    'qwq': 32768,

    # --- Cohere ---
    'command-r-plus': 128000,
    'command-r': 128000,
    'command-a': 256000,

    # --- Perplexity ---
    'sonar-pro': 200000,
    'sonar': 128000,

    # --- MiniMax ---
    'minimax': 1000000,

    # --- Moonshot / Kimi ---
    'moonshot': 128000,
    'kimi': 128000,

    # --- Microsoft ---
    'phi-4': 16000,
    'phi-3': 128000,

    # --- Nvidia ---
    'nemotron': 131072,

    # --- Yi ---
    'yi-large': 32768,
    'yi-1.5': 16384,

    # --- 01.ai ---
    'yi-lightning': 16384,

    # --- Nous ---
    'hermes': 131072,
    'nous-hermes': 131072,

    # --- Xiaomi ---
    'mimo-v2.5-pro': 1048576,
    'mimo-v2.5': 1048576,

    # --- Open community ---
    'dolphin': 32768,
    'mythomax': 4096,
    'wizard': 32768,
    'openchat': 8192,
    'solar': 32768,
}

_CACHE_TTL_SECONDS = 300.0
_MAX_CACHE_SCOPES = 128
_cache_lock = threading.RLock()
_context_cache = OrderedDict()
_ollama_runtime_endpoints = OrderedDict()


def _cache_read(cache, key):
    with _cache_lock:
        entry = cache.get(key)
        if entry is None:
            return None
        expires, value = entry
        if expires <= time.monotonic():
            cache.pop(key, None)
            return None
        cache.move_to_end(key)
        return value


def _cache_store(cache, key, value):
    with _cache_lock:
        cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, value)
        cache.move_to_end(key)
        while len(cache) > _MAX_CACHE_SCOPES:
            cache.popitem(last=False)


def _request_kind(endpoint_url, headers, endpoint_kind):
    if endpoint_kind is not None:
        return endpoint_kind if endpoint_kind in {"auto", "local", "api", "proxy"} else None
    # An admitted route must not acquire another registration's classification.
    return _configured_endpoint_kind(endpoint_url) if headers is None else None


def _scope(endpoint_url, headers, kind):
    from src.model_capability_cache import scope_for_endpoint
    value = scope_for_endpoint(endpoint_url, headers)
    return (value, kind or "auto") if value else None


def _local_for_kind(endpoint_url, kind):
    if kind in {"api", "proxy"}:
        return False
    if kind == "local":
        return True
    try:
        host = (urlparse(endpoint_url).hostname or "").lower()
        return host in _LOCAL_HOSTS or _is_private_ip_literal(host) or _in_tailscale_range(host)
    except (TypeError, ValueError):
        return False


def _probe_get(endpoint_url, target_url, headers):
    """Use only caller-selected credentials, without redirects or origin changes."""
    if not target_url:
        return None
    source, target = urlparse(endpoint_url), urlparse(target_url)

    def origin(parsed):
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)

    if origin(source) is None or origin(source) != origin(target):
        return None
    kwargs = {"timeout": REQUEST_TIMEOUT}
    if headers is not None:
        if not isinstance(headers, Mapping) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in headers.items()):
            return None
        from src.tls_overrides import llm_verify
        kwargs.update(headers=dict(headers), follow_redirects=False, verify=llm_verify())
    return httpx.get(target_url, **kwargs)


def _get_context_length_cached(endpoint_url, model, *, headers=None, endpoint_kind=None):
    kind = _request_kind(endpoint_url, headers, endpoint_kind)
    scope = _scope(endpoint_url, headers, kind)
    if scope is None:
        return DEFAULT_CONTEXT, False
    local = _local_for_kind(endpoint_url, kind)
    dynamic = _cache_read(_ollama_runtime_endpoints, scope) or _ollama_ps_url(endpoint_url, kind) is not None
    key = (scope, model)
    cached = _cache_read(_context_cache, key) if not local and not dynamic else None
    if cached is not None:
        return cached
    value = _query_context_length(endpoint_url, model, headers=headers, endpoint_kind=kind)
    if not local and not dynamic and (value[0] != DEFAULT_CONTEXT or kind in {"api", "proxy"}):
        _cache_store(_context_cache, key, value)
    return value


def get_context_length(endpoint_url: str, model: str, *, headers=None, endpoint_kind=None) -> int:
    """Resolve a window for one already-selected route and credential identity."""
    return _get_context_length_cached(endpoint_url, model, headers=headers, endpoint_kind=endpoint_kind)[0]


def get_session_context_length(session, *, owner) -> int:
    """Refresh the owned registration before UI reads use any stored credential."""
    model = getattr(session, "model", "")
    url = getattr(session, "endpoint_url", "")
    endpoint_id = getattr(session, "endpoint_id", None)
    if not endpoint_id:
        return get_context_length(url, model, headers={})
    from src.owner_identity import effective_storage_owner
    admitted = effective_storage_owner(owner)
    if admitted is None:
        return DEFAULT_CONTEXT
    try:
        from src.model_admission import require_owner_model
        from src.endpoint_resolver import resolve_endpoint_by_id
        require_owner_model(admitted, model)
        route = resolve_endpoint_by_id(endpoint_id, model, owner=admitted, require_exact_model=True)
        if route is None:
            return DEFAULT_CONTEXT
        return get_context_length(route[0], route[1], headers=route[2])
    except Exception as exc:
        logger.debug("Session context admission failed: %s", type(exc).__name__)
        return DEFAULT_CONTEXT


def get_context_length_known(endpoint_url: str, model: str, *, headers=None, endpoint_kind=None) -> Tuple[int, bool]:
    """Return the window and its evidence flag from the same scoped lookup."""
    return _get_context_length_cached(endpoint_url, model, headers=headers, endpoint_kind=endpoint_kind)


def budget_context_for_model(endpoint_url: str, model: str, *, fallback: int = 0,
                             headers=None, endpoint_kind=None) -> int:
    """Use proven context evidence; retain the caller's fallback on probe errors."""
    try:
        options = {}
        if headers is not None:
            options["headers"] = headers
        if endpoint_kind is not None:
            options["endpoint_kind"] = endpoint_kind
        ctx, known = get_context_length_known(endpoint_url, model, **options)
        return ctx if known else 0
    except Exception:
        return fallback


def _lookup_known(model: str) -> Optional[int]:
    """Check known context windows by substring match.

    Picks the LONGEST matching key so a short key never shadows a more specific
    one. Without this, 'o1' (200k) precedes 'o1-mini' (128k) in the table and a
    first-match return would report o1-mini's window as 200k.
    """
    name = model.lower()
    basename = name.split("/")[-1] if "/" in name else name
    basename = basename.split(":")[0]  # strip :free, :extended etc.
    best_key: Optional[str] = None
    best_ctx: Optional[int] = None
    for key, ctx in KNOWN_CONTEXT_WINDOWS.items():
        if key in basename or key in name:
            if best_key is None or len(key) > len(best_key):
                best_key, best_ctx = key, ctx
    return best_ctx


def _positive_window(value):
    if type(value) is int:
        return value if 0 < value <= 2**53 else None
    if type(value) is float and math.isfinite(value) and value.is_integer() and 0 < value <= 2**53:
        return int(value)
    return None


def _model_ctx_from_entry(m: dict) -> Optional[int]:
    """Extract a positive context window from one /models catalog entry.

    Checks the common top-level fields first, then a nested meta/model_extra
    object. Returns None when no positive window is reported.
    """
    if not isinstance(m, dict):
        return None
    for field in (
        "context_length",
        "context_window",
        "max_model_len",
        "max_context_length",
        "max_seq_len",
    ):
        val = m.get(field)
        if (window := _positive_window(val)) is not None:
            return window
    meta = m.get("meta") or m.get("model_extra") or {}
    if isinstance(meta, dict):
        # n_ctx is the actual serving context (set via -c flag in llama.cpp)
        for field in ("n_ctx", "context_length", "context_window", "max_model_len"):
            val = meta.get(field)
            if (window := _positive_window(val)) is not None:
                return window
    return None


_catalog_ctx_cache = OrderedDict()


def _catalog_context(payload, model):
    rows = payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return None
    values = [(row.get("id"), _model_ctx_from_entry(row)) for row in rows if isinstance(row, dict)]
    exact = [ctx for mid, ctx in values if mid == model and ctx is not None]
    if exact:
        return exact[0] if len(set(exact)) == 1 else None
    aliases = [ctx for mid, ctx in values if isinstance(mid, str) and mid.rsplit("/", 1)[-1] == model.rsplit("/", 1)[-1] and ctx is not None]
    return aliases[0] if len(aliases) == 1 else None


def _proxy_catalog_context(endpoint_url, model, *, headers=None, endpoint_kind=None):
    """Cache catalog evidence only within its route, account and lifetime."""
    scope = _scope(endpoint_url, headers, endpoint_kind)
    if scope is None:
        return None
    payload = _cache_read(_catalog_ctx_cache, scope)
    if payload is None:
        from src.endpoint_resolver import build_models_url
        try:
            response = _probe_get(endpoint_url, build_models_url(endpoint_url), headers)
            if response is None or not response.is_success:
                return None
            payload = response.json()
            rows = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                return None
            # Retain only model IDs and windows, not raw provider payloads.
            payload = {"data": [{"id": row["id"], "context_length": window}
                                for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)
                                if (window := _model_ctx_from_entry(row)) is not None]}
            _cache_store(_catalog_ctx_cache, scope, payload)
        except Exception as exc:
            logger.debug("Context catalog probe failed: %s", type(exc).__name__)
            return None
    return _catalog_context(payload, model)


def _ollama_ps_url(endpoint_url: str, configured_kind: Optional[str]) -> Optional[str]:
    """Return a local Ollama ``/api/ps`` URL for a compatible endpoint URL."""

    if configured_kind == "proxy":
        return None
    try:
        parsed = urlparse(endpoint_url or "")
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or not host:
            return None
        named_local_ollama = (
            configured_kind in ("api", "local")
            and "." not in host
            and "ollama" in host
        )
        network_local = (
            host in _LOCAL_HOSTS
            or _is_private_ip_literal(host)
            or _in_tailscale_range(host)
            or named_local_ollama
        )
        path = (parsed.path or "").rstrip("/")
        ollama_looking = (
            parsed.port == 11434
            or "ollama" in host
            or path == "/api"
            or "/api/" in path
            or (host in _LOCAL_HOSTS and configured_kind == "api")
        )
        if not network_local or not ollama_looking:
            return None

        api_path = ""
        for suffix in (
            "/v1/chat/completions",
            "/v1/completions",
            "/v1/models",
            "/v1",
        ):
            if path.endswith(suffix):
                api_path = path[: -len(suffix)] + "/api"
                break
        if not api_path:
            for suffix in ("/api/chat", "/api/generate", "/api/tags", "/api/ps"):
                if path.endswith(suffix):
                    api_path = path[: -len(suffix)] + "/api"
                    break
        if not api_path and (path == "/api" or path.endswith("/api")):
            api_path = path
        if not api_path and not path:
            api_path = "/api"
        if not api_path:
            return None

        return parsed._replace(
            path=api_path.rstrip("/") + "/ps",
            params="",
            query="",
            fragment="",
        ).geturl()
    except Exception:
        return None


def _probe_ollama_runtime_context(
    endpoint_url: str,
    model: str,
    *,
    configured_kind: Optional[str],
    headers=None,
) -> Optional[int]:
    """Ask Ollama for the allocation of a currently loaded model."""

    ps_url = _ollama_ps_url(endpoint_url, configured_kind)
    if not ps_url:
        return None
    try:
        response = _probe_get(endpoint_url, ps_url, headers)
        if response is None or not response.is_success:
            return None
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            return None

        # A valid /api/ps response prevents caching static runtime assumptions.
        _cache_store(_ollama_runtime_endpoints, _scope(endpoint_url, headers, configured_kind), True)

        from src.model_capability_readers import ollama

        runtime = ollama.runtime_context_from_ps_payload(
            model,
            payload,
            base_url=endpoint_url,
        )
        return runtime.allocated_context_tokens if runtime is not None else None
    except Exception as exc:
        logger.debug("Ollama context probe failed: %s", type(exc).__name__)
        return None


def _query_context_length(endpoint_url: str, model: str, *, headers=None, endpoint_kind=None) -> Tuple[int, bool]:
    """Query the model API for context length. Returns (context_length, known) where
    ``known`` is False only for the bare DEFAULT_CONTEXT fallback."""
    known = _lookup_known(model)
    api_ctx = None
    configured_kind = _request_kind(endpoint_url, headers, endpoint_kind)
    scope = _scope(endpoint_url, headers, configured_kind)

    # Probe actual Ollama allocation before API-kind static fallback.
    if configured_kind == "api":
        runtime_ctx = _probe_ollama_runtime_context(
            endpoint_url,
            model,
            configured_kind=configured_kind, headers=headers,
        )
        if runtime_ctx:
            logger.info(
                "Ollama loaded runtime allocation for %s: %s",
                model,
                runtime_ctx,
            )
            return runtime_ctx, True
        if _cache_read(_ollama_runtime_endpoints, scope):
            # An unloaded model has no effective runtime allocation.
            return DEFAULT_CONTEXT, False

    # Large OpenAI-compatible proxies can make /models expensive. If the
    # endpoint is explicitly configured as API/proxy, prefer known context
    # metadata (or the default) over downloading the full catalog.
    if configured_kind in ("api", "proxy"):
        if known:
            logger.info(f"Using known context window for {model}: {known}")
            return known, True
        # Not in the known table: read the real window from the catalog (cached
        # once per endpoint) instead of capping every unknown model at the
        # default — that under-reported large windows on aggregators like
        # OpenRouter (issue #4886).
        api_ctx = _proxy_catalog_context(endpoint_url, model, headers=headers, endpoint_kind=configured_kind)
        if api_ctx:
            logger.info(f"Proxy catalog reports context window for {model}: {api_ctx}")
            return api_ctx, True
        return DEFAULT_CONTEXT, False

    # Try llama.cpp /slots endpoint first — reports actual serving context
    if _local_for_kind(endpoint_url, configured_kind):
        base = _normalize_base_for_compare(endpoint_url)
        if base.endswith("/v1"):
            base = base[:-3]
        try:
            r = _probe_get(endpoint_url, f"{base}/slots", headers)
            if r is not None and r.is_success:
                slots = r.json()
                if isinstance(slots, list) and slots:
                    n_ctx = slots[0].get("n_ctx")
                    if type(n_ctx) is int and n_ctx > 0:
                        logger.info(f"llama.cpp /slots reports n_ctx={n_ctx} for {model}")
                        return n_ctx, True
        except Exception:
            pass
        # llama-server only exposes /slots when started with --slots. Its
        # /props endpoint still reports the active serving context, and is the
        # authoritative value for single-slot servers.
        try:
            r = _probe_get(endpoint_url, f"{base}/props", headers)
            if r is not None and r.is_success:
                props = r.json()
                generation = props.get("default_generation_settings") if isinstance(props, dict) else None
                n_ctx = generation.get("n_ctx") if isinstance(generation, dict) else None
                if type(n_ctx) in (int, float) and math.isfinite(n_ctx) and n_ctx > 0:
                    logger.info(f"llama.cpp /props reports n_ctx={int(n_ctx)} for {model}")
                    return int(n_ctx), True
        except Exception:
            pass

    if configured_kind != "api":
        runtime_ctx = _probe_ollama_runtime_context(
            endpoint_url,
            model,
            configured_kind=configured_kind, headers=headers,
        )
        if runtime_ctx:
            logger.info(
                "Ollama loaded runtime allocation for %s: %s",
                model,
                runtime_ctx,
            )
            return runtime_ctx, True
        if _cache_read(_ollama_runtime_endpoints, scope):
            return DEFAULT_CONTEXT, False

    # GitHub Copilot's /models requires auth + X-GitHub-Api-Version headers that
    # aren't available here; an unauthenticated probe just 400s. All Copilot
    # picker models are major API models covered by the known-context table, so
    # rely on that instead of a doomed network call.
    from src.copilot import is_copilot_base
    if is_copilot_base(endpoint_url):
        if known:
            logger.info(f"Using known context window for {model}: {known}")
            return known, True
        return DEFAULT_CONTEXT, False

    from src.endpoint_resolver import build_models_url

    models_url = build_models_url(endpoint_url)
    try:
        r = _probe_get(endpoint_url, models_url, headers)
        if r is not None and r.is_success:
            api_ctx = _catalog_context(r.json(), model)
    except Exception as e:
        logger.debug("Context probe failed: %s", type(e).__name__)

    # For local/self-hosted endpoints, trust the API value (user set --max-model-len)
    # For cloud APIs, use the larger value (API can report low defaults)
    if api_ctx and known:
        _is_local = _local_for_kind(endpoint_url, configured_kind)
        if _is_local and api_ctx < known:
            logger.info(f"Local endpoint reports {api_ctx} for {model} (known max: {known}) — using API value")
            return api_ctx, True
        result = max(api_ctx, known)
        if api_ctx < known:
            logger.info(f"API reported {api_ctx} for {model}, using known {known} instead")
        return result, True
    if api_ctx:
        return api_ctx, True
    if known:
        logger.info(f"Using known context window for {model}: {known}")
        return known, True

    return DEFAULT_CONTEXT, False


def estimate_text_tokens(value: object) -> int:
    """Estimate tokens for one text value without undercounting Unicode."""

    text = value if isinstance(value, str) else str(value or "")
    dense = 0
    for char in text:
        if ord(char) > 127:
            dense += 1
    return dense + int((len(text) - dense) * 0.3)


# Private alias retained for callers/tests that imported the helper while it was
# internal. New code should use the public name so every context-shaping path
# shares the same Unicode-aware estimate.
_estimate_text_tokens = estimate_text_tokens


def estimate_tokens(messages: List[Dict]) -> int:
    """Rough token estimate for a list of messages.

    Uses chars * 0.3 for Latin-heavy text and approximately one token per CJK
    character. A single global character ratio severely undercounts CJK input
    and can prevent context compaction from running before provider rejection.
    Also adds ~4 tokens per message for role/formatting overhead, and counts
    assistant tool_calls (name + arguments) — a tool-only turn carries
    content=None with the real payload in tool_calls, so ignoring them made the
    estimate (and the compaction/trim gates that rely on it) blind to large
    tool arguments.
    """
    total = 0
    for msg in messages:
        total += 4  # per-message overhead (role, separators)
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_text_tokens(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    total += estimate_text_tokens(item.get("text", ""))
        # Tool calls carry real payload too: a tool-only assistant turn is stored
        # with content=None and the actual args (e.g. a create_document body) in
        # tool_calls[].function.arguments. Ignoring them made large tool arguments
        # read as ~0 tokens, so the compaction/trim gates missed genuine overflow.
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else tc
                name = fn.get("name", "") or ""
                args = fn.get("arguments", "") or ""
                if not isinstance(args, str):
                    args = str(args)  # some shapes store arguments as a dict
                total += 4  # per tool-call overhead (id, type, wrapper)
                total += estimate_text_tokens(str(name) + args)
    return total
