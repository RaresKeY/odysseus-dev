"""Full canonical resolver/SQL harness, with no real network or key reads."""
import importlib.util
from pathlib import Path

import pytest

from src.decisions import DecisionError
from src.decisions.campaign import BillingContract, digest

SPEC = importlib.util.spec_from_file_location("jev_live_campaign_runner", Path(__file__).resolve().parents[1] / "scripts/jev_live_campaign.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


@pytest.mark.asyncio
async def test_full_offline_registered_resolver_sql_capture_replay(tmp_path, monkeypatch):
    from core import database as d
    monkeypatch.setattr(d, "SessionLocal", d.SessionLocal)
    monkeypatch.setattr(runner, "read_test_key", lambda: pytest.fail("offline key read forbidden"))
    report = await runner.run(tmp_path, wall_seconds=30)
    assert report["new_live_calls"] == 0 and report["effects"] == 0
    assert report["totals"]["attempted"] == len(runner.load_suite()["cases"])
    assert report["totals"]["valid"] == report["totals"]["attempted"]
    assert report["accounting"]["charged_or_held"] == 30 * report["totals"]["attempted"]
    assert report["offline_capture_replays"] == report["totals"]["valid"]
    assert not report["accounting"]["halted"]
    for case in runner.load_suite()["cases"]:
        payload = runner.request_for(case)
        assert "gold" not in payload.state and "rationale" not in payload.state


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "source", "suite", "unverified"])
async def test_live_prerequisites_checked_before_only_key_reader(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(runner, "read_test_key", lambda: pytest.fail("key read before admission"))
    monkeypatch.setenv("ODYSSEUS_JEV_HOST_LAUNCHER", "reviewed-v1")
    config = {"schema": "odysseus-jev-live-config/v1", "source_sha256": runner.source_identity(),
              "suite_sha256": digest(runner.load_suite()), "model": runner.MODEL, "url": runner.URL,
              "campaign_id": runner.CAMPAIGN_ID, "limit": 5_000_000, "admitted_stages": {"smoke": "offline-review"},
              "billing_contract": {"input_tokens": 65536, "output_tokens": 65536, "evidence_sha256": "1" * 64, "verified": False}}
    if failure == "missing": config = None
    if failure == "source": config["source_sha256"] = "0" * 64
    if failure == "suite": config["suite_sha256"] = "0" * 64
    with pytest.raises(DecisionError):
        await runner.run(tmp_path, live=True, config=config)


@pytest.mark.asyncio
async def test_corrupted_capture_refused_without_ledger_change(tmp_path, monkeypatch):
    from core import database as d
    from src.decisions.campaign import CampaignLedger
    monkeypatch.setattr(d, "SessionLocal", d.SessionLocal)
    await runner.run(tmp_path, wall_seconds=30)
    ledger = CampaignLedger(tmp_path / "budget/campaign.sqlite3", authorization=runner.AUTHORIZATION)
    before = ledger.path.read_bytes()
    capture = next((tmp_path / "captures").glob("*.json"))
    raw = runner.decode(capture.read_bytes())
    raw["response"]["model"] = "other-model"
    capture.write_bytes(runner.encode(raw))
    with pytest.raises(DecisionError, match="invalid_capture"):
        runner.replay_capture(capture, ledger)
    assert ledger.path.read_bytes() == before
