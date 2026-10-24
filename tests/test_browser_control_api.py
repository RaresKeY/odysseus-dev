"""Human browser routes enforce identity, owner scope and fresh privilege."""
import hashlib
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI
from core import database as d
from routes.browser_control_routes import setup_browser_control_routes
from tests.test_chat_automation_state import store


@pytest.fixture
def browser_api(store, monkeypatch):
    from src import browser_pipe
    monkeypatch.setattr(d,'SessionLocal',store.session_factory)
    calls=[];records={};privileges={'can_use_browser':True}
    app=FastAPI()
    app.state.auth_manager=SimpleNamespace(is_configured=True,get_privileges=lambda owner:dict(privileges))
    @app.middleware('http')
    async def identity(request,call_next):
        request.state.current_user=request.headers.get('test-owner')
        return await call_next(request)
    class Record:
        live=True
        task_grant=None
        pending_task_grant=None
        def invalidate_document(self):calls.append('invalidate')
        async def human_control(self,args,grant):calls.append(('action',grant.owner));return {'executed':True}
    async def enrol(owner,sid,manifest,profile_root):
        calls.append(('enrol',owner,sid));record=Record();records[(owner,sid)]=record;return record
    monkeypatch.setattr(browser_pipe,'enrol',enrol)
    monkeypatch.setattr(browser_pipe,'get',lambda owner,sid:records.get((owner,sid)))
    monkeypatch.setattr(browser_pipe.ProducerManifest,'load',lambda path:object())
    app.include_router(setup_browser_control_routes())
    return SimpleNamespace(app=app,calls=calls,records=records,privileges=privileges)


@pytest.mark.asyncio
async def test_browser_rejects_missing_authenticated_owner(browser_api,monkeypatch):
    monkeypatch.setenv('AUTH_ENABLED','true')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=browser_api.app),base_url='http://test') as c:
        assert (await c.post('/api/session/a/browser')).status_code==401
    assert browser_api.calls==[]


@pytest.mark.asyncio
async def test_explicit_auth_disabled_operator_uses_same_local_registry(browser_api,store,monkeypatch):
    from src.owner_identity import DEFAULT_LOCAL_OWNER
    monkeypatch.setenv('AUTH_ENABLED','false')
    with store.transaction() as db:
        db.add(d.Session(id='local',name='local',owner=DEFAULT_LOCAL_OWNER,model='manual',endpoint_url='http://synthetic.invalid'))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=browser_api.app),base_url='http://test') as c:
        assert (await c.post('/api/session/local/browser')).status_code==200
        scope=await c.post('/api/session/local/browser/scope',json={'url':'https://example.test/start','effects':[],'fields':[]})
        assert scope.status_code==200
    assert browser_api.calls[0]==('enrol',DEFAULT_LOCAL_OWNER,'local')
    assert browser_api.records[(DEFAULT_LOCAL_OWNER,'local')].pending_task_grant[0].owner==DEFAULT_LOCAL_OWNER


@pytest.mark.asyncio
async def test_revoked_browser_privilege_stops_enrol_and_human_actions(browser_api):
    browser_api.privileges['can_use_browser']=False
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=browser_api.app),base_url='http://test',headers={'test-owner':'alice'}) as c:
        assert (await c.post('/api/session/a/browser')).status_code==403
        browser_api.privileges['can_use_browser']=True
        assert (await c.post('/api/session/a/browser')).status_code==200
        assert (await c.post('/api/session/a/browser/scope',json={'url':'https://example.test','effects':['click']})).status_code==200
        browser_api.privileges['can_use_browser']=False
        assert (await c.post('/api/session/a/browser/action',json={'action':'click','ref':'n0','observation':'old'})).status_code==403
        assert (await c.get('/api/session/a/browser/view')).status_code==403
    assert not any(isinstance(call,tuple) and call[0]=='action' for call in browser_api.calls)


@pytest.mark.asyncio
async def test_owner_browser_scope_checks_remain_server_owned(browser_api):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=browser_api.app),base_url='http://test',headers={'test-owner':'alice'}) as c:
        assert (await c.post('/api/session/a/browser')).status_code==200
        body={'url':'https://example.test','effects':[],'checks':[{'kind':'text_contains','value':'Confirmed'}]}
        result=await c.post('/api/session/a/browser/scope',json=body)
        assert result.status_code==200
        grant=browser_api.records[('alice','a')].pending_task_grant[0]
        assert dict(grant.completion_checks[0])=={'kind':'text_contains','value':'Confirmed'}
        assert (await c.post('/api/session/c/browser')).status_code==404
