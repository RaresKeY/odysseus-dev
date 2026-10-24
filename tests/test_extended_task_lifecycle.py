"""Canonical task snapshots and ordinary cards stay separate from authority."""
import json
import pytest
from fastapi import HTTPException
from core import database as d
from src.chat_automation import prepare_chat_turn, TurnEventObserver
from tests.test_extended_routing_contracts import store, session, Provider, schemas
from src.decision_routing import prepare_tool_routing


@pytest.mark.asyncio
async def test_snapshot_precedes_superseding_and_completed_prose_is_historical(store):
    first = await prepare_chat_turn(store, 'alice', session(), '  Original task  ')
    store.record_answer(first, 'Would you like more?')
    store.record(first, 'completed')
    second = await prepare_chat_turn(store, 'alice', session(), 'yes')
    assert second.prior_task['previous_request'] == '  Original task  '
    assert second.prior_task['previous_answer'] == 'Would you like more?'
    assert second.prior_task['previous_status'] == 'completed'
    assert second.prior_task['active_task'] is None and second.prior_task['pending_question'] is None
    with store.transaction() as db:
        assert db.query(d.ChatAutomationSubmission).filter_by(submission_id=first.run_id).one().status == 'completed'


@pytest.mark.asyncio
async def test_actual_sse_ordinary_card_snapshot_resume_and_wrong_owner(store):
    first = await prepare_chat_turn(store, 'alice', session(), 'Do the original task')
    observer = TurnEventObserver(first)
    frame = 'data: ' + json.dumps({'type': 'ask_user', 'data': {'question': 'Which folder?'}}) + '\n\n'
    observer.feed(frame[:20]); observer.feed(frame[20:])
    identifier = first.pending_input_id
    assert identifier
    second = await prepare_chat_turn(store, 'alice', session(), 'workspace')
    assert second.prior_task['previous_status'] == 'awaiting_input'
    assert second.prior_task['pending_question']['card_id'] == identifier
    assert not first.current()
    with pytest.raises(HTTPException):
        store.resolve_input('bob', 'a', identifier, 'workspace', submission_id=second.run_id)
    result = store.resolve_input('alice', 'a', identifier, 'workspace', submission_id=second.run_id)
    assert result['resolved']
    with pytest.raises(HTTPException):
        store.resolve_input('alice', 'a', identifier, 'workspace', submission_id=second.run_id)


@pytest.mark.asyncio
async def test_confirmation_is_intent_only_and_cannot_create_pending_from_prose(store):
    store.update_chat('alice', 'a', {'followup_enabled': True}, 0)
    first = await prepare_chat_turn(store, 'alice', session(), 'old')
    store.record_answer(first, 'Approve?')
    store.record(first, 'completed')
    second = await prepare_chat_turn(store, 'alice', session(), 'yes')
    await prepare_tool_routing(second, schemas(), provider=Provider({'followup': 'confirmation'}))
    assert second.followup.relation == 'unknown' and second.followup.linked_card_id is None
    assert second.followup.public()['intent_only'] and second.pending_approval_id is None


@pytest.mark.asyncio
async def test_late_event_cancelled_and_action_cards_do_not_create_ordinary_input(store):
    first = await prepare_chat_turn(store, 'alice', session(), 'old')
    assert store.record_input(first, {'kind': 'tool_approval', 'approval_id': 'opaque', 'question': 'Allow?'}) is None
    await prepare_chat_turn(store, 'alice', session(), 'new')
    assert store.record_input(first, {'question': 'Late?'}) is None
    store.record(first, 'completed')
    with store.transaction() as db:
        assert db.query(d.ChatAutomationSubmission).filter_by(submission_id=first.run_id).one().status == 'cancelled'


@pytest.mark.asyncio
async def test_clarification_expiry_and_fork_preferences_no_authority(store):
    first = await prepare_chat_turn(store, 'alice', session(), 'old')
    identifier = store.record_input(first, {'question': 'Which?'}, ttl=1)
    store.clock = lambda: 10**12
    second = await prepare_chat_turn(store, 'alice', session(), 'late')
    assert second.prior_task['previous_status'] == 'expired'
    assert second.prior_task['pending_question'] is None
    with pytest.raises(HTTPException):
        store.resolve_input('alice', 'a', identifier, 'late', submission_id=second.run_id)


def test_additive_migration_preserves_legacy_rows_without_inferred_task(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text, inspect
    engine = create_engine('sqlite:///' + str(tmp_path / 'legacy.db'))
    with engine.begin() as db:
        db.execute(text('CREATE TABLE chat_automation_state (session_id VARCHAR PRIMARY KEY)'))
        db.execute(text('CREATE TABLE chat_automation_submissions (submission_id VARCHAR PRIMARY KEY, intent_digest VARCHAR)'))
        db.execute(text("INSERT INTO chat_automation_submissions VALUES ('legacy', 'old-digest')"))
    monkeypatch.setattr(d, 'engine', engine)
    d._migrate_chat_automation_semantics()
    d._migrate_chat_automation_semantics()
    with engine.connect() as db:
        row = db.execute(text('SELECT submission_id,intent_digest,current_ask,prior_task FROM chat_automation_submissions')).one()
        assert tuple(row) == ('legacy', 'old-digest', None, None)
        assert 'feature_settings' in {column['name'] for column in inspect(db).get_columns('chat_automation_state')}
    engine.dispose()


@pytest.mark.asyncio
async def test_followup_continuation_persists_original_lineage_without_replacing_current_ask(store):
    store.update_chat('alice', 'a', {'followup_enabled': True}, 0)
    first = await prepare_chat_turn(store, 'alice', session(), '  Original exact task  ')
    store.record(first, 'completed')
    second = await prepare_chat_turn(store, 'alice', session(), '  Adapt the second part  ')
    await prepare_tool_routing(second, schemas(), provider=Provider({'followup': 'continuation'}))
    with store.transaction() as db:
        row = db.query(d.ChatAutomationSubmission).filter_by(submission_id=second.run_id).one()
        assert row.task_lineage_id == first.run_id and row.original_submission_id == first.run_id
        assert row.current_ask == '  Adapt the second part  '
        assert db.query(d.ChatAutomationSubmission).count() == 2


@pytest.mark.asyncio
async def test_real_ingress_sse_records_card_and_json_answer_without_history_authority(store, monkeypatch):
    import httpx
    from fastapi import FastAPI, Request
    from starlette.responses import StreamingResponse
    from types import SimpleNamespace
    import src.chat_automation as automation
    from src import auth_helpers
    from routes import chat_routes, chat_helpers
    from src.chat_automation import automation_ingress
    monkeypatch.setattr(automation, 'AutomationStore', lambda: store)
    monkeypatch.setattr(auth_helpers, 'require_api_token_scope', lambda *a: None)
    monkeypatch.setattr(chat_routes, '_verify_session_owner', lambda *a: None)
    monkeypatch.setattr(chat_helpers, '_enforce_chat_privileges', lambda *a: None)
    sess = session()
    sess.history = [SimpleNamespace(role='assistant', content='Would you approve?', metadata={})]
    manager = SimpleNamespace(get_session=lambda _: sess)
    app = FastAPI()
    @app.middleware('http')
    async def identify(request, next_handler):
        request.state.current_user = 'alice'
        return await next_handler(request)
    @app.post('/stream')
    @automation_ingress(manager)
    async def stream(request: Request):
        async def events():
            yield 'data: ' + json.dumps({'type': 'ask_user', 'data': {'question': 'Which folder?', 'options': [{'label': 'Workspace'}, {'label': 'Other'}]}}) + '\n\n'
        return StreamingResponse(events(), media_type='text/event-stream')
    @app.post('/json')
    @automation_ingress(manager)
    async def answer(request: Request):
        return {'response': 'Would you like more?'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        result = await client.post('/stream', json={'session': 'a', 'message': 'Do task'})
        assert result.status_code == 200
        state = store.task_state('alice', 'a')
        assert state['previous_status'] == 'awaiting_input'
        assert state['pending_question']['options'][0]['label'] == 'Workspace'
        card = state['pending_question']['card_id']
        result = await client.post('/json', json={'session': 'a', 'message': 'Workspace', 'input_card_id': card})
        assert result.status_code == 200
    state = store.task_state('alice', 'a')
    assert state['previous_status'] == 'completed' and state['pending_question'] is None
    assert state['previous_answer'] == 'Would you like more?'
    with store.transaction() as db:
        latest = db.query(d.ChatAutomationSubmission).filter_by(submission_id=state['submission_id']).one()
        assert latest.preparation['sources'][0]['trust'] == 'untrusted_history'
        assert db.query(d.ChatAutomationInputCard).one().status == 'resolved'
