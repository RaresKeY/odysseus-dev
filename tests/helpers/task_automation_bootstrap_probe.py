"""Synthetic full startup probe. Invoked only inside the secretless runner."""
import asyncio
import json
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
state = Path(os.environ['ODYSSEUS_DATA_DIR'])
state.mkdir(mode=0o700, parents=True, exist_ok=True)
(state / 'auth.json').write_text('{"users":{}}')
import app
import core.database as d
import src.builtin_actions as actions
from src import event_bus
from src.agent_runtime import authority
from src.chat_automation import SqlAttemptStore
from src.decisions import DecisionProvider


async def finish_startup():
    await app._shutdown_event()
    tasks = list(getattr(app.app.state, '_startup_tasks', []))
    for task in tasks:
        if hasattr(task, 'cancel') and not task.done():
            task.cancel()
    await asyncio.gather(*(t for t in tasks if hasattr(t, 'cancel')), return_exceptions=True)
    app.app.state._startup_tasks = []


async def checked_startup():
    # Observe both complete startup calls, not only the later event window.
    provider = AsyncMock(side_effect=AssertionError('No startup inference'))
    reserve = Mock(side_effect=AssertionError('No startup decision reservation'))
    action = AsyncMock(side_effect=AssertionError('No startup task handler'))
    llm = AsyncMock(side_effect=AssertionError('No startup task inference'))
    research = AsyncMock(side_effect=AssertionError('No startup task research'))
    with patch.object(DecisionProvider, 'evaluate', provider), \
         patch.object(SqlAttemptStore, 'reserve', reserve), \
         patch.object(type(app.task_scheduler), '_execute_action', action), \
         patch.object(type(app.task_scheduler), '_execute_llm_task', llm), \
         patch.object(type(app.task_scheduler), '_execute_research_task', research):
        await app._startup_event()
        await asyncio.sleep(0)
        for spy in (provider, reserve, action, llm, research):
            spy.assert_not_called()


async def run():
    await checked_startup()
    await finish_startup()
    with d.SessionLocal() as db:
        assert db.query(d.ScheduledTask).count() == 0
    # A named account appears after first boot, matching the deployment trigger.
    (state / 'auth.json').write_text(json.dumps({'users': {'guarduser': {
        'password_hash': 'synthetic-no-login', 'is_admin': True}}}))
    await checked_startup()
    # Full-OFF wins even with loop1; normal-ON uses the external-worker loop0.
    assert app.task_scheduler._running is False
    enabled = os.environ['ODYSSEUS_TASK_AUTOMATION_ENABLED'] == '1'
    calls = []
    async def effect(**kwargs):
        granted = authority.active_request_authority()
        assert granted is not None and granted.permits(authority.task_operation('action', 'consolidate_memory', None))
        calls.append(True)
        return 'Synthetic final effect; no memory/provider work.', True
    async def quiet(*args, **kwargs):
        return None
    reserve = Mock(side_effect=AssertionError('No budget reservation'))
    provider = AsyncMock(side_effect=AssertionError('No provider call'))
    with d.SessionLocal() as db:
        assert db.query(d.Session).count() == 1
        assert db.query(d.CrewMember).count() == 1
        tasks = db.query(d.ScheduledTask).all()
        assert len(tasks) == 11 and all(task.request_authority_json for task in tasks)
        before = {task.id: task.request_authority_json for task in tasks}
    restored = Mock(wraps=authority.restore_task_authority)
    with patch.dict(actions.BUILTIN_ACTIONS, {'consolidate_memory': effect}), \
         patch('src.interactive_gate.wait_for_interactive_quiet', quiet), \
         patch('src.interactive_gate.has_foreground_activity', return_value=False), \
         patch.object(authority, 'restore_task_authority', restored), \
         patch.object(DecisionProvider, 'evaluate', provider), \
         patch.object(SqlAttemptStore, 'reserve', reserve):
        for _ in range(5):
            await event_bus._handle_event('memory_added', 'guarduser')
        for _ in range(200):
            if not app.task_scheduler._executing and not app.task_scheduler._task_handles:
                break
            await asyncio.sleep(.005)
        else:
            raise AssertionError('Automatic synthetic work failed to finish')
        assert len(calls) == int(enabled) == restored.call_count
        reserve.assert_not_called(); provider.assert_not_called()
    with d.SessionLocal() as db:
        assert db.query(d.TaskRun).count() == int(enabled)
        assert db.query(d.DecisionAttempt).count() == 0
        assert before == {task.id: task.request_authority_json for task in db.query(d.ScheduledTask)}
    await finish_startup()
    print('TASK_AUTOMATION_BOOTSTRAP_PROBE=' + json.dumps({
        'loop_running': False, 'automatic_enabled': enabled, 'seeded_tasks': 11,
        'task_runs': int(enabled), 'final_mock_effects': len(calls),
        'provider_calls': 0, 'startup_provider_and_task_handlers': 0,
        'loop_setting': os.environ['ODYSSEUS_INPROCESS_TASKS'], 'authority_restores': restored.call_count,
        'stored_authorities_preserved': True}))


try:
    asyncio.run(run())
finally:
    d.engine.dispose()
