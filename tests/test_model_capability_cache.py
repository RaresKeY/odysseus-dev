import pytest

from src import model_capability_cache as cache
from src.model_capability_readers.base import stable_model_id_for


@pytest.fixture(autouse=True)
def clear_catalogs():
    cache.clear()
    yield
    cache.clear()


def _payload(model="Exact-Model", **metadata):
    return {"models": [{"slug": model, "priority": 1, **metadata}]}


def _remember(base_url="https://chatgpt.com/backend-api/codex", headers=None, payload=None):
    return cache.remember_catalog(
        payload if payload is not None else _payload(supported_reasoning_levels=["low"]),
        base_url=base_url, headers=headers, vendor="chatgpt_subscription",
    )


@pytest.mark.parametrize(("base", "operation"), [
    ("https://API.example/v1", "https://api.example/v1/chat/completions"),
    ("https://api.example/v1?tenant=A", "https://api.example/v1/models?tenant=A"),
    ("https://chatgpt.com/backend-api/codex", "https://chatgpt.com/backend-api/codex/responses"),
    ("http://localhost:11434", "http://localhost:11434/api/chat"),
    ("http://localhost:11434", "http://localhost:11434/api/tags"),
])
def test_known_terminal_operations_share_one_route_scope(base, operation):
    assert cache.scope_for_endpoint(base) == cache.scope_for_endpoint(operation)


@pytest.mark.parametrize("other", [
    "http://api.example/Case/v1?tenant=A",
    "https://api.example/case/v1?tenant=A",
    "https://api.example:443/Case/v1?tenant=A",
    "https://api.example/Case/v1?tenant=B",
    "https://api.example/Case/v1/?tenant=A",
    "https://api.example/other/v1?tenant=A",
])
def test_scheme_path_case_explicit_port_query_and_base_path_are_distinct(other):
    assert cache.scope_for_endpoint("https://api.example/Case/v1?tenant=A") != cache.scope_for_endpoint(other)


def test_userinfo_and_every_header_participate_without_retaining_secrets():
    base = "https://User:Secret-A@api.example/v1?key=Query-Secret"
    headers = {"Authorization": "Bearer Header-Secret", "X-Route": "tenant-A"}
    records = _remember(base_url=base, headers=headers)
    scope = cache.scope_for_endpoint(base, headers)
    assert scope == cache.scope_for_endpoint(base, {"x-route": "tenant-A", "authorization": "Bearer Header-Secret"})
    assert scope != cache.scope_for_endpoint(base.replace("Secret-A", "secret-a"), headers)
    assert scope != cache.scope_for_endpoint(base, {**headers, "X-Route": "tenant-B"})
    assert scope != cache.scope_for_endpoint(base, {**headers, "Authorization": "Bearer other"})
    assert records[0].raw == {}
    retained = repr(cache._catalogs)
    assert all(secret not in retained for secret in ["Secret-A", "Query-Secret", "Header-Secret", "tenant-A"])


def test_records_are_bound_to_exact_model_vendor_route_and_credentials():
    base = "https://chatgpt.com/backend-api/codex"
    headers = {"Authorization": "Bearer account-a"}
    record, = _remember(base, headers)
    assert record.stable_model_id == stable_model_id_for(
        "chatgpt_subscription", "Exact-Model", endpoint_id=cache.scope_for_endpoint(base, headers),
    )
    assert cache.cached_model_record("Exact-Model", base_url=base + "/responses", headers=headers,
                                     vendor="chatgpt-subscription") == record
    assert cache.cached_model_record("exact-model", base_url=base, headers=headers) is None
    assert cache.cached_model_record(" Exact-Model", base_url=base, headers=headers) is None
    assert cache.cached_model_record("Exact-Model", base_url=base, headers=headers, vendor="openai") is None
    assert cache.cached_model_record("Exact-Model", base_url=base, headers={"Authorization": "Bearer account-b"}) is None
    assert cache.cached_model_record("Exact-Model", base_url=base) is None
    assert cache.cached_model_record("Exact-Model", base_url="https://other.test/v1", headers=headers) is None
    assert cache.cached_model_record("Exact-Model", base_url="", headers=headers) is None


@pytest.mark.parametrize("headers", ["Bearer token", {"Authorization": 123}, {None: "token"}, {"": "token"}])
def test_malformed_header_identity_never_collapses_to_anonymous_scope(headers):
    assert cache.scope_for_endpoint("https://api.example/v1", headers) == ""
    assert _remember(headers=headers) == ()


@pytest.mark.parametrize("base_url", ["", None, "api.example/v1", "ftp://api.example/v1", "https://api.example:bad/v1"])
def test_missing_or_invalid_route_has_no_unscoped_fallback(base_url):
    assert cache.scope_for_endpoint(base_url) == ""
    assert cache.remember_catalog(_payload(), base_url=base_url) == ()
    assert cache.cached_model_record("Exact-Model", base_url=base_url) is None


def test_successful_plain_or_empty_catalog_replaces_old_control_evidence():
    base = "https://chatgpt.com/backend-api/codex"
    _remember(base)
    assert cache.cached_model_record("Exact-Model", base_url=base).deterministic_controls
    _remember(base, payload=_payload())
    assert cache.cached_model_record("Exact-Model", base_url=base).deterministic_controls == ()
    _remember(base, payload={"models": []})
    assert cache.cached_model_record("Exact-Model", base_url=base) is None


def test_duplicate_inventory_id_is_ambiguous():
    base = "https://unknown.test/v1"
    cache.remember_catalog({"data": [{"id": "same"}, {"id": "same"}]}, base_url=base)
    assert cache.cached_model_record("same", base_url=base) is None


def test_snapshot_expiry_returns_unknown_without_a_probe(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(cache, "_CACHE_TTL_SECONDS", 5.0)
    base = "https://chatgpt.com/backend-api/codex"
    _remember(base)
    now[0] = 104.9
    assert cache.cached_model_record("Exact-Model", base_url=base) is not None
    now[0] = 105.0
    assert cache.cached_model_record("Exact-Model", base_url=base) is None


def test_catalog_count_is_bounded_and_recently_used_snapshot_survives(monkeypatch):
    monkeypatch.setattr(cache, "_MAX_CATALOGS", 2)
    first, second, third = (f"https://{name}.test/v1" for name in ("first", "second", "third"))
    _remember(first)
    _remember(second)
    assert cache.cached_model_record("Exact-Model", base_url=first) is not None
    _remember(third)
    assert cache.cached_model_record("Exact-Model", base_url=first) is not None
    assert cache.cached_model_record("Exact-Model", base_url=second) is None
    assert cache.cached_model_record("Exact-Model", base_url=third) is not None
