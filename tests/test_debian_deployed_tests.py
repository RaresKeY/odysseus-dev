"""Regression coverage for deployment-plan safety and honest catalogue reports."""
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/debian_deployed_tests.py"
spec = importlib.util.spec_from_file_location("debian_suite", PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def plan():
    return {"schema": "odysseus-debian-test-plan/v1", "expected_source": "a" * 40,
            "variants": [{"name": "first", "endpoint_id": "saved-a", "model": "model-a"},
                         {"name": "second", "endpoint_id": "saved-b", "model": "model-b"}]}


def catalog():
    return {"cases": [{"id": key, "title": key, "profile": "live-api", "preconditions": "test target"}
                      for key in ("RT-01", "CH-01", "AP-03")], "history": []}


def test_report_preserves_failed_variants_and_unexecuted_procedures():
    rows = m.merge_catalog(catalog(), [{"id": "CH-01", "status": "passed", "variant": "one"},
                                       {"id": "CH-01", "status": "failed", "variant": "two"}])
    assert {r["id"]: r["status"] for r in rows} == {"RT-01": "not_run", "CH-01": "failed", "AP-03": "not_run"}
    assert len(rows[1]["runs"]) == 2
    with pytest.raises(m.live.CheckFailure, match="unknown_case_receipt"):
        m.merge_catalog(catalog(), [{"id": "fake", "status": "passed"}])


def test_invalid_or_duplicate_variants_fail_before_login(tmp_path):
    value = plan();value["variants"][1]["name"] = "first"
    p = tmp_path / "plan.json";p.write_text(json.dumps(value))
    with pytest.raises(m.live.CheckFailure, match="duplicate_or_invalid"):
        m.read_plan(p)
    value["variants"][1]["name"] = "second";value["expected_source"] = "latest"
    p.write_text(json.dumps(value))
    with pytest.raises(m.live.CheckFailure, match="invalid_source"):
        m.read_plan(p)


def test_all_requested_profiles_and_model_variants_execute(tmp_path, monkeypatch):
    p = tmp_path / "plan.json";p.write_text(json.dumps(plan()))
    c = tmp_path / "cases.json";c.write_text(json.dumps(catalog()))
    calls = []
    class Suite:
        def __init__(self, client, login, options):
            self.options = options;self.results = []
        def run(self):
            calls.append((self.options.suite, self.options.model))
            self.results.append({"id": "CH-01" if self.options.suite == "native" else "RT-01", "status": "passed"})
        def run_extended(self):
            calls.append(("extended", self.options.model))
            self.results.append({"id": "AP-03", "status": "passed"})
    monkeypatch.setattr(m, "Extended", Suite)
    monkeypatch.setattr(m.live, "load_login", lambda p: {})
    out = tmp_path / "report.json"
    code = m.main(["--plan", str(p), "--catalog", str(c), "--login-file", "unused", "--output", str(out),
                   "--profiles", "core,extended,native", "--isolated-state", "--allow-paid"])
    assert code == 0
    assert calls == [("crud", "model-a"), ("extended", "model-a"), ("native", "model-a"), ("native", "model-b")]


def test_paid_or_stateful_profiles_require_explicit_fixture(tmp_path):
    with pytest.raises(m.live.CheckFailure, match="disposable"):
        m.main(["--plan", "not-read", "--login-file", "not-read", "--profiles", "native", "--output", str(tmp_path / "out")])


def test_incomplete_stream_exposes_status_without_raw_provider_error():
    stream = io.BytesIO(b'data: {"type":"error","error":"OpenAI returned HTTP 400: private content sk-secret"}\n\n')
    with pytest.raises(m.live.CheckFailure) as caught:
        m.live.read_events(stream)
    assert caught.value.evidence == {"event_count": 1, "http_statuses": [400]}
    assert "private" not in str(caught.value) and "sk-secret" not in json.dumps(caught.value.evidence)
