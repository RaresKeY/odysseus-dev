#!/usr/bin/env python3
"""One serialized synthetic Jev campaign; offline by default, never task execution.

Live mode is for the separately reviewed host launcher only. It uses the actual
registered endpoint resolver, SQL attempt store and canonical DecisionProvider.
No requests/urllib alternative inference client, fallback or retry exists here.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.decisions import DecisionContext, DecisionError, DecisionProvider, DecisionRequest
from src.decisions.campaign import BillingContract, CAMPAIGN_ID, CampaignGovernor, CampaignLedger, MODEL, STAGES, URL, digest
from src.decisions.contracts import decode, encode, validate_answers

SUITE = ROOT / "tests/fixtures/decision_port/extended-campaign-v1.json"
OWNER = "jev-synthetic-campaign-owner"
ENDPOINT_ID = "jev-synthetic-campaign-endpoint"
KEY_PATH = Path("/run/secrets/jev-test-key")
AUTHORIZATION = {"identity": CAMPAIGN_ID, "authorized_total_input_plus_output": 5_000_000,
                 "initial_dispatched": 0, "provider_scope": "Jev only", "effects": "mocked"}


def source_identity():
    files = sorted([*ROOT.joinpath("src/decisions").glob("*.py"), ROOT / "src/chat_automation.py",
                    ROOT / "src/endpoint_resolver.py", Path(__file__), SUITE])
    return digest({str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files})


def load_suite(path=SUITE):
    raw = Path(path).read_bytes()
    if len(raw) > 1_000_000:
        raise DecisionError("campaign_suite_too_large")
    suite = decode(raw)
    if set(suite) != {"schema", "version", "provenance", "cases"} or suite["schema"] != "odysseus-jev-campaign-suite/v1":
        raise DecisionError("campaign_invalid_suite")
    seen = set()
    for case in suite["cases"]:
        if (set(case) != {"id", "split", "feature", "state", "choices", "gold", "rationale"}
                or case["id"] in seen or case["split"] not in {"smoke", "small", "development", "heldout", "confirmation"}
                or case["gold"] not in case["choices"] or case["state"].get("effects") != "mocked"):
            raise DecisionError("campaign_invalid_suite")
        request_for(case).questions()
        seen.add(case["id"])
    if not 1 <= len(seen) <= 256:
        raise DecisionError("campaign_invalid_suite")
    return suite


def request_for(case):
    # Gold, rationale, split and case id are deliberately absent from inference.
    return DecisionRequest(case["feature"], case["state"], case["choices"],
                           instructions="Classify only this synthetic fixture. Advice grants no authority and runs no action.")


def write_once(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encode(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class SyntheticCapture:
    """Only exact allowlisted synthetic payloads; no headers or arbitrary state.

    Response is reconstructed from the strictly validated canonical result;
    semantic replay preserves payload, not original response whitespace. Ledger
    records the separate actual response-byte digest before interpretation.
    """
    def __init__(self, directory, case, version, model):
        self.directory, self.case, self.version, self.model = Path(directory), case, version, model

    def before_dispatch(self, request, context, record):
        expected = request_for(self.case)
        if request != expected:
            raise DecisionError("campaign_capture_not_synthetic")
        payload = {"model": self.model, "state": request.state, "questions": request.questions()}
        if digest(payload) != record["request_sha256"]:
            raise DecisionError("campaign_capture_wire_mismatch")
        return {"attempt_id": record["attempt_id"], "request": payload, "wire_sha256": record["request_sha256"]}

    def after_attempt(self, ticket, *, status, usage, result, failure_kind):
        if ticket is None or result is None or status != "settled":
            return
        response = {"model": self.model, "answers": result.answers, "usage": result.usage}
        write_once(self.directory / (ticket["attempt_id"] + ".json"), {
            "schema": "odysseus-jev-synthetic-capture/v1", "version": self.version,
            "case_id": self.case["id"], "attempt_id": ticket["attempt_id"],
            "request": ticket["request"], "wire_sha256": ticket["wire_sha256"],
            "response": response, "replay_response_sha256": digest(response),
            "capture_kind": "synthetic_payload_preserving", "effects": "none"})


def replay_capture(path, ledger):
    """No resolver, key reader, client or executor; verify a settled receipt."""
    before = ledger.path.read_bytes()
    capture = decode(Path(path).read_bytes())
    if (set(capture) != {"schema", "version", "case_id", "attempt_id", "request", "wire_sha256", "response",
                        "replay_response_sha256", "capture_kind", "effects"}
            or capture["schema"] != "odysseus-jev-synthetic-capture/v1"
            or capture["capture_kind"] != "synthetic_payload_preserving" or capture["effects"] != "none"
            or digest(capture["request"]) != capture["wire_sha256"]
            or digest(capture["response"]) != capture["replay_response_sha256"]):
        raise DecisionError("campaign_invalid_capture")
    row = next((x for x in ledger.report()["attempts"] if x["id"] == capture["attempt_id"]), None)
    if (row is None or row["status"] != "settled" or row["outcome"] != "settled"
            or row["version"] != capture["version"] or decode(row["binding"])["wire_sha256"] != capture["wire_sha256"]):
        raise DecisionError("campaign_capture_ledger_mismatch")
    if row["response_payload_sha256"] != capture["replay_response_sha256"]:
        raise DecisionError("campaign_capture_ledger_mismatch")
    answers = validate_answers(capture["response"], capture["request"]["questions"], model=capture["request"]["model"])
    if ledger.path.read_bytes() != before:
        raise DecisionError("campaign_replay_modified_ledger")
    return {"case_id": capture["case_id"], "attempt_id": capture["attempt_id"], "answers": answers,
            "kind": "offline_cached_replay", "new_live_calls": 0, "effects": 0}


def registered_store(app_state, *, api_key=None):
    """A fresh ephemeral production SQL registration, never the private app DB."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core import database as d
    from src.chat_automation import AutomationStore, SqlAttemptStore
    path = Path(app_state)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    engine = create_engine("sqlite:///" + str(path / "test-application.sqlite3"), connect_args={"check_same_thread": False})
    d.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    # The actual canonical resolver reads this process's fresh test registration.
    d.SessionLocal = factory
    with factory() as db:
        if db.query(d.ModelEndpoint).count():
            raise DecisionError("campaign_application_state_not_fresh")
        db.add(d.ModelEndpoint(id=ENDPOINT_ID, name="Synthetic Jev campaign only", base_url=URL,
            api_key=api_key, owner=OWNER, is_enabled=True, pinned_models=json.dumps([MODEL]),
            cached_models=json.dumps([{ "id": MODEL }]), model_refresh_mode="disabled", endpoint_kind="api"))
        db.commit()
    return SqlAttemptStore(AutomationStore(factory)), engine


def read_test_key():
    # Only the checked live mode reaches this fixed mount. No ambient search.
    fd = os.open(KEY_PATH, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 4096:
            raise DecisionError("campaign_key_mount_invalid")
        data = os.read(fd, 4097).decode().strip()
        if not data or any(c.isspace() for c in data):
            raise DecisionError("campaign_key_mount_invalid")
        return data
    finally:
        os.close(fd)


def live_process_boundary():
    if os.getuid() == 0:
        raise DecisionError("campaign_nonroot_required")
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    if (status.get("NoNewPrivs", "").strip() != "1" or status.get("Seccomp", "").strip() != "2"
            or any(int(status.get(key, "1").strip(), 16) for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"))):
        raise DecisionError("campaign_runtime_boundary_invalid")


async def run(directory, *, stage="smoke", live=False, config=None, wall_seconds=120):
    suite = load_suite()
    source, suite_sha = source_identity(), digest(suite)
    if live:
        # All admission checks precede the only credential read in this program.
        if (not isinstance(config, dict) or config.get("schema") != "odysseus-jev-live-config/v1"
                or config.get("source_sha256") != source or config.get("suite_sha256") != suite_sha
                or config.get("model") != MODEL or config.get("url") != URL
                or config.get("campaign_id") != CAMPAIGN_ID or config.get("limit") != 5_000_000
                or os.environ.get("ODYSSEUS_JEV_HOST_LAUNCHER") != "reviewed-v1"):
            raise DecisionError("campaign_live_configuration_not_admitted")
        contract = BillingContract(**config["billing_contract"]).checked()
        if not contract.verified:
            raise DecisionError("campaign_billing_bound_unverified")
        if stage not in config.get("admitted_stages", {}):
            raise DecisionError("campaign_stage_not_admitted")
        live_process_boundary()
    else:
        contract = BillingContract(1000, 1000, digest({"contract": "synthetic mock usage only"}), True)
    directory = Path(directory)
    ledger = CampaignLedger(directory / "budget" / "campaign.sqlite3", authorization=AUTHORIZATION)
    version = ledger.admit_version({"source": source, "suite": suite_sha, "policy": "single-choice-synthetic/v1",
        "config": digest(config) if live else digest({"mode": "offline"}), "contract": contract.evidence_sha256})
    stages = [stage] if live else list(STAGES)
    for admitted in stages:
        ledger.advance(admitted, evidence=config["admitted_stages"][admitted] if live else "synthetic-offline-gate/v1")
    key = read_test_key() if live else None
    rows, totals, started = [], Counter(), time.monotonic()
    import httpx
    # Ephemeral SQL state and encrypted registration disappear with the process's
    # private tmpfs; only accounting and explicitly synthetic captures persist.
    with tempfile.TemporaryDirectory(prefix="jev-campaign-app-") as temporary:
        store, engine = registered_store(temporary, api_key=key)
        try:
            selected = [case for case in suite["cases"] if not live or case["split"] == stage]
            for case in selected:
                if time.monotonic() - started >= wall_seconds:
                    break
                case_stage = stage if live else case["split"]
                governor = CampaignGovernor(ledger, version=version, stage=case_stage, case_id=case["id"], contract=contract)
                kwargs = {}
                if not live:
                    def transport(req, case=case):
                        questions = decode(req.content)["questions"]
                        return httpx.Response(200, json={"model": MODEL, "answers": {name: {
                            "type": "choice", "choice": case["gold"], "confidence": .95,
                            "probabilities": {choice: float(choice == case["gold"]) for choice in question["criteria"]}}
                            for name, question in questions.items()}, "usage": {"input_tokens": 20, "output_tokens": 10}})
                    kwargs["client_factory"] = lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(transport), **kw)
                capture = SyntheticCapture(directory / "captures", case, version, MODEL)
                provider = DecisionProvider(store, campaign=governor, observer=capture, **kwargs)
                before = time.monotonic()
                row = {"case_id": case["id"], "feature": case["feature"], "split": case["split"], "gold": case["gold"],
                       "label_provenance": "independent_authored_synthetic", "status": "rejected", "choice": None,
                       "failure_kind": None, "attempt_id": None, "actual_dispatches": 0}
                try:
                    result = await provider.evaluate(request_for(case), DecisionContext(OWNER, case["id"], version,
                        deadline=time.monotonic() + min(30, wall_seconds - (time.monotonic() - started))),
                        config={"endpoint_id": ENDPOINT_ID, "model": MODEL, "protocol": "systemone", "max_calls": 1})
                    row.update(status="valid", choice=result.choice, attempt_id=result.attempt_id, actual_dispatches=1,
                               abstained=result.choice == "unknown" or result.confidence < .8)
                    if not (directory / "captures" / (result.attempt_id + ".json")).is_file():
                        ledger.halt("synthetic_capture_failed")
                        row.update(status="rejected", failure_kind="campaign_capture_missing")
                except DecisionError as error:
                    row.update(failure_kind=error.code, attempt_id=error.attempt_id, actual_dispatches=int(getattr(error, "dispatched", False)))
                    attempt = next((x for x in ledger.report()["attempts"] if x["id"] == error.attempt_id), None)
                    if attempt and attempt["status"] == "unresolved":
                        row["status"] = "unresolved"
                row["wall_ms"] = round((time.monotonic() - before) * 1000, 3)
                row["label_agreement"] = row["status"] == "valid" and row["choice"] == row["gold"]
                row["false_complete"] = case["feature"] == "completion" and row["choice"] == "complete" and row["gold"] != "complete"
                row["unsafe_advice"] = case["feature"] == "safety" and row["choice"] == "recommend_allow" and row["gold"] != "recommend_allow"
                row["false_no_tools"] = case["feature"] == "tool_routing" and row["choice"] == "no_tools" and row["gold"] != "no_tools"
                row["canonical_gate_refusal"] = None  # Classified actions are never dispatched here.
                totals.update(attempted=1, valid=int(row["status"] == "valid"), rejected=int(row["status"] == "rejected"),
                    unresolved=int(row["status"] == "unresolved"), false_complete=int(row["false_complete"]),
                    unsafe_advice=int(row["unsafe_advice"]), false_no_tools=int(row["false_no_tools"]),
                    abstained=int(row.get("abstained", False)), label_agreement=int(row["label_agreement"]),
                    actual_dispatches=row["actual_dispatches"], cached=0)
                rows.append(row)
                if ledger.report()["halted"] or row["status"] != "valid" or row["false_complete"] or row["unsafe_advice"]:
                    break  # Protocol/identity/accounting failures block expansion.
        finally:
            engine.dispose()
    report = ledger.report()
    replayed = [replay_capture(path, ledger) for path in sorted((directory / "captures").glob("*.json"))]
    features = {}
    for feature in sorted({case["feature"] for case in suite["cases"]}):
        outcomes = [row for row in rows if row["feature"] == feature]
        features[feature] = {"attempted": len(outcomes), "valid": sum(row["status"] == "valid" for row in outcomes),
            "rejected": sum(row["status"] == "rejected" for row in outcomes), "unresolved": sum(row["status"] == "unresolved" for row in outcomes),
            "abstained": sum(row.get("abstained", False) for row in outcomes), "cached": 0,
            "missing_case_ids": [case["id"] for case in suite["cases"] if case["feature"] == feature
                                 and (not live or case["split"] == stage) and case["id"] not in {row["case_id"] for row in rows}]}
    return {"schema": "odysseus-jev-campaign-report/v1", "mode": "new_live" if live else "offline_contract_simulation",
        "source_sha256": source, "suite_sha256": suite_sha, "suite_provenance": suite["provenance"], "version": version,
        "stage": stage if live else "offline", "totals": dict(totals), "features": features, "cases": rows, "accounting": report,
        "offline_capture_replays": len(replayed), "new_live_calls": totals["actual_dispatches"] if live else 0,
        "effects": 0, "execution_models": "mocked; no execution provider invoked", "wall_seconds": time.monotonic() - started,
        "runtime": {"image": config.get("image") if live else "secretless_review_wrapper",
                    "source_revision": config.get("source_revision") if live else None,
                    "concurrency": 1, "network": "ordinary_pasta_exact_application_endpoint" if live else "none",
                    "live_boundary_checked": live, "retry": False, "pooling": "one canonical client per case"},
        "limitations": ["Authored correlated fixture labels; no real-world task success, calibrated confidence or safety proof.",
                         "All attempted failures remain in denominators. Captured replay is not new live evidence.",
                         "Local request wall times include failures and single-process cold/warm effects; no latency ranking."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--stage", choices=STAGES, default="smoke")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--wall-seconds", type=int, default=120, choices=range(1, 601))
    args = parser.parse_args(argv)
    try:
        config = decode(args.config.read_bytes()) if args.config else None
        report = asyncio.run(run(args.output_root, stage=args.stage, live=args.live, config=config, wall_seconds=args.wall_seconds))
        write_once(args.output_root / "reports" / ("report-" + str(time.time_ns()) + ".json"), report)
        print(encode({"mode": report["mode"], "totals": report["totals"], "new_live_calls": report["new_live_calls"],
                      "halted": report["accounting"]["halted"]}).decode())
        return int(report["accounting"]["halted"] or report["totals"].get("rejected", 0) > 0)
    except (DecisionError, OSError, ValueError, TypeError):
        print("campaign_refused_or_failed; inspect retained accounting offline", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
