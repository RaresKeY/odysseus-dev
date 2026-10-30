"""Complete offline proposal -> provider -> canonical grant -> dispatch checks."""
import asyncio
from collections import namedtuple
from dataclasses import replace
import json
from pathlib import Path
import sys
import time
from types import ModuleType, SimpleNamespace

import httpx
import pytest

from src.agent_runtime.authority import RequestAuthority, OperationGrant
from src.approval_review import ApprovalReviewService
from src.chat_policy import policy_matches_binding
from src.decisions import (DecisionProvider, DecisionResult, MemoryAttemptStore,
                           ResolvedDecisionEndpoint)
from src.tool_approval_scopes import (ToolApprovalScope, stamp_chat_session_grant,
                                      CHAT_SESSION_APPROVAL_SIGNATURE_FIELD)
from src.tool_approvals import ToolApprovalStore
from src.tool_capabilities import ToolRunSecurityContext, capabilities_for_action
from src.chat_policy import ChatPolicy


CONFIG = {"endpoint_id": "reviewer", "model": "decision-model", "protocol": "systemone"}


@pytest.fixture
def proposal(tmp_path):
    (tmp_path / "note.txt").write_text("before")
    current = {"contract_version": 1, "owner": "alice", "session_id": "chat",
               "approval_mode": "auto_approve", "policy_revision": 2,
               "authority_epoch": 3, "elevation_ceiling": False,
               "elevation_grant": None, "effective_roots": [str(tmp_path)]}
    authority = RequestAuthority("request", "alice", "chat", str(tmp_path),
                                 (OperationGrant("write_file"),))
    store = ToolApprovalStore()
    content = json.dumps({"path": "note.txt", "content": "after"})
    cancelled = [False]
    pending = store.create(owner="Alice", session_id="chat", origin_run_id="run",
                           tool_name="write_file", content=content, workspace=str(tmp_path),
                           request_text=" \tOriginal ask 🦉\n ", request_authority=authority,
                           external_untrusted_context_seen=True,
                           capabilities=capabilities_for_action("write_file", content),
                           policy_record=current, submission_id="submission",
                           policy_validator=lambda p: not cancelled[0] and policy_matches_binding(
                               p.policy_binding, current, owner=p.owner, session_id=p.session_id))
    return store, pending, current, cancelled


def decision(choice="allow", probability=.99, confidence=.99, *, risk="low", authorization="high",
             policy_check="clear", effect_scope="narrow", **changes):
    from src.approval_policy import QUESTIONS
    if choice == "review":
        policy_check = "insufficient_evidence"
    elif choice == "deny":
        policy_check = "explicit_restriction"
    selected = dict(risk_level=risk, user_authorization=authorization,
                    policy_check=policy_check, effect_scope=effect_scope)
    answers = {qid: {"type": "choice", "choice": selected[qid], "confidence": confidence,
        "probabilities": {key: probability if key == selected[qid] else (1-probability)/(len(choices)-1)
                          for key in choices}} for qid, choices in QUESTIONS.items()}
    answer = answers["risk_level"]
    return DecisionResult(answer["choice"], confidence, answer["probabilities"],
                          {"input_tokens": 20, "output_tokens": 5}, "attempt",
                          {"model": "decision-model", "endpoint_id": "reviewer",
                           "protocol": "systemone", "distribution_source": "provider_reported"},
                          answers, **changes)


def evaluator(result=None, *, mutate=None):
    calls = []
    async def call(request, context):
        calls.append((request, context))
        if mutate:
            value = mutate()
            if asyncio.iscoroutine(value):
                await value
        if isinstance(result, Exception):
            raise result
        return result if result is not None else decision()
    return call, calls


def claim(grant, pending, **changes):
    return grant.claim(**{"owner": pending.owner, "session_id": pending.session_id,
                          "tool_name": pending.tool_name, "content": pending.content,
                          "workspace": pending.workspace, **changes})


@pytest.mark.asyncio
async def test_real_mocked_http_call_grants_one_exact_action_and_dispatches_once(proposal, monkeypatch):
    # This standalone MemoryAttemptStore exercise has no chat submission.
    # Initialize its canonical SQL lookup boundary explicitly rather than
    # relying on another test to create a process-global in-memory schema.
    from core import database as database
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    database.Base.metadata.create_all(engine)
    monkeypatch.setattr(database, 'SessionLocal', sessionmaker(bind=engine))
    store, pending, policy, _ = proposal
    attempts, requests = MemoryAttemptStore(), []
    def transport(request):
        requests.append(json.loads(request.content))
        assert next(iter(attempts.records.values()))["status"] == "reserved"
        value = decision()
        return httpx.Response(200, json={"model": "decision-model", "answers": value.answers,
                                        "usage": value.usage})
    endpoint = ResolvedDecisionEndpoint("reviewer", "decision-model", "systemone",
                                       "https://synthetic.invalid/v1/systemone", {})
    provider = DecisionProvider(attempts, resolver=lambda *_: endpoint,
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(transport), **kwargs))
    async def evaluate(request, context):
        return await provider.evaluate(request, context, config=CONFIG)
    outcome = await ApprovalReviewService(store).review(pending, policy,
        deterministically_admitted=True, evaluator=evaluate)
    assert outcome.status == "approved" and len(requests) == 1
    assert requests[0]["state"]["server_context"]["user_evidence"][0]["text"] == pending.request_text
    grant = outcome.approval
    assert grant.scope is ToolApprovalScope.SINGLE_ACTION and not grant.allow_remaining_actions
    assert grant.reviewer == "decision_provider" and grant.decision_attempt_id
    assert store.peek(pending.approval_id) is None
    assert store.consume_reviewed(pending, owner="alice", session_id="chat", decision_attempt_id="again") is None
    assert not claim(grant, pending, content=pending.content + " ")

    from src import tool_execution
    effects = []
    async def fake_executor(block, **kwargs):
        effects.append(block)
        return "synthetic write", {"output": "written", "exit_code": 0}
    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", fake_executor)
    block = namedtuple("Block", ["tool_type", "content"])(pending.tool_name, pending.content)
    security = ToolRunSecurityContext(external_untrusted_context_seen=True)
    kwargs = dict(owner="alice", session_id="chat", workspace=pending.workspace,
                  request_authority=pending.request_authority, security_context=security, exact_approval=grant)
    _, first = await tool_execution.execute_tool_block(block, **kwargs)
    _, second = await tool_execution.execute_tool_block(block, **kwargs)
    assert first["exit_code"] == 0 and not first.get("blocked")
    assert second["blocked"] and len(effects) == 1
    assert next(iter(attempts.records.values()))["usage"] == {"input_tokens": 20, "output_tokens": 5}


@pytest.mark.asyncio
@pytest.mark.parametrize("result,status", [
    (decision("review"), "uncertain"), (decision("deny"), "denied"),
    (decision(probability=.94), "uncertain"), (decision(confidence=.94), "uncertain"),
    (TimeoutError("private diagnostic"), "error"), (RuntimeError("private diagnostic"), "error"),
    ({"choice": "allow", "confidence": 1}, "error"),
    (replace(decision(), usage=None), "error"),
    (replace(decision(), probabilities={"allow": 1}), "error"),
    (replace(decision(), choice="deny"), "error"),
])
async def test_auto_review_failure_stops_without_human_continuation_or_retry(proposal, result, status):
    store, pending, policy, _ = proposal
    call, calls = evaluator(result)
    service = ApprovalReviewService(store)
    outcome = await service.review(pending, policy, deterministically_admitted=True, evaluator=call)
    assert outcome.status == status and outcome.approval is None
    stopped = outcome.tool_result()
    assert stopped['executed'] is False and stopped['blocked'] and stopped['exit_code'] == 1 and stopped['error']
    assert 'ask_user' not in stopped and 'approval_required' not in stopped
    assert "private diagnostic" not in json.dumps(stopped)
    assert store.peek(pending.approval_id) is None
    again = await service.review(pending, policy, deterministically_admitted=True, evaluator=call)
    assert again.status == "stale" and len(calls) == 1
    assert store.consume(pending.approval_id, owner="alice", session_id="chat", decision="approve_once") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,admitted,status", [("always_ask", True, "manual"),
    ("auto_approve", False, "blocked"), ("yolo", False, "manual"), ("yolo", True, "bypass")])
async def test_mode_does_not_make_missing_authority_eligible(proposal, mode, admitted, status):
    store, old, policy, _ = proposal
    policy["approval_mode"] = mode
    pending = replace(old, policy_binding=ChatPolicy.from_record(policy).binding())
    # Replacement is a fixture-only re-seal; runtime policies are never edited.
    store._pending[old.approval_id] = pending
    call, calls = evaluator()
    outcome = await ApprovalReviewService(store).review(pending, policy,
        deterministically_admitted=admitted, evaluator=call)
    assert outcome.status == status and not calls and outcome.approval is None


@pytest.mark.asyncio
async def test_missing_server_authority_and_hard_denial_never_reach_provider(proposal):
    store, pending, policy, _ = proposal
    without = replace(pending, request_authority=None)
    store._pending[pending.approval_id] = without
    call, calls = evaluator()
    service = ApprovalReviewService(store)
    assert (await service.review(without, policy, deterministically_admitted=True, evaluator=call)).status == "blocked"
    assert (await service.review(without, policy, deterministically_admitted=True, evaluator=call, hard_denied=True)).status == "stale"
    assert not calls and store.peek(pending.approval_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"policy_revision": 3}, {"authority_epoch": 4},
    {"elevation_ceiling": True}, {"elevation_grant": {"root": "wider"}},
    {"effective_roots": ["wider"]}, {"approval_mode": "yolo"}, {"owner": "bob"},
    {"session_id": "other"}])
async def test_changes_during_independent_call_retire_proposal(proposal, change):
    store, pending, policy, _ = proposal
    initial = dict(policy)
    call, calls = evaluator(mutate=lambda: policy.update(change))
    outcome = await ApprovalReviewService(store).review(pending, initial,
        deterministically_admitted=True, evaluator=call)
    assert outcome.status == "stale" and outcome.approval is None
    assert len(calls) == 1 and store.peek(pending.approval_id) is None


@pytest.mark.asyncio
async def test_duplicate_concurrent_review_has_single_provider_invocation(proposal):
    store, pending, policy, _ = proposal
    started, finish = asyncio.Event(), asyncio.Event()
    async def wait():
        started.set()
        await finish.wait()
    call, calls = evaluator(mutate=wait)
    service = ApprovalReviewService(store)
    first = asyncio.create_task(service.review(pending, policy, deterministically_admitted=True, evaluator=call))
    await started.wait()
    duplicate = await service.review(pending, policy, deterministically_admitted=True, evaluator=call)
    finish.set()
    winner = await first
    assert duplicate.status == "unavailable" and winner.status == "stale" and len(calls) == 1
    assert store.peek(pending.approval_id) is None
    assert 'ask_user' not in duplicate.tool_result()


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["before", "during", "after_grant"])
async def test_cancellation_invalidates_review_and_consumed_grants(proposal, when):
    store, pending, policy, cancelled = proposal
    if when == "before":
        cancelled[0] = True
    call, calls = evaluator(mutate=(lambda: cancelled.__setitem__(0, True)) if when == "during" else None)
    outcome = await ApprovalReviewService(store).review(pending, policy,
        deterministically_admitted=True, evaluator=call, cancelled=lambda: cancelled[0])
    if when == "after_grant":
        assert outcome.status == "approved"
        cancelled[0] = True
        assert not claim(outcome.approval, pending)
    else:
        assert outcome.status == "cancelled" and outcome.approval is None
        assert len(calls) == (when == "during")
    assert store.peek(pending.approval_id) is None


@pytest.mark.asyncio
async def test_consumed_grant_rechecks_expiry_policy_and_scope_at_claim(proposal, monkeypatch):
    store, pending, policy, _ = proposal
    call, _ = evaluator()
    outcome = await ApprovalReviewService(store).review(pending, policy, deterministically_admitted=True, evaluator=call)
    grant = outcome.approval
    policy["authority_epoch"] += 1
    assert not claim(grant, pending)
    policy["authority_epoch"] -= 1
    monkeypatch.setattr(time, "time", lambda: pending.expires_at + 1)
    assert not claim(grant, pending)


def test_new_contract_cards_cannot_create_legacy_broad_grants(proposal):
    store, pending, _, _ = proposal
    card = pending.public_payload()
    assert card["kind"] == "tool_approval"
    assert [entry["value"] for entry in card["options"]] == ["approve_once", "deny"]
    for decision_value in ("approve", "approve_task"):
        assert store.consume(pending.approval_id, owner="alice", session_id="chat", decision=decision_value) is None
        assert store.peek(pending.approval_id) is pending
    card["response"] = "approve"
    card[CHAT_SESSION_APPROVAL_SIGNATURE_FIELD] = "stale"
    stamp_chat_session_grant(card, "chat", "approve")
    assert CHAT_SESSION_APPROVAL_SIGNATURE_FIELD not in card
    assert store.consume(pending.approval_id, owner="bob", session_id="chat", decision="approve_once") is None
    assert store.consume(pending.approval_id, owner="alice", session_id="other", decision="approve_once") is None


@pytest.fixture
def canonical_dispatch(proposal, monkeypatch):
    """Canonical dispatcher plus complete HTTP provider, with fake SQL ingress.

    The SQL/CAS owner has its own integration suite. This injected ingress has
    the same dynamic-policy seam and never runs application source on host.
    """
    from src import decisions, tool_execution, tool_approvals
    from src import approval_context
    from src import approval_fallback
    monkeypatch.setattr(approval_fallback, "model_snapshot", lambda _: "fixture-model")
    from src.decisions import observability
    monkeypatch.setattr(approval_context, "user_snapshot", lambda _: "")
    monkeypatch.setattr(approval_context, "record_human_decision", lambda *_: None)
    monkeypatch.setattr(observability, "default_observability", lambda *_: SimpleNamespace(record_approval=lambda *_: None))
    store, initial, policy, cancelled = proposal
    current_binding = [ChatPolicy.from_record(policy).binding()]
    attempts, calls, effects = MemoryAttemptStore(), [], []
    bound = SimpleNamespace(owner="alice", session_id="chat", submission_id="submission",
        run_id="submission", raw_ask=initial.request_text, policy=policy,
        configuration={"decision_provider": CONFIG}, pending=False,
        store=SimpleNamespace(policy=lambda *_args, **_kwargs: dict(policy), session_factory=None))
    bound.current = lambda: not cancelled[0] and policy_matches_binding(current_binding[0], policy)
    automation = ModuleType("src.chat_automation")
    automation.current_turn = lambda: bound
    automation.SqlAttemptStore = lambda *_: attempts
    automation.validate_elevation_operation = lambda *_: None
    monkeypatch.setitem(sys.modules, "src.chat_automation", automation)
    monkeypatch.setattr(tool_approvals, "tool_approval_store", store)
    transport_hook = [None]
    def transport(request):
        calls.append(request)
        if transport_hook[0]:
            transport_hook[0]()
        value = decision()
        return httpx.Response(200, json={"model": "decision-model", "answers": value.answers,
                                        "usage": value.usage})
    real_provider = DecisionProvider(attempts,
        resolver=lambda *_: ResolvedDecisionEndpoint("reviewer", "decision-model", "systemone",
            "https://synthetic.invalid/v1/systemone", {}),
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(transport), **kwargs))
    monkeypatch.setattr(decisions, "DecisionProvider", lambda *_: real_provider)
    async def execute(block, **kwargs):
        effects.append(block)
        return "synthetic write", {"output": "written", "exit_code": 0}
    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", execute)
    async def dispatch(*, grant=None, authority=None, bypass=False):
        block = namedtuple("Block", ["tool_type", "content"])(initial.tool_name, initial.content)
        return await tool_execution.execute_tool_block(block, owner="alice", session_id="chat",
            workspace=initial.workspace, request_authority=authority or initial.request_authority,
            security_context=ToolRunSecurityContext(approval_gate_bypassed=bypass), exact_approval=grant)
    def set_mode(mode):
        policy["approval_mode"] = mode
        current_binding[0] = ChatPolicy.from_record(policy).binding()
    return SimpleNamespace(dispatch=dispatch, set_mode=set_mode, bound=bound, store=store,
        initial=initial, calls=calls, effects=effects, policy=policy, hook=transport_hook, attempts=attempts)


@pytest.mark.asyncio
async def test_dispatch_auto_calls_independent_provider_and_executes_with_untainted_context(canonical_dispatch):
    fixture = canonical_dispatch
    _, result = await fixture.dispatch()
    assert result["exit_code"] == 0 and len(fixture.effects) == 1 and len(fixture.calls) == 1
    assert not fixture.bound.pending


@pytest.mark.asyncio
async def test_dispatch_always_ask_has_exact_human_continuation_and_no_legacy_bypass(canonical_dispatch):
    fixture = canonical_dispatch
    fixture.set_mode("always_ask")
    _, result = await fixture.dispatch(bypass=True)
    assert result["approval_required"] and not fixture.effects and not fixture.calls
    card = result["ask_user"]
    pending = fixture.store.peek(card["approval_id"])
    assert pending.bound_turn is fixture.bound and fixture.bound.pending
    assert fixture.store.consume(card["approval_id"], owner="alice", session_id="chat", decision="approve_task") is None
    grant = fixture.store.consume(card["approval_id"], owner="alice", session_id="chat", decision="approve_once")
    _, accepted = await fixture.dispatch(grant=grant)
    _, duplicate = await fixture.dispatch(grant=grant)
    assert accepted["exit_code"] == 0 and duplicate["blocked"] and len(fixture.effects) == 1


@pytest.mark.asyncio
async def test_dispatch_yolo_only_bypasses_discretionary_review(canonical_dispatch):
    fixture = canonical_dispatch
    fixture.set_mode("yolo")
    _, result = await fixture.dispatch()
    assert result["exit_code"] == 0 and len(fixture.effects) == 1 and not fixture.calls
    restricted = replace(fixture.initial.request_authority, denied=frozenset({"write_file"}))
    _, denied = await fixture.dispatch(authority=restricted)
    assert denied["blocked"] and len(fixture.effects) == 1 and not fixture.calls


@pytest.mark.asyncio
async def test_dispatch_missing_semantic_authority_gets_human_card_without_model(canonical_dispatch):
    fixture = canonical_dispatch
    fixture.set_mode("yolo")
    no_write = replace(fixture.initial.request_authority, grants=())
    _, result = await fixture.dispatch(authority=no_write)
    assert result["approval_required"] and not fixture.calls and not fixture.effects


@pytest.mark.asyncio
async def test_dispatch_policy_change_during_call_prevents_effect(canonical_dispatch):
    fixture = canonical_dispatch
    fixture.hook[0] = lambda: fixture.policy.__setitem__("authority_epoch", 99)
    _, result = await fixture.dispatch()
    assert result["blocked"] and not fixture.effects and len(fixture.calls) == 1


@pytest.mark.asyncio
async def test_dispatch_rechecks_actual_target_identity_after_provider(canonical_dispatch):
    fixture = canonical_dispatch
    def replace_file():
        workspace = Path(fixture.initial.workspace)
        replacement = workspace / "replacement.txt"
        replacement.write_text("changed identity")
        replacement.replace(workspace / "note.txt")
    fixture.hook[0] = replace_file
    _, result = await fixture.dispatch()
    assert result["blocked"] and result["failure_kind"] == "resource_identity_denied"
    assert not fixture.effects and len(fixture.calls) == 1


@pytest.mark.asyncio
async def test_pending_human_card_is_reused_without_new_provider_attempt(canonical_dispatch):
    fixture = canonical_dispatch
    fixture.set_mode("always_ask")
    _, first = await fixture.dispatch()
    _, again = await fixture.dispatch()
    assert first["ask_user"]["approval_id"] == again["ask_user"]["approval_id"]
    assert not fixture.calls and not fixture.effects
    grant = fixture.store.consume(first["ask_user"]["approval_id"], decision="approve_once",
                                   owner="alice", session_id="chat")
    assert grant and not fixture.bound.pending and fixture.bound.pending_approval_id is None


@pytest.mark.asyncio
async def test_task_cancellation_retires_proposal_without_review_retry(proposal):
    store, pending, policy, _ = proposal
    async def cancelled_call(request, context):
        raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await ApprovalReviewService(store).review(pending, policy,
            deterministically_admitted=True, evaluator=cancelled_call)
    assert store.peek(pending.approval_id) is None


def test_concurrent_human_responses_consume_exactly_once(proposal):
    from concurrent.futures import ThreadPoolExecutor
    store, original, policy, _ = proposal
    policy["approval_mode"] = "always_ask"
    pending = store.create(owner=original.owner, session_id=original.session_id,
        origin_run_id=original.origin_run_id, tool_name=original.tool_name,
        content=original.content, workspace=original.workspace,
        external_untrusted_context_seen=True,
        request_authority=original.request_authority,
        request_text=original.request_text, submission_id=original.submission_id,
        capabilities=capabilities_for_action(original.tool_name, original.content),
        policy_record=policy, policy_validator=original.policy_validator)
    def consume(_):
        return store.consume(pending.approval_id, decision="approve_once", owner="alice", session_id="chat")
    with ThreadPoolExecutor(max_workers=4) as pool:
        grants = list(pool.map(consume, range(8)))
    assert sum(grant is not None for grant in grants) == 1
