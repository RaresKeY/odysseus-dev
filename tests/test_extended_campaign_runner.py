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


@pytest.mark.asyncio
async def test_partial_canonical_stage_restart_replays_then_finishes_unrun_cases(tmp_path, monkeypatch):
    from core import database as d
    monkeypatch.setattr(d, "SessionLocal", d.SessionLocal)
    original = d.SessionLocal
    first = await runner.run(tmp_path, wall_seconds=30, max_new_cases=1)
    assert d.SessionLocal is original
    assert first["totals"]["actual_dispatches"] == 1
    first_attempt = first["accounting"]["attempts"][0]
    resumed = await runner.run(tmp_path, wall_seconds=30)
    assert resumed["version"] == first["version"]
    assert resumed["totals"]["cached"] == 1
    assert resumed["totals"]["actual_dispatches"] == len(runner.load_suite()["cases"]) - 1
    assert len(resumed["accounting"]["attempts"]) == len(runner.load_suite()["cases"])
    assert resumed["accounting"]["attempts"][0] == first_attempt
    assert resumed["accounting"]["charged_or_held"] == 30 * len(runner.load_suite()["cases"])
    all_cached = await runner.run(tmp_path, wall_seconds=30)
    assert all_cached["totals"]["cached"] == len(runner.load_suite()["cases"])
    assert all_cached["totals"]["actual_dispatches"] == 0
    assert all_cached["accounting"] == resumed["accounting"]
    assert d.SessionLocal is original


@pytest.mark.asyncio
async def test_harness_restores_original_sql_factory_and_ordinary_store(tmp_path, monkeypatch):
    from core import database as d
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from src.chat_automation import AutomationStore
    engine = create_engine("sqlite:///" + str(tmp_path / "ordinary.sqlite3"))
    d.Base.metadata.create_all(engine)
    ordinary_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(d, "SessionLocal", ordinary_factory)
    with ordinary_factory() as db:
        db.add(d.Session(id="ordinary", name="ordinary", owner="alice", endpoint_url="https://ordinary.invalid/v1", model="ordinary"))
        db.commit()
    await runner.run(tmp_path / "campaign", wall_seconds=30, max_new_cases=1)
    assert d.SessionLocal is ordinary_factory
    with AutomationStore().transaction() as db:
        assert db.query(d.Session).filter_by(id="ordinary", owner="alice").one().name == "ordinary"
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [True, False])
async def test_resume_missing_or_changed_capture_fails_closed_without_new_spend(tmp_path, monkeypatch, missing):
    from core import database as d
    monkeypatch.setattr(d, "SessionLocal", d.SessionLocal)
    first = await runner.run(tmp_path, wall_seconds=30, max_new_cases=1)
    capture = next((tmp_path / "captures").glob("*.json"))
    if missing:
        capture.unlink()
    else:
        payload = runner.decode(capture.read_bytes())
        payload["request"]["state"]["ask"] = "changed"
        payload["wire_sha256"] = digest(payload["request"])
        capture.write_bytes(runner.encode(payload))
    ledger_bytes = (tmp_path / "budget/campaign.sqlite3").read_bytes()
    with pytest.raises(DecisionError):
        await runner.run(tmp_path, wall_seconds=30)
    assert (tmp_path / "budget/campaign.sqlite3").read_bytes() == ledger_bytes


@pytest.mark.asyncio
async def test_cached_valid_but_false_complete_still_blocks_same_source_expansion(tmp_path, monkeypatch):
    import httpx
    from core import database as d
    monkeypatch.setattr(d, "SessionLocal", d.SessionLocal)
    original_transport = httpx.MockTransport
    calls = []
    def make_transport(handler):
        def unsafe(request):
            calls.append(request)
            reply = handler(request)
            payload = runner.decode(request.content)
            if payload["state"].get("assistant_claim") == "DONE":
                raw = reply.json()
                answer = raw["answers"]["decision"]
                answer["choice"] = "complete"
                answer["probabilities"] = {key: float(key == "complete") for key in answer["probabilities"]}
                return httpx.Response(200, json=raw)
            return reply
        return original_transport(unsafe)
    monkeypatch.setattr(httpx, "MockTransport", make_transport)
    first = await runner.run(tmp_path, wall_seconds=30)
    assert first["totals"]["false_complete"] == 1
    assert len(first["accounting"]["attempts"]) < len(runner.load_suite()["cases"])
    previous_calls = len(calls)
    ledger_bytes = (tmp_path / "budget/campaign.sqlite3").read_bytes()
    with pytest.raises(DecisionError, match="cached_material_failure"):
        await runner.run(tmp_path, wall_seconds=30)
    assert len(calls) == previous_calls
    assert (tmp_path / "budget/campaign.sqlite3").read_bytes() == ledger_bytes
