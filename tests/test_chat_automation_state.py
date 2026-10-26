"""Durable owner, CAS, revocation and exact attempt boundaries."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from core import database as d
from src.chat_automation import AutomationStore, SqlAttemptStore, prepare_chat_turn, owner_key


@pytest.fixture
def store(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'state.db'), connect_args={"timeout": 10})
    d.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    store = AutomationStore(factory)
    with factory() as db:
        for sid, owner in [('a', 'alice'), ('b', 'alice'), ('c', 'bob')]:
            db.add(d.Session(id=sid, name=sid, owner=owner, model='manual', endpoint_url='http://local/v1'))
        db.commit()
    yield store
    engine.dispose()


@pytest.mark.parametrize('saved_mode', ['always_ask', 'auto_approve', 'yolo'])
def test_new_chat_always_asks_without_resetting_saved_modes_or_forks(store, saved_mode):
    assert store.policy('alice', 'a')['approval_mode'] == 'always_ask'
    saved = store.update_chat('alice', 'a', {'approval_mode': saved_mode, 'followup_enabled': True}, 0)
    preferences = store.configuration('alice')
    assert preferences['last_explicit_mode'] == saved_mode
    assert store.policy('alice', 'b')['approval_mode'] == 'always_ask'
    with store.transaction() as db:
        db.add(d.Session(id='new', name='new', owner='alice', model='m', endpoint_url='http://local'))
        db.flush()
        store.state(db, 'alice', 'new', new=True)
    reopened = AutomationStore(store.session_factory)
    assert reopened.policy('alice', 'new')['approval_mode'] == 'always_ask'
    assert reopened.policy('alice', 'a') == saved
    # The creation flag must also leave an already-saved chat untouched.
    with reopened.transaction() as db:
        _, existing = reopened.state(db, 'alice', 'a', new=True)
        assert existing.approval_mode == saved_mode
    assert reopened.configuration('alice') == preferences
    store.fork('alice', 'a', 'b')
    forked = reopened.policy('alice', 'b')
    assert forked['approval_mode'] == saved_mode and forked['followup_enabled'] is True
    assert forked['elevation_grant'] is None
    assert reopened.configuration('alice') == preferences
    with pytest.raises(HTTPException) as denied:
        store.policy('bob', 'a')
    assert denied.value.status_code == 404
    assert store.configuration('bob')['last_explicit_mode'] == 'always_ask'


def test_simultaneous_cas_one_winner(store):
    store.policy('alice', 'a')
    def change(mode):
        try:
            store.update_chat('alice', 'a', {'approval_mode': mode}, 0)
            return True
        except HTTPException as e:
            assert e.status_code == 409
            return False
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(change, ['auto_approve', 'yolo'])) == [False, True]


@pytest.mark.asyncio
async def test_exact_ask_binding_and_late_finally_cannot_revive(store):
    raw = '  First ask\n\tλ  '
    session = SimpleNamespace(id='a', model='manual', endpoint_url='http://local/v1', headers={}, endpoint_id=None)
    first = await prepare_chat_turn(store, 'alice', session, raw)
    assert first.raw_ask == raw and first.current()
    second = await prepare_chat_turn(store, 'alice', session, 'next')
    assert not first.current() and second.current()
    store.record(first, 'running')
    assert not first.current()
    with pytest.raises(HTTPException):
        await prepare_chat_turn(store, 'alice', session, 'next', submission_id=second.submission_id)
    store.update_configuration('alice', {'elevation_ceiling': True}, store.configuration('alice')['revision'])
    assert not second.current()
    store.update_configuration('alice', {'elevation_ceiling': False}, store.configuration('alice')['revision'])
    assert not second.current()


def test_unresolved_attempt_is_durable_and_not_retried(store):
    from src.decisions.contracts import DecisionError
    attempts = SqlAttemptStore(store)
    rec = {'owner': 'alice', 'session_id': 'a', 'run_id': 'run', 'attempt_id': 'one'}
    attempts.reserve(rec, max_calls=3)
    attempts.settle('one', status='unresolved', usage=None, failure_kind='timeout')
    reopened = SqlAttemptStore(AutomationStore(store.session_factory))
    with pytest.raises(DecisionError):
        reopened.reserve({**rec, 'attempt_id': 'two'}, max_calls=3)
    assert reopened.records_for_run('alice', 'a', 'run')[0]['usage'] is None
    assert reopened.records_for_run('bob', 'a', 'run') == []


def test_auth_disabled_owner_namespace():
    from src.owner_identity import DEFAULT_LOCAL_OWNER
    assert owner_key(None) == owner_key(DEFAULT_LOCAL_OWNER)
