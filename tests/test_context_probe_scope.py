"""Context probes use the admitted account, route and bounded cache lifetime."""
from types import SimpleNamespace

import httpx
import pytest

from src import model_context as context

URL = "https://gateway.example/team/v1/chat/completions"
MODEL = "served-fixture"


@pytest.fixture(autouse=True)
def isolated_context(monkeypatch):
    for cache in (context._context_cache, context._catalog_ctx_cache, context._ollama_runtime_endpoints):
        cache.clear()
    monkeypatch.setattr(context, "_configured_endpoint_kind", lambda _: None)
    monkeypatch.setattr("src.endpoint_resolver.resolve_url", lambda url: url)
    yield
    for cache in (context._context_cache, context._catalog_ctx_cache, context._ollama_runtime_endpoints):
        cache.clear()


def response(size, model=MODEL):
    return httpx.Response(200, json={"data": [{"id": model, "context_length": size}]})


def test_context_and_catalog_caches_are_account_scoped(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return response(8192 if kwargs["headers"]["Authorization"] == "Bearer first" else 32768)

    monkeypatch.setattr(context.httpx, "get", get)
    first, second = {"Authorization": "Bearer first"}, {"Authorization": "Bearer second"}
    assert context.get_context_length(URL, MODEL, headers=first, endpoint_kind="proxy") == 8192
    assert context.get_context_length(URL, MODEL, headers=second, endpoint_kind="proxy") == 32768
    assert context.get_context_length(URL, MODEL, headers=first, endpoint_kind="proxy") == 8192
    assert len(calls) == 2
    assert all(kwargs["follow_redirects"] is False for _, kwargs in calls)
    assert "Bearer" not in repr(context._context_cache) + repr(context._catalog_ctx_cache)


def test_selected_headers_never_trigger_global_registration_lookup(monkeypatch):
    def forbidden(_):
        raise AssertionError("An admitted request cannot search unrelated endpoint rows")

    monkeypatch.setattr(context, "_configured_endpoint_kind", forbidden)
    monkeypatch.setattr(context.httpx, "get", lambda url, **kwargs: response(8192))
    assert context.get_context_length(URL, MODEL, headers={"Authorization": "Bearer selected"}) == 8192


def test_anonymous_probe_cannot_borrow_previous_account_evidence(monkeypatch):
    calls = []

    def get(url, **kwargs):
        headers = kwargs.get("headers") or {}
        calls.append(headers)
        return response(32768 if headers else 4096)

    monkeypatch.setattr(context.httpx, "get", get)
    assert context.get_context_length(URL, MODEL, headers={"Authorization": "Bearer selected"}) == 32768
    assert context.get_context_length(URL, MODEL, headers={}) == 4096
    assert calls == [{"Authorization": "Bearer selected"}, {}]


def test_credential_rotation_and_route_prefixes_replace_cache_identity(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append((url, dict(kwargs["headers"])))
        return response(8192 * len(calls))

    monkeypatch.setattr(context.httpx, "get", get)
    headers = {"Authorization": "Bearer old"}
    assert context.get_context_length(URL, MODEL, headers=headers, endpoint_kind="proxy") == 8192
    headers["Authorization"] = "Bearer new"
    assert context.get_context_length(URL, MODEL, headers=headers, endpoint_kind="proxy") == 16384
    assert context.get_context_length(URL.replace("/team/", "/other/"), MODEL, headers=headers, endpoint_kind="proxy") == 24576
    assert len(calls) == 3


def test_cache_expiry_refreshes_the_same_account(monkeypatch):
    clock = [1.0]
    monkeypatch.setattr(context, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return response(8192 * len(calls))

    monkeypatch.setattr(context.httpx, "get", get)
    headers = {"Authorization": "Bearer fixture"}
    assert context.get_context_length(URL, MODEL, headers=headers, endpoint_kind="proxy") == 8192
    clock[0] += context._CACHE_TTL_SECONDS + 1
    assert context.get_context_length(URL, MODEL, headers=headers, endpoint_kind="proxy") == 16384


def test_cache_scope_count_is_bounded(monkeypatch):
    monkeypatch.setattr(context, "_MAX_CACHE_SCOPES", 2)
    monkeypatch.setattr(context.httpx, "get", lambda url, **kwargs: response(8192))
    for i in range(5):
        context.get_context_length(URL, MODEL, headers={"X-Account": str(i)}, endpoint_kind="proxy")
    assert len(context._context_cache) == len(context._catalog_ctx_cache) == 2


def test_redirect_or_rewritten_origin_receives_no_credentials(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(302, headers={"location": "https://other.example/models"})

    monkeypatch.setattr(context.httpx, "get", get)
    headers = {"Authorization": "Bearer selected"}
    assert context.get_context_length_known(URL, MODEL, headers=headers) == (context.DEFAULT_CONTEXT, False)
    assert len(calls) == 1 and calls[0][1]["follow_redirects"] is False
    monkeypatch.setattr("src.endpoint_resolver.build_models_url", lambda _: "https://other.example/models")
    assert context.get_context_length_known(URL, MODEL, headers=headers) == (context.DEFAULT_CONTEXT, False)
    assert len(calls) == 1


def test_authenticated_private_server_overrides_name_table_without_registry_search(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        assert kwargs["headers"] == {"Authorization": "Bearer selected"}
        return response(4096, "qwen3-fixture") if url.endswith("/models") else httpx.Response(404)

    monkeypatch.setattr(context.httpx, "get", get)
    url = "http://192.168.1.50:8092/team/v1/chat/completions"
    assert context.get_context_length(url, "qwen3-fixture", headers={"Authorization": "Bearer selected"}) == 4096
    assert all("/team/" in url for url in calls)
    assert calls[-1].endswith("/team/v1/models")


def test_loaded_ollama_allocation_uses_selected_credentials(monkeypatch):
    def get(url, **kwargs):
        assert kwargs["headers"] == {"Authorization": "Bearer selected"}
        if url.endswith("/api/ps"):
            return httpx.Response(200, json={"models": [{"name": "served-fixture:latest", "context_length": 4096}]})
        return httpx.Response(404)

    monkeypatch.setattr(context.httpx, "get", get)
    assert context.get_context_length("http://localhost:11434/v1/chat/completions", MODEL,
                                      headers={"Authorization": "Bearer selected"}, endpoint_kind="api") == 4096


def test_ambiguous_model_aliases_do_not_choose_first_catalog_row(monkeypatch):
    monkeypatch.setattr(context.httpx, "get", lambda url, **kwargs: httpx.Response(200, json={"data": [
        {"id": "one/served-fixture", "context_length": 8192},
        {"id": "two/served-fixture", "context_length": 32768},
    ]}))
    assert context.get_context_length_known(URL, MODEL, headers={}, endpoint_kind="proxy") == (context.DEFAULT_CONTEXT, False)
    assert context.get_context_length(URL, "two/served-fixture", headers={}, endpoint_kind="proxy") == 32768


@pytest.mark.parametrize("invalid", [True, False, float("inf"), float("nan"), -1, 0, .5, 1.5, 10**400])
def test_invalid_context_numbers_stay_unknown(invalid):
    assert context._model_ctx_from_entry({"context_length": invalid}) is None


def test_budget_forwards_selected_headers(monkeypatch):
    seen = []

    def get(url, model, **kwargs):
        seen.append(kwargs)
        return 8192, True

    monkeypatch.setattr(context, "get_context_length_known", get)
    assert context.budget_context_for_model(URL, MODEL, headers={"Authorization": "Bearer selected"}) == 8192
    assert seen == [{"headers": {"Authorization": "Bearer selected"}}]


@pytest.mark.parametrize("available", [False, True])
def test_session_metadata_revalidates_registration_instead_of_reusing_saved_headers(monkeypatch, available):
    calls = []
    session = SimpleNamespace(endpoint_id="selected", endpoint_url=URL, model=MODEL,
                              headers={"Authorization": "Bearer stale"})
    monkeypatch.setattr("src.owner_identity.effective_storage_owner", lambda owner: owner)
    monkeypatch.setattr("src.model_admission.require_owner_model", lambda owner, model: None)

    def resolve(endpoint_id, model, *, owner, require_exact_model):
        assert (endpoint_id, model, owner, require_exact_model) == ("selected", MODEL, "alice", True)
        return (URL, MODEL, {"Authorization": "Bearer current"}) if available else None

    monkeypatch.setattr("src.endpoint_resolver.resolve_endpoint_by_id", resolve)
    monkeypatch.setattr(context, "get_context_length", lambda *args, **kwargs: calls.append(kwargs) or 8192)
    assert context.get_session_context_length(session, owner="alice") == (8192 if available else context.DEFAULT_CONTEXT)
    assert calls == ([{"headers": {"Authorization": "Bearer current"}}] if available else [])


def test_session_metadata_without_owner_never_resolves_credentials(monkeypatch):
    session = SimpleNamespace(endpoint_id="selected", endpoint_url=URL, model=MODEL)
    monkeypatch.setattr("src.owner_identity.effective_storage_owner", lambda owner: None)
    monkeypatch.setattr("src.endpoint_resolver.resolve_endpoint_by_id", lambda *a, **k: pytest.fail("ownerless lookup"))
    assert context.get_session_context_length(session, owner=None) == context.DEFAULT_CONTEXT
