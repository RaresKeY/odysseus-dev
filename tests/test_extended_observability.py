"""Real SQL observation/capture boundaries; no provider or executable replay."""
import copy
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import database as d
from src.chat_automation import AutomationStore, SqlAttemptStore
from src.decisions.contracts import DecisionContext, DecisionError, DecisionRequest, DecisionResult, encode
from src.decisions.observability import (CAPTURE_SCHEMA, DecisionObservability, observation_models,
    rename_observability_owner, retire_observability_owner, validate_capture)


@pytest.fixture
def observations(tmp_path):
    observation_models()
    engine = create_engine('sqlite:///' + str(tmp_path / 'observations.db'))
    d.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        for sid, owner in [('a', 'alice'), ('b', 'alice'), ('c', 'bob')]:
            db.add(d.Session(id=sid, name=sid, owner=owner, model='manual', endpoint_url='http://local'))
        db.commit()
    clock = [1000.0]
    observer = DecisionObservability(factory, clock=lambda: clock[0])
    observer.test_clock = clock
    yield observer
    engine.dispose()


def test_default_observer_is_shared_per_factory_and_never_reuses_another_database(observations, tmp_path):
    from src.decisions.observability import default_observability
    first=default_observability(observations.session_factory)
    assert default_observability(observations.session_factory) is first
    other_engine=create_engine('sqlite:///'+str(tmp_path/'another.db'))
    d.Base.metadata.create_all(other_engine)
    second_factory=sessionmaker(bind=other_engine)
    second=default_observability(second_factory)
    assert second is not first and second.session_factory is second_factory
    assert first.session_factory is observations.session_factory
    other_engine.dispose()


def test_model_auto_capture_keeps_known_questions_but_omits_roles_models_and_labels(observations):
    observations.consent('alice','a',{'purposes':['model_auto'],'required':True})
    observations.bind_turn('alice','a','auto-run')
    request=DecisionRequest('model_auto',{'ask':'PRIVATE_ASK','candidates':{
        'candidate_0':{'model':'PRIVATE_EXECUTION_MODEL','user_role':'PRIVATE_ROLE_EVIDENCE'}}},
        {'candidate_0':'PRIVATE_ROLE_EVIDENCE','unavailable':'Unknown'},question_id='execution_model',
        additional_questions={'reasoning_effort':{'low':'Low','high':'High'}})
    context=DecisionContext('alice','a','auto-run')
    record={'attempt_id':'model-auto-one','owner':'alice','session_id':'a','run_id':'auto-run',
        'purpose':'model_auto','request_sha256':'a'*64,'request_bytes':123,
        'provider_identity':{'model':'PRIVATE_DECIDER','endpoint_id':'PRIVATE_ENDPOINT','protocol':'systemone'},
        'policy_revision':0,'authority_epoch':0,'status':'reserved','usage':None}
    ledger=SqlAttemptStore(AutomationStore(observations.session_factory));ledger.reserve(record,max_calls=32)
    ticket=observations.before_dispatch(request,context,record)
    answers={name:{'choice':next(iter(question['criteria'])),'confidence':.9,
        'probabilities':{key:(.9 if i==0 else .1) for i,key in enumerate(question['criteria'])}}
        for name,question in request.questions().items()}
    main=answers['execution_model'];usage={'input_tokens':12,'output_tokens':3}
    result=DecisionResult(main['choice'],main['confidence'],main['probabilities'],usage,
        'model-auto-one',record['provider_identity'],answers)
    ledger.settle('model-auto-one',status='settled',usage=usage)
    observations.after_attempt(ticket,status='settled',usage=usage,result=result)
    history=observations.history('alice','a')['items'][0]
    assert history['purpose']=='model_auto' and set(history['questions'])=={'execution_model','reasoning_effort'}
    capture=observations.capture('alice','a','auto-run');encoded=encode(capture)
    assert len(capture['records'])==1 and b'PRIVATE_' not in encoded
    assert b'PRIVATE_' not in encode(history)


def test_private_turn_consumes_consent_without_creating_capture(observations):
    observations.consent('alice','a',{'purposes':['approach']})
    assert observations.bind_turn('alice','a','private-run',ephemeral=True) is None
    with observations.session_factory() as db:
        assert db.query(observations.models.consent).count()==0
        assert db.query(observations.models.capture).count()==0


@pytest.mark.asyncio
async def test_actual_default_provider_observer_uses_same_sql_factory_as_admission(observations):
    import httpx
    from src.chat_automation import prepare_chat_turn
    from src.decisions import DecisionProvider, ResolvedDecisionEndpoint
    from src.decisions.observability import default_observability
    store=AutomationStore(observations.session_factory)
    observer=default_observability(observations.session_factory)
    observer.consent('alice','a',{'purposes':['tools'],'required':True})
    session=SimpleNamespace(id='a',endpoint_id=None,model='manual',endpoint_url='https://execution.invalid/v1',headers={})
    bound=await prepare_chat_turn(store,'alice',session,'PRIVATE_EXACT_ASK')
    request=DecisionRequest('tools',{'ask':bound.raw_ask},{'PRIVATE_CHOICE':'private description','other':'Other'})
    def transport(req):
        assert observer.history('alice','a')['items'][0]['status']=='reserved'
        return httpx.Response(200,json={'model':'decision','answers':{'decision':{
            'type':'choice','choice':'PRIVATE_CHOICE','confidence':.99,
            'probabilities':{'PRIVATE_CHOICE':.99,'other':.01}}},'usage':{'input_tokens':12,'output_tokens':3}})
    provider=DecisionProvider(SqlAttemptStore(store),resolver=lambda *_:ResolvedDecisionEndpoint(
        'review','decision','systemone','https://synthetic.invalid/v1/systemone',{}),
        client_factory=lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(transport),**kw))
    await provider.evaluate(request,DecisionContext('alice','a',bound.run_id),
        config={'endpoint_id':'review','model':'decision','protocol':'systemone'})
    history=observer.history('alice','a')['items']
    assert len(history)==1 and history[0]['status']=='settled'
    assert history[0]['usage']=={'input_tokens':12,'output_tokens':3}
    capture=observer.capture('alice','a',bound.run_id)
    assert len(capture['records'])==1 and b'PRIVATE_EXACT_ASK' not in encode(capture) and b'PRIVATE_CHOICE' not in encode(capture)


def attempt(observer, *, number='one', run='run', failure=None, usage=None, settle=True):
    sentinel = 'PRIVATE_S3CR3T_ASK_DESTINATION'
    request = DecisionRequest('browser', {'ask': sentinel, sentinel: {'prepared_values': [sentinel],
        'url': 'https://user:PRIVATE_S3CR3T_ASK_DESTINATION@example.invalid/?token=' + sentinel}},
        {sentinel: sentinel, 'other-' + sentinel: sentinel}, question_id=sentinel, instructions=sentinel)
    context = DecisionContext('alice', 'a', run)
    record = {'attempt_id': number, 'owner': context.owner, 'session_id': 'a', 'run_id': run,
        'purpose': 'browser', 'request_sha256': hashlib.sha256(sentinel.encode()).hexdigest(),
        'request_bytes': 123, 'provider_identity': {'model': sentinel, 'endpoint_id': sentinel,
            'protocol': 'systemone', 'distribution_source': 'provider_reported', 'headers': {'Authorization': sentinel}},
        'policy_revision': 1, 'authority_epoch': 0, 'status': 'reserved', 'usage': None}
    ledger = SqlAttemptStore(AutomationStore(observer.session_factory))
    ledger.reserve(record, max_calls=32)
    ticket = observer.before_dispatch(request, context, record)
    result = DecisionResult(sentinel, .9, {sentinel: .9, 'other-' + sentinel: .1}, usage, number,
        record['provider_identity'], {sentinel: {'choice': sentinel, 'confidence': .9,
            'probabilities': {sentinel: .9, 'other-' + sentinel: .1}}})
    if settle:
        ledger.settle(number, status='unresolved' if failure else 'settled', usage=usage, failure_kind=failure)
        observer.test_clock[0] += .012
        observer.after_attempt(ticket, status='unresolved' if failure else 'settled', usage=usage,
            result=None if failure else result, failure_kind=failure)
    return ledger, ticket, result


def test_no_capture_without_explicit_bound_consent_and_no_private_hash_export(observations):
    ledger, _, _ = attempt(observations)
    public = observations.history('alice', 'a')['items'][0]
    assert public['questions'] == {'q001': {'choice': 'c001', 'confidence': .9,
        'probabilities': {'c001': .9, 'c002': .1}}}
    assert public['usage'] is None and public['usage_provenance'] == 'unknown_held'
    assert public['duration_ms'] == 12 and public['capture_state'] == 'not_requested'
    data = encode(public)
    assert b'PRIVATE_S3CR3T' not in data
    assert ledger.records_for_run('alice', 'a', 'run')[0]['request_sha256'].encode() not in data
    with pytest.raises(HTTPException) as absent:
        observations.capture('alice', 'a', 'run')
    assert absent.value.status_code == 404


def test_capture_consent_single_turn_redaction_provenance_and_reload(observations):
    observations.consent('alice', 'a', {'purposes': ['browser']})
    policy = observations.bind_turn('alice', 'a', 'run')
    assert observations.bind_turn('alice', 'a', 'run') == policy
    assert observations.bind_turn('alice', 'a', 'next') is None
    ledger, _, _ = attempt(observations, usage={'input_tokens': 12, 'output_tokens': 3})
    reopened = DecisionObservability(observations.session_factory, clock=observations.clock)
    capture = reopened.capture('alice', 'a', 'run')
    assert validate_capture(encode(capture)) == {'valid': True, 'records': 1,
        'source_kind': 'captured_live_redacted', 'exact_original_replay': False, 'scored': False,
        'provider_calls': 0, 'action_calls': 0}
    packet = capture['records'][0]['packet']
    assert packet['fixture']['state'] == {'content_omitted': True}
    assert packet['expected'] is None and packet['ordinal'] == 1
    assert b'PRIVATE_S3CR3T' not in encode(capture)
    assert ledger.records_for_run('alice', 'a', 'run')[0]['request_sha256'].encode() not in encode(capture)
    assert packet['observation']['usage_provenance'] == 'provider_reported'


def test_consent_does_not_capture_different_run_purpose_or_fork(observations):
    observations.consent('alice', 'a', {'purposes': ['approach']})
    observations.bind_turn('alice', 'a', 'run')
    attempt(observations)
    assert observations.capture('alice', 'a', 'run')['records'] == []
    assert observations.bind_turn('alice', 'b', 'fork') is None
    attempt(observations, number='next-attempt', run='different')
    with pytest.raises(HTTPException):
        observations.capture('alice', 'a', 'different')


@pytest.mark.parametrize('method', ['history', 'capture', 'delete_capture', 'consent', 'bind_turn', 'handoffs'])
def test_foreign_owner_cannot_enumerate_read_write_or_delete(observations, method):
    args = {'history': (), 'capture': ('run',), 'delete_capture': (),
        'consent': ({'purposes': ['browser']},), 'bind_turn': ('run',), 'handoffs': ()}[method]
    with pytest.raises(HTTPException) as denied:
        getattr(observations, method)('bob', 'a', *args)
    assert denied.value.status_code == 404


def test_stable_history_pagination_and_unknown_fixed_failure(observations):
    for number in ['first', 'second', 'third']:
        attempt(observations, number=number, run=number, failure='decision_transport_failed')
    page = observations.history('alice', 'a', limit=2)
    attempt(observations, number='later', run='later')
    older = observations.history('alice', 'a', before=page['next_before'], limit=2)
    assert [item['attempt_id'] for item in page['items'] + older['items']] == ['third', 'second', 'first']
    assert page['items'][0]['failure_kind'] == 'decision_transport_failed'
    assert page['items'][0]['usage'] is None and page['items'][0]['status'] == 'unresolved'
    with pytest.raises(HTTPException):
        observations.history('alice', 'a', before='garbage!')


def test_capacity_required_prevents_spend_and_optional_keeps_metadata(observations):
    observations.consent('alice', 'a', {'purposes': ['browser'], 'required': True, 'max_bytes': 1})
    observations.bind_turn('alice', 'a', 'run')
    with pytest.raises(DecisionError, match='decision_capture_capacity_exceeded'):
        attempt(observations)
    # No provider callback exists in this service; the canonical provider owns
    # settlement of this reserved, pre-dispatch attempt as cancelled.
    observations.delete_capture('alice', 'a')
    observations.consent('alice', 'a', {'purposes': ['browser'], 'max_bytes': 1})
    observations.bind_turn('alice', 'a', 'other')
    attempt(observations, number='two', run='other')
    assert observations.history('alice', 'a')['items'][0]['capture_state'] == 'capacity_exceeded'


def test_expiry_actually_deletes_persisted_payload_and_pending_consent(observations):
    observations.consent('alice', 'a', {'purposes': ['browser'], 'retention_seconds': 5})
    observations.bind_turn('alice', 'a', 'run')
    attempt(observations)
    observations.consent('alice', 'b', {'purposes': ['browser'], 'expires_in_seconds': 5})
    observations.test_clock[0] += 6
    observations.purge_expired()
    with observations.session_factory() as db:
        assert db.query(observations.models.capture).count() == 0
        assert db.query(observations.models.consent).count() == 0
    assert observations.history('alice', 'a')['items']


def test_delete_during_inflight_never_recreates_capture(observations):
    observations.consent('alice', 'a', {'purposes': ['browser']})
    observations.bind_turn('alice', 'a', 'run')
    ledger, ticket, result = attempt(observations, settle=False)
    observations.delete_capture('alice', 'a', 'run')
    ledger.settle('one', status='settled', usage=None)
    observations.after_attempt(ticket, status='settled', result=result)
    assert observations.history('alice', 'a')['items'][0]['capture_state'] == 'expired_or_deleted'
    with pytest.raises(HTTPException):
        observations.capture('alice', 'a', 'run')


def test_capture_invalid_duplicate_corrupted_version_order_and_prediction(observations):
    observations.consent('alice', 'a', {'purposes': ['browser']})
    observations.bind_turn('alice', 'a', 'run')
    attempt(observations)
    original = observations.capture('alice', 'a', 'run')
    duplicate = copy.deepcopy(original)
    duplicate['records'] *= 2
    with pytest.raises(ValueError):
        validate_capture(encode(duplicate))
    for field, value in [('schema', 'old/v1'), ('ordinal', 99), ('exact_original_replay', True)]:
        broken = copy.deepcopy(original)
        broken['records'][0]['packet'][field] = value
        broken['records'][0]['sanitized_sha256'] = hashlib.sha256(encode(broken['records'][0]['packet'])).hexdigest()
        with pytest.raises(ValueError):
            validate_capture(encode(broken))
    broken = copy.deepcopy(original)
    broken['records'][0]['sanitized_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        validate_capture(encode(broken))
    with pytest.raises(DecisionError):
        validate_capture(b'{"schema":"a","schema":"b"}')


def test_offline_cli_never_imports_database_or_invokes_provider(observations, tmp_path, monkeypatch, capsys):
    observations.consent('alice', 'a', {'purposes': ['browser']})
    observations.bind_turn('alice', 'a', 'run')
    ledger, _, _ = attempt(observations)
    before = ledger.records_for_run('alice', 'a', 'run')
    path = tmp_path / 'capture.json'
    path.write_bytes(encode(observations.capture('alice', 'a', 'run')))
    from src.decisions.provider import DecisionProvider
    monkeypatch.setattr(DecisionProvider, 'evaluate', lambda *_args, **_kwargs: pytest.fail('Provider touched'))
    monkeypatch.setattr(d, 'SessionLocal', lambda: pytest.fail('Database touched'))
    script = Path(__file__).resolve().parents[1] / 'scripts/replay_decision_capture.py'
    spec = importlib.util.spec_from_file_location('offline_capture', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main([str(path)]) == 0
    assert '"provider_calls": 0' in capsys.readouterr().out
    assert ledger.records_for_run('alice', 'a', 'run') == before


def test_handoff_requires_actual_owned_run_and_immutable_safe_receipt(observations):
    with pytest.raises(ValueError):
        observations.record_handoff('alice', 'a', 'fictional', action_ids=['made-up'])
    attempt(observations)
    receipt = observations.record_handoff('alice', 'a', 'run', attempt_ids=['one'],
        action_ids=['canonical-action'], check_ids=['canonical-check'], stop_reason='handoff')
    assert receipt['completion_verified'] is False and receipt['executable'] is False
    receipt['stop_reason'] = 'tampered'
    assert observations.handoffs('alice', 'a')[0]['stop_reason'] == 'handoff'
    assert 'grant' not in encode(observations.handoffs('alice', 'a')).decode()
    with pytest.raises(ValueError):
        observations.record_handoff('alice', 'a', 'run', attempt_ids=['foreign'])


def test_canonical_rename_preserves_metadata_but_retires_capture_grants(observations):
    observations.consent('alice', 'a', {'purposes': ['browser']})
    observations.bind_turn('alice', 'a', 'run')
    attempt(observations)
    with observations.session_factory() as db:
        rename_observability_owner(db, 'alice', 'renamed')
        db.query(d.Session).filter_by(owner='alice').update({'owner': 'renamed'})
        db.commit()
    assert observations.history('renamed', 'a')['items']
    with pytest.raises(HTTPException):
        observations.capture('renamed', 'a', 'run')
    with observations.session_factory() as db:
        retire_observability_owner(db, 'renamed')
        db.commit()
        assert db.query(observations.models.activity).count() == 0


def test_bounded_history_survives_restart(observations, monkeypatch):
    import src.decisions.observability as module
    monkeypatch.setattr(module, 'MAX_HISTORY', 2)
    for number in ['one', 'two', 'three']:
        attempt(observations, number=number, run=number)
    reopened = DecisionObservability(observations.session_factory, clock=observations.clock)
    assert [item['attempt_id'] for item in reopened.history('alice', 'a')['items']] == ['three', 'two']


def test_required_capture_outage_before_dispatch_fails_closed_optional_metadata_does_not(observations, monkeypatch):
    observations.consent('alice', 'a', {'purposes': ['browser'], 'required': True})
    observations.bind_turn('alice', 'a', 'required')
    _, ticket, _ = attempt(observations, number='seed', run='other', settle=False)
    request = DecisionRequest('browser', {}, {'a': 'a', 'b': 'b'})
    record = {'attempt_id': 'outage', 'run_id': 'required'}
    def unavailable(**_kwargs):
        raise RuntimeError('PRIVATE_DATABASE_ERROR')
    monkeypatch.setattr(observations, '_transaction', unavailable)
    with pytest.raises(DecisionError, match='decision_capture_store_unavailable'):
        observations.before_dispatch(request, DecisionContext('alice', 'a', 'required'), record)
    assert observations.before_dispatch(request, DecisionContext('alice', 'a', 'optional'),
        {**record, 'run_id': 'optional'}) is None
    # A failure after dispatch is always swallowed, even for required capture.
    observations.after_attempt(ticket, status='unresolved', failure_kind='decision_transport_failed')


def test_metadata_never_exports_arbitrary_exception_text(observations):
    _, ticket, _ = attempt(observations, settle=False)
    observations.after_attempt(ticket, status='unresolved', failure_kind='decision_private_secret_sentinel')
    public = observations.history('alice', 'a')['items'][0]
    assert public['failure_kind'] == 'decision_failed'
    assert b'private_secret_sentinel' not in encode(public)


def test_session_delete_cascades_metadata_capture_and_consents(observations):
    observations.consent('alice', 'a', {'purposes': ['browser']})
    observations.bind_turn('alice', 'a', 'run')
    attempt(observations)
    observations.consent('alice', 'a', {'purposes': ['browser']})
    with observations.session_factory() as db:
        db.query(d.Session).filter_by(id='a').delete()
        db.commit()
        assert db.query(observations.models.activity).count() == 0
        assert db.query(observations.models.capture).count() == 0
        assert db.query(observations.models.consent).count() == 0


def test_actual_auth_deletion_retires_observations_atomically(observations, monkeypatch, tmp_path):
    import core.auth as auth
    monkeypatch.setattr(auth, '_hash_password', lambda value: 'hash:' + value)
    monkeypatch.setattr(d, 'SessionLocal', observations.session_factory)
    manager = auth.AuthManager(str(tmp_path / 'auth.json'))
    assert manager.create_user('admin', 'password-value', is_admin=True)
    assert manager.create_user('alice', 'password-value')
    observations.consent('alice', 'a', {'purposes': ['browser']})
    observations.bind_turn('alice', 'a', 'run')
    attempt(observations)
    assert manager.delete_user('alice', 'admin')
    with observations.session_factory() as db:
        assert db.query(observations.models.activity).count() == 0
        assert db.query(observations.models.capture).count() == 0


def test_actual_session_route_deletes_observations_without_foreign_keys(observations, monkeypatch):
    import routes.session_routes as routes
    monkeypatch.setattr(routes, 'SessionLocal', observations.session_factory)
    observations.consent('alice', 'a', {'purposes': ['browser']})
    observations.bind_turn('alice', 'a', 'run')
    attempt(observations)
    def delete_session(sid):
        with observations.session_factory() as db:
            db.connection().exec_driver_sql('PRAGMA foreign_keys=OFF')
            db.query(d.Session).filter_by(id=sid).delete()
            db.commit()
            return True
    manager = SimpleNamespace(delete_session=delete_session)
    router = routes.setup_session_routes(manager, {})
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith('/session/{sid}') and 'DELETE' in route.methods)
    request = SimpleNamespace(state=SimpleNamespace(current_user='alice', api_token=False), headers={})
    assert endpoint(request, 'a') == {'status': 'deleted'}
    with observations.session_factory() as db:
        assert db.query(observations.models.activity).count() == 0
        assert db.query(observations.models.capture).count() == 0
