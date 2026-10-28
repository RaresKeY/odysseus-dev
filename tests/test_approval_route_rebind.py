"""Owner-authorized foreground fallback must rebind review, never old grants."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from core import database as d
from tests.test_chat_automation_state import store
from tests.test_chat_approval_sql_integration import dispatch_runtime
from tests.test_approval_fallback import install_fallback, openai_answer
from src.chat_automation import prepare_chat_turn


@pytest.fixture
def backup(store, dispatch_runtime, monkeypatch):
    from src import endpoint_resolver, foreground_model_routing, model_admission
    monkeypatch.setattr(endpoint_resolver, 'SessionLocal', store.session_factory)
    monkeypatch.setattr(model_admission, '_AUTH_MANAGER', SimpleNamespace(users={'alice': {}},
        get_privileges=lambda _: {'allowed_models': ['manual', 'backup-model', 'decision-model'], 'allowed_models_restricted': True}))
    with store.transaction() as db:
        db.add(d.ModelEndpoint(id='backup', name='Backup', owner='alice', model_type='llm',
            base_url='https://backup.invalid/v1', cached_models='["backup-model"]'))
    route = ('https://backup.invalid/v1/chat/completions', 'backup-model', {})
    policy = foreground_model_routing.ForegroundModelPolicy(enabled=True, fallback_candidates=(route,),
        fallback_descriptors=({'endpoint_id': 'backup'},))
    monkeypatch.setattr(foreground_model_routing, 'resolve_foreground_model_policy', lambda owner: policy)
    return route


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['always_ask', 'auto_approve', 'yolo'])
async def test_authorized_foreground_rebind_then_actual_tool_dispatch(store, dispatch_runtime, backup, monkeypatch, mode):
    rt = dispatch_runtime
    store.update_configuration('alice', {'decision_provider': None}, 1)
    store.update_chat('alice', 'a', {'approval_mode': mode}, 0)
    calls = []
    def transport(request):
        calls.append(request)
        assert str(request.url) == backup[0]
        return httpx.Response(200, json=openai_answer(model='backup-model'))
    install_fallback(monkeypatch, transport)
    bound = await prepare_chat_turn(store, 'alice', rt.session, 'Write local fixture.')
    bound.rebind_manual(backup, status=503)
    _, result = await rt.dispatch(bound)
    if mode == 'always_ask':
        assert result['approval_required']
        grant = rt.approvals.consume(result['ask_user']['approval_id'], owner='alice', session_id='a', decision='approve_once')
        _, result = await rt.dispatch(bound, grant)
    assert result['exit_code'] == 0 and len(rt.effects) == 1
    assert len(calls) == (1 if mode == 'auto_approve' else 0)


@pytest.mark.asyncio
async def test_pending_old_route_is_retired_and_cannot_revive(store, dispatch_runtime, backup):
    rt = dispatch_runtime
    bound = await prepare_chat_turn(store, 'alice', rt.session, 'Write fixture.')
    _, old = await rt.dispatch(bound)
    old_id = old['ask_user']['approval_id']
    bound.rebind_manual(backup, status=503)
    assert rt.approvals.peek(old_id) is None and not bound.pending
    assert rt.approvals.consume(old_id, owner='alice', session_id='a', decision='approve_once') is None
    _, fresh = await rt.dispatch(bound)
    assert fresh['approval_required'] and fresh['ask_user']['approval_id'] != old_id


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['unauthorized', 'changed_registration', 'changed_saved', 'stale'])
async def test_rebind_refuses_unapproved_or_stale_route(store, dispatch_runtime, backup, monkeypatch, fault):
    from src import foreground_model_routing
    rt = dispatch_runtime
    bound = await prepare_chat_turn(store, 'alice', rt.session, 'Write.')
    if fault == 'unauthorized':
        monkeypatch.setattr(foreground_model_routing, 'resolve_foreground_model_policy', lambda _: foreground_model_routing.ForegroundModelPolicy())
    elif fault == 'changed_registration':
        with store.transaction() as db: db.get(d.ModelEndpoint, 'backup').base_url = 'https://changed.invalid/v1'
    elif fault == 'changed_saved':
        with store.transaction() as db: db.get(d.Session, 'a').model = 'backup-model'
    else:
        await prepare_chat_turn(store, 'alice', rt.session, 'New ask.')
    with pytest.raises(RuntimeError): bound.rebind_manual(backup, status=503)
    assert bound.review_route_revision == 0


@pytest.mark.asyncio
async def test_inflight_old_route_allow_is_stale_after_authorized_rebind(store, dispatch_runtime, backup):
    rt = dispatch_runtime
    store.update_chat('alice', 'a', {'approval_mode':'auto_approve'}, 0)
    bound = await prepare_chat_turn(store, 'alice', rt.session, 'Write.')
    rt.hooks.append(lambda: bound.rebind_manual(backup, status=503))
    _, result = await rt.dispatch(bound)
    assert result['failure_kind'] == 'approval_stale' and not rt.effects
    assert not rt.approvals._pending
