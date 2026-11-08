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
            self.options = options;self.results = [];self.marker = "synthetic-fixture"
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
    assert caught.value.evidence == {"event_count": 1, "http_statuses": [400], "tool_receipts": [], "command_sha256": []}
    assert "private" not in str(caught.value) and "sk-secret" not in json.dumps(caught.value.evidence)


def test_explicit_alias_snapshot_does_not_guess_or_substitute(tmp_path):
    value = plan()
    value["variants"][0]["reported_model"] = "model-a-2025-08-07"
    p = tmp_path / "plan.json";p.write_text(json.dumps(value))
    assert m.read_plan(p)["variants"][0]["reported_model"] == "model-a-2025-08-07"


def test_chat_endpoint_can_be_a_reviewer_fallback():
    data = {"endpoints": [{"id":"chat", "model_type":"llm"}], "decision_endpoints": [
        {"id":"chat", "model_type":"llm"}, {"id":"jev", "model_type":"decision"}]}
    client = SimpleNamespace(request=lambda path:data)
    suite = m.Extended(client, {}, SimpleNamespace())
    assert suite.decision_separation()["separate_chat_and_decision_endpoints"]


def test_failed_continuation_retains_native_dispatch_evidence():
    stream = io.BytesIO(b'data: {"type":"tool_start","tool":"bash","command":"printf probe"}\n\n'
                        b'data: {"type":"tool_output","tool":"bash","execution_attempted":true,"exit_code":0,"output":"private-body"}\n\n'
                        b'data: {"type":"error","error":"HTTP 429 private-provider-body"}\n\n')
    with pytest.raises(m.live.CheckFailure) as caught:
        m.live.read_events(stream)
    assert caught.value.evidence["http_statuses"] == [429]
    assert caught.value.evidence["tool_receipts"] == [{"tool":"bash", "execution_attempted":True, "exit_code":0, "blocked":None}]
    assert len(caught.value.evidence["command_sha256"][0]) == 64
    assert "private" not in json.dumps(caught.value.evidence)


def test_invalid_stop_binding_is_rejected_before_connect(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network must not be reached")
    monkeypatch.setattr(m.live.http.client, "HTTPConnection", forbidden)
    client = m.live.Client("http://127.0.0.1:7825")
    with pytest.raises(m.live.CheckFailure, match="invalid_run_id"):
        with client.response("/api/chat/stop/fixture", {}, run_id="bad\r\nheader"):
            pass


def test_content_oracle_requires_exact_byte_verification_and_native_receipt():
    import copy
    events = [{'type':'tool_start','tool':tool,'command':'exact-check' if tool=='bash' else ''} for tool in ['write_file','read_file','bash']]
    events += [{'type':'tool_output','tool':tool,'execution_attempted':True,'exit_code':0,'output':'marker' if tool=='bash' else ''} for tool in ['write_file','read_file','bash']]
    events += [{'type':'final_response','content':'marker'}, {'type':'metrics','data':{'round_models':['actual']*4,'round_endpoint_ids':['saved']*4}}]
    m.verify_content_probe(events, 'marker', 'exact-check', 'actual', 'saved')
    for index, key, value in [(2,'command','different'), (5,'exit_code',1), (5,'execution_attempted',False)]:
        bad=copy.deepcopy(events);bad[index][key]=value
        with pytest.raises(m.live.CheckFailure):
            m.verify_content_probe(bad, 'marker', 'exact-check', 'actual', 'saved')


def test_receipt_aggregation_does_not_login_or_replay_and_rejects_stale_source(tmp_path, monkeypatch):
    p=tmp_path/'plan.json';p.write_text(json.dumps(plan()))
    c=tmp_path/'cases.json';c.write_text(json.dumps(catalog()))
    previous={'schema':'odysseus-debian-test-results/v1','expected_source':'a'*40,'cases':[{'runs':[{'id':'CH-01','status':'failed','reason':'provider_429'}]}]}
    prev=tmp_path/'previous.json';prev.write_text(json.dumps(previous))
    monkeypatch.setattr(m.live, 'load_login', lambda p: (_ for _ in ()).throw(AssertionError('no login')))
    args=['--plan',str(p),'--catalog',str(c),'--login-file','unused','--profiles','receipts','--previous-report',str(prev),'--output',str(tmp_path/'out.json')]
    assert m.main(args)==1
    previous['expected_source']='b'*40;prev.write_text(json.dumps(previous))
    with pytest.raises(m.live.CheckFailure, match='stale_or_invalid_previous'):
        m.main(args)
