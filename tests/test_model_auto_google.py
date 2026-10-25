"""Documented exact Google effort controls through real mocked transport."""
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from core import database as d
from src import llm_core, model_auto
from src.chat_automation import prepare_chat_submission
from tests.test_model_auto import registered, store, Reviewer, ASK

BASE='https://generativelanguage.googleapis.com/v1beta/openai'
URL=BASE+'/chat/completions'


def google_pool(store, model='gemini-2.5-pro', description='  Established documented repeatable execution.\nPreserve this wording.  '):
    with store.transaction() as db:
        row=db.query(d.ModelEndpoint).filter_by(id='one').one()
        row.base_url=BASE;row.cached_models=json.dumps([model]);row.pinned_models=None
    pool=[{'endpoint_id':'one','model':model,'description':description}]
    store.update_chat('alice','a',{'auto_models':pool,'route_mode':'auto'},0)
    return pool


def session():
    return SimpleNamespace(id='a',name='a',model='manual',endpoint_id=None,thinking_mode='off')


@pytest.mark.parametrize('model,efforts',list(model_auto.GOOGLE_EFFORTS.items()))
def test_exact_canonical_google_capabilities(model,efforts):
    actual=model_auto.execution_capabilities(URL,model)
    assert actual['supported_efforts']==list(efforts)
    assert actual['capability_revision']==model_auto.GOOGLE_CAPABILITY_REVISION
    assert not {'xhigh','max','ultra'} & set(efforts)


@pytest.mark.parametrize('url,model',[(BASE,'models/gemini-2.5-pro'),(BASE,'gemini-unknown-preview'),
    (BASE,'gemini-3-pro-image-preview'),('https://proxy.invalid/v1','gemini-2.5-pro'),
    ('http://generativelanguage.googleapis.com/v1beta/openai','gemini-2.5-pro'),
    (BASE+'?key=synthetic','gemini-2.5-pro'),(BASE+'#fragment','gemini-2.5-pro'),
    ('https://userinfo@generativelanguage.googleapis.com/v1beta/openai','gemini-2.5-pro'),
    ('https://@generativelanguage.googleapis.com/v1beta/openai','gemini-2.5-pro'),
    ('https://generativelanguage.googleapis.com:444/v1beta/openai','gemini-2.5-pro'),
    ('https://generativelanguage.googleapis.com.evil.invalid/v1beta/openai','gemini-2.5-pro')])
def test_unknown_endpoint_model_or_noncanonical_alias_has_no_inferred_effort(url,model):
    assert model_auto.execution_capabilities(url,model)['supported_efforts']==['default']


@pytest.mark.asyncio
@pytest.mark.parametrize('stream',[False,True])
async def test_google_selected_effort_and_verbatim_role_reach_actual_transport(registered,monkeypatch,stream):
    pool=google_pool(registered)
    reviewer=Reviewer(effort='high')
    bound=await prepare_chat_submission(registered,'alice',session(),ASK,decision_provider=reviewer)
    assert registered.policy('alice','a')['auto_models']==pool
    request=reviewer.calls[0]
    assert request.state['candidates']['candidate_0']['user_role']==pool[0]['description']
    assert pool[0]['description'] in json.loads(request.choices['candidate_0'].split('quoted data: ',1)[1])
    calls=[]
    def transport(request):
        payload=json.loads(request.content);calls.append(payload)
        assert str(request.url)==URL
        assert payload['model']=='gemini-2.5-pro' and payload['reasoning_effort']=='high'
        assert payload['messages']==[{'role':'user','content':ASK}]
        if stream:
            return httpx.Response(200,text='data: '+json.dumps({'choices':[{'delta':{'content':'complete'}}]})+'\n\ndata: [DONE]\n\n')
        return httpx.Response(200,json={'model':'gemini-2.5-pro','choices':[{'message':{'content':'complete'}}]})
    monkeypatch.setattr(llm_core,'_get_cached_response',lambda *a:None)
    monkeypatch.setattr(llm_core,'_set_cached_response',lambda *a,**kw:None)
    monkeypatch.setattr(llm_core,'get_context_length',lambda *a,**kw:32768)
    monkeypatch.setattr(llm_core,'_is_host_dead',lambda *a:False)
    monkeypatch.setattr(llm_core,'note_model_activity',lambda *a,**kw:None)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        monkeypatch.setattr(llm_core,'_get_http_client',lambda:client)
        with bound.activate():
            if stream:
                chunks=[c async for c in llm_core.stream_llm(URL,'gemini-2.5-pro',[{'role':'user','content':ASK}],
                    headers=bound.headers,session_id='a',reasoning_effort='high',thinking_mode='off')]
                assert any('complete' in c for c in chunks)
            else:
                assert await llm_core.llm_call_async(URL,'gemini-2.5-pro',[{'role':'user','content':ASK}],
                    headers=bound.headers,session_id='a',reasoning_effort='high')=='complete'
    assert len(calls)==1


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['capability_revision','endpoint','role'])
async def test_stale_google_evidence_stops_before_provider_transport(registered,monkeypatch,change):
    pool=google_pool(registered)
    bound=await prepare_chat_submission(registered,'alice',session(),ASK,decision_provider=Reviewer(effort='high'))
    if change=='capability_revision':monkeypatch.setattr(model_auto,'GOOGLE_CAPABILITY_REVISION','changed')
    if change=='endpoint':
        with registered.transaction() as db:db.query(d.ModelEndpoint).filter_by(id='one').one().base_url='https://proxy.invalid/v1'
    if change=='role':registered.update_chat('alice','a',{'auto_models':[{**pool[0],'description':'Changed role'}]},1)
    calls=[]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:calls.append(req))) as client:
        monkeypatch.setattr(llm_core,'_get_http_client',lambda:client)
        with bound.activate(),pytest.raises(HTTPException):
            await llm_core.llm_call_async(URL,'gemini-2.5-pro',[{'role':'user','content':ASK}],
                headers=bound.headers,session_id='a',reasoning_effort='high')
    assert calls==[]


@pytest.mark.asyncio
async def test_supported_union_effort_cannot_be_used_with_incompatible_pro_model(registered):
    google_pool(registered)
    with registered.transaction() as db:
        db.query(d.ModelEndpoint).filter_by(id='one').one().cached_models=json.dumps(['gemini-2.5-pro','gemini-2.5-flash'])
    pool=[{'endpoint_id':'one','model':m} for m in ['gemini-2.5-pro','gemini-2.5-flash']]
    registered.update_chat('alice','a',{'auto_models':pool},1)
    with pytest.raises(HTTPException) as error:
        await prepare_chat_submission(registered,'alice',session(),ASK,decision_provider=Reviewer(effort='none'))
    assert 'unsupported reasoning effort' in error.value.detail


@pytest.mark.asyncio
async def test_candidate_specific_roles_are_preserved_as_data_and_bound_to_revision(registered):
    pool=[{'endpoint_id':'one','model':'shared','description':'Exact retrieval and documented repeatable work.'},
          {'endpoint_id':'two','model':'shared','description':'Learning, teaching, bespoke work and interacting contracts.'}]
    registered.update_chat('alice','a',{'auto_models':pool,'route_mode':'auto'},0)
    reviewer=Reviewer('candidate_1')
    bound=await prepare_chat_submission(registered,'alice',session(),ASK,decision_provider=reviewer)
    choices=reviewer.calls[0].choices
    assert pool[0]['description'] in choices['candidate_0'] and pool[1]['description'] in choices['candidate_1']
    assert bound.descriptor['auto_candidate_snapshot'][1]['description']==pool[1]['description']
    registered.update_chat('alice','a',{'auto_models':[{**pool[0],'description':'Changed'},pool[1]]},1)
    assert not bound.current()
