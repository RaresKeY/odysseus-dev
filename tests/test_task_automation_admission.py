"""Real SQL/event/authority execution paths; provider/effect boundaries are fake.

Execute only through the secretless review runner. No application credentials.
"""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.task_automation import task_automation_enabled


@pytest.fixture
def execution(tmp_path, monkeypatch):
    import core.database as database
    import src.builtin_actions as actions
    import src.event_bus as bus
    import src.agent_runtime.authority as authority
    from src.chat_automation import SqlAttemptStore
    from src.decisions import DecisionProvider
    from src.task_scheduler import TaskScheduler

    engine = create_engine('sqlite:///' + str(tmp_path / 'tasks.db'), connect_args={'check_same_thread': False})
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, 'SessionLocal', factory)
    monkeypatch.delenv('ODYSSEUS_TASK_AUTOMATION_ENABLED', raising=False)
    monkeypatch.setenv('ODYSSEUS_INPROCESS_TASKS', '0')
    scheduler = TaskScheduler(None)
    monkeypatch.setattr(bus, '_task_scheduler', scheduler)
    # Create a legitimate exact-action authority BEFORE the negative spies.
    grant = authority.seal_task_authority(None, 'action', 'consolidate_memory', owner='alice')
    with factory() as db:
        db.add(database.ScheduledTask(id='event', owner='alice', name='Synthetic event',
            task_type='action', action='consolidate_memory', request_authority_json=grant,
            status='active', trigger_type='event', trigger_event='memory_added',
            trigger_count=5, trigger_counter=4))
        db.commit()
    restored = Mock(wraps=authority.restore_task_authority)
    monkeypatch.setattr(authority, 'restore_task_authority', restored)
    effects = []
    async def final_effect(**kwargs):
        admitted = authority.active_request_authority()
        assert admitted is not None and admitted.permits(authority.task_operation('action', 'consolidate_memory', None))
        effects.append(kwargs['task_name'])
        return 'Synthetic final effect only; no inference or mutation.', True
    monkeypatch.setitem(actions.BUILTIN_ACTIONS, 'consolidate_memory', final_effect)
    monkeypatch.setattr('src.interactive_gate.wait_for_interactive_quiet', AsyncMock())
    monkeypatch.setattr('src.interactive_gate.has_foreground_activity', lambda: False)
    provider = AsyncMock(side_effect=AssertionError('No inference authorized'))
    reserve = Mock(side_effect=AssertionError('No decision spending authorized'))
    monkeypatch.setattr(DecisionProvider, 'evaluate', provider)
    monkeypatch.setattr(SqlAttemptStore, 'reserve', reserve)
    yield database, factory, scheduler, bus, effects, restored, provider, reserve
    engine.dispose()


async def drain(scheduler):
    for _ in range(200):
        if not scheduler._executing and not scheduler._task_handles:
            return
        await asyncio.sleep(.005)
    raise AssertionError('Synthetic task did not finish')


@pytest.mark.parametrize('value,expected', [(None, True), ('1', True), (' TRUE ', True), ('yes', True),
    ('on', True), ('0', False), ('false', False), ('off', False), ('no', False), ('', False), ('typo', False)])
def test_explicit_deployment_admission(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv('ODYSSEUS_TASK_AUTOMATION_ENABLED', raising=False)
    else:
        monkeypatch.setenv('ODYSSEUS_TASK_AUTOMATION_ENABLED', value)
    assert task_automation_enabled() is expected


@pytest.mark.asyncio
@pytest.mark.parametrize('value,expected', [(None, 1), ('1', 1), ('0', 0)])
async def test_actual_event_to_sql_authority_handler_with_loop_off(execution, monkeypatch, value, expected):
    d, factory, scheduler, bus, effects, restored, provider, reserve = execution
    if value is not None:
        monkeypatch.setenv('ODYSSEUS_TASK_AUTOMATION_ENABLED', value)
    await bus._handle_event('memory_added', 'alice')
    await drain(scheduler)
    with factory() as db:
        task = db.query(d.ScheduledTask).filter_by(id='event').one()
        assert task.trigger_counter == (0 if expected else 4)
        assert task.run_count == expected
        assert db.query(d.TaskRun).count() == expected
        assert db.query(d.DecisionAttempt).count() == 0
        if expected:
            assert db.query(d.TaskRun).one().status == 'success'
        else:
            assert task.next_run is None and task.last_run is None
    assert len(effects) == expected == restored.call_count
    provider.assert_not_called(); reserve.assert_not_called()
    assert not scheduler._running  # loop-only0 does not disable normal events


@pytest.mark.asyncio
async def test_full_off_returns_before_owner_lookup_database_or_loop_schedule(execution, monkeypatch):
    d, factory, scheduler, bus, effects, restored, provider, reserve = execution
    monkeypatch.setenv('ODYSSEUS_TASK_AUTOMATION_ENABLED', '0')
    forbidden = Mock(side_effect=AssertionError('Disabled automatic path entered'))
    monkeypatch.setattr(bus, '_resolve_event_owner', forbidden)
    monkeypatch.setattr(d, 'SessionLocal', forbidden)
    monkeypatch.setattr(asyncio, 'get_running_loop', forbidden)
    bus.fire_event('memory_added', None)
    await bus._handle_event('memory_added', None)
    await scheduler.start()
    await scheduler._check_due_tasks()
    await scheduler._loop()
    await scheduler._note_pings_loop()
    await scheduler._event_pings_loop()
    await scheduler._run_chained('event')
    await scheduler._execute_task('event', automatic=True)
    assert await scheduler.run_automatic_task('event') is False
    forbidden.assert_not_called(); restored.assert_not_called()
    provider.assert_not_called(); reserve.assert_not_called()
    assert not effects and not scheduler._executing and not scheduler._task_handles


def test_sync_fire_event_full_off_never_opens_a_loop(monkeypatch):
    import src.event_bus as bus
    monkeypatch.setenv('ODYSSEUS_TASK_AUTOMATION_ENABLED', '0')
    forbidden = Mock(side_effect=AssertionError('No async dispatch permitted'))
    monkeypatch.setattr(asyncio, 'get_running_loop', forbidden)
    monkeypatch.setattr(asyncio, 'run', forbidden)
    bus.fire_event('memory_added', 'alice')
    forbidden.assert_not_called()


@pytest.mark.asyncio
async def test_delayed_on_event_rechecks_off_before_counter(execution, monkeypatch):
    d, factory, scheduler, bus, effects, restored, provider, reserve = execution
    callbacks = []
    loop = asyncio.get_running_loop()
    with monkeypatch.context() as patcher:
        patcher.setattr(loop, 'call_later', lambda delay, callback: callbacks.append(callback))
        bus.fire_event('memory_added', 'alice')
    assert len(callbacks) == 1
    monkeypatch.setenv('ODYSSEUS_TASK_AUTOMATION_ENABLED', '0')
    await callbacks[0]()
    with factory() as db:
        assert db.query(d.ScheduledTask).one().trigger_counter == 4
        assert db.query(d.TaskRun).count() == 0
    assert not effects; restored.assert_not_called(); reserve.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('force', [False, True])
async def test_explicit_manual_run_survives_full_off_without_automatic_chain(execution, monkeypatch, force):
    d, factory, scheduler, bus, effects, restored, provider, reserve = execution
    monkeypatch.setenv('ODYSSEUS_TASK_AUTOMATION_ENABLED', '0')
    with factory() as db:
        parent = db.query(d.ScheduledTask).one()
        child = d.ScheduledTask(id='child', owner='alice', name='Synthetic child', task_type='action',
            action=parent.action, request_authority_json=parent.request_authority_json, status='active')
        db.add(child); db.flush(); parent.then_task_id = child.id; db.commit()
    assert await scheduler.run_task_now('event', force=force)
    # Force mode does not claim _executing, so allow its coroutine to register.
    await asyncio.sleep(.001)
    await drain(scheduler)
    with factory() as db:
        assert db.query(d.TaskRun).count() == 1
        assert db.query(d.TaskRun).one().status == 'success'
        assert db.query(d.ScheduledTask).filter_by(id='child').one().run_count == 0
    assert effects == ['Synthetic event'] and restored.call_count == 1
    provider.assert_not_called(); reserve.assert_not_called()


@pytest.mark.asyncio
async def test_default_on_chaining_retains_actual_execution(execution):
    d, factory, scheduler, bus, effects, restored, provider, reserve = execution
    with factory() as db:
        parent = db.query(d.ScheduledTask).one()
        db.add(d.ScheduledTask(id='child', owner='alice', name='Synthetic child', task_type='action',
            action=parent.action, request_authority_json=parent.request_authority_json, status='active'))
        db.flush(); parent.then_task_id = 'child'; db.commit()
    assert await scheduler.run_task_now('event')
    for _ in range(200):
        await asyncio.sleep(.005)
        with factory() as db:
            if db.query(d.TaskRun).filter_by(status='success').count() == 2:
                break
    else:
        raise AssertionError('Default chain did not run')
    await drain(scheduler)
    assert effects == ['Synthetic event', 'Synthetic child']
    assert restored.call_count == 2; provider.assert_not_called(); reserve.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('disable_at', ['model_slot', 'quiet_gate'])
async def test_queued_automatic_work_rechecks_before_authority(execution, monkeypatch, disable_at):
    d, factory, scheduler, bus, effects, restored, provider, reserve = execution
    if disable_at == 'model_slot':
        await scheduler._run_semaphore.acquire()
    else:
        async def quiet(*args):
            monkeypatch.setenv('ODYSSEUS_TASK_AUTOMATION_ENABLED', '0')
        monkeypatch.setattr('src.interactive_gate.wait_for_interactive_quiet', quiet)
    await bus._handle_event('memory_added', 'alice')
    if disable_at == 'model_slot':
        for _ in range(200):
            await asyncio.sleep(.005)
            with factory() as db:
                if db.query(d.TaskRun).filter_by(status='queued').count():
                    break
        else:
            raise AssertionError('Expected real queued receipt')
        monkeypatch.setenv('ODYSSEUS_TASK_AUTOMATION_ENABLED', '0')
        scheduler._run_semaphore.release()
    await drain(scheduler)
    with factory() as db:
        run = db.query(d.TaskRun).one()
        assert run.status == 'aborted' and 'disabled' in run.error
        assert db.query(d.ScheduledTask).one().run_count == 0
    assert not effects; restored.assert_not_called(); provider.assert_not_called(); reserve.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
async def test_direct_due_scan_is_automatic(execution, monkeypatch, enabled):
    d, factory, scheduler, bus, effects, restored, provider, reserve = execution
    monkeypatch.setenv('ODYSSEUS_TASK_AUTOMATION_ENABLED', '1' if enabled else '0')
    due = datetime.utcnow() - timedelta(minutes=1)
    with factory() as db:
        db.query(d.ScheduledTask).one().next_run = due; db.commit()
    await scheduler._check_due_tasks()
    await drain(scheduler)
    with factory() as db:
        assert db.query(d.TaskRun).count() == int(enabled)
        if not enabled:
            assert db.query(d.ScheduledTask).one().next_run == due
    assert len(effects) == int(enabled); provider.assert_not_called(); reserve.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
async def test_actual_token_webhook_is_automatic_before_database(execution, monkeypatch, enabled):
    import httpx
    from fastapi import FastAPI
    import routes.task.task_routes as routes
    d,factory,scheduler,bus,effects,restored,provider,reserve=execution
    with factory() as db:
        db.query(d.ScheduledTask).one().webhook_token='synthetic-token';db.commit()
    monkeypatch.setenv('ODYSSEUS_TASK_AUTOMATION_ENABLED','1' if enabled else '0')
    forbidden=Mock(side_effect=AssertionError('Disabled webhook must not open SQL'))
    monkeypatch.setattr(routes,'SessionLocal',factory if enabled else forbidden)
    app=FastAPI();app.include_router(routes.setup_task_routes(scheduler))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post('/api/tasks/event/webhook/synthetic-token')
        assert response.status_code==(200 if enabled else 503)
        await drain(scheduler)
    assert len(effects)==int(enabled)==restored.call_count
    forbidden.assert_not_called();provider.assert_not_called();reserve.assert_not_called()
    with factory() as db:
        assert db.query(d.TaskRun).count()==int(enabled)
        assert db.query(d.DecisionAttempt).count()==0


@pytest.mark.asyncio
async def test_default_on_webhook_still_requires_exact_token(execution, monkeypatch):
    import httpx
    from fastapi import FastAPI
    import routes.task.task_routes as routes
    d,factory,scheduler,bus,effects,restored,provider,reserve=execution
    monkeypatch.setattr(routes,'SessionLocal',factory)
    app=FastAPI();app.include_router(routes.setup_task_routes(scheduler))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post('/api/tasks/event/webhook/wrong-token')
        assert response.status_code==404
    assert not effects;restored.assert_not_called();reserve.assert_not_called()


@pytest.mark.parametrize('enabled', [False, True])
def test_full_startup_account_after_first_boot_and_event_dispatch(tmp_path, enabled):
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    home=tmp_path/'home';home.mkdir(mode=0o700)
    data=tmp_path/'data'
    env={**os.environ, 'ODYSSEUS_DATA_DIR':str(data),'DATABASE_URL':'sqlite:///'+str(data/'app.db'),
        'AUTH_ENABLED':'true','LOCALHOST_BYPASS':'false','ODYSSEUS_DISABLE_MCP':'1',
        'ODYSSEUS_INPROCESS_TASKS':'0','ODYSSEUS_TASK_AUTOMATION_ENABLED':'1' if enabled else '0',
        'ODYSSEUS_INPROCESS_POLLERS':'0','ODYSSEUS_TOOL_INDEX_PREWARM':'0','ODYSSEUS_STARTUP_WARMUPS':'0',
        'ODYSSEUS_MODEL_KEEPALIVE':'0','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
        'ANONYMIZED_TELEMETRY':'false','CHROMADB_HOST':'127.0.0.1','CHROMADB_PORT':'8100',
        'CHROMADB_CONNECT_TIMEOUT':'0.01','HOME':str(home),'XDG_CONFIG_HOME':str(home/'config'),
        'XDG_CACHE_HOME':str(home/'cache')}
    probe=Path(__file__).parent/'helpers/task_automation_bootstrap_probe.py'
    result=subprocess.run([sys.executable,'-I','-B',str(probe.resolve())],cwd=tmp_path,env=env,
        capture_output=True,text=True,timeout=90)
    assert result.returncode==0, result.stdout[-6000:]+result.stderr[-6000:]
    marker='TASK_AUTOMATION_BOOTSTRAP_PROBE='
    report=json.loads(next(line[len(marker):] for line in result.stdout.splitlines() if line.startswith(marker)))
    assert report['task_runs']==report['authority_restores']==int(enabled)
    assert report['provider_calls']==0 and report['stored_authorities_preserved']
