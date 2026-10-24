"""Fresh admin restrictions cover decision spend and live execution authority."""
import json
from types import SimpleNamespace
import httpx
import pytest
from core import database as d
from core.auth import AuthManager
from src.chat_automation import prepare_chat_turn, SqlAttemptStore, rename_automation_owner, retire_automation_owner
from src.decisions import DecisionProvider, DecisionContext, DecisionRequest, DecisionError
from src.decisions.config import resolve_decision_endpoint
from tests.test_chat_automation_state import store


@pytest.fixture
def admin(store, tmp_path, monkeypatch):
    from src import model_admission
    manager = AuthManager(str(tmp_path / 'auth.json'))
    manager._config = {'users': {'admin': {'is_admin':True}, 'alice': {'is_admin':False,
        'privileges': {'allowed_models':['cheap'], 'allowed_models_restricted':True}}}}
    monkeypatch.setattr(model_admission, '_AUTH_MANAGER', manager)
    monkeypatch.setattr(d, 'SessionLocal', store.session_factory)
    import src.endpoint_resolver as resolver
    monkeypatch.setattr(resolver, 'SessionLocal', store.session_factory)
    with store.transaction() as db:
        db.add(d.ModelEndpoint(id='registered',name='shared',base_url='https://synthetic.invalid/v1',
            is_enabled=True,cached_models=json.dumps(['cheap','premium']), owner=None))
    return manager


@pytest.mark.asyncio
async def test_forbidden_decision_model_never_spends(store, admin):
    calls=[]
    provider=DecisionProvider(SqlAttemptStore(store),client_factory=lambda **kw:httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request:calls.append(request)),**kw))
    with pytest.raises(DecisionError,match='decision_model_not_allowed'):
        await provider.evaluate(DecisionRequest('approval',{}, {'allow':'allow','deny':'deny'}),
            DecisionContext('alice','a','run'), config={'endpoint_id':'registered','model':'premium','protocol':'openai_json'})
    assert calls==[]


@pytest.mark.asyncio
async def test_all_three_approach_models_admitted_before_classifier(store, admin, monkeypatch):
    mappings={key:{'endpoint_id':'registered','model':'cheap','reasoning_effort':None}
        for key in ('established_procedure','bounded_domain','approach_discovery')}
    mappings['approach_discovery']['model']='premium'
    store.update_configuration('alice', {'approaches':mappings,'decision_provider':{
        'endpoint_id':'registered','model':'cheap','protocol':'openai_json'}},0)
    store.update_chat('alice','a',{'route_mode':'auto'},0)
    calls=[]
    from src import decisions
    async def never(*args,**kwargs):calls.append(True);raise AssertionError('classifier ran before all mappings admitted')
    monkeypatch.setattr(decisions.DecisionProvider,'evaluate',never)
    session=SimpleNamespace(id='a',model='cheap',endpoint_id='registered',endpoint_url='unused',headers={})
    with pytest.raises(DecisionError,match='execution_model_not_allowed'):
        await prepare_chat_turn(store,'alice',session,'Choose an approach')
    assert calls==[]


@pytest.mark.asyncio
async def test_privilege_revocation_invalidates_bound_turn_and_does_not_revive(store, admin):
    session=SimpleNamespace(id='a',model='cheap',endpoint_id=None,endpoint_url='https://synthetic.invalid/v1',headers={})
    bound=await prepare_chat_turn(store,'alice',session,'Use the workspace')
    assert bound.current()
    admin.set_privileges('alice', {'can_use_bash':True})
    assert not bound.current()
    # Epoch/settings restore cannot resurrect this admission fingerprint while
    # the account remains revoked/deleted.
    admin._config['users'].pop('alice')
    assert not bound.current()


@pytest.mark.asyncio
async def test_owner_rename_keeps_preferences_and_retires_live_permissions(store):
    store.update_chat('alice','a',{'approval_mode':'auto_approve'},0)
    bound=await prepare_chat_turn(store,'alice',SimpleNamespace(id='a',model='manual',endpoint_id=None,
        endpoint_url='https://synthetic.invalid',headers={}), 'Original ask')
    with store.transaction() as db:
        db.query(d.Session).filter_by(owner='alice').update({'owner':'renamed'},synchronize_session=False)
        rename_automation_owner(db,'alice','renamed')
    assert not bound.current()
    assert store.policy('renamed','a')['approval_mode']=='auto_approve'
    assert store.policy('renamed','a')['elevation_grant'] is None
    assert store.configuration('alice')['last_explicit_mode']=='always_ask'
    with store.transaction() as db:retire_automation_owner(db,'renamed')
    assert store.policy('renamed','a')['approval_mode']=='always_ask'
    assert store.configuration('renamed')['elevation_ceiling'] is False
