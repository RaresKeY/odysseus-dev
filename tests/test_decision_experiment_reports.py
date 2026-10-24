"""Report replay is frozen, credential-free and cannot execute classified work."""
import copy
import importlib.util
import json
from pathlib import Path
import socket

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("decision_experiments", ROOT / "scripts/decision_experiments.py")
experiments = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiments)


@pytest.mark.asyncio
async def test_replay_is_deterministic_without_network_or_credentials(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("offline replay attempted a network connection")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    for name in ["OPENAI_API_KEY", "JEV_API", "TYPESAFE_API_KEY", "ANTHROPIC_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    fixtures = experiments.load_fixtures(experiments.DEFAULT_FIXTURES)
    first = await experiments.replay(fixtures)
    second = await experiments.replay(fixtures)
    assert first == second
    assert first["totals"]["attempted"] == 9
    assert first["totals"]["invalid"] == 1
    assert first["totals"]["unresolved"] == 1
    assert first["totals"]["unsafe_allows"] == 1
    assert first["totals"]["uncertain"] == 1
    assert first["downstream_task_success"] is None
    assert first["usage"]["live_calls"] == 0 and first["usage"]["actual_provider_tokens"] is None
    assert first["latency"]["median_ms"] is None
    assert all(row["task_success"] is None for row in first["cases"])
    ambiguous = next(row for row in first["cases"] if row["status"] == "unresolved")
    assert ambiguous["usage"] is None and ambiguous["retained_reservation"]["output_tokens"] > 0


def test_mutating_a_frozen_fixture_changes_identity(tmp_path):
    data = experiments.load_fixtures(experiments.DEFAULT_FIXTURES)
    changed = copy.deepcopy(data)
    changed["cases"][0]["state"]["request"] += " changed"
    assert experiments.digest(data) != experiments.digest(changed)
    changed["cases"][1]["id"] = changed["cases"][0]["id"]
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="Invalid fixture"):
        experiments.load_fixtures(path)


def test_cli_replay_writes_portable_report_and_has_no_live_mode(tmp_path):
    output = tmp_path / "report.json"
    assert experiments.main(["--output", str(output)]) == 0
    value = json.loads(output.read_text())
    assert value["schema"] == "odysseus-decision-experiment/v1"
    schema = json.loads((ROOT / "docs/decision-experiment-v1.schema.json").read_text())
    assert set(schema["required"]) == set(value)
    assert set(schema["properties"]["cases"]["items"]["required"]) == set(value["cases"][0])
    assert all(not Path(path).is_absolute() for path in value["source_hashes"])
    assert "secret" not in output.read_text().lower()
    with pytest.raises(SystemExit):
        experiments.main(["--live"])


def test_historical_aggregates_remain_reported_and_limited():
    path = ROOT / "tests/fixtures/decision_port/historical-aggregates-v1.json"
    data = json.loads(path.read_text())
    assert data["evidence_kind"] == "historical_reported" and data["current_validation"] is False
    safety, browser, routing = data["studies"]
    assert safety["safety_cases"] == 11 and safety["unsafe_approvals_reported"] == 0
    assert browser["optimized_decisions"] > browser["baseline_decisions"]
    assert browser["latency_speedup"] is None and routing["task_execution"] is False
