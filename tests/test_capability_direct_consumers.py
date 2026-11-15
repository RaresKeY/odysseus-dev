"""Cover capability filtering at editor and Ajax HTTP boundaries."""

import asyncio
import json
from dataclasses import replace

import httpx

from src import clean_agent_preview, editor_agent_model, llm_core
from src import model_capabilities as mc
from src import model_capability_cache as cache
from src.model_capability_readers import records_from_payload

BASE = "https://openrouter.ai/api/v1"
MODEL = "future-model"
HEADERS = {"Authorization": "Bearer account-a"}
MESSAGES = [{"role": "user", "content": "Plan a safe edit."}]


def restrict_temperature(monkeypatch):
    scope = cache.scope_for_endpoint(BASE, HEADERS)
    record, = records_from_payload(
        {"data": [{"id": MODEL}]},
        base_url=BASE, endpoint_id=scope, vendor="openrouter",
    )
    control = mc.DeterministicControl.build(
        control=mc.CONTROL_TEMPERATURE,
        status=mc.ASSERTION_UNSUPPORTED,
        source=mc.SOURCE_PROVIDER_READER,
        confidence=mc.CONFIDENCE_PROVIDER_REPORTED,
    )
    strict = replace(record, deterministic_controls=(control,))
    original = cache.cached_model_record

    def lookup(model, *, base_url, headers=None, vendor=None):
        if model == MODEL and cache.scope_for_endpoint(base_url, headers) == scope:
            return strict
        return original(model, base_url=base_url, headers=headers, vendor=vendor)

    monkeypatch.setattr(cache, "cached_model_record", lookup)


def test_editor_planner_filters_only_its_authorized_endpoint(monkeypatch):
    restrict_temperature(monkeypatch)
    sent = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, url, *, json, headers):
            sent.append((url, dict(json), headers))
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(editor_agent_model.httpx, "AsyncClient", lambda **kwargs: Client())
    assert asyncio.run(editor_agent_model._post_chat(
        BASE, MODEL, MESSAGES, HEADERS, 3,
    )) == "ok"
    assert "temperature" not in sent[-1][1]

    assert asyncio.run(editor_agent_model._post_chat(
        BASE, MODEL, MESSAGES, {"Authorization": "Bearer account-b"}, 3,
    )) == "ok"
    assert sent[-1][1]["temperature"] == 0.2
    assert sent[0][0].endswith("/chat/completions")


def test_ajax_preview_filters_at_each_http_attempt(monkeypatch):
    restrict_temperature(monkeypatch)
    sent = []

    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, content="data: [DONE]\\n\\n")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            request = {
                "model": MODEL, "messages": MESSAGES,
                "temperature": 0.2, "max_tokens": 64, "stream": True,
            }
            async with clean_agent_preview.preview_model_response(
                client, BASE + "/chat/completions", HEADERS, request, {},
            ) as result:
                assert result.status_code == 200
            return request

    original = asyncio.run(run())
    assert original["temperature"] == 0.2
    assert len(sent) == 1
    assert "temperature" not in sent[0]


def test_editor_uses_native_subscription_transport(monkeypatch):
    called = []

    async def native(url, model, messages, **kwargs):
        called.append((url, model, messages, kwargs))
        return "ok"

    monkeypatch.setattr(llm_core, "llm_call_async", native)
    result = asyncio.run(editor_agent_model._post_chat(
        "https://chatgpt.com/backend-api/codex", MODEL, MESSAGES,
        {"Authorization": "Bearer account-a"}, 3,
    ))
    assert result == "ok"
    assert called[0][0] == "https://chatgpt.com/backend-api/codex"
    assert called[0][3]["headers"]["Authorization"] == "Bearer account-a"
