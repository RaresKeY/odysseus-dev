"""Canonical owner admission, total lifecycle deadlines and known-use budgets."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import threading
import time

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.decisions import DecisionContext, DecisionError, DecisionProvider, DecisionRequest, MemoryAttemptStore
from src.decisions.config import resolve_decision_endpoint
from src.decisions.contracts import encode
from src.approach_routing import resolve_execution_route
from tests.test_decision_provider import CONFIG, CONTEXT, REQUEST, endpoint, provider, reply


@pytest.mark.asyncio
async def test_literal_independent_systemone_wire_pair():
    fixture = json.loads((Path(__file__).parent / 'fixtures/decision_port/systemone-wire-v1.json').read_text())
    request = DecisionRequest('approval', {'request': '  Keep the original ask. λ  ', 'operation': 'read a synthetic file'},
        {'allow': 'Within the supplied scope', 'review': 'Requires a human', 'deny': 'Outside the supplied scope'},
        question_id='permission', instructions='Review only this synthetic operation.')
    calls = []
    def transport(sent):
        calls.append(sent)
        assert json.loads(sent.content) == fixture['request']
        return httpx.Response(200, content=json.dumps(fixture['response']).encode())
    client, _ = provider(transport)
    result = await client.evaluate(request, CONTEXT, config=CONFIG)
    assert result.choice == 'review' and result.usage == {'input_tokens': 108, 'output_tokens': 17}
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('owner', [None, '', '   ', '__odysseus_local__', 'internal-tool', True, 42])
async def test_missing_authenticated_owner_cannot_reach_resolver_or_http(monkeypatch, owner):
    monkeypatch.setenv('AUTH_ENABLED', 'true')
    calls, resolved = [], []
    client, store = provider(lambda r: calls.append(r), resolver=lambda *_: resolved.append(True))
    with pytest.raises(DecisionError, match='owner_unavailable'):
        await client.evaluate(REQUEST, replace(CONTEXT, owner=owner), config=CONFIG)
    assert resolved == calls == [] and not store.records


@pytest.mark.parametrize('auth,owner,admitted', [
    ('true', None, False), ('true', '__odysseus_local__', False),
    ('false', None, True), ('false', '__odysseus_local__', True), ('true', 'alice', False),
])
def test_canonical_local_registration_is_visible_only_in_explicit_no_login_mode(monkeypatch, auth, owner, admitted):
    import core.database as database
    engine = create_engine('sqlite://')
    database.ModelEndpoint.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(database.ModelEndpoint(id='local', owner='__odysseus_local__', name='local',
            base_url='http://127.0.0.1:8000/v1', cached_models='["decision-model"]'))
        db.commit()
    monkeypatch.setattr(database, 'SessionLocal', factory)
    monkeypatch.setenv('AUTH_ENABLED', auth)
    if admitted:
        assert resolve_decision_endpoint({**CONFIG, 'endpoint_id': 'local'}, owner).endpoint_id == 'local'
    else:
        with pytest.raises(DecisionError):
            resolve_decision_endpoint({**CONFIG, 'endpoint_id': 'local'}, owner)
    engine.dispose()


def test_execution_route_missing_owner_does_not_call_registered_resolver(monkeypatch):
    monkeypatch.setenv('AUTH_ENABLED', 'true')
    calls=[]
    with pytest.raises(DecisionError, match='owner_unavailable'):
        resolve_execution_route({'endpoint_id':'local','model':'m','reasoning_effort':None}, None,
            resolver=lambda *a, **k: calls.append(k))
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['async_resolver', 'async_cancel', 'sync_resolver', 'sync_cancel'])
async def test_whole_pre_dispatch_lifecycle_is_bounded(stage):
    calls, release = [], threading.Event()
    async def wait_async(*args):
        await asyncio.Event().wait()
    def wait_sync(*args):
        release.wait(2)
        return False if stage == 'sync_cancel' else endpoint()
    resolver = wait_async if stage == 'async_resolver' else wait_sync if stage == 'sync_resolver' else lambda *_: endpoint()
    cancelled = wait_async if stage == 'async_cancel' else wait_sync if stage == 'sync_cancel' else None
    client, store = provider(lambda r: calls.append(r), resolver=resolver)
    context=replace(CONTEXT, deadline=time.monotonic()+.04, cancelled=cancelled)
    start=time.monotonic()
    try:
        with pytest.raises(DecisionError, match='decision_deadline_exceeded') as error:
            await client.evaluate(REQUEST, context, config=CONFIG)
        assert time.monotonic()-start < .5
        assert error.value.attempt_id is None and not calls and not store.records
    finally:
        release.set()
    await asyncio.sleep(.01)
    assert calls == []


@pytest.mark.asyncio
async def test_expiry_after_reservation_before_http_is_not_ambiguous_spend():
    checks = 0
    async def cancelled():
        nonlocal checks
        checks += 1
        if checks == 3:
            await asyncio.Event().wait()
        return False
    calls=[]
    client, store=provider(lambda r:calls.append(r))
    with pytest.raises(DecisionError, match='decision_deadline_exceeded'):
        await client.evaluate(REQUEST, replace(CONTEXT, cancelled=cancelled, deadline=time.monotonic()+.04), config=CONFIG)
    record=next(iter(store.records.values()))
    assert record['status']=='cancelled' and record['usage'] is None and not calls
    assert record['failure_kind']=='decision_deadline_exceeded'


@pytest.mark.asyncio
async def test_failed_async_admission_is_sanitized_and_makes_no_reservation():
    async def resolver(*args):
        raise OSError('PRIVATE database path')
    calls=[]
    client,store=provider(lambda request:calls.append(request),resolver=resolver)
    with pytest.raises(DecisionError,match='decision_admission_failed') as error:
        await client.evaluate(REQUEST,CONTEXT,config=CONFIG)
    assert not calls and not store.records and 'PRIVATE' not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('malformed', [False, True])
@pytest.mark.parametrize('store_kind', ['memory', 'sql'])
@pytest.mark.parametrize('protocol', ['systemone', 'openai_json'])
async def test_known_over_budget_usage_blocks_another_dispatch_without_becoming_unknown(tmp_path, monkeypatch, malformed, store_kind, protocol):
    if store_kind=='sql':
        from core import database
        from src.chat_automation import AutomationStore, SqlAttemptStore
        engine=create_engine('sqlite:///'+str(tmp_path/'attempts.db'))
        database.Base.metadata.create_all(engine)
        store=SqlAttemptStore(AutomationStore(sessionmaker(bind=engine)))
    else:
        store=MemoryAttemptStore()
    payload=reply()
    payload['usage']={'input_tokens':65537,'output_tokens':10}
    if malformed: payload['answers']['decision']['choice']='invented'
    known_usage=dict(payload['usage'])
    if protocol=='openai_json':
        payload={'model':'decision-model','choices':[{'finish_reason':'stop','message':{
            'content':'not JSON' if malformed else json.dumps({k:v for k,v in payload.items() if k!='usage'})}}],
            'usage':{'prompt_tokens':65537,'completion_tokens':10}}
    calls=[]
    client,_=provider(lambda r:(calls.append(r),httpx.Response(200,json=payload))[1],store=store,protocol=protocol)
    config={**CONFIG,'protocol':protocol}
    with pytest.raises(DecisionError,match='decision_usage_exceeded_reservation'):
        await client.evaluate(REQUEST,CONTEXT,config=config)
    records=store.records_for_run('alice','chat','run')
    assert records[0]['usage']==known_usage and records[0]['status']=='rejected'
    assert records[0]['failure_kind']=='decision_usage_exceeded_reservation'
    with pytest.raises(DecisionError,match='decision_budget_unavailable'):
        await client.evaluate(REQUEST,CONTEXT,config=config)
    assert len(calls)==1
    if store_kind=='sql':engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('state', [{1:'coerced key'}, {'tuple':(1,2)}, {'nonfinite':float('nan')}])
async def test_donor_strict_json_mechanics_reject_coercion_before_spending(state):
    calls=[]
    client,store=provider(lambda r:calls.append(r))
    with pytest.raises(DecisionError,match='decision_invalid_request'):
        await client.evaluate(replace(REQUEST,state=state),CONTEXT,config=CONFIG)
    assert not calls and not store.records
