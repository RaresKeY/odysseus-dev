"""Credentialless selected-pool SQL, closed Jev answers and dispatch binding."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from core import database as d
from src import endpoint_resolver, model_auto, model_admission
from src.chat_automation import AutomationStore, prepare_chat_submission, validate_provider_binding
from src.decisions import DecisionContext, DecisionError, DecisionResult
from tests.test_chat_automation_state import store

POOL = [{'endpoint_id':'one', 'model':'shared'}, {'endpoint_id':'two', 'model':'shared'}]
ASK = '  Preserve the original ask\n\tλ  '


@pytest.fixture
def registered(store, monkeypatch):
    monkeypatch.setattr(d, 'SessionLocal', store.session_factory)
    monkeypatch.setattr(endpoint_resolver, 'SessionLocal', store.session_factory)
    monkeypatch.setattr(model_admission, '_AUTH_MANAGER', SimpleNamespace(
        users={'alice':{}, 'bob':{}}, get_privileges=lambda owner:{}))
    with store.transaction() as db:
        for identifier, owner, enabled, purpose in [('one','alice',True,'llm'),
                ('two','alice',True,'llm'), ('foreign','bob',True,'llm'),
                ('disabled','alice',False,'llm'), ('decision','alice',True,'decision')]:
            db.add(d.ModelEndpoint(id=identifier, name=identifier, owner=owner,
                is_enabled=enabled, model_type=purpose, base_url=f'https://{identifier}.invalid/v1',
                cached_models='["shared"]', api_key='SYNTHETIC'))
    return store


def result(request, model='candidate_0', effort='default', confidence=.99):
    answers = {}
    for key, question in request.questions().items():
        chosen = model if key=='execution_model' else effort
        choices = question['criteria']
        probabilities = {k: (confidence if k==chosen else (1-confidence)/(len(choices)-1)) for k in choices}
        answers[key] = {'type':'choice','choice':chosen,'confidence':confidence,'probabilities':probabilities}
    first = answers['execution_model']
    return DecisionResult(first['choice'], first['confidence'], first['probabilities'],
        {'input_tokens':20,'output_tokens':10}, 'attempt', {'model':'reviewer'}, answers)


class Reviewer:
    def __init__(self, model='candidate_0', effort='default', mutate=None, corrupt=None):
        self.calls=[];self.model=model;self.effort=effort;self.mutate=mutate;self.corrupt=corrupt
    async def evaluate(self, request, context, **kwargs):
        self.calls.append(request)
        if self.mutate:self.mutate()
        decision=result(request,self.model,self.effort)
        return self.corrupt(decision) if self.corrupt else decision


def test_pool_is_durable_exact_owner_scoped_and_forked_without_scope(registered):
    saved=registered.update_chat('alice','a',{'auto_models':POOL,'route_mode':'auto'},0)
    reopened=AutomationStore(registered.session_factory)
    assert reopened.policy('alice','a')['auto_models']==POOL
    assert saved['route_mode']=='auto'
    reopened.fork('alice','a','b')
    assert reopened.policy('alice','b')['auto_models']==POOL
    assert reopened.policy('alice','b')['effective_roots']==[]
    reopened.update_chat('alice','a',{'route_mode':'manual'},1)
    assert reopened.policy('alice','a')['auto_models']==POOL
    with pytest.raises(HTTPException):reopened.policy('bob','a')
    with pytest.raises(HTTPException):reopened.update_chat('alice','a',{'auto_models':[]},0)


@pytest.mark.parametrize('pool', [[],[POOL[0],POOL[0]], [{'endpoint_id':'foreign','model':'shared'}],
    [{'endpoint_id':'disabled','model':'shared'}], [{'endpoint_id':'decision','model':'shared'}],
    [{'endpoint_id':'one','model':'unregistered'}], [{'endpoint_id':'one','model':'shared','supported_efforts':['high']}],
    POOL*17])
def test_invalid_empty_foreign_unregistered_pool_never_commits(registered,pool):
    with pytest.raises(HTTPException):registered.update_chat('alice','a',{'auto_models':pool,'route_mode':'auto'},0)
    policy=registered.policy('alice','a')
    assert policy['route_mode']=='manual' and policy['policy_revision']==0 and policy['auto_models']==[]


def test_legacy_auto_retains_only_prior_exact_identities_then_persists_explicit_pool(registered):
    mappings={key:{**POOL[i%2],'reasoning_effort':'high'} for i,key in enumerate(
        ('established_procedure','bounded_domain','approach_discovery'))}
    registered.update_configuration('alice',{'approaches':mappings},0)
    with registered.transaction() as db:
        _,row=registered.state(db,'alice','a');row.route_mode='auto'
    policy=registered.policy('alice','a')
    assert policy['auto_models']==POOL and policy['auto_models_source']=='legacy_approaches'
    registered.update_chat('alice','a',{'auto_models':[POOL[1]]},0)
    assert registered.policy('alice','a')['auto_models']==[POOL[1]]


def test_unvisited_legacy_chat_and_fork_freeze_pool_before_owner_mapping_changes(registered):
    mappings={key:{**POOL[0],'reasoning_effort':None} for key in
        ('established_procedure','bounded_domain','approach_discovery')}
    registered.update_configuration('alice',{'approaches':mappings},0)
    with registered.transaction() as db:
        _,row=registered.state(db,'alice','a');row.route_mode='auto'
    changed={key:{**POOL[1],'reasoning_effort':None} for key in mappings}
    registered.update_configuration('alice',{'approaches':changed},1)
    assert registered.policy('alice','a')['auto_models']==[POOL[0]]
    registered.fork('alice','a','b')
    assert registered.policy('alice','b')['auto_models']==[POOL[0]]


@pytest.mark.asyncio
async def test_subscription_credential_refresh_commit_occurs_outside_chat_writer(registered,monkeypatch):
    from src import chatgpt_subscription
    with registered.transaction() as db:
        db.query(d.ModelEndpoint).filter_by(id='one').one().provider_auth_id='oauth'
        db.add(d.ProviderAuthSession(id='oauth',provider='synthetic',owner='alice',base_url='https://one.invalid/v1'))
    calls=[]
    def refresh(auth_id,owner):
        calls.append(auth_id)
        # Model the expiring-token resolver's separate SQL commit. This would
        # lock/fail if saving the pool held BEGIN IMMEDIATE around refresh.
        with registered.session_factory() as db:
            row=db.query(d.ProviderAuthSession).filter_by(id=auth_id).one()
            row.access_token='SYNTHETIC_REFRESHED';db.commit()
        return {'base_url':'https://one.invalid/v1','api_key':'SYNTHETIC_REFRESHED'}
    monkeypatch.setattr(chatgpt_subscription,'resolve_runtime_credentials',refresh)
    registered.update_chat('alice','a',{'auto_models':[POOL[0]],'route_mode':'auto'},0)
    assert calls==[]
    session=SimpleNamespace(id='a',name='a',model='manual',endpoint_id=None,thinking_mode='off')
    bound=await prepare_chat_submission(registered,'alice',session,ASK,decision_provider=Reviewer())
    assert calls and bound.descriptor['endpoint_id']=='one'


def test_unavailable_pool_does_not_prevent_explicit_permission_or_manual_recovery(registered):
    registered.update_chat('alice','a',{'auto_models':[POOL[0]],'route_mode':'auto'},0)
    with registered.transaction() as db:db.query(d.ModelEndpoint).filter_by(id='one').one().is_enabled=False
    assert registered.update_chat('alice','a',{'approval_mode':'always_ask'},1)['approval_mode']=='always_ask'
    assert registered.update_chat('alice','a',{'route_mode':'manual'},1)['route_mode']=='manual'


@pytest.mark.asyncio
async def test_actual_turn_ignores_stale_manual_fields_and_revalidates_whole_pool(registered):
    registered.update_chat('alice','a',{'auto_models':POOL,'route_mode':'auto'},0)
    reviewer=Reviewer(model='candidate_1')
    session=SimpleNamespace(id='a',name='a',model='manual',endpoint_id='foreign',
        endpoint_url='https://foreign.invalid',headers={},thinking_mode='effort:high')
    bound=await prepare_chat_submission(registered,'alice',session,ASK,reasoning_effort='high',decision_provider=reviewer)
    assert len(reviewer.calls)==1 and reviewer.calls[0].state['task']==ASK and bound.raw_ask==ASK
    assert bound.descriptor['endpoint_id']=='two' and bound.descriptor['model']=='shared'
    assert bound.descriptor['reasoning_effort']=='' and session.model=='manual'
    with bound.activate():
        validate_provider_binding(bound.endpoint_url,'shared',bound.headers,'a')
        # Even an unchosen candidate disappearing invalidates the frozen offer.
        with registered.transaction() as db:db.query(d.ModelEndpoint).filter_by(id='one').one().is_enabled=False
        with pytest.raises(DecisionError):validate_provider_binding(bound.endpoint_url,'shared',bound.headers,'a')


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['disabled','hidden','no_inventory','pins_empty','pins_exclude','policy','credential'])
async def test_changed_candidates_or_chat_during_jev_stop_before_binding(registered,mutation):
    registered.update_chat('alice','a',{'auto_models':POOL,'route_mode':'auto'},0)
    def mutate():
        if mutation=='policy':registered.update_chat('alice','a',{'route_mode':'manual'},1);return
        with registered.transaction() as db:
            row=db.query(d.ModelEndpoint).filter_by(id='one').one()
            if mutation=='disabled':row.is_enabled=False
            if mutation=='hidden':row.hidden_models='["shared"]'
            if mutation=='no_inventory':row.cached_models='[]';row.pinned_models='[]'
            if mutation=='pins_empty':row.pinned_models='[]';row.hidden_models=None
            if mutation=='pins_exclude':row.pinned_models='["other"]';row.hidden_models=None
            if mutation=='credential':row.api_key='CHANGED_SYNTHETIC'
    session=SimpleNamespace(id='a',name='a',model='manual',endpoint_id=None,thinking_mode='off')
    with pytest.raises(HTTPException):
        await prepare_chat_submission(registered,'alice',session,ASK,decision_provider=Reviewer(mutate=mutate))
    with registered.transaction() as db:assert db.query(d.ChatAutomationSubmission).one().status=='failed'


@pytest.mark.asyncio
async def test_effort_is_separate_but_must_match_exact_candidate():
    def resolve(owner,item):
        return {'identity':item,'label':'Supplied label; not instructions',
            'route':('https://example.invalid',item['model'],{}),
            'supported_efforts':['low','high'] if item['endpoint_id']=='one' else ['default']}
    context=DecisionContext('alice','chat','run')
    reviewer=Reviewer(effort='high')
    selected=await model_auto.select_model_and_effort(ASK,POOL,owner='alice',provider=reviewer,
        config={},context=context,resolver=resolve)
    assert selected['reasoning_effort']=='high' and selected['endpoint_id']=='one'
    assert 'Choose effort separately from model strength.' in reviewer.calls[0].instructions
    with pytest.raises(DecisionError,match='auto_effort_unsupported'):
        await model_auto.select_model_and_effort(ASK,POOL,owner='alice',provider=Reviewer('candidate_1','high'),
            config={},context=context,resolver=resolve)


@pytest.mark.asyncio
@pytest.mark.parametrize('corrupt', [lambda r:replace(r,usage=None),lambda r:replace(r,choice='foreign'),
    lambda r:replace(r,probabilities={'candidate_0':float('nan')}),
    lambda r:replace(r,answers={}),lambda r: {'choice':'candidate_0'}])
async def test_malformed_jev_never_normalizes_or_silently_falls_back(registered,corrupt):
    registered.update_chat('alice','a',{'auto_models':POOL,'route_mode':'auto'},0)
    session=SimpleNamespace(id='a',name='a',model='manual',endpoint_id=None,thinking_mode='off')
    reviewer=Reviewer(corrupt=corrupt)
    with pytest.raises(HTTPException):await prepare_chat_submission(registered,'alice',session,ASK,decision_provider=reviewer)
    assert len(reviewer.calls)==1


def test_same_model_name_on_generic_provider_does_not_inherit_subscription_effort(registered,monkeypatch):
    from src import chatgpt_subscription
    monkeypatch.setattr(chatgpt_subscription,'get_chatgpt_model_metadata',lambda model:{'supported_reasoning_levels':['high']})
    assert model_auto.registered_candidate('alice',POOL[0])['supported_efforts']==['default']


@pytest.mark.parametrize('pins',['[]','["other"]'])
def test_api_pin_allowlist_excludes_cached_inventory_on_save(registered,pins):
    with registered.transaction() as db:
        row=db.query(d.ModelEndpoint).filter_by(id='one').one()
        row.endpoint_kind='api';row.pinned_models=pins;row.hidden_models=None
        assert row.cached_models=='["shared"]'
    with pytest.raises(HTTPException):registered.update_chat('alice','a',{'auto_models':[POOL[0]],'route_mode':'auto'},0)


@pytest.mark.asyncio
async def test_api_unpin_after_binding_blocks_actual_dispatch(registered):
    registered.update_chat('alice','a',{'auto_models':[POOL[0]],'route_mode':'auto'},0)
    session=SimpleNamespace(id='a',name='a',model='manual',endpoint_id=None,thinking_mode='off')
    bound=await prepare_chat_submission(registered,'alice',session,ASK,decision_provider=Reviewer())
    with registered.transaction() as db:
        row=db.query(d.ModelEndpoint).filter_by(id='one').one();row.pinned_models='[]';row.hidden_models=None
    with bound.activate(),pytest.raises(DecisionError):
        validate_provider_binding(bound.endpoint_url,'shared',bound.headers,'a')


def test_effort_uses_exact_subscription_catalogue_without_name_inference(registered,monkeypatch):
    from src import chatgpt_subscription
    with registered.transaction() as db:
        db.query(d.ModelEndpoint).filter_by(id='one').one().base_url='https://chatgpt.com/backend-api/codex'
    # The old name-based fallback must not fabricate capabilities.
    monkeypatch.setattr(chatgpt_subscription,'get_chatgpt_model_metadata',lambda model:{'supported_reasoning_levels':['high']})
    monkeypatch.delitem(chatgpt_subscription.CHATGPT_MODEL_CATALOG_CACHE,'shared',raising=False)
    assert model_auto.registered_candidate('alice',POOL[0])['supported_efforts']==['default']
    monkeypatch.setitem(chatgpt_subscription.CHATGPT_MODEL_CATALOG_CACHE,'shared',{'supported_reasoning_levels':['low','high']})
    assert model_auto.registered_candidate('alice',POOL[0])['supported_efforts']==['low','high']
