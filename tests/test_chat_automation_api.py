"""Owner settings API exercises real SQL transactions, not a second prefs store."""
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI
from core import database as d
from routes.chat_automation_routes import setup_chat_automation_routes
from tests.test_chat_automation_state import store, configured_reviewer


@pytest.fixture
def api(store, monkeypatch):
    monkeypatch.setattr(d, 'SessionLocal', store.session_factory)
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True)
    @app.middleware('http')
    async def identity(request, call_next):
        request.state.current_user = request.headers.get('test-owner')
        if request.headers.get('test-token'):
            request.state.api_token = True
            request.state.api_token_owner = 'alice'
        return await call_next(request)
    app.include_router(setup_chat_automation_routes(None))
    return app


@pytest.mark.asyncio
async def test_owner_cas_roundtrip_and_no_cross_owner_state(api):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test', headers={'test-owner':'alice'}) as c:
        first = await c.get('/api/session/a/automation')
        assert first.status_code == 200 and first.json()['approval_mode'] == 'always_ask'
        put = await c.put('/api/session/a/automation', json={'policy_revision':0,'changes':{'approval_mode':'yolo'}})
        assert put.status_code == 200 and put.json()['approval_mode'] == 'yolo'
        assert (await c.get('/api/chat-settings')).json()['last_explicit_mode'] == 'yolo'
        assert (await c.get('/api/session/b/automation')).json()['approval_mode'] == 'always_ask'
        assert (await c.put('/api/session/a/automation', json={'policy_revision':0,'changes':{'approval_mode':'auto_approve'}})).status_code == 409
        assert (await c.get('/api/session/c/automation')).status_code == 404
        assert (await c.put('/api/session/a/automation', headers={'Origin':'https://attacker.invalid'}, json={'policy_revision':1,'changes':{'approval_mode':'auto_approve'}})).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize('saved_mode', ['auto_approve', 'yolo'])
async def test_created_session_api_defaults_to_ask_and_keeps_saved_chat(api, store, configured_reviewer, monkeypatch, saved_mode):
    from core import session_manager
    monkeypatch.setattr(session_manager, 'SessionLocal', store.session_factory)
    manager = session_manager.SessionManager.__new__(session_manager.SessionManager)
    manager.sessions, manager.upload_handler = {}, None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test', headers={'test-owner':'alice'}) as c:
        saved = await c.put('/api/session/a/automation', json={'policy_revision':0,'changes':{'approval_mode':saved_mode}})
        assert saved.status_code == 200
        preferences = (await c.get('/api/chat-settings')).json()
        assert preferences['last_explicit_mode'] == saved_mode
        # Exercise the shared production session-creation transaction, without a provider call.
        manager.create_session('new', 'New chat', 'http://synthetic.invalid/v1', 'manual', owner='alice')
        for _ in range(2):
            new_chat = await c.get('/api/session/new/automation')
            assert new_chat.status_code == 200
            assert new_chat.json()['approval_mode'] == 'always_ask'
            assert new_chat.json()['policy_revision'] == 0
            existing = (await c.get('/api/session/a/automation')).json()
            assert existing['approval_mode'] == saved_mode
            assert existing['policy_revision'] == saved.json()['policy_revision']
        assert (await c.get('/api/chat-settings')).json() == preferences


@pytest.mark.asyncio
async def test_settings_cannot_be_changed_by_api_token_or_unknown_user(api):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test') as c:
        assert (await c.get('/api/chat-settings')).status_code == 401
        assert (await c.get('/api/chat-settings', headers={'test-owner':'api','test-token':'yes'})).status_code == 403


@pytest.mark.asyncio
async def test_owner_can_clear_configured_reviewer_without_network(api, configured_reviewer, monkeypatch):
    import socket

    def unexpected_network(*args, **kwargs):
        pytest.fail("Removing Jev must not perform DNS or inference")

    monkeypatch.setattr(socket, 'getaddrinfo', unexpected_network)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test',
                                 headers={'test-owner': 'alice'}) as c:
        previous = (await c.get('/api/chat-settings')).json()
        assert previous['decision_provider']['endpoint_id'] == 'reviewer'
        result = await c.put('/api/chat-settings', json={
            'revision': previous['revision'], 'changes': {'decision_provider': None}})
        assert result.status_code == 200
        assert result.json()['decision_provider'] is None
        saved = (await c.get('/api/chat-settings')).json()
        assert saved['decision_provider'] is None
        assert saved['revision'] == previous['revision'] + 1
        stale = await c.put('/api/chat-settings', json={
            'revision': previous['revision'], 'changes': {'decision_provider': None}})
        assert stale.status_code == 409


@pytest.mark.asyncio
async def test_ceiling_not_a_deployment_override(api):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test', headers={'test-owner':'alice'}) as c:
        assert (await c.get('/api/chat-settings')).json()['elevation_ceiling'] is False
        result=await c.put('/api/chat-settings', json={'revision':0,'changes':{'elevation_ceiling':True}})
        assert result.status_code==200
        chat=(await c.get('/api/session/a/automation')).json()
        assert chat['effective_roots']==[] and chat['execution_scope']=='workspace'
        assert (await c.put('/api/session/a/automation',json={'policy_revision':0,'changes':{'elevation_roots':[{'path':'/tmp','access':'write'}]}})).status_code==403


@pytest.mark.asyncio
async def test_invalid_configuration_never_commits(api):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test', headers={'test-owner':'alice'}) as c:
        bad={'endpoint_id':'ep','model':'model','protocol':'openai_json','timeout_seconds':'forever'}
        result=await c.put('/api/chat-settings',json={'revision':0,'changes':{'decision_provider':bad}})
        assert result.status_code==422
        assert (await c.get('/api/chat-settings')).json()['revision']==0
