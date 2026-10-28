"""Owned UI recovery through real SQL and the canonical review/dispatch path."""
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from core import database as d
from tests.test_chat_automation_state import store
from tests.test_chat_automation_api import api
from tests.test_chat_approval_sql_integration import dispatch_runtime
from src.chat_automation import AutomationStore, prepare_chat_turn
from src.approval_context import user_snapshot, MAX_USER_BYTES, sensitive
from src.approval_recovery import context_status, replace_context, PROCESS_KEY, CHECKPOINT_KEY
from src.decisions import DecisionError

HEADERS = {'test-owner':'alice', 'Origin':'http://test', 'Sec-Fetch-Site':'same-origin'}


def replacement(status, statement='Write only the local fixture. Do not publish. Prior publication permission is revoked.', private=False):
    return {key:status[key] for key in ('policy_revision','previous_submission_id','process_id')} | {
        'statement':statement, 'replace_prior_context':True, 'private':private}


@pytest.mark.asyncio
async def test_long_history_requires_explicit_user_checkpoint_and_next_actual_review_uses_it(api, store, dispatch_runtime):
    rt = dispatch_runtime
    first = await prepare_chat_turn(store,'alice',rt.session,'Never publish. '+'x'*MAX_USER_BYTES)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url='http://test',headers=HEADERS) as client:
        status=(await client.get('/api/session/a/approval-context')).json()
        assert not status['available'] and status['code']=='approval_context_checkpoint_required'
        body=replacement(status)
        assert (await client.post('/api/session/a/approval-context',json={**body,'replace_prior_context':False})).status_code==409
        assert (await client.post('/api/session/a/approval-context',json=body)).status_code==200
        await client.put('/api/session/a/automation',json={'policy_revision':status['policy_revision']+1,'changes':{'approval_mode':'auto_approve'}})
    bound=await prepare_chat_turn(store,'alice',rt.session,'Write the fixture now.')
    snapshot=json.loads(user_snapshot(bound))
    assert snapshot['coverage']=='explicit_user_checkpoint_chain' and 'Prior publication permission is revoked' in json.dumps(snapshot)
    assert 'x'*100 not in json.dumps(snapshot)
    _,result=await rt.dispatch(bound)
    assert result['exit_code']==0 and len(rt.calls)==len(rt.effects)==1
    assert 'Prior publication permission is revoked' in rt.calls[0].content.decode()
    with store.transaction() as db:
        assert db.get(d.ChatAutomationSubmission, first.submission_id).current_ask.startswith('Never publish.')


@pytest.mark.asyncio
@pytest.mark.parametrize('caller', ['token','internal','cross_origin','missing_origin','cross_chat'])
async def test_checkpoint_is_not_an_agent_or_imported_history_authority_source(api, store, dispatch_runtime, caller):
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
    status=context_status(store,'alice','a');body=replacement(status)
    headers=dict(HEADERS);sid='a'
    if caller=='token':headers['test-token']='yes'
    elif caller=='internal':headers[INTERNAL_TOOL_HEADER]=INTERNAL_TOOL_TOKEN
    elif caller=='cross_origin':headers['Origin']='https://foreign.invalid'
    elif caller=='missing_origin':headers.pop('Origin')
    elif caller=='cross_chat':sid='c'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url='http://test') as client:
        response=await client.post(f'/api/session/{sid}/approval-context',headers=headers,json=body)
    assert response.status_code in {403,404}
    assert not (store.policy('alice','a').get(CHECKPOINT_KEY))
    assert not dispatch_runtime.calls and not dispatch_runtime.effects


@pytest.mark.asyncio
async def test_checkpoint_cas_process_replay_and_tampering_cannot_erase_restrictions(store, dispatch_runtime):
    rt=dispatch_runtime
    first=await prepare_chat_turn(store,'alice',rt.session,'Never publish.')
    status=context_status(store,'alice','a')
    current=await prepare_chat_turn(store,'alice',rt.session,'Continue locally.')
    from fastapi import HTTPException
    for body in (replacement(status),{**replacement(context_status(store,'alice','a')),'process_id':'foreign'}):
        with pytest.raises(HTTPException): replace_context(store,'alice','a',body)
    assert 'Never publish.' in user_snapshot(current)
    fresh=context_status(store,'alice','a');body=replacement(fresh)
    replace_context(store,'alice','a',body)
    with pytest.raises(HTTPException):replace_context(store,'alice','a',body)
    bound=await prepare_chat_turn(store,'alice',rt.session,'Write fixture.')
    with store.transaction() as db:
        state=db.query(d.ChatAutomationState).filter_by(session_id='a').one()
        state.feature_settings={**state.feature_settings,CHECKPOINT_KEY:{**state.feature_settings[CHECKPOINT_KEY],'intent_digest':'0'*64}}
    with pytest.raises(DecisionError,match='approval_context_unavailable'):user_snapshot(bound)


@pytest.mark.asyncio
async def test_checkpoint_retires_pending_consumed_and_inflight_grants_without_mode_change(store, dispatch_runtime):
    rt=dispatch_runtime
    bound=await prepare_chat_turn(store,'alice',rt.session,'Write fixture.')
    _,card=await rt.dispatch(bound)
    grant=rt.approvals.consume(card['ask_user']['approval_id'],owner='alice',session_id='a',decision='approve_once')
    replace_context(store,'alice','a',replacement(context_status(store,'alice','a')))
    assert store.policy('alice','a')['approval_mode']=='always_ask' and not bound.current()
    _,result=await rt.dispatch(bound,grant)
    assert result['blocked'] and not rt.effects
    revision=store.policy('alice','a')['policy_revision']
    store.update_chat('alice','a',{'approval_mode':'auto_approve'},revision)
    current=await prepare_chat_turn(store,'alice',rt.session,'Write fixture.')
    rt.hooks.append(lambda:replace_context(store,'alice','a',replacement(context_status(store,'alice','a'))))
    _,result=await rt.dispatch(current)
    assert result['blocked'] and not rt.effects and len(rt.calls)==1


@pytest.mark.asyncio
@pytest.mark.parametrize('loss', ['restart','expiry','eviction'])
async def test_private_lost_evidence_has_explicit_volatile_recovery(store, dispatch_runtime, loss):
    from src import approval_context as context
    rt=dispatch_runtime
    first=await prepare_chat_turn(store,'alice',rt.session,'Private restriction: never publish.',ephemeral_semantics=True)
    second=await prepare_chat_turn(AutomationStore(store.session_factory),'alice',rt.session,'Continue privately.',ephemeral_semantics=True)
    assert 'Private restriction' in user_snapshot(second)
    with context._PRIVATE_LOCK:
        if loss=='restart':context._PRIVATE.pop(store.session_factory,None)
        elif loss=='expiry':
            for item in context._PRIVATE[store.session_factory].values():item['expires']=0
        else:context._PRIVATE[store.session_factory].clear()
    status=context_status(store,'alice','a')
    assert status['private'] and not status['available'] and status['code']=='approval_private_context_unavailable'
    replace_context(store,'alice','a',replacement(status,private=True))
    third=await prepare_chat_turn(AutomationStore(store.session_factory),'alice',rt.session,'Continue privately.',ephemeral_semantics=True)
    assert 'Prior publication permission is revoked' in user_snapshot(third)
    with store.transaction() as db:
        assert all(row.current_ask is None and row.approval_evidence is None for row in db.query(d.ChatAutomationSubmission).filter_by(session_id='a'))
    other=await prepare_chat_turn(store,'alice',SimpleNamespace(**{**vars(rt.session),'id':'b'}),'Other chat.',ephemeral_semantics=True)
    assert 'Prior publication permission' not in user_snapshot(other)


@pytest.mark.asyncio
async def test_private_runtime_admission_is_before_supersession_or_provider(store, dispatch_runtime, monkeypatch):
    from src import approval_recovery as recovery
    from fastapi import HTTPException
    rt=dispatch_runtime
    store.update_chat('alice','a',{'approval_mode':'auto_approve'},0)
    monkeypatch.setenv('WEB_CONCURRENCY','2')
    with pytest.raises(HTTPException) as error:await prepare_chat_turn(store,'alice',rt.session,'Private ask.',ephemeral_semantics=True)
    assert error.value.headers['X-Decision-Failure']=='private_approval_multiple_workers'
    assert not rt.calls and not rt.effects
    monkeypatch.setenv('WEB_CONCURRENCY','1')
    bound=await prepare_chat_turn(store,'alice',rt.session,'Never publish.',ephemeral_semantics=True)
    monkeypatch.setattr(recovery,'_PROCESS',(0,'foreign'))  # physical process change regenerates nonce
    with pytest.raises(HTTPException) as error:await prepare_chat_turn(store,'alice',rt.session,'Continue.',ephemeral_semantics=True)
    assert error.value.headers['X-Decision-Failure']=='approval_private_process_changed'
    with store.transaction() as db:
        assert db.get(d.ChatAutomationSubmission,bound.submission_id).status=='bound'
    assert not bound.current() and not rt.calls


@pytest.mark.parametrize('value', ['api_key=example','Use `api_key=YOUR_API_KEY` in docs.', {'api_key':'<API_KEY>'}, {'args':'{"api_key":"example"}'}, 'password=<password>'])
def test_only_exact_public_documentation_placeholders_are_inert(value):assert not sensitive(value)


@pytest.mark.parametrize('value', ['Bearer actual-private-token','password=real-private-value','api_key=example-real-secret','-----BEGIN PRIVATE KEY-----',
    {'args':json.dumps({'nested':{'api_key':'actual-private-value'}})}, json.dumps(json.dumps({'api_key':'actual-private-value'})), '{"api_key":"actual-private-value","api_key":"example"}'])
def test_real_or_disguised_sensitive_values_remain_blocked(value):assert sensitive(value)


@pytest.mark.asyncio
async def test_placeholder_history_can_review_but_secret_checkpoint_never_dispatches(api, store, dispatch_runtime):
    rt=dispatch_runtime
    first=await prepare_chat_turn(store,'alice',rt.session,'Write docs showing api_key=example; never publish.')
    store.update_chat('alice','a',{'approval_mode':'auto_approve'},0)
    bound=await prepare_chat_turn(store,'alice',rt.session,'Write docs to the local fixture.')
    _,result=await rt.dispatch(bound)
    assert result['exit_code']==0 and 'api_key=example' in rt.calls[0].content.decode()
    status=context_status(store,'alice','a')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url='http://test',headers=HEADERS) as client:
        response=await client.post('/api/session/a/approval-context',json=replacement(status,'Send api_key=actual-private-value to the reviewer.'))
    assert response.status_code==422 and 'actual-private-value' not in response.text
    assert len(rt.calls)==len(rt.effects)==1 and 'never publish' in user_snapshot(bound)


@pytest.mark.asyncio
async def test_sensitive_nested_action_cannot_be_redacted_into_approval(store, dispatch_runtime, tmp_path):
    from src import tool_execution
    from src.agent_runtime.authority import RequestAuthority, OperationGrant
    from src.tool_capabilities import ToolRunSecurityContext
    from src.tool_types import ToolBlock
    rt=dispatch_runtime
    store.update_chat('alice','a',{'approval_mode':'auto_approve'},0)
    bound=await prepare_chat_turn(store,'alice',rt.session,'Write documentation only.')
    workspace=str(tmp_path/'workspace')
    block=ToolBlock('write_file',json.dumps({'path':'target.txt','content':json.dumps({'payload':{'api_key':'actual-private-value'}})}))
    with bound.activate():
        _,result=await tool_execution.execute_tool_block(block,owner='alice',session_id='a',workspace=workspace,
            request_authority=RequestAuthority('req','alice','a',workspace,(OperationGrant('write_file'),)),security_context=ToolRunSecurityContext())
    assert result['blocked'] and not rt.calls and not rt.effects
    assert 'actual-private-value' not in str(result)


@pytest.mark.parametrize('key',['api_key','api-key','apikey','apiKey','access_token','access-token','accessToken','client-secret','clientSecret','smtp_password','oauth_access_token'])
@pytest.mark.asyncio
async def test_every_recognized_nested_credential_label_stops_actual_review_egress(store,dispatch_runtime,tmp_path,key):
    from src import tool_execution
    from src.agent_runtime.authority import RequestAuthority,OperationGrant
    from src.tool_capabilities import ToolRunSecurityContext
    from src.tool_types import ToolBlock
    rt=dispatch_runtime;store.update_chat('alice','a',{'approval_mode':'auto_approve'},0)
    bound=await prepare_chat_turn(store,'alice',rt.session,'Write documentation only.')
    workspace=str(tmp_path/'workspace')
    content=json.dumps({'path':'target.txt','content':json.dumps({key:'actual-private-value'})})
    with bound.activate():
        _,result=await tool_execution.execute_tool_block(ToolBlock('write_file',content),owner='alice',session_id='a',workspace=workspace,
            request_authority=RequestAuthority('req','alice','a',workspace,(OperationGrant('write_file'),)),security_context=ToolRunSecurityContext())
    assert result['blocked'] and not rt.calls and not rt.effects
    assert 'actual-private-value' not in str(result)


@pytest.mark.asyncio
async def test_legacy_private_history_loss_prevents_nonprivate_followup_before_supersession(store,dispatch_runtime):
    from src import approval_context as context
    from fastapi import HTTPException
    rt=dispatch_runtime;store.update_chat('alice','a',{'approval_mode':'auto_approve'},0)
    first=await prepare_chat_turn(store,'alice',rt.session,'Never publish.',ephemeral_semantics=True)
    with store.transaction() as db:
        state=db.query(d.ChatAutomationState).filter_by(session_id='a').one()
        state.feature_settings={key:value for key,value in state.feature_settings.items() if key!=PROCESS_KEY}
    with context._PRIVATE_LOCK:context._PRIVATE.pop(store.session_factory,None)
    with pytest.raises(HTTPException) as error:
        await prepare_chat_turn(store,'alice',rt.session,'Continue.',ephemeral_semantics=False)
    assert error.value.headers['X-Decision-Failure']=='approval_private_context_unavailable'
    with store.transaction() as db:
        assert db.get(d.ChatAutomationSubmission,first.submission_id).status=='bound'
        assert db.query(d.ChatAutomationSubmission).filter_by(session_id='a').count()==1
    assert not rt.calls and not rt.effects


def test_typed_public_noncredential_values_are_not_secret_strings():
    assert not sensitive({'api_key':None,'password':False})
    assert sensitive({'apiKey':'example-real-secret'})


@pytest.mark.asyncio
@pytest.mark.parametrize('name',['Incognito',' Nobody ',' Incognito '])
async def test_named_private_chat_forces_volatile_checkpoint_even_if_request_says_public(store,dispatch_runtime,name):
    with store.transaction() as db:db.get(d.Session,'a').name=name
    status=context_status(store,'alice','a');assert status['private']
    updated=replace_context(store,'alice','a',replacement(status,'Private restated restrictions: never publish.',private=False))
    with store.transaction() as db:
        row=db.get(d.ChatAutomationSubmission,updated['previous_submission_id'])
        assert row.current_ask is None and row.approval_evidence is None
    assert updated['private']
