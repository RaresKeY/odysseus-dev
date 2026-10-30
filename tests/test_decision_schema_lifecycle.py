"""Decision persistence belongs to normal database startup, not service order."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]


def run_isolated(source, data_dir):
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT,
        env={**os.environ, "DATABASE_URL": f"sqlite:///{data_dir / 'app.db'}",
             "ODYSSEUS_DATA_DIR": str(data_dir)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_observation_schema_exists_before_service_and_survives_restart(tmp_path):
    bootstrap = '''
        from sqlalchemy import inspect
        import core.database as database

        expected = {"decision_activity", "decision_capture_consents",
                    "decision_capture_runs", "decision_handoffs"}
        assert expected <= set(inspect(database.engine).get_table_names())
        before = set(database.Base.metadata.tables)
        from src.decisions.observability import observation_models
        assert observation_models().activity is database.DecisionActivity
        assert set(database.Base.metadata.tables) == before
        database.init_db()
    '''
    run_isolated(bootstrap + '''
        with database.SessionLocal() as session:
            session.add(database.Session(id="kept", owner="alice", name="Kept",
                                         endpoint_url="", model=""))
            session.commit()
            session.add(database.DecisionActivity(attempt_id="one", owner_key="alice",
                session_id="kept", run_id="run", created_at=1, projection={"status": "settled"}))
            session.commit()
    ''', tmp_path)
    run_isolated(bootstrap + '''
        with database.SessionLocal() as session:
            row = session.query(database.DecisionActivity).filter_by(attempt_id="one").one()
            assert row.projection == {"status": "settled"}
    ''', tmp_path)


def test_offline_observation_import_does_not_initialize_persistence(tmp_path):
    run_isolated('''
        import sys
        from src.decisions.observability import validate_capture
        assert "core.database" not in sys.modules
    ''', tmp_path)
    assert not (tmp_path / "app.db").exists()
