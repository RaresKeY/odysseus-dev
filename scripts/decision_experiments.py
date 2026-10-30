#!/usr/bin/env python3
"""Offline decision-contract replay; this program has no live/provider mode."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx

from src.decisions import (DecisionContext, DecisionError, DecisionProvider, DecisionRequest,
                           MemoryAttemptStore, ResolvedDecisionEndpoint)
from src.decisions.contracts import SCHEMA_VERSION, decode, encode

FIXTURE_SCHEMA = "odysseus-decision-fixtures/v1"
REPORT_SCHEMA = "odysseus-decision-experiment/v1"
DEFAULT_FIXTURES = ROOT / "tests/fixtures/decision_port/synthetic-v1.json"
IMPLEMENTATION_FILES = (
    "src/decisions/contracts.py", "src/decisions/config.py", "src/decisions/provider.py",
    "src/decisions/store.py", "src/approach_routing.py", "scripts/decision_experiments.py",
    "src/automation_task_state.py", "src/automation_catalog.py",
)


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def load_fixtures(path: Path) -> dict:
    if path.stat().st_size > 1_000_000:
        raise ValueError("Fixture set exceeds offline bound")
    value = decode(path.read_bytes())
    cases = value.get("cases")
    if (set(value) != {"schema", "version", "label_provenance", "cases"}
            or value["schema"] != FIXTURE_SCHEMA or not isinstance(value["version"], str)
            or not isinstance(value["label_provenance"], str)
            or not isinstance(cases, list) or not 1 <= len(cases) <= 256):
        raise ValueError("Invalid fixture-set contract")
    seen = set()
    for case in cases:
        required = {"id", "source_group", "purpose", "state", "choices", "expected_choice", "expected_deny", "response"}
        if (not isinstance(case, dict) or set(case) - required - {"transport_error"}
                or not required.issubset(case) or not isinstance(case["id"], str)
                or not case["id"] or len(case["id"]) > 100 or case["id"] in seen
                or not isinstance(case["source_group"], str)
                or not isinstance(case["purpose"], str)
                or case["purpose"] not in {"approval", "approach", "browser"}
                or type(case["expected_deny"]) is not bool
                or case.get("transport_error") is not None and case.get("transport_error") != "timeout"):
            raise ValueError("Invalid fixture case")
        DecisionRequest(case["purpose"], case["state"], case["choices"]).questions()
        if case["expected_choice"] not in case["choices"]:
            raise ValueError("Expected choice is outside the frozen labels")
        seen.add(case["id"])
    return value


async def replay(fixtures: dict) -> dict:
    """Replay through the real bounded provider using an in-memory HTTP transport.

    Every request resolves to a synthetic descriptor and MockTransport; no
    configuration, credentials, URL discovery or actual task/browser action is
    consumed. Stored probabilities/usage are authored fixtures, not new model
    measurements. Random attempt UUIDs are deliberately omitted from reports.
    """
    cases, totals, confusion = [], Counter(), {}
    for case in fixtures["cases"]:
        request = DecisionRequest(case["purpose"], case["state"], case["choices"])
        store, calls = MemoryAttemptStore(), []

        def transport(http_request):
            calls.append(http_request)
            if case.get("transport_error"):
                raise httpx.ReadTimeout("synthetic timeout")
            response = case["response"]
            return httpx.Response(200, content=encode(response) if isinstance(response, dict) else response.encode())

        endpoint = ResolvedDecisionEndpoint("offline-fixture", "synthetic-decider", "systemone",
                                            "https://offline.invalid/v1/systemone", {})
        provider = DecisionProvider(store, resolver=lambda *_: endpoint,
                                    client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(transport), **kw))
        choice, error, status, uncertain, usage = None, None, "invalid", False, None
        try:
            result = await provider.evaluate(request, DecisionContext("synthetic-owner", case["id"], case["id"]),
                                             config={"endpoint_id": "offline-fixture", "model": "synthetic-decider", "protocol": "systemone"})
            choice, status, usage = result.choice, "valid", result.usage
            threshold = .95 if case["purpose"] == "approval" else .80
            uncertain = result.confidence < threshold or result.probabilities[result.choice] < threshold
        except DecisionError as failure:
            error = failure.code
        record = next(iter(store.records.values()), None)
        if record is not None:
            usage = record["usage"]
            if record["status"] == "unresolved":
                status = "unresolved"
        prediction = choice if status == "valid" else status
        matrix = confusion.setdefault(case["purpose"], {})
        matrix.setdefault(case["expected_choice"], {})[prediction] = matrix.get(case["expected_choice"], {}).get(prediction, 0) + 1
        unsafe = case["purpose"] == "approval" and case["expected_deny"] and choice == "allow" and not uncertain
        row = {"fixture_id": case["id"], "source_group": case["source_group"], "purpose": case["purpose"],
               "request_sha256": digest({"model": "synthetic-decider", "state": request.state, "questions": request.questions()}),
               "response_sha256": digest(case["response"]), "status": status,
               "expected_choice": case["expected_choice"], "choice": choice,
               "label_agreement": status == "valid" and choice == case["expected_choice"],
               "uncertain": uncertain, "unsafe_allow": unsafe,
               "failure_kind": error, "attempts": len(calls), "usage": usage,
               "usage_source": "authored_fixture" if usage is not None else "unknown",
               "retained_reservation": record["reservation"] if record and status == "unresolved" else None,
               "task_success": None, "task_success_reason": "No classified action or browser is executed in replay."}
        cases.append(row)
        totals.update(attempted=1, valid=int(status == "valid"), invalid=int(status == "invalid"),
                      unresolved=int(status == "unresolved"), uncertain=int(uncertain),
                      label_agreement=int(row["label_agreement"]), unsafe_allows=int(unsafe),
                      safety_cases=int(case["purpose"] == "approval"),
                      safety_negative_cases=int(case["purpose"] == "approval" and case["expected_deny"]))
    return {
        "schema": REPORT_SCHEMA, "mode": "offline_contract_replay", "evidence_kind": "authored_synthetic_fixture",
        "fixture_version": fixtures["version"], "fixture_sha256": digest(fixtures),
        "label_provenance": fixtures["label_provenance"], "decision_schema": SCHEMA_VERSION,
        "source_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in IMPLEMENTATION_FILES},
        "policy_versions": {"approval": "odysseus-approval/v1", "approach": "odysseus-approach/v1", "browser": "offline-synthetic/v1"},
        "thresholds": {"approval": .95, "approach": .80, "browser": .80},
        "provider": {"model": "synthetic-decider", "protocol": "systemone", "distribution_source": "authored_fixture"},
        "runtime": {"python": ".".join(map(str, sys.version_info[:3])), "concurrency": 1,
                    "order": "frozen_fixture_order", "inference": False, "network": False},
        "totals": dict(totals), "confusion_matrices": confusion, "cases": cases,
        "usage": {"live_calls": 0, "actual_provider_tokens": None,
                  "actual_provider_tokens_reason": "Offline mock transport; saved usage is synthetic.",
                  "unknown_fixture_attempts": totals["unresolved"]},
        "latency": {"median_ms": None, "p95_ms": None, "reason": "Replay does not measure provider latency."},
        "downstream_task_success": None,
        "limitations": ["Author-assigned correlated labels; not held-out safety or model-quality proof.",
                        "Invalid and unresolved cases remain in attempted denominators.",
                        "Routing label agreement is separate from executor/task quality.",
                        "No browser actions, paid provider calls, credentials or classified actions execute."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    fixtures = load_fixtures(args.fixtures)
    report = asyncio.run(replay(fixtures))
    text = json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
