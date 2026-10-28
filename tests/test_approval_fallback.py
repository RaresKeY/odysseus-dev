"""Offline complete provider fallback, accounting and exact dispatch contracts."""
import asyncio
from dataclasses import replace
import json
import time

import httpx
import pytest

from tests.test_approval_review import proposal, decision, evaluator, claim
from tests.test_chat_automation_state import store
from tests.test_chat_approval_sql_integration import dispatch_runtime
from tests.test_chat_automation_api import api
from tests.test_model_auto import registered, CATEGORIES, Reviewer
from src.approval_review import ApprovalReviewService
from src.approval_fallback import ConversationAssessment, ConversationReviewProvider
from src.chat_automation import prepare_chat_turn
from src.decisions import DecisionError


LABELS = dict(risk_level="low", user_authorization="high", policy_check="clear", effect_scope="narrow")


def fallback_result(**changes):
    return ConversationAssessment(changes.pop("labels", dict(LABELS)), changes.pop("uncertain", False),
        {"input_tokens": 30, "output_tokens": 10}, "fallback-attempt",
        {"model": "selected-model", "endpoint_id": "selected-provider", "protocol": "approval_openai"}, **changes)


@pytest.mark.asyncio
@pytest.mark.parametrize("primary", [None, DecisionError("decision_provider_not_configured"),
    DecisionError("decision_http_error"), DecisionError("decision_transport_failed"),
    DecisionError("decision_invalid_answers"), DecisionError("decision_model_mismatch"), object()])
async def test_technical_failure_or_absence_uses_one_independent_fallback(proposal, primary):
    store, pending, policy, _ = proposal
    first, first_calls = evaluator(primary)
    second, second_calls = evaluator(fallback_result())
    result = await ApprovalReviewService(store).review(pending, policy, deterministically_admitted=True,
        evaluator=first if primary is not None else None, fallback_evaluator=second)
    assert result.status == "approved" and len(second_calls) == 1
    assert result.decision["decision_source"] == "conversation_model"
    assert result.decision["probabilities"] is None and result.decision["confidence"] is None
    assert claim(result.approval, pending) and not claim(result.approval, pending)
    if first_calls:
        assert first_calls[0][0] is second_calls[0][0]
        assert first_calls[0][1].deadline < second_calls[0][1].deadline <= time.monotonic() + 60


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [decision("deny"), decision("review"), decision(confidence=.5), decision(risk="critical")])
async def test_valid_primary_stop_is_final(proposal, answer):
    store, pending, policy, _ = proposal
    first, _ = evaluator(answer)
    second, calls = evaluator(fallback_result())
    result = await ApprovalReviewService(store).review(pending, policy, deterministically_admitted=True,
        evaluator=first, fallback_evaluator=second)
    assert result.status in {"denied", "uncertain"} and not calls and result.approval is None


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["decision_budget_unavailable", "decision_capture_store_unavailable", "decision_cancelled"])
async def test_accounting_or_cancellation_does_not_admit_fallback(proposal, code):
    store, pending, policy, _ = proposal
    first, _ = evaluator(DecisionError(code))
    second, calls = evaluator(fallback_result())
    result = await ApprovalReviewService(store).review(pending, policy, deterministically_admitted=True,
        evaluator=first, fallback_evaluator=second)
    assert result.approval is None and not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [fallback_result(uncertain=True), fallback_result(labels={**LABELS, "risk_level": "critical"}), DecisionError("decision_transport_failed")])
async def test_fallback_uncertainty_deny_and_dual_failure_stop(proposal, fallback):
    store, pending, policy, _ = proposal
    first, _ = evaluator(DecisionError("decision_http_error"))
    second, calls = evaluator(fallback)
    result = await ApprovalReviewService(store).review(pending, policy, deterministically_admitted=True,
        evaluator=first, fallback_evaluator=second)
    assert len(calls) == 1 and result.approval is None
    assert result.status in {"uncertain", "denied", "error"}


@pytest.mark.asyncio
async def test_primary_timeout_leaves_time_for_fallback_and_task_cancel_propagates(proposal):
    store, pending, policy, _ = proposal
    async def hanging(*_):
        await asyncio.Event().wait()
    second, calls = evaluator(fallback_result())
    result = await ApprovalReviewService(store).review(pending, policy, deterministically_admitted=True,
        evaluator=hanging, fallback_evaluator=second, deadline=time.monotonic() + .1)
    assert result.status == "approved" and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["primary", "fallback"])
async def test_cancelling_pending_review_never_starts_another_call(proposal, phase):
    store, pending, policy, _ = proposal
    started = asyncio.Event()
    async def hanging(*_):
        started.set()
        await asyncio.Event().wait()
    second, calls = evaluator(fallback_result())
    task = asyncio.create_task(ApprovalReviewService(store).review(pending, policy,
        deterministically_admitted=True, evaluator=hanging if phase == "primary" else None,
        fallback_evaluator=second if phase == "primary" else hanging))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not calls and store.peek(pending.approval_id) is None


def install_fallback(monkeypatch, handler):
    from src import approval_fallback
    monkeypatch.setattr(approval_fallback, "ConversationReviewProvider", lambda store, bound:
        ConversationReviewProvider(store, bound, client_factory=lambda **kwargs:
            httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)))


def openai_answer(model="manual", **changes):
    return {"model": model, "usage": {"prompt_tokens": 30, "completion_tokens": 10},
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({**LABELS, "uncertain": False, **changes})}}]}


@pytest.mark.asyncio
async def test_absent_jev_uses_selected_route_and_records_real_usage(store, dispatch_runtime, monkeypatch):
    from src.decisions.observability import default_observability
    rt = dispatch_runtime
    store.update_configuration("alice", {"decision_provider": None}, 1)
    calls = []
    def transport(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert str(request.url) == "http://local/v1/chat/completions"
        assert payload["model"] == "manual" and "tools" not in payload
        assert "Original private ask" in payload["messages"][1]["content"]
        return httpx.Response(200, json=openai_answer())
    install_fallback(monkeypatch, transport)
    store.update_chat("alice", "a", {"approval_mode": "auto_approve"}, 0)
    bound = await prepare_chat_turn(store, "alice", rt.session, "Original private ask")
    _, result = await rt.dispatch(bound)
    assert result["exit_code"] == 0 and len(calls) == len(rt.effects) == 1 and not rt.calls
    assert result["approval_review"]["decision_source"] == "conversation_model"
    records = rt.attempts.records_for_run("alice", "a", bound.run_id)
    assert len(records) == 1 and records[0]["usage"] == {"input_tokens": 30, "output_tokens": 10}
    item = default_observability(store.session_factory).history("alice", "a")["items"][0]
    assert item["provider"]["model"] == "manual" and item["provider"]["distribution_source"] == "not_reported"
    assert item["approval"]["fallback_reason"] == "decision_provider_not_configured"
    assert "Original private ask" not in json.dumps(item)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "malformed", "transport"])
async def test_failed_real_jev_keeps_held_usage_and_links_one_fallback(store, dispatch_runtime, monkeypatch, failure):
    from src import decisions
    from src.decisions import ResolvedDecisionEndpoint
    from src.decisions.provider import DecisionProvider
    rt = dispatch_runtime
    def primary(request):
        if failure == "transport":
            raise httpx.ReadTimeout("synthetic timeout")
        if failure == "http":
            return httpx.Response(401, json={"error": "synthetic"})
        return httpx.Response(200, json={"model": "decision-model", "answers": {}, "usage": {"input_tokens": 12, "output_tokens": 3}})
    provider = DecisionProvider(rt.attempts, resolver=lambda *_: ResolvedDecisionEndpoint(
        "reviewer", "decision-model", "systemone", "https://synthetic.invalid/v1/systemone", {}),
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(primary), **kwargs))
    monkeypatch.setattr(decisions, "DecisionProvider", lambda *_: provider)
    calls = []
    def fallback(request):
        calls.append(request)
        return httpx.Response(200, json=openai_answer())
    install_fallback(monkeypatch, fallback)
    store.update_chat("alice", "a", {"approval_mode": "auto_approve"}, 0)
    bound = await prepare_chat_turn(store, "alice", rt.session, "Write the file")
    _, result = await rt.dispatch(bound)
    assert result["exit_code"] == 0 and len(calls) == len(rt.effects) == 1
    records = rt.attempts.records_for_run("alice", "a", bound.run_id)
    assert len(records) == 2
    first = next(r for r in records if not r.get("fallback_of"))
    second = next(r for r in records if r.get("fallback_of"))
    assert second["fallback_of"] == first["attempt_id"] and second["status"] == "settled"
    assert first["status"] == ("rejected" if failure == "malformed" else "unresolved")
    assert first["usage"] == ({"input_tokens": 12, "output_tokens": 3} if failure == "malformed" else None)
    with pytest.raises(DecisionError, match="decision_budget_unavailable"):
        rt.attempts.reserve({**second, "attempt_id": "duplicate"}, max_calls=32)


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["before", "during"])
async def test_changed_conversation_model_invalidates_review(store, dispatch_runtime, monkeypatch, when):
    from core import database as d
    rt = dispatch_runtime
    store.update_configuration("alice", {"decision_provider": None}, 1)
    store.update_chat("alice", "a", {"approval_mode": "auto_approve"}, 0)
    bound = await prepare_chat_turn(store, "alice", rt.session, "write")
    def change():
        with store.transaction() as db:
            db.get(d.Session, "a").model = "changed-model"
    def fallback(request):
        change()
        return httpx.Response(200, json=openai_answer())
    install_fallback(monkeypatch, fallback)
    if when == "before":
        change()
    _, result = await rt.dispatch(bound)
    assert result["blocked"] and not rt.effects


@pytest.mark.asyncio
async def test_readiness_with_only_selected_model_enables_auto_without_probe(api, store, monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: pytest.fail("No readiness DNS probe"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test", headers={"test-owner": "alice"}) as client:
        ready = (await client.get("/api/session/a/automation")).json()
        assert ready["auto_approve_availability"]["reviewer_source"] == "conversation_model"
        saved = await client.put("/api/session/a/automation", json={"policy_revision": 0, "changes": {"approval_mode": "auto_approve"}})
        assert saved.status_code == 200
        assert (await client.get("/api/session/b/automation")).json()["approval_mode"] == "always_ask"


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol,url", [
    ("openai", "https://models.invalid/v1"),
    ("anthropic", "https://api.anthropic.com/v1/messages"),
    ("ollama", "http://localhost:11434/api/chat"),
    ("responses", "https://chatgpt.com/backend-api/codex"),
])
async def test_supported_selected_provider_contracts_dispatch_once(store, dispatch_runtime, monkeypatch, protocol, url):
    from core import database as d
    rt = dispatch_runtime
    rt.session.endpoint_url = url
    rt.session.headers = {"Authorization": "Bearer synthetic-private-provider-key"}
    with store.transaction() as db:
        row = db.get(d.Session, "a")
        row.endpoint_url, row.headers = url, dict(rt.session.headers)
    store.update_configuration("alice", {"decision_provider": None}, 1)
    store.update_chat("alice", "a", {"approval_mode": "auto_approve"}, 0)
    calls = []
    def transport(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload["model"] == "manual" and "tools" not in payload
        assert "synthetic-private-provider-key" not in request.content.decode()
        content = json.dumps({**LABELS, "uncertain": False})
        if protocol == "openai":
            raw = openai_answer()
        elif protocol == "anthropic":
            assert request.headers["x-api-key"] == "synthetic-private-provider-key"
            raw = {"model": "manual", "stop_reason": "end_turn", "content": [{"type": "text", "text": content}],
                   "usage": {"input_tokens": 30, "output_tokens": 10}}
        elif protocol == "ollama":
            assert payload["options"]["num_ctx"] == 16384
            raw = {"model": "manual", "done": True, "done_reason": "stop", "message": {"content": content},
                   "prompt_eval_count": 30, "eval_count": 10}
        else:
            assert payload["stream"] is True and payload["store"] is False
            raw = {"type": "response.completed", "response": {"model": "manual", "status": "completed",
                "usage": {"input_tokens": 30, "output_tokens": 10},
                "output": [{"type": "message", "content": [{"type": "output_text", "text": content}]}]}}
            return httpx.Response(200, content="data: " + json.dumps(raw) + "\n\n", headers={"Content-Type": "text/event-stream"})
        return httpx.Response(200, json=raw)
    install_fallback(monkeypatch, transport)
    bound = await prepare_chat_turn(store, "alice", rt.session, "write")
    _, result = await rt.dispatch(bound)
    assert result.get("exit_code") == 0, result
    assert len(calls) == len(rt.effects) == 1
    assert result["approval_review"]["provider_identity"]["protocol"] == "approval_" + protocol


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["wrong_model", "missing_usage", "tool_call", "truncated", "extra_field", "missing_field", "invented_label"])
async def test_malformed_fallback_never_executes_or_retries(store, dispatch_runtime, monkeypatch, fault):
    rt = dispatch_runtime
    store.update_configuration("alice", {"decision_provider": None}, 1)
    store.update_chat("alice", "a", {"approval_mode": "auto_approve"}, 0)
    raw = openai_answer()
    if fault == "wrong_model": raw["model"] = "substitute"
    elif fault == "missing_usage": raw.pop("usage")
    elif fault == "tool_call": raw["choices"][0]["message"]["tool_calls"] = [{"name": "bash"}]
    elif fault == "truncated": raw["choices"][0]["finish_reason"] = "length"
    else:
        body = json.loads(raw["choices"][0]["message"]["content"])
        if fault == "extra_field": body["allow"] = True
        elif fault == "missing_field": body.pop("uncertain")
        else: body["risk_level"] = "safe"
        raw["choices"][0]["message"]["content"] = json.dumps(body)
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json=raw)
    install_fallback(monkeypatch, transport)
    bound = await prepare_chat_turn(store, "alice", rt.session, "write")
    _, result = await rt.dispatch(bound)
    assert result["blocked"] and len(calls) == 1 and not rt.effects


@pytest.mark.asyncio
async def test_auto_routing_fallback_uses_resolved_turn_provider_not_manual_model(store, dispatch_runtime, registered, monkeypatch):
    rt = dispatch_runtime
    store.update_configuration("alice", {"decision_provider": None}, 1)
    store.update_chat("alice", "a", {"auto_categories": CATEGORIES, "route_mode": "auto", "approval_mode": "auto_approve"}, 0)
    selected = Reviewer(model="straightforward_tasks")
    bound = await prepare_chat_turn(store, "alice", rt.session, "write", decision_provider=selected)
    assert bound.descriptor["model"] == "shared" and bound.descriptor["endpoint_id"] == "two"
    calls = []
    def transport(request):
        calls.append(request)
        assert str(request.url) == "https://two.invalid/v1/chat/completions"
        assert json.loads(request.content)["model"] == "shared"
        assert request.headers["Authorization"] == "Bearer SYNTHETIC"
        return httpx.Response(200, json=openai_answer(model="shared"))
    install_fallback(monkeypatch, transport)
    _, result = await rt.dispatch(bound)
    assert result.get("exit_code") == 0, result
    assert len(calls) == len(rt.effects) == 1 and rt.session.model == "manual"
