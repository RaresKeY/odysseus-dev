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
    assert reopened.policy('alice','b')['approval_mode'] == 'auto_approve'
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
    trusted_policy = json.dumps(model_auto.ROUTING_POLICY, ensure_ascii=False, separators=(',', ':'))
    for question in request.questions().values():
        assert trusted_policy in question['instructions']
        assert 'unfamiliar model names or absent explicit effort controls do not mean unavailable' in question['instructions']
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
        key:{**POOL[0],'reasoning_effort':None} for key in names}},registered.configuration('alice')['revision'])
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


def create_chat_with_model_defaults(store, sid, owner='alice'):
    with store.transaction() as db:
        db.add(d.Session(id=sid, name=sid, owner=owner, model='manual', endpoint_url='http://local/v1'))
        db.flush()
        store.state(db, owner, sid, new=True)
    return store.policy(owner, sid)


def test_last_saved_auto_setup_is_used_only_for_new_chats_of_same_owner(registered):
    original = registered.update_chat('alice', 'a', {'auto_categories': CATEGORIES,
        'route_mode': 'auto', 'approval_mode': 'yolo', 'followup_enabled': True}, 0)
    reopened = AutomationStore(registered.session_factory)
    newborn = create_chat_with_model_defaults(reopened, 'new')
    assert newborn['auto_categories'] == CATEGORIES and newborn['route_mode'] == 'auto'
    assert newborn['approval_mode'] == 'auto_approve' and newborn['elevation_grant'] is None
    assert newborn['followup_enabled'] is False
    assert reopened.policy('alice', 'b')['auto_categories'] == {}
    assert reopened.policy('alice', 'b')['route_mode'] == 'manual'
    assert create_chat_with_model_defaults(reopened, 'bob-new', 'bob')['auto_categories'] == {}
    assert reopened.policy('alice', 'a') == original
    changed = {key: CATEGORIES[other] for key, other in
        [('complex_tasks', 'straightforward_tasks'), ('straightforward_tasks', 'complex_tasks')]}
    reopened.update_chat('alice', 'a', {'auto_categories': changed}, original['policy_revision'])
    assert create_chat_with_model_defaults(reopened, 'newer')['auto_categories'] == changed
    assert reopened.policy('alice', 'new')['auto_categories'] == CATEGORIES


def test_manual_choice_retains_categories_and_becomes_new_chat_default(registered):
    saved = registered.update_chat('alice', 'a', {'auto_categories': CATEGORIES, 'route_mode': 'auto'}, 0)
    registered.update_chat('alice', 'a', {'route_mode': 'manual'}, saved['policy_revision'])
    newborn = create_chat_with_model_defaults(registered, 'new')
    assert newborn['route_mode'] == 'manual' and newborn['auto_categories'] == CATEGORIES
    registered.update_chat('alice', 'new', {'route_mode': 'auto'}, newborn['policy_revision'])
    assert create_chat_with_model_defaults(registered, 'next')['route_mode'] == 'auto'


def test_failed_save_does_not_replace_defaults_and_repeated_save_does_not_change_revision(registered):
    saved = registered.update_chat('alice', 'a', {'auto_categories': CATEGORIES, 'route_mode': 'auto'}, 0)
    preferences = registered.configuration('alice')
    registered.update_chat('alice', 'a', {'auto_categories': CATEGORIES, 'route_mode': 'auto'}, saved['policy_revision'])
    assert registered.configuration('alice') == preferences
    with pytest.raises(HTTPException):
        registered.update_chat('alice', 'a', {'auto_categories': {'complex_tasks': POOL[0]}}, saved['policy_revision'])
    with pytest.raises(HTTPException):
        registered.update_chat('alice', 'a', {'route_mode': 'manual'}, 0)
    assert registered.configuration('alice') == preferences
    assert create_chat_with_model_defaults(registered, 'new')['auto_categories'] == CATEGORIES


def test_unavailable_saved_default_remains_auto_without_choosing_another_model(registered):
    registered.update_chat('alice', 'a', {'auto_categories': CATEGORIES, 'route_mode': 'auto'}, 0)
    with registered.transaction() as db:
        db.query(d.ModelEndpoint).filter_by(id='two').one().is_enabled = False
    newborn = create_chat_with_model_defaults(registered, 'new')
    assert newborn['route_mode'] == 'auto' and newborn['auto_categories'] == CATEGORIES
    with pytest.raises(DecisionError):
        model_auto.category_snapshot('alice', newborn['auto_categories'])


@pytest.mark.asyncio
async def test_new_chat_defaults_do_not_invalidate_another_chats_admitted_turn(registered):
    registered.update_chat('alice', 'a', {'auto_categories': CATEGORIES, 'route_mode': 'auto'}, 0)
    session = SimpleNamespace(id='a', name='a', model='manual', endpoint_id=None, thinking_mode='off')
    bound = await prepare_chat_submission(registered, 'alice', session, ASK, decision_provider=Reviewer())
    registered.update_chat('alice', 'b', {'auto_categories': categories(POOL[1]), 'route_mode': 'auto'}, 0)
    assert bound.current()
    registered.update_configuration('alice', {'decision_provider': None}, registered.configuration('alice')['revision'])
    assert not bound.current()


@pytest.mark.parametrize('direct_model_pick', [False, True])
def test_manual_pick_in_older_chat_preserves_last_saved_category_pair(registered, direct_model_pick):
    registered.update_chat('alice', 'a', {'auto_categories': CATEGORIES, 'route_mode': 'auto'}, 0)
    assert registered.policy('alice', 'b')['auto_categories'] == {}
    if direct_model_pick:
        with registered.transaction() as db:
            prefs, state = registered.state(db, 'alice', 'b')
            state.route_mode = 'manual'
            registered.remember_model_defaults(prefs, state)
    else:
        registered.update_chat('alice', 'b', {'route_mode': 'manual'}, 0)
    newborn = create_chat_with_model_defaults(registered, 'new')
    assert newborn['route_mode'] == 'manual'
    assert newborn['auto_categories'] == CATEGORIES
    assert registered.policy('alice', 'b')['auto_categories'] == {}


def test_manual_pick_does_not_restore_an_older_chats_category_pair(registered):
    older = {key: CATEGORIES[other] for key, other in
        [('complex_tasks', 'straightforward_tasks'), ('straightforward_tasks', 'complex_tasks')]}
    first = registered.update_chat('alice', 'b', {'auto_categories': older}, 0)
    registered.update_chat('alice', 'a', {'auto_categories': CATEGORIES, 'route_mode': 'auto'}, 0)
    registered.update_chat('alice', 'b', {'route_mode': 'manual'}, first['policy_revision'])
    assert create_chat_with_model_defaults(registered, 'new')['auto_categories'] == CATEGORIES
    registered.update_chat('alice', 'b', {'route_mode': 'auto'}, first['policy_revision'])
    assert create_chat_with_model_defaults(registered, 'after-auto')['auto_categories'] == older


def test_explicit_category_clear_replaces_saved_pair_in_manual_mode(registered):
    saved = registered.update_chat('alice', 'a', {'auto_categories': CATEGORIES}, 0)
    registered.update_chat('alice', 'a', {'auto_categories': {}}, saved['policy_revision'])
    assert create_chat_with_model_defaults(registered, 'new')['auto_categories'] == {}
