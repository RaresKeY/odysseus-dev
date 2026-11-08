#!/usr/bin/env python3
"""Run the fork regression catalogue against an explicitly selected deployment.

Uses only the protected application socket/login. No SSH, engine access, ambient
provider keys, dependency installation, or model substitution is required.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
from types import SimpleNamespace
import uuid

_spec = importlib.util.spec_from_file_location("fork_live", Path(__file__).with_name("fork_live_tests.py"))
live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live)
ROOT = Path(__file__).resolve().parents[1]
PROFILES = {"core", "extended", "provider", "native"}


def read_plan(path):
    raw = Path(path).read_bytes()
    live.require(len(raw) <= 65536, "plan_too_large")
    plan = json.loads(raw)
    live.require(plan.get("schema") == "odysseus-debian-test-plan/v1", "invalid_plan_schema")
    live.require(bool(re.fullmatch(r"[0-9a-f]{40}", plan.get("expected_source", ""))), "invalid_source")
    variants = plan.get("variants")
    live.require(isinstance(variants, list) and 0 < len(variants) <= 8, "invalid_variants")
    names = set()
    for v in variants:
        live.require(isinstance(v, dict) and set(v) <= {"name", "endpoint_id", "model", "expected_context"}, "invalid_variant")
        for key in ("name", "endpoint_id", "model"):
            value = v.get(key)
            live.require(isinstance(value, str) and 0 < len(value) <= 200 and all(ord(c) >= 32 for c in value), "invalid_variant_identifier")
        live.require(v["name"] not in names and re.fullmatch(r"[a-z0-9_-]+", v["name"]), "duplicate_or_invalid_variant_name")
        names.add(v["name"])
    return plan


def merge_catalog(catalog, observations):
    """Live coverage remains separate from offline references and unrun procedures."""
    known = {c["id"] for c in catalog["cases"]}
    live.require(all(r["id"] in known for r in observations), "unknown_case_receipt")
    rows = []
    for case in catalog["cases"]:
        runs = [r for r in observations if r["id"] == case["id"]]
        states = {r["status"] for r in runs}
        status = next((s for s in ("failed", "blocked", "not_run", "skipped", "passed") if s in states), "not_run")
        rows.append({"id": case["id"], "title": case["title"], "profile": case["profile"],
                     "status": status, "runs": runs,
                     "reason": None if runs else "procedure_not_executed",
                     "preconditions": case["preconditions"],
                     "automated_references": case.get("automated_references", [])})
    return rows


class Extended(live.Suite):
    """Additional stateful checks run only in an explicitly disposable state copy."""
    def saved_modes(self):
        previous = self.client.request("/api/chat-settings")["last_explicit_mode"]
        with self.session() as sid:
            path = "/api/session/" + sid + "/automation"
            try:
                for mode in ("always_ask", "yolo"):
                    state = self.client.request(path)
                    self.client.request(path, {"policy_revision": state["policy_revision"], "changes": {"approval_mode": mode}}, method="PUT")
                    self.client.request("/api/auth/logout", {})
                    self.authenticate()
                    live.require(self.client.request(path)["approval_mode"] == mode, "saved_mode_changed_after_login")
            finally:
                state = self.client.request(path)
                self.client.request(path, {"policy_revision": state["policy_revision"], "changes": {"approval_mode": previous}}, method="PUT")
        return {"ask_and_yolo_retained": True}

    def fork_policy(self):
        previous = self.client.request("/api/chat-settings")["last_explicit_mode"]
        with self.session() as sid:
            path = "/api/session/" + sid + "/automation"
            child = None
            try:
                state = self.client.request(path)
                self.client.request(path, {"policy_revision": state["policy_revision"], "changes": {"approval_mode": "always_ask"}}, method="PUT")
                before = self.client.request(path)
                child = self.client.request("/api/session/" + sid + "/fork", {"keep_count": 0})["id"]
                policy = self.client.request("/api/session/" + child + "/automation")
                live.require(policy["approval_mode"] == "auto_approve" and not policy.get("elevation_grant"), "fork_inherited_approval_or_grant")
                live.require(self.client.request(path) == before, "fork_changed_source")
            finally:
                if child:
                    self.client.request("/api/session/" + child, method="DELETE")
                state = self.client.request(path)
                self.client.request(path, {"policy_revision": state["policy_revision"], "changes": {"approval_mode": previous}}, method="PUT")
        return {"fork_default": "auto_approve", "source_unchanged": True, "cleanup": True}

    def invalid_assignment(self):
        before = self.client.request("/api/chat-settings")
        mappings = json.loads(json.dumps(before["approaches"]))
        live.require(isinstance(mappings, dict) and len(mappings) == 3, "three_approach_fixture_required")
        key = next(iter(mappings))
        mappings[key] = {**mappings[key], "endpoint_id": "missing-" + uuid.uuid4().hex, "model": self.args.model}
        self.client.request("/api/chat-settings", {"revision": before["revision"], "changes": {
            "approaches": mappings}}, method="PUT", expected=422)
        live.require(self.client.request("/api/chat-settings") == before, "rejected_assignment_changed_settings")
        return {"unavailable_assignment_rejected": True}

    def categories_persist(self):
        before = self.client.request("/api/chat-settings")
        with self.session():
            self.client.request("/api/auth/logout", {})
            self.authenticate()
            live.require(self.client.request("/api/chat-settings") == before, "categories_changed_after_new_chat")
        return {"configuration_unchanged_across_new_chat_and_login": True}

    def decision_separation(self):
        data = self.client.request("/api/chat-settings/models")
        chats = {x["id"] for x in data["endpoints"]}
        decisions = {x["id"] for x in data["decision_endpoints"]}
        live.require(decisions and not chats.intersection(decisions), "decision_endpoint_in_chat_picker")
        return {"separate_chat_and_decision_endpoints": True}

    def run_extended(self):
        try:
            if not self.check("RT-01", self.runtime):
                return
            self.check("AP-03", self.fork_policy)
            self.check("AP-04", self.saved_modes)
            self.check("MD-03", self.categories_persist)
            self.check("MD-07", self.invalid_assignment)
            self.check("MD-16", self.decision_separation)
        finally:
            if self.client.cookies:
                self.check("AU-02", self.logout)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=ROOT / "docs/testing/fork-regression/cases.json")
    parser.add_argument("--login-file", required=True)
    parser.add_argument("--socket")
    parser.add_argument("--origin", default="http://127.0.0.1:7824")
    parser.add_argument("--profiles", default="core,extended")
    parser.add_argument("--isolated-state", action="store_true", help="Confirm this target is a disposable deployment/state copy")
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--receipt", type=Path, action="append", default=[], help="Additional exact-source browser/smoke results in the documented receipt schema")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    profiles = set(args.profiles.split(","))
    live.require(profiles and profiles <= PROFILES, "invalid_profiles")
    live.local_origin(args.origin)
    live.require("extended" not in profiles or args.isolated_state, "extended_requires_disposable_state")
    live.require(not (profiles & {"provider", "native"}) or (args.allow_paid and args.isolated_state), "paid_profiles_require_explicit_disposable_target")
    plan = read_plan(args.plan)
    catalog = json.loads(args.catalog.read_text())
    login = live.load_login(args.login_file)
    observations = []
    for index, variant in enumerate(plan["variants"]):
        for profile in ("core", "extended", "provider", "native"):
            if profile not in profiles or (profile == "provider" and "native" in profiles):
                continue
            if index and profile in {"core", "extended"}:
                continue
            client = live.Client(args.origin, args.socket)
            options = SimpleNamespace(suite="crud" if profile == "core" else profile,
                expected_source=plan["expected_source"], endpoint_id=variant["endpoint_id"],
                model=variant["model"], expected_context=variant.get("expected_context"), manual_template=None)
            suite = Extended(client, login, options)
            if profile == "extended":
                suite.run_extended()
            else:
                suite.run()
            observations.extend({**r, "variant": variant["name"], "profile": profile} for r in suite.results)
    # Supplemental receipts are accepted only for this exact deployed revision.
    for path in args.receipt:
        receipt = json.loads(path.read_text())
        live.require(receipt.get("expected_source") == plan["expected_source"] and receipt.get("schema") == "odysseus-debian-supplement/v1", "stale_or_invalid_receipt")
        for row in receipt["results"]:
            live.require(row.get("status") in {"passed", "failed", "blocked", "skipped", "not_run"}, "invalid_receipt_status")
            observations.append(row)
    rows = merge_catalog(catalog, observations)
    counts = dict(Counter(r["status"] for r in rows))
    report = {"schema": "odysseus-debian-test-results/v1", "time_utc": datetime.now(timezone.utc).isoformat(),
              "expected_source": plan["expected_source"], "profiles": sorted(profiles),
              "counts": counts, "status": "failed" if counts.get("failed") else "incomplete" if counts.get("not_run") or counts.get("blocked") else "passed",
              "cases": rows,
              "historical_problems": [{"id": h["id"], "cases": h["cases"], "statuses": {r["id"]: r["status"] for r in rows if r["id"] in h["cases"]}} for h in catalog["history"]]}
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    args.output.chmod(0o600)
    print(json.dumps({"status": report["status"], "counts": counts, "output": str(args.output)}))
    return 1 if counts.get("failed") else 2 if report["status"] == "incomplete" else 0


if __name__ == "__main__":
    raise SystemExit(main())
