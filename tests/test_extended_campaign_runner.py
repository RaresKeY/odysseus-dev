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
async def test_cached_false_complete_is_scored_without_retry_or_material_halt(tmp_path, monkeypatch):
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
    assert first["totals"]["false_complete"] >= 1
    assert len(first["accounting"]["attempts"]) == len(runner.load_suite()["cases"])
    assert not first["accounting"]["halted"]
    assert any(row["stop_classification"] == "scored_advisory_label_error" for row in first["cases"])
    previous_calls = len(calls)
    ledger_bytes = (tmp_path / "budget/campaign.sqlite3").read_bytes()
    resumed = await runner.run(tmp_path, wall_seconds=30)
    assert resumed["totals"]["cached"] == len(runner.load_suite()["cases"])
    assert len(calls) == previous_calls
    assert (tmp_path / "budget/campaign.sqlite3").read_bytes() == ledger_bytes


def approved_config():
    from dataclasses import asdict
    from src.decisions.campaign import RootApprovedChoiceProfile
    return {"schema": "odysseus-jev-live-config/v1", "source_sha256": runner.source_identity(),
            "suite_sha256": digest(runner.load_suite()), "model": runner.MODEL, "url": runner.URL,
            "campaign_id": runner.CAMPAIGN_ID, "limit": 5000000, "operational_stop": 1000000,
            "admitted_stages": {"smoke": "synthetic-root-review"},
            "planning_profile": asdict(RootApprovedChoiceProfile("3" * 64, root_approved=True))}


@pytest.mark.asyncio
async def test_approved_profile_uses_native_transport_and_config_change_replays_without_new_calls(tmp_path, monkeypatch):
    import httpx
    from src.decisions import DecisionProvider
    from src.decisions.campaign import digest as wire_digest
    calls, key_reads = [], []
    suite = runner.load_suite()
    expected = {wire_digest({"model": runner.MODEL, "state": runner.request_for(case).state,
                             "questions": runner.request_for(case).questions()}): case
                for case in suite["cases"] if case["split"] == "smoke"}
    def transport(request):
        calls.append(request)
        case = expected[wire_digest(request.content)]
        questions = runner.decode(request.content)["questions"]
        return httpx.Response(200, json={"model": runner.MODEL, "answers": {name: {
            "type": "choice", "choice": runner.answer_gold(case, name), "confidence": .99,
            "probabilities": {choice: float(choice == runner.answer_gold(case, name))
                              for choice in question["criteria"]}} for name, question in questions.items()},
            "usage": {"input_tokens": 20, "output_tokens": 10}})
    monkeypatch.setattr(runner, "DecisionProvider", lambda store, **kw: DecisionProvider(store,
        client_factory=lambda **client_kw: httpx.AsyncClient(transport=httpx.MockTransport(transport), **client_kw), **kw))
    monkeypatch.setattr(runner, "live_process_boundary", lambda: None)
    monkeypatch.setattr(runner, "read_test_key", lambda: (key_reads.append("mock-only"), "synthetic-key")[1])
    monkeypatch.setenv("ODYSSEUS_JEV_HOST_LAUNCHER", "reviewed-v1")
    config = approved_config()
    first = await runner.run(tmp_path, live=True, config=config)
    assert len(calls) == 2 and first["accounting"]["charged_or_held"] == 60
    assert first["admission"]["kind"] == "root-reviewed-typed-choice/v1"
    assert "not a published vendor maximum" in first["admission"]["basis"]
    changed = {**config, "review_note": "same frozen source and suite; no repeat"}
    cached = await runner.run(tmp_path, live=True, config=changed)
    assert len(calls) == 2 and cached["totals"]["cached"] == 2
    assert cached["quality_denominators"]["evaluated_cases_including_cached"] == 2
    assert len(cached["accounting"]["versions"]) == 2
    assert cached["accounting"]["charged_or_held"] == 60


@pytest.mark.asyncio
async def test_oversized_frozen_case_is_refused_before_live_key_reader(tmp_path, monkeypatch):
    from copy import deepcopy
    suite = deepcopy(runner.load_suite())
    suite["cases"][-1]["state"]["untrusted_text"] = "x" * 9000
    monkeypatch.setattr(runner, "load_suite", lambda: suite)
    monkeypatch.setattr(runner, "read_test_key", lambda: pytest.fail("profile failure reached key reader"))
    monkeypatch.setenv("ODYSSEUS_JEV_HOST_LAUNCHER", "reviewed-v1")
    with pytest.raises(DecisionError, match="profile_refused"):
        await runner.run(tmp_path, live=True, config=approved_config())


def test_suite_has_progressive_coverage_and_real_production_request_factories():
    suite = runner.load_suite()
    assert {split: sum(case["split"] == split for case in suite["cases"])
            for split in ("smoke", "small", "development")} == {"smoke": 2, "small": 24, "development": 80}
    assert 80 <= sum(case["split"] == "heldout" for case in suite["cases"]) <= 192
    for case in suite["cases"]:
        request = runner.request_for(case)
        questions = request.questions()
        for name, question in questions.items():
            assert runner.answer_gold(case, name) in question["criteria"]
        if case["split"] == "smoke":
            assert len(questions) == 1
        if case["feature"] == "approach":
            assert set(questions) == {"approach", "readiness"}
        if case["feature"] == "diagnostics":
            assert set(questions) == {"online_search", "fresh_information", "project_context"}
        if case["feature"] == "safety":
            assert set(request.choices) == {"allow", "deny", "review"}


@pytest.mark.asyncio
async def test_material_protocol_failure_charges_usage_and_durably_halts(tmp_path, monkeypatch):
    import httpx
    original_transport = httpx.MockTransport
    def malformed(handler):
        def transport(request):
            raw = handler(request).json()
            raw["model"] = "wrong-model"
            return httpx.Response(200, json=raw)
        return original_transport(transport)
    monkeypatch.setattr(httpx, "MockTransport", malformed)
    report = await runner.run(tmp_path, wall_seconds=30)
    assert report["totals"]["actual_dispatches"] == 1
    assert report["accounting"]["charged_or_held"] == 30
    assert report["accounting"]["halted"]
    assert report["cases"][0]["stop_classification"] == "material_protocol_identity_lifecycle_or_capture_failure"
    ledger_bytes = (tmp_path / "budget/campaign.sqlite3").read_bytes()
    with pytest.raises(DecisionError):
        await runner.run(tmp_path, wall_seconds=30)
    assert (tmp_path / "budget/campaign.sqlite3").read_bytes() == ledger_bytes


@pytest.mark.parametrize("mutation", ["question", "choice", "primary", "not_mapping"])
def test_expected_answers_rejects_unknown_question_choice_or_inconsistent_primary(tmp_path, mutation):
    from copy import deepcopy
    suite = deepcopy(runner.load_suite())
    case = next(case for case in suite["cases"] if case["feature"] == "diagnostics")
    case["expected_answers"] = {"online_search": case["gold"], "fresh_information": case["gold"], "project_context": "yes"}
    if mutation == "question": case["expected_answers"]["invented"] = "yes"
    if mutation == "choice": case["expected_answers"]["project_context"] = "invented"
    if mutation == "primary": case["expected_answers"]["online_search"] = "unclear" if case["gold"] != "unclear" else "yes"
    if mutation == "not_mapping": case["expected_answers"] = []
    path = tmp_path / "suite.json"
    path.write_bytes(runner.encode(suite))
    with pytest.raises(DecisionError, match="invalid_expected_answers"):
        runner.load_suite(path)


def test_expected_answers_independent_fields_remain_outside_inference(tmp_path):
    from copy import deepcopy
    suite = deepcopy(runner.load_suite())
    case = next(case for case in suite["cases"] if case["feature"] == "diagnostics")
    before = runner.encode({"state": runner.request_for(case).state, "questions": runner.request_for(case).questions()})
    case["expected_answers"] = {"online_search": case["gold"], "fresh_information": "unclear", "project_context": "yes"}
    path = tmp_path / "suite.json"
    path.write_bytes(runner.encode(suite))
    checked = next(item for item in runner.load_suite(path)["cases"] if item["id"] == case["id"])
    assert runner.answer_gold(checked, "fresh_information") == "unclear"
    assert runner.answer_gold(checked, "project_context") == "yes"
    assert runner.encode({"state": runner.request_for(checked).state, "questions": runner.request_for(checked).questions()}) == before
