"""Explicit two-category mapping, full donor policy and safe legacy retention."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from core import database as d
from src import model_auto
from src.chat_automation import AutomationStore, prepare_chat_submission
from src.decisions import DecisionContext, DecisionError
from tests.test_model_auto import ASK, POOL, CATEGORIES, Reviewer, categories, registered, store


def test_complete_user_policy_survives_only_neutral_model_mapping():
    donor = json.loads((Path(__file__).parent/'fixtures/model_auto/codex_jev_policy.json').read_text())
    def mapped(value):
        if isinstance(value, str):
            return value.replace('gpt-6.1-sol','complex_tasks').replace('gpt-6-luna','straightforward_tasks').replace('Sol','Complex tasks').replace('Luna','Straightforward tasks')
        if isinstance(value, list): return [mapped(item) for item in value]
        if isinstance(value, dict): return {key:mapped(item) for key,item in value.items()}
        return value
    assert model_auto.ROUTING_POLICY == mapped(donor)
    assert len(model_auto.ROUTING_POLICY['anchors']) == 10
    assert ['Implement a known complex protocol with direct checks','complex_tasks:low'] in model_auto.ROUTING_POLICY['anchors']
    assert 'Effort does not limit persistence or retries.' in model_auto.ROUTING_POLICY['effort']
    assert 'not learning or teaching' in model_auto.CATEGORY_CRITERIA['straightforward_tasks']
    assert 'learning something new, teaching, explanations of how or why' in model_auto.CATEGORY_CRITERIA['complex_tasks']
    assert 'quality' not in json.dumps(model_auto.CATEGORY_CRITERIA)


@pytest.mark.parametrize('value', [None, [], {}, {'complex_tasks':POOL[0]},
    {**CATEGORIES,'extra':POOL[0]}, {**CATEGORIES,'complex_tasks':None},
    {**CATEGORIES,'complex_tasks':{**POOL[0],'description':'No roles'}},
    {**CATEGORIES,'complex_tasks':{'endpoint_id':'one','model':' '}},
    {**CATEGORIES,'complex_tasks':{'endpoint_id':True,'model':'shared'}},
    {**CATEGORIES,'straightforward_tasks':{'endpoint_id':'foreign','model':'shared'}},
    {**CATEGORIES,'straightforward_tasks':{'endpoint_id':'decision','model':'shared'}},
    {**CATEGORIES,'straightforward_tasks':{'endpoint_id':'disabled','model':'shared'}}])
def test_invalid_incomplete_or_unavailable_categories_cannot_activate(registered,value):
    with pytest.raises(HTTPException):
        registered.update_chat('alice','a',{'auto_categories':value,'route_mode':'auto'},0)
    assert registered.policy('alice','a')['auto_categories'] == {}
    assert registered.policy('alice','a')['route_mode'] == 'manual'


def test_same_exact_model_can_be_assigned_to_both_categories_and_forked(registered):
    assignments=categories(POOL[0])
    source=registered.update_chat('alice','a',{'approval_mode':'yolo','auto_categories':assignments,'route_mode':'auto'},0)
    preferences=registered.configuration('alice')
    reopened=AutomationStore(registered.session_factory)
    assert reopened.policy('alice','a')['auto_categories'] == assignments
    reopened.fork('alice','a','b')
    assert reopened.policy('alice','b')['auto_categories'] == assignments
    assert reopened.policy('alice','b')['route_mode'] == 'auto'
    assert reopened.policy('alice','b')['approval_mode'] == 'always_ask'
    assert not reopened.policy('alice','b')['effective_roots']
    assert reopened.policy('alice','a') == source
    assert reopened.configuration('alice') == preferences
    with pytest.raises(HTTPException): reopened.policy('bob','a')


def test_partial_manual_draft_persists_but_auto_requires_both_assignments(registered):
    draft={'complex_tasks':POOL[0]}
    saved=registered.update_chat('alice','a',{'auto_categories':draft},0)
    assert saved['route_mode']=='manual' and saved['auto_categories']==draft
    with pytest.raises(HTTPException) as stopped:
        registered.update_chat('alice','a',{'route_mode':'auto'},1)
    assert 'Complex tasks and Straightforward tasks' in stopped.value.detail
    assert registered.policy('alice','a')['auto_categories']==draft


@pytest.mark.asyncio
async def test_saved_single_pool_and_roles_preserved_without_inferred_categories(registered):
    prior=[{**POOL[0],'description':'User text remains verbatim.'},POOL[1]]
    with registered.transaction() as db:
        _, row=registered.state(db,'alice','a')
        row.route_mode='auto';row.feature_settings={'auto_models':prior,'auto_models_source':'explicit','completion_advice_enabled':True}
    policy=registered.policy('alice','a')
    assert policy['auto_models']==prior and policy['auto_categories']=={}
    assert 'Assign a model to Complex tasks and Straightforward tasks' in policy['restriction']
    reviewer=Reviewer()
    session=SimpleNamespace(id='a',name='a',model='manual',endpoint_id=None,thinking_mode='off')
    with pytest.raises(HTTPException) as stopped:
        await prepare_chat_submission(registered,'alice',session,ASK,decision_provider=reviewer)
    assert stopped.value.headers['X-Decision-Failure']=='auto_categories_incomplete'
    assert not reviewer.calls
    registered.update_chat('alice','a',{'auto_categories':CATEGORIES},0)
    assert registered.policy('alice','a')['auto_models']==prior
    assert registered.policy('alice','a')['completion_advice_enabled'] is True
    assert registered.policy('alice','a')['auto_categories']==CATEGORIES


@pytest.mark.asyncio
async def test_request_uses_full_policy_and_closed_categories_without_model_descriptions(registered):
    registered.update_chat('alice','a',{'auto_categories':CATEGORIES,'route_mode':'auto'},0)
    reviewer=Reviewer('straightforward_tasks')
    session=SimpleNamespace(id='a',name='a',model='manual',endpoint_id=None,thinking_mode='off')
    bound=await prepare_chat_submission(registered,'alice',session,ASK,decision_provider=reviewer)
    request=reviewer.calls[0]
    assert request.state['task']==ASK and request.state['routing_policy']==model_auto.ROUTING_POLICY
    assert set(request.choices)=={'complex_tasks','straightforward_tasks','unavailable'}
    assert request.state['categories']['complex_tasks']['endpoint_id']=='one'
    assert request.state['categories']['straightforward_tasks']['endpoint_id']=='two'
    assert not {'user_role','description','role_trust'} & set(request.state['categories']['complex_tasks'])
    assert bound.descriptor['auto_category']=='straightforward_tasks' and bound.descriptor['endpoint_id']=='two'


@pytest.mark.asyncio
async def test_policy_or_category_mutation_during_decision_invalidates_offer(monkeypatch):
    def resolve(owner,item):
        return {'identity':dict(item),'label':'Synthetic','route':('https://example.invalid',item['model'],{}),'supported_efforts':['default']}
    for change in ('policy','assignment'):
        assignments={key:dict(value) for key,value in CATEGORIES.items()}
        def mutate():
            if change=='policy':monkeypatch.setitem(model_auto.ROUTING_POLICY,'scope','Changed scope')
            else:assignments['straightforward_tasks']=dict(POOL[0])
        with pytest.raises(DecisionError,match='auto_models_changed'):
            await model_auto.select_model_and_effort(ASK,assignments,owner='alice',provider=Reviewer(mutate=mutate),
                config={},context=DecisionContext('alice','chat','run'),resolver=resolve)
        monkeypatch.undo()


def test_new_category_chats_never_materialize_unrelated_legacy_mapping_history(registered):
    names=('established_procedure','bounded_domain','approach_discovery')
    mappings={key:{**POOL[1],'reasoning_effort':None} for key in names}
    registered.update_configuration('alice',{'approaches':mappings},0)
    registered.update_chat('alice','a',{'auto_categories':CATEGORIES,'route_mode':'auto'},0)
    registered.policy('alice','a')
    with registered.transaction() as db:
        _, row=registered.state(db,'alice','a')
        assert 'auto_models' not in row.feature_settings
    registered.update_configuration('alice',{'approaches':{
        key:{**POOL[0],'reasoning_effort':None} for key in names}},1)
    with registered.transaction() as db:
        _, row=registered.state(db,'alice','a')
        assert 'auto_models' not in row.feature_settings
        assert row.feature_settings['auto_categories']==CATEGORIES


def test_explicit_assignments_clear_stale_legacy_invalid_guidance_without_deleting_history(registered):
    with registered.transaction() as db:
        _, row=registered.state(db,'alice','a')
        row.route_mode='auto'
        row.feature_settings={'auto_models':[],'auto_models_source':'legacy_invalid'}
    assert 'Assign a model' in registered.policy('alice','a')['restriction']
    saved=registered.update_chat('alice','a',{'auto_categories':CATEGORIES},0)
    assert saved['auto_models']==[] and saved['auto_models_source']=='legacy_invalid'
    assert saved['restriction'] is None
    assert registered.policy('alice','a')['restriction'] is None
