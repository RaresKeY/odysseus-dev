"""Exercise canonical catalog evidence at the real HTTP request boundary."""

import asyncio
import json

import httpx
import pytest

from src import chatgpt_subscription, llm_core, model_capability_cache

BASE = "https://chatgpt.com/backend-api/codex"
MESSAGES = [
    {"role": "system", "content": "Keep the protocol instructions."},
    {"role": "user", "content": "Say hello."},
]
MODELS = ["gpt-5.6-luna", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-6.1-sol", "gpt-4.1", "unlisted-future-model"]


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    model_capability_cache.clear()
    llm_core._response_cache.clear()
    llm_core._response_model_cache.clear()
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda *_: False)
    monkeypatch.setattr(llm_core, "get_context_length", lambda *_: 8192)
    yield
    model_capability_cache.clear()
    llm_core._response_cache.clear()
    llm_core._response_model_cache.clear()


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("temperature", [0, 0.2, 1])
def test_subscription_contract_is_independent_of_model_name_and_temperature(model, temperature):
    payload = llm_core._build_chatgpt_responses_payload(
        model, MESSAGES, temperature, 256, stream=True, reasoning_effort="high",
        tools=[{"type": "web_search"}],
    )
    assert set(payload) == {"model", "instructions", "input", "stream", "store", "reasoning"}
    assert payload["model"] == model
    assert payload["instructions"] == MESSAGES[0]["content"]
    assert payload["input"][0]["role"] == "user"
    assert payload["reasoning"] == {"effort": "high"}
    assert payload["store"] is False


def test_final_payload_filter_cannot_reintroduce_temperature():
    assert llm_core._strip_chatgpt_native_tool_surfaces({
        "model": "future", "temperature": 0, "max_output_tokens": 10,
        "tools": [{"type": "web_search"}], "input": [],
    }) == {"model": "future", "input": []}


def fetched_catalog(monkeypatch, token, levels):
    payload = {"models": [{
        "slug": "future-model", "priority": 1, "supported_reasoning_levels": levels,
        "default_reasoning_level": levels[0] if levels else None,
    }]}
    monkeypatch.setattr(chatgpt_subscription.httpx, "get", lambda *a, **k: httpx.Response(200, json=payload))
    assert chatgpt_subscription.fetch_available_models(token) == ["future-model"]


async def send_subscription(monkeypatch, headers, effort, *, asynchronous=False):
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        events = [
            {"type": "response.output_text.delta", "delta": "hello"},
            {"type": "response.completed", "response": {"usage": {"input_tokens": 1, "output_tokens": 1}}},
        ]
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            content="".join("data: " + json.dumps(event) + "\n\n" for event in events),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
        if asynchronous:
            result = await llm_core.llm_call_async(
                BASE, "future-model", MESSAGES, headers=headers,
                temperature=0.2, max_tokens=64, reasoning_effort=effort,
            )
            assert result == "hello"
        else:
            result = [chunk async for chunk in llm_core.stream_llm(
                BASE + "/responses", "future-model", MESSAGES, headers=headers,
                temperature=0.2, max_tokens=64, reasoning_effort=effort,
            )]
            assert result[-1] == "data: [DONE]\n\n"
    assert len(captured) == 1
    assert "temperature" not in captured[0]
    assert "max_output_tokens" not in captured[0]
    return captured[0]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("effort,expected", [("high", {"effort": "high"}), ("low", None)])
def test_fetched_native_evidence_reaches_stream_and_async_wire_payload(monkeypatch, asynchronous, effort, expected):
    fetched_catalog(monkeypatch, "account-a", ["high"])
    payload = asyncio.run(send_subscription(
        monkeypatch, chatgpt_subscription.chatgpt_headers("account-a"), effort,
        asynchronous=asynchronous,
    ))
    assert payload.get("reasoning") == expected


def test_same_model_on_another_account_cannot_supply_control_evidence(monkeypatch):
    fetched_catalog(monkeypatch, "account-a", ["high"])
    fetched_catalog(monkeypatch, "account-b", ["low"])
    a = asyncio.run(send_subscription(monkeypatch, chatgpt_subscription.chatgpt_headers("account-a"), "high"))
    b = asyncio.run(send_subscription(monkeypatch, chatgpt_subscription.chatgpt_headers("account-b"), "high"))
    assert a["reasoning"] == {"effort": "high"}
    assert "reasoning" not in b


def test_identity_only_refresh_clears_previous_constraints(monkeypatch):
    fetched_catalog(monkeypatch, "account-a", ["high"])
    monkeypatch.setattr(chatgpt_subscription.httpx, "get", lambda *a, **k: httpx.Response(
        200, json={"models": [{"slug": "future-model"}]},
    ))
    chatgpt_subscription.fetch_available_models("account-a")
    payload = asyncio.run(send_subscription(monkeypatch, chatgpt_subscription.chatgpt_headers("account-a"), "low"))
    # Unknown metadata preserves existing explicit-effort behavior.
    assert payload["reasoning"] == {"effort": "low"}


@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1",
    "https://openrouter.ai/api/v1",
    "https://custom.example/v1",
])
def test_same_model_name_on_other_compatible_providers_keeps_temperature(monkeypatch, url):
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
            return await llm_core.llm_call_async(
                url, "gpt-6-astra", MESSAGES, temperature=0.2, max_tokens=64,
            )

    assert asyncio.run(run()) == "hello"
    assert captured[0]["temperature"] == 0.2


@pytest.mark.parametrize("url", [
    "https://chatgpt.com/v1",
    "https://sub.chatgpt.com/backend-api/codex",
    "https://chatgpt.com.evil.example/backend-api/codex",
])
def test_catalog_vendor_identity_does_not_select_subscription_transport(url):
    assert llm_core._detect_provider(url) != "chatgpt-subscription"


def test_response_cache_is_partitioned_when_control_evidence_changes(monkeypatch):
    headers = chatgpt_subscription.chatgpt_headers("account-a")
    before = llm_core._get_cache_key(BASE, "future-model", MESSAGES, 0.2, 64, headers=headers)
    fetched_catalog(monkeypatch, "account-a", ["high"])
    after = llm_core._get_cache_key(BASE, "future-model", MESSAGES, 0.2, 64, headers=headers)
    assert before != after
    fetched_catalog(monkeypatch, "account-a", ["low"])
    changed = llm_core._get_cache_key(BASE, "future-model", MESSAGES, 0.2, 64, headers=headers)
    assert after != changed


def test_catalog_provider_identity_is_not_overridden_by_compatible_dialect():
    # A local Ollama native catalog can be served beside an OpenAI-compatible
    # inference endpoint; the transport's legacy fallback is not vendor proof.
    url = "https://custom.example/v1"
    model_capability_cache.remember_catalog(
        {"models": [{"name": "future-model", "model": "future-model", "details": {"family": "llama"}}]},
        base_url=url, vendor="ollama",
    )
    controls = llm_core._request_controls_for(
        url, "future-model", None, provider="openai", dialect="openai_chat"
    )
    assert controls.vendor == "ollama"
    assert controls.record is not None


@pytest.mark.parametrize("catalog,expected", [
    ({"models": [{"id": "not-a-codex-slug"}]}, []),
    ({"data": [{"id": "not-a-codex-catalog"}]}, []),
    ({"models": [{"slug": "identity-only"}, {"id": "other"}]}, ["identity-only"]),
])
def test_subscription_discovery_keeps_native_slug_identity(monkeypatch, catalog, expected):
    monkeypatch.setattr(chatgpt_subscription.httpx, "get", lambda *a, **k: httpx.Response(200, json=catalog))
    assert chatgpt_subscription.fetch_available_models("account-a") == expected


def test_account_fallback_resolves_control_evidence_for_each_actual_request(monkeypatch):
    fetched_catalog(monkeypatch, "account-a", ["high"])
    fetched_catalog(monkeypatch, "account-b", ["low"])
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        if request.headers["authorization"] == "Bearer account-a":
            return httpx.Response(429, text="quota exhausted")
        events = [
            {"type": "response.output_text.delta", "delta": "hello"},
            {"type": "response.completed", "response": {"usage": {"input_tokens": 1, "output_tokens": 1}}},
        ]
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="".join(
            "data: " + json.dumps(event) + "\n\n" for event in events
        ))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
            return await llm_core.llm_call_async_with_fallback([
                (BASE, "future-model", chatgpt_subscription.chatgpt_headers("account-a")),
                (BASE, "future-model", chatgpt_subscription.chatgpt_headers("account-b")),
            ], MESSAGES, temperature=0.2, max_tokens=64, reasoning_effort="high")

    assert asyncio.run(run()) == "hello"
    assert len(captured) == 2
    assert captured[0]["reasoning"] == {"effort": "high"}
    assert "reasoning" not in captured[1]
    assert all("temperature" not in payload for payload in captured)


def test_advertised_future_effort_keeps_its_wire_spelling(monkeypatch):
    fetched_catalog(monkeypatch, "account-a", ["future-effort"])
    payload = asyncio.run(send_subscription(
        monkeypatch, chatgpt_subscription.chatgpt_headers("account-a"), "future-effort",
    ))
    assert payload["reasoning"] == {"effort": "future-effort"}


def test_async_retry_reloads_tightened_control_evidence(monkeypatch):
    from dataclasses import replace
    from src import model_capabilities as mc
    from src.model_capability_readers import records_from_payload

    url = "https://openrouter.ai/api/v1"
    model = "future-model"
    scope = model_capability_cache.scope_for_endpoint(url)
    record, = records_from_payload(
        {"data": [{"id": model}]}, base_url=url, endpoint_id=scope, vendor="openrouter",
    )
    unsupported = mc.DeterministicControl.build(
        control=mc.CONTROL_TEMPERATURE,
        status=mc.ASSERTION_UNSUPPORTED,
        source=mc.SOURCE_PROVIDER_READER,
        confidence=mc.CONFIDENCE_PROVIDER_REPORTED,
    )
    strict_record = replace(record, deterministic_controls=(unsupported,))
    state = {"strict": False}
    monkeypatch.setattr(
        model_capability_cache, "cached_model_record",
        lambda *args, **kwargs: strict_record if state["strict"] else record,
    )
    monkeypatch.setattr(llm_core.LLMConfig, "RETRY_DELAY", 0)
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: None)
    captured = []

    async def post(_client, target, _headers, **kwargs):
        captured.append(kwargs["json"])
        if len(captured) == 1:
            state["strict"] = True
            return httpx.Response(429, text="try again")
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", post)
    before = llm_core._get_cache_key(url, model, MESSAGES, 0.2, 64)
    response = asyncio.run(llm_core.llm_call_async(
        url, model, MESSAGES, temperature=0.2, max_tokens=64, max_retries=2,
    ))
    after = llm_core._get_cache_key(url, model, MESSAGES, 0.2, 64)
    assert response == "hello"
    assert captured[0]["temperature"] == 0.2
    assert "temperature" not in captured[1]
    assert before != after
    assert llm_core._response_cache.get(after) == "hello"
    assert before not in llm_core._response_cache
