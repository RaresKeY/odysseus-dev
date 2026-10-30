"""Native llama-swap catalog controls must remain route-scoped."""

import asyncio
import json

import httpx
import pytest

from src import llm_core, model_capability_cache as cache
from src import model_capability_readers as readers
from src.provider_capability_schemas import resolve_provider

URL = "https://gateway.example/swap/v1"
MODEL = "served-llama"
PAYLOAD = {"data": [
    {"id": MODEL, "meta": {"llamaswap": {"reasoning_efforts": ["medium", "High", "medium", "xhigh"]}}},
    {"id": "plain", "meta": {"llamaswap": {}}},
]}


@pytest.fixture(autouse=True)
def clean_catalogs():
    cache.clear()
    llm_core._response_cache.clear()
    llm_core._response_model_cache.clear()
    yield
    cache.clear()
    llm_core._response_cache.clear()
    llm_core._response_model_cache.clear()


def test_native_evidence_is_scoped_and_preserves_allowed_order():
    resolved = resolve_provider(PAYLOAD, base_url=URL)
    assert resolved.provider_id == "llamaswap"
    rows = readers.records_from_payload(PAYLOAD, base_url=URL)
    assert {row.vendor for row in rows} == {"llamaswap"}
    (effort,) = rows[0].deterministic_controls
    assert effort.control == "reasoning_effort"
    assert effort.status == "claimed"
    assert list(dict(effort.evidence)["allowed_values"]) == ["medium", "high", "xhigh"]
    assert rows[1].deterministic_controls == ()


def test_generic_and_known_vendor_envelopes_do_not_acquire_llamaswap_evidence():
    generic = {"data": [{"id": MODEL, "reasoning_efforts": ["high"]}]}
    assert readers.records_from_payload(generic, base_url=URL)[0].deterministic_controls == ()
    recognized = readers.records_from_payload(PAYLOAD, base_url="https://openrouter.ai/api/v1")
    assert all(row.vendor == "openrouter" for row in recognized)
    assert all(not row.deterministic_controls for row in recognized)


@pytest.mark.parametrize(("value", "expected"), [
    ("medium", True),
    ("high", True),
    ("xhigh", True),
    ("low", False),
])
def test_scoped_http_request_control(monkeypatch, value, expected):
    headers = {"Authorization": "Bearer route-a"}
    cache.remember_catalog(PAYLOAD, base_url=URL, headers=headers)
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
            monkeypatch.setattr(llm_core, "_is_host_dead", lambda *_: False)
            monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
            monkeypatch.setattr(llm_core, "get_context_length", lambda *a: 8192)
            return await llm_core.llm_call_async(
                URL, MODEL, [{"role": "user", "content": "Hi"}],
                headers=headers, reasoning_effort=value, max_tokens=64,
            )

    assert asyncio.run(run()) == "ok"
    assert ("reasoning_effort" in captured[0]) is expected
    if expected:
        assert captured[0]["reasoning_effort"] == value


def test_no_cross_account_evidence(monkeypatch):
    headers = {"Authorization": "Bearer route-a"}
    cache.remember_catalog(PAYLOAD, base_url=URL, headers=headers)
    missing = llm_core._request_controls_for(
        URL, MODEL, {"Authorization": "Bearer route-b"},
        provider="openai", dialect="openai_chat",
    )
    assert missing.record is None
    assert missing.permits("reasoning_effort", "low")
    matched = llm_core._request_controls_for(
        URL, MODEL, headers, provider="openai", dialect="openai_chat",
    )
    assert matched.record is not None
    assert not matched.permits("reasoning_effort", "low")


def test_path_prefixes_do_not_share_catalog():
    headers = {"Authorization": "Bearer route-a"}
    cache.remember_catalog(PAYLOAD, base_url=URL, headers=headers)
    assert cache.cached_model_record(MODEL, base_url="https://gateway.example/plain/v1", headers=headers) is None
