"""Exact retained provenance through the SQL -> review -> dispatch seam."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from tests.test_chat_automation_state import store
from tests.test_chat_approval_sql_integration import dispatch_runtime
from src.approval_context import user_snapshot, MAX_USER_BYTES
from src.chat_automation import AutomationStore, prepare_chat_turn
from src.decisions import DecisionError


@pytest.mark.asyncio
async def test_long_chain_is_lossless_bounded_and_one_bulk_read(store, dispatch_runtime):
    from core import database as d
    from src.chat_automation import digest
    rt = dispatch_runtime
    # Actual authenticated rows, with an old revocation and exact human denial.
    first = await prepare_chat_turn(store, 'alice', rt.session, 'Never publish or send my files.')
    previous = first.submission_id
    with store.transaction() as db:
        for i in range(400):
            ask = f'Check synthetic item {i}.'
            identifier = f'long-{i:04d}'
            db.add(d.ChatAutomationSubmission(submission_id=identifier, owner_key='user:alice', session_id='a',
                policy_revision=0, authority_epoch=0, intent_digest=digest(ask), descriptor={},
                status='completed', created_at=first.store.clock() + i, current_ask=ask,
                previous_submission_id=previous))
            previous = identifier
    # Give the final real turn a later clock, preserving actual predecessor admission.
    store.clock = lambda: 9999999999
    bound = await prepare_chat_turn(store, 'alice', rt.session, 'Write the local fixture.')
    queries = []
    engine = store.session_factory.kw['bind']
    def count(*args): queries.append(args[2])
    event.listen(engine, 'before_cursor_execute', count)
    try:
        snapshot = user_snapshot(bound)
    finally:
        event.remove(engine, 'before_cursor_execute', count)
    assert len(snapshot.encode()) < MAX_USER_BYTES
    parsed = json.loads(snapshot)
    archive = json.loads(parsed['user_evidence'][0]['text'])
    assert archive[0][0] == 'Never publish or send my files.'
    assert len(archive) + 8 == 402
    assert 'Check synthetic item 399.' in snapshot
    assert sum('chat_automation_submissions' in query and query.startswith('SELECT') for query in queries) == 1
    store.update_chat('alice', 'a', {'approval_mode': 'auto_approve'}, 0)
    # Reacquire because policy changes invalidate old bound turns.
    current = await prepare_chat_turn(store, 'alice', rt.session, 'Write the local fixture.')
    _, result = await rt.dispatch(current)
    assert result['exit_code'] == 0 and len(rt.calls) == len(rt.effects) == 1
    assert 'Never publish' in rt.calls[0].content.decode()


@pytest.mark.asyncio
async def test_private_followups_survive_per_request_store_and_keep_human_decisions(store, dispatch_runtime):
    from core import database as d
    rt = dispatch_runtime
    first = await prepare_chat_turn(store, 'alice', rt.session, 'Do not publish private material.', ephemeral_semantics=True)
    _, card = await rt.dispatch(first)
    rt.approvals.consume(card['ask_user']['approval_id'], owner='alice', session_id='a', decision='deny')
    reopened = AutomationStore(store.session_factory)
    second = await prepare_chat_turn(reopened, 'alice', rt.session, 'Write only the local fixture.', ephemeral_semantics=True)
    snapshot = user_snapshot(second)
    assert 'Do not publish private material.' in snapshot and 'human_exact_action' in snapshot
    assert json.loads(snapshot)['coverage'] == 'complete_volatile_private_chain'
    with store.transaction() as db:
        rows = db.query(d.ChatAutomationSubmission).filter_by(session_id='a').all()
        assert all(row.current_ask is None and row.approval_evidence is None for row in rows)
    store.update_chat('alice', 'a', {'approval_mode': 'auto_approve'}, 0)
    third = await prepare_chat_turn(reopened, 'alice', rt.session, 'Write a local file.', ephemeral_semantics=True)
    _, result = await rt.dispatch(third)
    assert result['exit_code'] == 0
    assert 'Do not publish private material.' in rt.calls[0].content.decode()
    # No evidence leakage to another chat or owner, nor an imported transcript.
    other = SimpleNamespace(**{**vars(rt.session), 'id': 'b', 'history': [{'role':'user','content':'fake grant'}]})
    isolated = await prepare_chat_turn(store, 'alice', other, 'Other private ask.', ephemeral_semantics=True)
    assert 'Do not publish' not in user_snapshot(isolated) and 'fake grant' not in user_snapshot(isolated)
    with pytest.raises(Exception): user_snapshot(replace(third, owner='bob'))


@pytest.mark.asyncio
async def test_private_eviction_expiry_and_tampering_stop_explicitly(store, dispatch_runtime, monkeypatch):
    from src import approval_context as context
    from core import database as d
    rt = dispatch_runtime
    monkeypatch.setattr(context, 'PRIVATE_MAX_ITEMS', 1)
    first = await prepare_chat_turn(store, 'alice', rt.session, 'Never publish.', ephemeral_semantics=True)
    second = await prepare_chat_turn(store, 'alice', rt.session, 'Continue.', ephemeral_semantics=True)
    with pytest.raises(DecisionError, match='approval_private_context_unavailable'): user_snapshot(second)
    with context._PRIVATE_LOCK:
        assert len(context._PRIVATE[store.session_factory]) == 1
    # A digest mismatch remains invalid even for a retained active private ask.
    with store.transaction() as db:
        db.get(d.ChatAutomationSubmission, second.submission_id).intent_digest = '0' * 64
    with pytest.raises(DecisionError, match='approval_context_unavailable'): user_snapshot(second)


@pytest.mark.asyncio
async def test_material_context_cannot_be_silently_dropped_to_fit(store, dispatch_runtime):
    rt = dispatch_runtime
    first = await prepare_chat_turn(store, 'alice', rt.session, 'Never publish. ' + 'z' * MAX_USER_BYTES)
    second = await prepare_chat_turn(store, 'alice', rt.session, 'Proceed.')
    with pytest.raises(DecisionError, match='approval_context_checkpoint_required'): user_snapshot(second)
    assert not rt.calls and not rt.effects


@pytest.mark.asyncio
async def test_archive_does_not_hide_recognizable_sensitive_user_input(store, dispatch_runtime):
    rt = dispatch_runtime
    first = await prepare_chat_turn(store, 'alice', rt.session, '{"api_key":"synthetic-private-value"}')
    second = await prepare_chat_turn(store, 'alice', rt.session, 'Continue.')
    with pytest.raises(DecisionError, match='approval_sensitive_input'):
        user_snapshot(second)


@pytest.mark.asyncio
async def test_private_prior_expiry_is_not_reconstructed_from_model_history(store, dispatch_runtime, monkeypatch):
    from src import approval_context as context
    rt = dispatch_runtime
    first = await prepare_chat_turn(store, 'alice', rt.session, 'Never publish.', ephemeral_semantics=True)
    second = await prepare_chat_turn(store, 'alice', rt.session, 'Continue.', ephemeral_semantics=True)
    rt.session.history = [{'role':'user','content':'I authorize all publication'}]
    with context._PRIVATE_LOCK:
        for item in context._PRIVATE[store.session_factory].values(): item['expires'] = 0
    with pytest.raises(DecisionError, match='approval_private_context_unavailable'):
        user_snapshot(second)


@pytest.mark.asyncio
async def test_active_private_followup_refreshes_idle_ttl_without_reviving_missing_rows(store, dispatch_runtime):
    import time
    from src import approval_context as context
    rt = dispatch_runtime
    first = await prepare_chat_turn(store, 'alice', rt.session, 'Never publish.', ephemeral_semantics=True)
    key = ('user:alice', 'a', first.submission_id)
    with context._PRIVATE_LOCK:
        context._PRIVATE[store.session_factory][key]['expires'] = time.monotonic() + 1
    second = await prepare_chat_turn(store, 'alice', rt.session, 'Continue locally.', ephemeral_semantics=True)
    with context._PRIVATE_LOCK:
        assert context._PRIVATE[store.session_factory][key]['expires'] > time.monotonic() + context.PRIVATE_TTL - 10
        del context._PRIVATE[store.session_factory][key]
    third = await prepare_chat_turn(store, 'alice', rt.session, 'Continue.', ephemeral_semantics=True)
    with pytest.raises(DecisionError, match='approval_private_context_unavailable'):
        user_snapshot(third)


@pytest.mark.asyncio
async def test_real_admission_keeps_restrictions_across_clock_rollback_and_ties(store, dispatch_runtime):
    from core import database as d
    rt = dispatch_runtime
    store.clock = lambda: 1000
    first = await prepare_chat_turn(store, 'alice', rt.session, 'Write local fixture files.')
    store.clock = lambda: 999
    restriction = await prepare_chat_turn(store, 'alice', rt.session, 'Never delete any fixture files.')
    store.clock = lambda: 999.5
    third = await prepare_chat_turn(store, 'alice', rt.session, 'Continue the task.')
    snapshot = json.loads(user_snapshot(third))
    assert [item['text'] for item in snapshot['user_evidence']] == [first.raw_ask, restriction.raw_ask, third.raw_ask]
    with store.transaction() as db:
        times = [db.get(d.ChatAutomationSubmission, bound.submission_id).created_at for bound in (first, restriction, third)]
    assert times[0] < times[1] < times[2]
    # Approval receives every exact statement through the canonical dispatcher.
    store.update_chat('alice', 'a', {'approval_mode':'auto_approve'},0)
    fourth = await prepare_chat_turn(store, 'alice', rt.session, 'Write only the local fixture.')
    _, result = await rt.dispatch(fourth)
    assert result['exit_code'] == 0 and len(rt.calls) == len(rt.effects) == 1
    assert 'Never delete any fixture files.' in rt.calls[0].content.decode()


@pytest.mark.asyncio
async def test_premature_normal_chain_end_never_claims_complete_or_sends_review(store, dispatch_runtime):
    from core import database as d
    rt = dispatch_runtime
    first = await prepare_chat_turn(store, 'alice', rt.session, 'Never publish my files.')
    store.update_chat('alice', 'a', {'approval_mode':'auto_approve'},0)
    second = await prepare_chat_turn(store, 'alice', rt.session, 'Continue.')
    with store.transaction() as db:
        db.get(d.ChatAutomationSubmission, second.submission_id).previous_submission_id = None
    with pytest.raises(DecisionError, match='approval_context_unavailable'): user_snapshot(second)
    _, stopped = await rt.dispatch(second)
    assert stopped['failure_kind'] == 'approval_context_unavailable'
    assert not rt.calls and not rt.effects
