"""Real turn authority, provider payloads and complete mocked HTTP execution."""
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import database as d
from src import llm_core
from src.chat_automation import AutomationStore, prepare_chat_turn
from src.foreground_model_routing import ForegroundModelPolicy

URL = "https://execution.invalid/v1/chat/completions"
HEADERS = {"Authorization": "Bearer SYNTHETIC"}
ASK = "  Preserve these exact edges.\n\tλ 🙂  "
MESSAGES = [{"role": "user", "content": ASK}]


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "binding.db"))
    d.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    store = AutomationStore(factory)
    with factory() as db:
        db.add(d.Session(id="chat", name="chat", owner="alice", model="selected", endpoint_url=URL))
        db.commit()
    monkeypatch.setattr(llm_core, "_get_cached_response", lambda *_: None)
    monkeypatch.setattr(llm_core, "_set_cached_response", lambda *a, **kw: None)
    monkeypatch.setattr(llm_core, "get_context_length", lambda *_: 32768)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda *_: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **kw: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *_: None)
    yield store
    engine.dispose()


async def bind(store, mode="auto", *, effort="low", url=URL, model="selected"):
    session = SimpleNamespace(id="chat", model=model, endpoint_url=url, headers=HEADERS,
                              endpoint_id=None, thinking_mode="effort:" + effort)
    turn = await prepare_chat_turn(store, "alice", session, ASK)
    # Classifier-to-route binding is covered separately. These execution tests
    # pin the actual descriptor while retaining the real persisted CAS/epoch.
    turn.policy["route_mode"] = mode
    return turn


def response(request, *, stream=False):
    payload = json.loads(request.content)
    if stream:
        data = {"model": payload["model"], "choices": [{"delta": {"content": "complete"}}]}
        return httpx.Response(200, text="data: " + json.dumps(data) + "\n\ndata: [DONE]\n\n")
    return httpx.Response(200, json={"model": payload["model"],
                                   "choices": [{"message": {"content": "complete"}}]})


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_actual_execution_uses_acknowledged_model_effort_and_exact_ask(store, monkeypatch, stream):
    turn, calls = await bind(store), []
    def transport(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert payload["model"] == "selected"
        assert payload["reasoning_effort"] == "low"
        assert payload["messages"] == MESSAGES
        assert request.headers["Authorization"] == HEADERS["Authorization"]
        return response(request, stream=stream)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
        with turn.activate():
            if stream:
                chunks = [chunk async for chunk in llm_core.stream_llm(URL, "selected", MESSAGES,
                    headers=HEADERS, session_id="chat", reasoning_effort="low", thinking_mode="off")]
                assert any("complete" in chunk for chunk in chunks)
            else:
                assert await llm_core.llm_call_async(URL, "selected", MESSAGES,
                    headers=HEADERS, session_id="chat", reasoning_effort="low") == "complete"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["model", "headers", "effort", "policy", "preset_utility"])
async def test_stale_or_mismatched_foreground_binding_never_dispatches(store, monkeypatch, change):
    turn, calls = await bind(store), []
    kwargs = {"headers": HEADERS, "session_id": "chat", "reasoning_effort": "low"}
    model = "selected"
    if change == "model": model = "injected"
    if change == "headers": kwargs["headers"] = {"Authorization": "Bearer OTHER"}
    if change == "effort": kwargs["reasoning_effort"] = "high"
    if change in {"policy", "preset_utility"}:
        store.update_configuration("alice", {"elevation_ceiling": True}, 0)
    if change == "preset_utility": kwargs["prompt_type"] = "utility"
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: calls.append(req))) as client:
        monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
        with turn.activate(), pytest.raises(HTTPException) as error:
            await llm_core.llm_call_async(URL, model, MESSAGES, **kwargs)
    assert error.value.status_code == 409 and error.value.fallback_eligible is False
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_revision_change_while_waiting_for_local_slot_is_rechecked(store, monkeypatch, stream):
    turn, calls = await bind(store), []
    @asynccontextmanager
    async def occupied_slot(*args):
        store.update_configuration("alice", {"elevation_ceiling": True}, 0)
        yield
    monkeypatch.setattr(llm_core, "_local_model_slot", occupied_slot)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: calls.append(req))) as client:
        monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
        with turn.activate(), pytest.raises(HTTPException) as error:
            if stream:
                _ = [chunk async for chunk in llm_core.stream_llm(URL, "selected", MESSAGES,
                    headers=HEADERS, session_id="chat", reasoning_effort="low")]
            else:
                await llm_core.llm_call_async(URL, "selected", MESSAGES,
                    headers=HEADERS, session_id="chat", reasoning_effort="low")
    assert error.value.status_code == 409 and calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_auto_route_does_not_retry_or_try_another_execution_provider(store, monkeypatch, stream):
    turn, calls = await bind(store), []
    def transport(request):
        calls.append(request)
        return httpx.Response(503, text="synthetic unavailable")
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
        with turn.activate():
            candidates = [(URL, "selected", HEADERS), ("https://other.invalid/v1/chat/completions", "other", {})]
            if stream:
                chunks = [chunk async for chunk in llm_core.stream_llm_with_fallback(candidates,
                    MESSAGES, session_id="chat", reasoning_effort="low", fallback_statuses={503})]
                assert any("error" in chunk for chunk in chunks)
            else:
                with pytest.raises(HTTPException):
                    await llm_core.llm_call_async_with_route_fallback(candidates, MESSAGES,
                        session_id="chat", reasoning_effort="low", fallback_statuses={503}, max_retries=4)
    assert len(calls) == 1 and calls[0].url.host == "execution.invalid"


@pytest.mark.asyncio
async def test_unsupported_effective_effort_fails_without_weakening_provider_rules(store, monkeypatch):
    url = "https://api.openai.com/v1/chat/completions"
    turn, calls = await bind(store, url=url, model="gpt-5.2", effort="high"), []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: calls.append(req))) as client:
        monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
        with turn.activate():
            chunks = [chunk async for chunk in llm_core.stream_llm(url, "gpt-5.2", MESSAGES,
                headers=HEADERS, session_id="chat", reasoning_effort="high",
                tools=[{"type": "function", "function": {"name": "test", "parameters": {}}}])]
    assert calls == [] and any("error" in chunk for chunk in chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize("consent", [True, False])
@pytest.mark.parametrize("stream", [False, True])
async def test_manual_fallback_requires_current_explicit_owner_consent(store, monkeypatch, consent, stream):
    from src import endpoint_resolver, foreground_model_routing
    turn, calls = await bind(store, "manual"), []
    fallback = ("https://fallback.invalid/v1/chat/completions", "backup", HEADERS)
    policy = ForegroundModelPolicy(enabled=consent, fallback_candidates=(fallback,),
                                    fallback_descriptors=({"endpoint_id": "backup-endpoint"},))
    monkeypatch.setattr(foreground_model_routing, "resolve_foreground_model_policy", lambda owner: policy)
    def resolve(endpoint_id, model, *, owner, require_exact_model):
        assert (endpoint_id, model, owner, require_exact_model) == ("backup-endpoint", "backup", "alice", True)
        return fallback
    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint_by_id", resolve)
    def transport(request):
        calls.append(request)
        if request.url.host == "execution.invalid":
            return httpx.Response(503, text="synthetic unavailable")
        return response(request, stream=stream)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
        with turn.activate():
            if stream:
                chunks = [chunk async for chunk in llm_core.stream_llm_with_fallback(
                    [(URL, "selected", HEADERS), fallback], MESSAGES, session_id="chat",
                    reasoning_effort="low", fallback_statuses={503})]
                assert any("complete" in chunk for chunk in chunks) is consent
            elif consent:
                answer, route, model = await llm_core.llm_call_async_with_route_fallback(
                    [(URL, "selected", HEADERS), fallback], MESSAGES, session_id="chat",
                    reasoning_effort="low", fallback_statuses={503}, max_retries=1)
                assert answer == "complete" and model == "backup" and route == fallback
            else:
                with pytest.raises(HTTPException):
                    await llm_core.llm_call_async_with_route_fallback(
                        [(URL, "selected", HEADERS), fallback], MESSAGES, session_id="chat",
                        reasoning_effort="low", fallback_statuses={503}, max_retries=1)
    assert len(calls) == (2 if consent else 1)
    assert (turn.descriptor.get("source") == "explicit_fallback") is consent
