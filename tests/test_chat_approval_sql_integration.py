"""Real SQL turn/revocation + complete mocked HTTP review + dispatch owner."""
from collections import namedtuple
import json
from types import SimpleNamespace

import httpx
import pytest

from tests.test_chat_automation_state import store  # shared isolated SQL fixture
from tests.test_approval_review import CONFIG, decision
from src.agent_runtime.authority import OperationGrant, RequestAuthority
from src.chat_automation import SqlAttemptStore, prepare_chat_turn
from src.decisions import DecisionProvider, ResolvedDecisionEndpoint
from src.tool_approvals import ToolApprovalStore
from src.tool_capabilities import ToolRunSecurityContext


@pytest.fixture
def dispatch_runtime(store, tmp_path, monkeypatch):
    from src import decisions, tool_approvals, tool_execution, model_admission
    from core import database as d
    monkeypatch.setattr(model_admission, '_AUTH_MANAGER', SimpleNamespace(
        users={'alice': {}}, get_privileges=lambda _owner: {
            'allowed_models': ['decision-model', 'manual'], 'allowed_models_restricted': True}))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "target.txt").write_text("before")
    with store.transaction() as db:
        db.add(d.ModelEndpoint(id='reviewer', name='Synthetic reviewer', owner='alice',
            base_url='https://synthetic.invalid/v1', cached_models='["decision-model"]'))
    store.update_configuration("alice", {"decision_provider": CONFIG}, 0)
    calls, effects = [], []
    attempts = SqlAttemptStore(store)
    def transport(request):
        calls.append(request)
        result = decision()
        return httpx.Response(200, json={"model": "decision-model", "answers": result.answers,
                                        "usage": result.usage})
    provider = DecisionProvider(attempts,
        resolver=lambda *_: ResolvedDecisionEndpoint("reviewer", "decision-model", "systemone",
            "https://synthetic.invalid/v1/systemone", {}),
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(transport), **kwargs))
    monkeypatch.setattr(decisions, "DecisionProvider", lambda *_: provider)
    approvals = ToolApprovalStore()
    monkeypatch.setattr(tool_approvals, "tool_approval_store", approvals)
    async def executor(block, **kwargs):
        effects.append(block)
        return "synthetic SQL-bound write", {"output": "written", "exit_code": 0}
    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", executor)
    authority = RequestAuthority("request", "alice", "a", str(workspace),
                                 (OperationGrant("write_file"),))
    block = namedtuple("Block", ["tool_type", "content"])("write_file",
        json.dumps({"path": "target.txt", "content": "after"}))
    async def dispatch(bound, exact=None):
        with bound.activate():
            return await tool_execution.execute_tool_block(block, owner="alice", session_id="a",
                workspace=str(workspace), request_authority=authority,
                security_context=ToolRunSecurityContext(), exact_approval=exact)
    session = SimpleNamespace(id="a", model="manual", endpoint_url="http://local/v1", headers={}, endpoint_id=None)
    return SimpleNamespace(dispatch=dispatch, session=session, calls=calls, effects=effects,
                           attempts=attempts, approvals=approvals)


@pytest.mark.asyncio
async def test_sql_bound_auto_review_dispatch_persists_real_attempt(store, dispatch_runtime):
    runtime = dispatch_runtime
    store.update_chat("alice", "a", {"approval_mode": "auto_approve"}, 0)
    raw = " \tWrite the synthetic file 🦉\n "
    bound = await prepare_chat_turn(store, "alice", runtime.session, raw)
    _, result = await runtime.dispatch(bound)
    assert result["exit_code"] == 0 and len(runtime.effects) == len(runtime.calls) == 1
    assert json.loads(runtime.calls[0].content)["state"]["trusted_context"]["user_request"] == raw
    records = runtime.attempts.records_for_run("alice", "a", bound.run_id)
    assert len(records) == 1 and records[0]["status"] == "settled"
    assert records[0]["usage"] == {"input_tokens": 20, "output_tokens": 5}
    assert runtime.attempts.records_for_run("bob", "a", bound.run_id) == []


@pytest.mark.asyncio
async def test_sql_policy_and_owner_ceiling_revoke_consumed_human_grant(store, dispatch_runtime):
    runtime = dispatch_runtime
    bound = await prepare_chat_turn(store, "alice", runtime.session, "write fixture")
    _, paused = await runtime.dispatch(bound)
    assert paused["approval_required"] and bound.pending
    grant = runtime.approvals.consume(paused["ask_user"]["approval_id"], owner="alice",
                                     session_id="a", decision="approve_once")
    assert grant and not bound.pending
    # Off/on cycles increment SQL epochs, and do not revive a consumed grant.
    revision = store.configuration("alice")["revision"]
    store.update_configuration("alice", {"elevation_ceiling": True}, revision)
    store.update_configuration("alice", {"elevation_ceiling": False}, revision + 1)
    _, denied = await runtime.dispatch(bound, grant)
    assert denied["blocked"] and not runtime.effects and not runtime.calls
