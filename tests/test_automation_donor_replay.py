"""Earlier acceptance failures remain explicit authored, offline evidence."""
import importlib.util
from pathlib import Path
import socket
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('donor_decision_experiments', ROOT / 'scripts/decision_experiments.py')
experiments = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiments)


@pytest.mark.asyncio
async def test_donor_lifecycle_replay_has_complete_attempted_denominator_and_no_live_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Donor fixture replay attempted network')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    monkeypatch.setattr(socket,'getaddrinfo',forbidden)
    fixtures=experiments.load_fixtures(ROOT / 'tests/fixtures/decision_port/donor-lifecycle-v1.json')
    report=await experiments.replay(fixtures)
    assert report['fixture_version']=='donor-lifecycle-v1'
    assert report['totals']['attempted']==4
    assert report['totals']['safety_negative_cases']==2
    assert report['usage']['live_calls']==0
    assert report['usage']['actual_provider_tokens'] is None
    assert report['downstream_task_success'] is None
    assert all(row['attempts']==1 for row in report['cases'])
    assert 'Authored synthetic' in report['label_provenance']
    assert 'src/automation_task_state.py' in report['source_hashes']
    assert all(row['source_group']=='prior-jev-lifecycle-reconstruction-v1' for row in report['cases'])
