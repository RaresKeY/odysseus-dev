"""Canonical dispatch continues independent actions through one bound outage."""
import asyncio
from collections import namedtuple
from copy import deepcopy
import json
import time

import httpx
import pytest

from tests.test_chat_automation_state import store
from tests.test_chat_approval_sql_integration import dispatch_runtime
from tests.test_approval_fallback import install_fallback, openai_answer
from tests.test_approval_review import CONFIG
from tests.test_approval_route_rebind import backup
from src.agent_runtime.authority import OperationGrant, RequestAuthority
from src.chat_automation import prepare_chat_turn
from src.decisions import DecisionContext, DecisionError, DecisionRequest, MemoryAttemptStore
from src.decisions.provider import DecisionProvider
from src.tool_capabilities import ToolRunSecurityContext


@pytest.fixture
def outage_runtime(store, dispatch_runtime, monkeypatch, tmp_path):
    from core import database as d
    from src import decisions, tool_execution
    rt = dispatch_runtime
    monkeypatch.setattr(d, "SessionLocal", store.session_factory)
    primary_calls, fallback_calls = [], []
    primary_started = asyncio.Event()
    settings = {"failure": "http", "fallback": {}}

    async def primary(request):
        primary_calls.append(request)
        primary_started.set()
        if settings["failure"] == "timeout":
            await asyncio.Event().wait()
        if settings["failure"] == "transport":
            raise httpx.ReadTimeout("synthetic timeout")
        if settings["failure"] == "invalid_json":
            return httpx.Response(200, content=b"not-json")
        if settings["failure"] == "invalid_utf8":
            return httpx.Response(200, content=b"\xff")
        if settings["failure"] == "invalid_answers":
            return httpx.Response(200, json={"model": "decision-model", "answers": {},
                "usage": {"input_tokens": 12, "output_tokens": 3}})
        if settings["failure"] == "missing_usage":
            return httpx.Response(200, json={"model":"decision-model", "answers":{}})
        return httpx.Response(401, json={"error": "synthetic unauthorized"})

    def fallback(request):
        fallback_calls.append(request)
        return httpx.Response(200, json=openai_answer(**settings["fallback"]))

    # Use the production registration resolver and SQL attempt owner. Only the
    # HTTP boundary is replaced, for both the primary and selected chat model.
    providers = []
    def make_provider(attempts):
        provider = DecisionProvider(attempts, client_factory=lambda **kwargs:
            httpx.AsyncClient(transport=httpx.MockTransport(primary), **kwargs))
        providers.append(provider)
        return provider
    monkeypatch.setattr(decisions, "DecisionProvider", make_provider)
    install_fallback(monkeypatch, fallback)
    workspace = str(tmp_path / "workspace")
    authority = RequestAuthority("request", "alice", "a", workspace,
                                 (OperationGrant("write_file"),))

    async def dispatch(bound, number):
        block = namedtuple("Block", ["tool_type", "content"])("write_file",
            json.dumps({"path": "target.txt", "content": f"after {number}"}))
        with bound.activate():
            return await tool_execution.execute_tool_block(block, owner="alice", session_id="a",
                workspace=workspace, request_authority=authority, security_context=ToolRunSecurityContext())

    async def start(*, max_calls=32, timeout=30):
        store.update_configuration("alice", {"decision_provider": {
            **CONFIG, "max_calls": max_calls, "timeout_seconds": timeout}}, 1)
        store.update_chat("alice", "a", {"approval_mode": "auto_approve"}, 0)
        return await prepare_chat_turn(store, "alice", rt.session, "Write each requested file version")

    rt.start, rt.action = start, dispatch
    rt.primary_calls, rt.fallback_calls, rt.settings = primary_calls, fallback_calls, settings
    rt.providers = providers
    rt.primary_started = primary_started
    return rt


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "transport", "timeout"])
async def test_sequential_actions_keep_one_primary_hold_and_separate_reviews(outage_runtime, failure):
    rt = outage_runtime
    rt.settings["failure"] = failure
    bound = await rt.start(timeout=.2 if failure == "timeout" else 30)
    outcomes = [(await rt.action(bound, number))[1] for number in range(3)]
    assert all(result["exit_code"] == 0 for result in outcomes), outcomes
    assert len(rt.primary_calls) == 1 and len(rt.fallback_calls) == len(rt.effects) == 3
    records = rt.attempts.records_for_run("alice", "a", bound.run_id)
    primary = next(r for r in records if r["provider_identity"]["protocol"] == "systemone")
    assert len(records) == 4 and primary["status"] == "unresolved" and primary["usage"] is None
    assert primary["reservation"] == {"input_tokens": 65536, "output_tokens": 65536}
    reviews = [r for r in records if r is not primary]
    assert len({r["approval_action_digest"] for r in reviews}) == 3
    assert all(r["status"] == "settled" and r["usage"] == {"input_tokens": 30, "output_tokens": 10}
               for r in reviews)
    assert outcomes[0]["approval_review"]["primary_attempt_id"] == primary["attempt_id"]
    assert all(r["approval_review"]["approval_outage_of"] == primary["attempt_id"] for r in outcomes[1:])
    # An ordinary decision cannot spend through the unknown primary usage.
    with pytest.raises(DecisionError, match="decision_budget_unavailable"):
        await rt.providers[-1].evaluate(DecisionRequest("advice", {}, {"a": "A", "b": "B"}),
            DecisionContext("alice", "a", bound.run_id), config=bound.configuration["decision_provider"])
    assert len(rt.primary_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["invalid_json", "invalid_utf8", "missing_usage", "invalid_answers"])
async def test_malformed_real_primary_stops_without_outage_or_fallback(outage_runtime, failure):
    rt = outage_runtime
    rt.settings["failure"] = failure
    bound = await rt.start()
    first = (await rt.action(bound, 0))[1]
    assert first["blocked"] and not first.get("approval_required")
    assert not rt.fallback_calls and not rt.effects
    records = rt.attempts.records_for_run("alice", "a", bound.run_id)
    assert len(records) == 1
    assert records[0]["failure_kind"] == {
        "invalid_json": "decision_invalid_json", "invalid_utf8": "decision_invalid_json",
        "missing_usage": "decision_invalid_response", "invalid_answers": "decision_invalid_answers",
    }[failure]
    known = failure == "invalid_answers"
    assert records[0]["status"] == ("rejected" if known else "unresolved")
    assert records[0]["usage"] == ({"input_tokens": 12, "output_tokens": 3} if known else None)
    # A later distinct action cannot reinterpret malformed output as an outage.
    second = (await rt.action(bound, 1))[1]
    assert second["blocked"] and not rt.fallback_calls and not rt.effects
    assert len(rt.primary_calls) == (2 if known else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["decision_invalid_json", "decision_incomplete_response"])
async def test_legacy_malformed_hold_cannot_exempt_another_review(outage_runtime, store, failure):
    from core import database as d
    rt = outage_runtime
    bound = await rt.start()
    assert (await rt.action(bound, 0))[1]["exit_code"] == 0
    root = next(r for r in rt.attempts.records_for_run("alice", "a", bound.run_id)
                if r["status"] == "unresolved")
    # Simulate persisted pre-repair outage metadata. Its old settled fallback
    # cannot make a malformed primary eligible for a new budget exemption.
    with store.transaction() as db:
        row = db.get(d.DecisionAttempt, root["attempt_id"])
        row.record = {**row.record, "failure_kind": failure}
    stopped = (await rt.action(bound, 1))[1]
    assert stopped["blocked"] and len(rt.primary_calls) == len(rt.fallback_calls) == len(rt.effects) == 1
    assert stopped["approval_review"]["failure_kind"] == "decision_budget_unavailable"


@pytest.mark.asyncio
async def test_enclosing_review_timeout_keeps_the_original_attempt_for_continuation(outage_runtime, monkeypatch):
    from src.approval_review import ApprovalReviewService
    original = ApprovalReviewService.review
    async def bounded_review(self, *args, **kwargs):
        return await original(self, *args, **kwargs, deadline=time.monotonic() + .4)
    monkeypatch.setattr(ApprovalReviewService, "review", bounded_review)
    rt = outage_runtime
    rt.settings["failure"] = "timeout"
    bound = await rt.start()
    first = (await rt.action(bound, 0))[1]
    second = (await rt.action(bound, 1))[1]
    assert first["exit_code"] == second["exit_code"] == 0, (first, second)
    assert len(rt.primary_calls) == 1 and len(rt.fallback_calls) == 2
    root = next(r for r in rt.attempts.records_for_run("alice", "a", bound.run_id) if r["status"] == "unresolved")
    assert root["failure_kind"] == "decision_deadline_exceeded" and root["usage"] is None
    assert second["approval_review"]["approval_outage_of"] == root["attempt_id"]


@pytest.mark.asyncio
async def test_explicit_cancellation_of_real_primary_never_starts_fallback(outage_runtime):
    rt = outage_runtime
    rt.settings["failure"] = "timeout"
    bound = await rt.start()
    task = asyncio.create_task(rt.action(bound, 0))
    await asyncio.wait_for(rt.primary_started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    while rt.providers[-1]._store_tasks:
        await asyncio.gather(*list(rt.providers[-1]._store_tasks), return_exceptions=True)
    assert not rt.fallback_calls and not rt.effects
    records = rt.attempts.records_for_run("alice", "a", bound.run_id)
    assert len(records) == 1 and records[0]["failure_kind"] == "decision_cancelled"
    assert records[0]["usage"] is None


@pytest.mark.asyncio
async def test_original_primary_call_limit_caps_outage_continuations(outage_runtime):
    rt = outage_runtime
    bound = await rt.start(max_calls=3)
    assert (await rt.action(bound, 0))[1]["exit_code"] == 0
    assert (await rt.action(bound, 1))[1]["exit_code"] == 0
    stopped = (await rt.action(bound, 2))[1]
    assert stopped["blocked"] and stopped["approval_review"]["failure_kind"] == "decision_budget_unavailable"
    assert len(rt.primary_calls) == 1 and len(rt.fallback_calls) == len(rt.effects) == 2
    assert len(rt.attempts.records_for_run("alice", "a", bound.run_id)) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["reserved", "unresolved", "overrun"])
async def test_continuation_cannot_exempt_an_unrelated_accounting_blocker(outage_runtime, store, blocker):
    from core import database as d
    from src.chat_automation import owner_key
    rt = outage_runtime
    bound = await rt.start()
    assert (await rt.action(bound, 0))[1]["exit_code"] == 0
    root = next(r for r in rt.attempts.records_for_run("alice", "a", bound.run_id) if r["status"] == "unresolved")
    unrelated = {**root, "attempt_id": "unrelated", "purpose": "advice",
                 "status": "rejected" if blocker == "overrun" else blocker,
                 "failure_kind": "decision_usage_exceeded_reservation" if blocker == "overrun" else None}
    # Simulate another durable in-flight/overrun owner; continuation may only
    # exempt its original primary, irrespective of call-count headroom.
    with store.transaction() as db:
        db.add(d.DecisionAttempt(attempt_id="unrelated", owner_key=owner_key("alice"),
            session_id="a", run_id=bound.run_id, status=unrelated["status"], record=unrelated))
    stopped = (await rt.action(bound, 1))[1]
    assert stopped["blocked"] and len(rt.primary_calls) == len(rt.fallback_calls) == len(rt.effects) == 1
    assert stopped["approval_review"]["failure_kind"] == "decision_budget_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("denied_action", [0, 1])
async def test_denied_fallback_is_final_for_its_exact_action(outage_runtime, denied_action):
    rt = outage_runtime
    bound = await rt.start()
    if denied_action:
        assert (await rt.action(bound, 0))[1]["exit_code"] == 0
    rt.settings["fallback"] = {"policy_check": "explicit_restriction"}
    denied = (await rt.action(bound, denied_action))[1]
    assert denied["blocked"] and denied["approval_review_status"] == "denied"
    rt.settings["fallback"] = {}
    repeated = (await rt.action(bound, denied_action))[1]
    assert repeated["blocked"] and len(rt.primary_calls) == 1 and len(rt.fallback_calls) == denied_action + 1
    assert len(rt.effects) == denied_action


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["primary_registration", "conversation_headers", "policy", "cancelled"])
async def test_outage_cannot_outlive_current_identity_or_authority(outage_runtime, store, change):
    from core import database as d
    rt = outage_runtime
    bound = await rt.start()
    assert (await rt.action(bound, 0))[1]["exit_code"] == 0
    if change == "primary_registration":
        with store.transaction() as db:
            db.get(d.ModelEndpoint, "reviewer").base_url = "https://changed.invalid/v1"
    elif change == "conversation_headers":
        with store.transaction() as db:
            db.get(d.Session, "a").headers = {"X-Changed": "yes"}
    elif change == "policy":
        store.update_chat("alice", "a", {"approval_mode": "always_ask"}, 1)
    else:
        store.record(bound, "cancelled")
    stopped = (await rt.action(bound, 1))[1]
    assert stopped.get("blocked") or stopped.get("approval_required"), stopped
    assert len(rt.primary_calls) == len(rt.fallback_calls) == len(rt.effects) == 1


@pytest.mark.asyncio
async def test_memory_store_preserves_the_same_continuation_boundaries(outage_runtime):
    rt = outage_runtime
    bound = await rt.start()
    assert (await rt.action(bound, 0))[1]["exit_code"] == 0
    assert (await rt.action(bound, 1))[1]["exit_code"] == 0
    records = rt.attempts.records_for_run("alice", "a", bound.run_id)
    continuation = next(r for r in records if r.get("approval_outage_of"))
    memory = MemoryAttemptStore()
    memory.records = {r["attempt_id"]: deepcopy(r) for r in records if r is not continuation}
    memory.reserve({**continuation, "status": "reserved"}, max_calls=32)
    memory.settle(continuation["attempt_id"], status="settled", usage=continuation["usage"])
    for changes in ({}, {"approval_action_digest": "other", "authority_epoch": 999},
                    {"approval_action_digest": "other", "provider_identity": {"protocol": "approval_openai", "model": "other"}}):
        with pytest.raises(DecisionError, match="decision_budget_unavailable"):
            memory.reserve({**continuation, **changes, "attempt_id": "forged", "status": "reserved"}, max_calls=32)
    assert memory.records[continuation["approval_outage_of"]]["usage"] is None


@pytest.mark.asyncio
async def test_established_outage_follows_only_authorized_registered_route_handoff(outage_runtime, backup):
    rt = outage_runtime
    bound = await rt.start()
    assert (await rt.action(bound, 0))[1]['exit_code'] == 0
    original = list(bound.review_route_lineage)
    bound.rebind_manual(backup, status=503)
    assert bound.review_route_lineage[:1] == original
    rt.settings['fallback'] = {'model': 'backup-model'}
    second = (await rt.action(bound, 1))[1]
    third = (await rt.action(bound, 2))[1]
    assert second['exit_code'] == third['exit_code'] == 0, (second, third)
    assert len(rt.primary_calls) == 1 and len(rt.fallback_calls) == len(rt.effects) == 3
    assert str(rt.fallback_calls[-1].url) == backup[0]
    records = rt.attempts.records_for_run('alice', 'a', bound.run_id)
    root = next(r for r in records if r['status'] == 'unresolved')
    assert root['usage'] is None and len(records) == 4
    continuations = [r for r in records if r.get('approval_outage_of')]
    assert all(r['approval_route_lineage'] == bound.review_route_lineage for r in continuations)
    assert all(r['approval_route_origin'] == bound.review_model_origin for r in continuations)
    assert all(r['provider_identity']['model'] == 'backup-model' for r in continuations)


@pytest.mark.asyncio
async def test_changed_identity_without_exact_server_route_lineage_is_not_handoff(outage_runtime, backup):
    rt = outage_runtime
    bound = await rt.start()
    assert (await rt.action(bound, 0))[1]['exit_code'] == 0
    bound.rebind_manual(backup, status=503)
    rt.settings['fallback'] = {'model': 'backup-model'}
    bound.review_route_lineage = ['0' * 64, bound.review_model_origin]
    stopped = (await rt.action(bound, 1))[1]
    assert stopped['blocked'] and len(rt.fallback_calls) == len(rt.effects) == 1


@pytest.mark.asyncio
async def test_outage_route_handoff_metadata_cannot_waive_original_call_limit(outage_runtime, backup):
    rt = outage_runtime
    bound = await rt.start(max_calls=2)
    assert (await rt.action(bound, 0))[1]['exit_code'] == 0
    bound.rebind_manual(backup, status=503)
    rt.settings['fallback'] = {'model': 'backup-model'}
    stopped = (await rt.action(bound, 1))[1]
    assert stopped['approval_review']['failure_kind'] == 'decision_budget_unavailable'
    assert len(rt.primary_calls) == len(rt.fallback_calls) == len(rt.effects) == 1


def test_route_handoff_requires_actual_origin_and_exact_previous_prefix():
    from src.decisions.store import _route_continues
    old = {'provider_identity': {'model':'A'}, 'approval_route_lineage':['a' * 64], 'approval_route_origin':'a' * 64}
    new = {'provider_identity': {'model':'B'}, 'approval_route_lineage':['a' * 64,'b' * 64], 'approval_route_origin':'b' * 64}
    assert _route_continues(new, old)
    for mutation in ({'approval_route_origin':'c' * 64}, {'approval_route_lineage':['c' * 64,'b' * 64]},
                     {'approval_route_lineage':[]}, {'approval_route_lineage':['a' * 64]},
                     {'approval_route_lineage':['a' * 64,'not-an-origin']}):
        assert not _route_continues({**new, **mutation}, old)
