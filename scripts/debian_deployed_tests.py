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
PROFILES = {"core", "extended", "provider", "native", "receipts"}


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
        live.require(isinstance(v, dict) and set(v) <= {"name", "endpoint_id", "model", "reported_model", "expected_context", "content_probe", "native_delay_seconds"}, "invalid_variant")
        for key in ("name", "endpoint_id", "model", *(["reported_model"] if "reported_model" in v else [])):
            value = v.get(key)
            live.require(isinstance(value, str) and 0 < len(value) <= 200 and all(ord(c) >= 32 for c in value), "invalid_variant_identifier")
        live.require(v["name"] not in names and re.fullmatch(r"[a-z0-9_-]+", v["name"]), "duplicate_or_invalid_variant_name")
        live.require(type(v.get("content_probe", False)) is bool, "invalid_content_probe")
        live.require(type(v.get("native_delay_seconds", 0)) is int and 0 <= v.get("native_delay_seconds", 0) <= 60, "invalid_native_delay")
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


def _provider_write_digest(event):
    import hashlib
    try:
        value = json.loads(event["action_receipt"]["provider_arguments"])["content"]
        return hashlib.sha256(value.encode("utf-8")).hexdigest() if isinstance(value, str) else None
    except (KeyError, TypeError, ValueError):
        return None


def verify_content_probe(events, marker, command, model, endpoint):
    # A final claim alone cannot prove the exact byte write/read/delete cycle.
    live.require(not any(e.get("type") in {"error", "intent_nudge_exhausted", "approval_required", "tool_approval_required"}
                         or e.get("blocked") or e.get("failed") or e.get("error") for e in events), "content_probe_error")
    text = "".join(str(e.get("delta", "")) + str(e.get("response", "")) + str(e.get("content", "")) for e in events)
    live.require(marker in text and "[Agent guard:" not in text, "content_probe_final_missing")
    metrics = [e.get("data", e) for e in events if e.get("type") == "metrics"]
    models = [v for m in metrics for v in m.get("round_models", [m.get("model")])]
    endpoints = [v for m in metrics for v in m.get("round_endpoint_ids", [m.get("endpoint_id")])]
    live.require(models and all(v == model for v in models), "actual_model_mismatch")
    live.require(endpoints and all(v == endpoint for v in endpoints), "actual_endpoint_mismatch")
    starts = [e for e in events if e.get("type") == "tool_start"]
    outputs = [e for e in events if e.get("type") == "tool_output"]
    live.require([e.get("tool") for e in starts] == ["write_file", "read_file", "bash"],
                 "content_probe_tool_sequence_mismatch")
    live.require(len(outputs) == 3 and not any(e.get("blocked") or e.get("failed") or e.get("error") for e in outputs),
                 "content_probe_failed_receipt")
    live.require(starts[-1].get("command") == command, "content_probe_command_mismatch")
    live.require(outputs[-1].get("tool") == "bash" and outputs[-1].get("execution_attempted") is True
                 and outputs[-1].get("exit_code") == 0 and str(outputs[-1].get("output", "")).strip() == marker,
                 "content_probe_byte_or_cleanup_mismatch")


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
        owner = self.client.request("/api/chat-settings")
        with self.session() as sid:
            path = "/api/session/" + sid + "/automation"
            before = self.client.request(path)
            pair = {"endpoint_id": self.args.endpoint_id, "model": self.args.model}
            missing = {"endpoint_id": "missing-" + uuid.uuid4().hex, "model": self.args.model}
            self.client.request(path, {"policy_revision": before["policy_revision"], "changes": {
                "route_mode": "auto", "auto_categories": {"complex_tasks": pair, "straightforward_tasks": missing}}}, method="PUT", expected=422)
            live.require(self.client.request(path) == before, "rejected_assignment_changed_chat")
        live.require(self.client.request("/api/chat-settings") == owner, "rejected_assignment_changed_defaults")
        return {"unavailable_assignment_rejected": True}

    def categories_persist(self):
        pair = {"endpoint_id": self.args.endpoint_id, "model": self.args.model}
        categories = {"complex_tasks": pair, "straightforward_tasks": pair}
        with self.session() as first:
            path = "/api/session/" + first + "/automation"
            old = self.client.request(path)
            try:
                self.client.request(path, {"policy_revision": old["policy_revision"], "changes": {
                    "route_mode": "auto", "auto_categories": categories}}, method="PUT")
                with self.session() as second:
                    self.client.request("/api/auth/logout", {})
                    self.authenticate()
                    policy = self.client.request("/api/session/" + second + "/automation")
                    live.require(policy["route_mode"] == "auto" and policy["auto_categories"] == categories, "new_chat_lost_auto_categories")
            finally:
                now = self.client.request(path)
                self.client.request(path, {"policy_revision": now["policy_revision"], "changes": {
                    "route_mode": old["route_mode"], "auto_categories": old["auto_categories"]}}, method="PUT")
        return {"new_chat_inherits_auto_categories": True, "same_model_in_both_categories": True, "restored_fixture_defaults": True}

    def decision_separation(self):
        data = self.client.request("/api/chat-settings/models")
        chats = {x["id"] for x in data["endpoints"]}
        decisions = {x["id"] for x in data["decision_endpoints"]}
        decision_only = {x["id"] for x in data["decision_endpoints"] if x.get("model_type") == "decision"}
        live.require(decision_only and not chats.intersection(decision_only) and all(x.get("model_type") == "llm" for x in data["endpoints"]), "decision_only_endpoint_in_chat_picker")
        return {"separate_chat_and_decision_endpoints": True}

    def content_probe(self):
        import hashlib
        marker = self.marker + "-content"
        name = marker + ".txt"
        content = "Nested fence\r\n```python\r\nprint('λ')\r\n```\r\n" + marker + "\r\n"
        digest = hashlib.sha256(content.encode()).hexdigest()
        command = "printf '%s  %s\\n' '" + digest + "' '" + name + "' | sha256sum --check --status && rm -- " + name + " && test ! -e " + name + " && printf '%s' '" + marker + "'"
        prompt = ("Use exactly three tools in order: write_file, read_file, bash. "
                  "Write this JSON string decoded as exact UTF-8 bytes to " + name + ": " + json.dumps(content) +
                  ". Read that same file. Then run this exact bash command once: " + command +
                  ". Reply with " + marker + ". Do not modify any other file.")
        with self.session(manual=True) as sid:
            with self.client.response("/api/chat_stream", {"session": sid, "message": prompt,
                    "mode": "agent", "allow_bash": "true", "allow_web_search": "false",
                    "use_web": "false", "use_rag": "false", "selected_endpoint_id": self.args.endpoint_id,
                    "selected_model": self.args.model}, form=True) as response:
                events = live.read_events(response)
            self.case_evidence = {"tools": [e.get("tool") for e in events if e.get("type") == "tool_start"],
                "content_sha256": digest,
                "tool_receipts": [{k: e.get(k) for k in ("tool", "exit_code", "execution_attempted", "blocked")}
                                  for e in events if e.get("type") == "tool_output"],
                "authorization_denied": any(t.get("stage") == "authorization_denied"
                    for e in events for t in (e.get("action_receipt") or {}).get("transitions", [])),
                "provider_write_matches_expected": [
                    _provider_write_digest(e) == digest for e in events
                    if e.get("type") == "tool_output" and e.get("tool") == "write_file"]}
            verify_content_probe(events, marker, command,
                                 self.args.reported_model or self.args.model, self.args.endpoint_id)
        return {"exact_utf8_bytes": True, "nested_fences": True, "crlf": True,
                "content_sha256": digest, "own_scratch_deleted": True}

    def run(self):
        super().run()
        if self.args.suite == "native" and getattr(self.args, "content_probe", False):
            if not any(r["id"] == "FS-01" and r["status"] == "passed" for r in self.results):
                self.blocked("FS-04", "native_preflight_failed")
                return
            try:
                self.authenticate()
                self.check("FS-04", self.content_probe)
            finally:
                if self.client.cookies:
                    self.check("AU-02", self.logout)

    def run_extended(self):
        try:
            if not self.check("RT-01", self.runtime):
                return
            self.check("AP-03", self.fork_policy)
            self.check("AP-04", self.saved_modes)
            self.check("MD-03", self.categories_persist)
            self.check("MD-06", self.categories_persist)
            self.check("MD-07", self.invalid_assignment)
            self.check("MD-16", self.decision_separation)
        finally:
            if self.client.cookies:
                self.check("AU-02", self.logout)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=ROOT / "docs/testing/fork-regression/cases.json")
    parser.add_argument("--login-file")
    parser.add_argument("--socket")
    parser.add_argument("--origin", default="http://127.0.0.1:7824")
    parser.add_argument("--profiles", default="core,extended")
    parser.add_argument("--isolated-state", action="store_true", help="Confirm this target is a disposable deployment/state copy")
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--receipt", type=Path, action="append", default=[], help="Additional exact-source browser/smoke results in the documented receipt schema")
    parser.add_argument("--previous-report", type=Path, help="Aggregate prior exact-source observations without replaying actions")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    profiles = set(args.profiles.split(","))
    live.require(profiles and profiles <= PROFILES, "invalid_profiles")
    live.local_origin(args.origin)
    live.require("extended" not in profiles or args.isolated_state, "extended_requires_disposable_state")
    live.require(not (profiles & {"provider", "native"}) or (args.allow_paid and args.isolated_state), "paid_profiles_require_explicit_disposable_target")
    plan = read_plan(args.plan)
    catalog = json.loads(args.catalog.read_text())
    live.require(profiles == {"receipts"} or args.login_file, "login_file_required")
    login = live.load_login(args.login_file) if profiles != {"receipts"} else {}
    observations = []
    if args.previous_report:
        previous = json.loads(args.previous_report.read_text())
        live.require(previous.get("schema") == "odysseus-debian-test-results/v1" and previous.get("expected_source") == plan["expected_source"], "stale_or_invalid_previous_report")
        observations.extend(row for case in previous["cases"] for row in case["runs"])
    for index, variant in enumerate(plan["variants"]):
        for profile in ("core", "extended", "provider", "native"):
            if profile not in profiles or (profile == "provider" and "native" in profiles):
                continue
            if index and profile in {"core", "extended"}:
                continue
            client = live.Client(args.origin, args.socket)
            options = SimpleNamespace(suite="crud" if profile == "core" else profile,
                expected_source=plan["expected_source"], endpoint_id=variant["endpoint_id"],
                model=variant["model"], reported_model=variant.get("reported_model"), content_probe=variant.get("content_probe", False), native_delay_seconds=variant.get("native_delay_seconds", 0), expected_context=variant.get("expected_context"), manual_template=None)
            suite = Extended(client, login, options)
            if profile == "extended":
                suite.run_extended()
            else:
                suite.run()
            observations.extend({**r, "fixture_marker": suite.marker, "variant": variant["name"], "profile": profile} for r in suite.results)
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
