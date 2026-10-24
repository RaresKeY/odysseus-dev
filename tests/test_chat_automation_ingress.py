"""Canonical JSON chat -> committed route -> actual mocked provider transport."""
import json
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI
from core import database as d
from core.models import Session
from core.session_manager import SessionManager
from src import llm_core
from src.chat_automation import AutomationStore
from tests.test_chat_automation_state import store


@pytest.fixture
def runtime(store,monkeypatch):
    from core import session_manager as smodule
    from routes import chat_routes,chat_helpers,session_routes
    from src import endpoint_resolver,foreground_model_routing,model_admission
    for module in (d,smodule,chat_routes,chat_helpers,session_routes,endpoint_resolver):
        monkeypatch.setattr(module,'SessionLocal',store.session_factory)
    with store.transaction() as db:
        db.add(d.ModelEndpoint(id='manual-registered',name='Manual',owner='alice',is_enabled=True,
            base_url='https://execution.invalid/v1',cached_models='["manual"]'))
        row=db.query(d.Session).filter_by(id='a').one()
        row.endpoint_id='manual-registered';row.endpoint_url='https://execution.invalid/v1/chat/completions'
    manager=SessionManager.__new__(SessionManager);manager.sessions={}
    manager.sessions['a']=Session(id='a',name='a',owner='alice',model='manual',endpoint_id='manual-registered',endpoint_url='https://execution.invalid/v1/chat/completions')
    monkeypatch.setattr(model_admission,'_AUTH_MANAGER',SimpleNamespace(users={'alice':{}},get_privileges=lambda owner:{}))
    class Handler:
        async def handle_memory_command(self,*args):return None
    async def context(sess,request,*args,**kwargs):
        return SimpleNamespace(user='alice',messages=[{'role':'user','content':kwargs['message']}],context_length=32768,
            uprefs={},preset=SimpleNamespace(character_name=None),route_messages=[])
    monkeypatch.setattr(chat_routes,'build_chat_context',context)
    monkeypatch.setattr(chat_routes,'run_post_response_tasks',lambda *a,**kw:None)
    monkeypatch.setattr(chat_routes,'clean_thinking_for_save',lambda content,metadata:(content,metadata))
    monkeypatch.setattr(foreground_model_routing,'_load_policy_preferences',lambda owner:{})
    monkeypatch.setattr(llm_core,'get_context_length',lambda *a,**kw:32768)
    monkeypatch.setattr(llm_core,'_get_cached_response',lambda *a,**kw:None)
    monkeypatch.setattr(llm_core,'_set_cached_response',lambda *a,**kw:None)
    monkeypatch.setattr(llm_core,'_is_host_dead',lambda *a:False)
    app=FastAPI();app.state.auth_manager=SimpleNamespace(get_privileges=lambda owner:{})
    @app.middleware('http')
    async def identity(request,call_next):request.state.current_user='alice';return await call_next(request)
    app.include_router(chat_routes.setup_chat_routes(manager,Handler(),SimpleNamespace(),SimpleNamespace(),SimpleNamespace(),SimpleNamespace()))
    return SimpleNamespace(app=app,manager=manager,store=store)


@pytest.mark.asyncio
async def test_manual_request_effort_and_original_ask_reach_actual_provider(runtime,monkeypatch):
    calls=[];raw='  Exact original ask\n\tλ  '
    def execution(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'model':'manual','choices':[{'message':{'content':'complete'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(execution)) as provider:
        monkeypatch.setattr(llm_core,'_get_http_client',lambda:provider)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app),base_url='http://test') as client:
            result=await client.post('/api/chat',json={'session':'a','message':raw,'reasoning_effort':'low'})
    assert result.status_code==200,result.text
    assert len(calls)==1 and calls[0]['reasoning_effort']=='low'
    assert calls[0]['messages']==[{'role':'user','content':raw}]
    with runtime.store.transaction() as db:
        turn=db.query(d.ChatAutomationSubmission).one()
        assert turn.status=='completed' and turn.descriptor['reasoning_effort']=='low'


@pytest.mark.asyncio
async def test_auto_endpoint_id_and_mapped_effort_override_stale_manual_fields(runtime,monkeypatch):
    from src import decisions
    from src.decisions import DecisionProvider,ResolvedDecisionEndpoint
    from src.chat_automation import SqlAttemptStore
    with runtime.store.transaction() as db:
        for identifier,base in [('chosen','https://execution.invalid/v1'),('same-model-other','https://other.invalid/v1')]:
            db.add(d.ModelEndpoint(id=identifier,name=identifier,owner='alice',is_enabled=True,base_url=base,cached_models='["same-model"]'))
    mappings={key:{'endpoint_id':'chosen','model':'same-model','reasoning_effort':'high'}
        for key in ('established_procedure','bounded_domain','approach_discovery')}
    runtime.store.update_configuration('alice',{'approaches':mappings,'decision_provider':{
        'endpoint_id':'review','model':'decision','protocol':'systemone'}},0)
    runtime.store.update_chat('alice','a',{'route_mode':'auto'},0)
    classifier=[];calls=[]
    def classify(request):
        classifier.append(json.loads(request.content))
        return httpx.Response(200,json={'model':'decision','answers':{
            'approach':{'type':'choice','choice':'bounded_domain','confidence':.98,'probabilities':{'established_procedure':.01,'bounded_domain':.98,'approach_discovery':.01}},
            'readiness':{'type':'choice','choice':'ready','confidence':.99,'probabilities':{'ready':.99,'needs_context':.01}}},'usage':{'input_tokens':10,'output_tokens':5}})
    decision_provider=DecisionProvider(SqlAttemptStore(runtime.store),resolver=lambda *a:ResolvedDecisionEndpoint(
        'review','decision','systemone','https://decision.invalid/v1/systemone',{}),
        client_factory=lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(classify),**kw))
    monkeypatch.setattr(decisions,'DecisionProvider',lambda **kw:decision_provider)
    def execution(request):
        calls.append(request)
        return httpx.Response(200,json={'model':'same-model','choices':[{'message':{'content':'complete'}}]})
    raw='  Solve this exact ask\n  '
    async with httpx.AsyncClient(transport=httpx.MockTransport(execution)) as provider:
        monkeypatch.setattr(llm_core,'_get_http_client',lambda:provider)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app),base_url='http://test') as client:
            result=await client.post('/api/chat',json={'session':'a','message':raw,'reasoning_effort':'low','selected_endpoint_id':'same-model-other'})
    assert result.status_code==200,result.text
    assert len(classifier)==len(calls)==1
    assert classifier[0]['state']['ask']==raw
    assert calls[0].url.host=='execution.invalid'
    assert json.loads(calls[0].content)['reasoning_effort']=='high'
    assert result.json()['endpoint_id']=='chosen'
    assert runtime.manager.sessions['a'].model=='manual'


@pytest.mark.asyncio
async def test_unconfigured_auto_choice_returns_visible_action_without_execution(runtime,monkeypatch):
    runtime.store.update_chat('alice','a',{'route_mode':'auto'},0)
    calls=[]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:calls.append(request))) as provider:
        monkeypatch.setattr(llm_core,'_get_http_client',lambda:provider)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app),base_url='http://test') as client:
            response=await client.post('/api/chat',json={'session':'a','message':'Original ask remains intact'})
    assert response.status_code==409
    assert 'manually' in response.json()['detail'] and calls==[]
    with runtime.store.transaction() as db:
        assert db.query(d.ChatAutomationSubmission).one().status=='failed'
