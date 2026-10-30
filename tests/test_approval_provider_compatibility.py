"""Offline selected-provider contracts and no-spend readiness/recovery."""
import json

import httpx
import pytest

from core import database as d
from src import approval_fallback as fallback, model_admission
from src.approval_fallback import ConversationEndpoint, ConversationReviewProvider
from src.approval_provider_contracts import approval_provider_contract, apply_output_contract
from src.chat_automation import prepare_chat_turn
from src.decisions import DecisionError
from tests.test_approval_adapter_boundaries import answer, URLS
from tests.test_approval_fallback import install_fallback, LABELS, openai_answer
from tests.test_chat_automation_state import store
from tests.test_chat_approval_sql_integration import dispatch_runtime

OPENAI = "https://api.openai.com/v1/chat/completions"
ANTHROPIC = "https://api.anthropic.com/v1/messages"
PAIRS = [
    ("openai", "gpt-4o", "gpt-4o-2024-08-06"),
    ("openai", "gpt-4o-mini", "gpt-4o-mini-2024-07-18"),
    ("openai", "gpt-5", "gpt-5-2025-08-07"),
    ("openai", "gpt-5-mini", "gpt-5-mini-2025-08-07"),
    ("openai", "gpt-5-nano", "gpt-5-nano-2025-08-07"),
    ("openai", "gpt-5.1", "gpt-5.1-2025-11-13"),
    ("openai", "gpt-5.2", "gpt-5.2-2025-12-11"),
    ("anthropic", "claude-sonnet-4-5", "claude-sonnet-4-5-20250929"),
    ("anthropic", "claude-haiku-4-5", "claude-haiku-4-5-20251001"),
]


def select(store, rt, model, url):
    rt.session.model, rt.session.endpoint_url = model, url
    # The ordinary owner allow-list is still consulted by readiness/dispatch.
    original = model_admission._AUTH_MANAGER.get_privileges
    model_admission._AUTH_MANAGER.get_privileges = lambda owner: {
        **original(owner), "allowed_models": ["manual", "decision-model", model]}
    with store.transaction() as db:
        row = db.get(d.Session, "a")
        row.model, row.endpoint_url = model, url


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol,selected,canonical", PAIRS)
async def test_documented_alias_keeps_selected_request_and_records_actual_reviewer(
        store, dispatch_runtime, monkeypatch, protocol, selected, canonical):
    rt = dispatch_runtime
    url = OPENAI if protocol == "openai" else ANTHROPIC
    select(store, rt, selected, url)
    store.update_configuration("alice", {"decision_provider": None}, 1)
    store.update_chat("alice", "a", {"approval_mode": "auto_approve"}, 0)
    calls = []

    def transport(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert str(request.url) == url and payload["model"] == selected
        assert "tools" not in payload and "tool_choice" not in payload
        schema = (payload["response_format"]["json_schema"]["schema"] if protocol == "openai"
                  else payload["output_config"]["format"]["schema"])
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {*LABELS, "uncertain"}
        raw = answer(protocol, None)
        raw["model"] = canonical
        return httpx.Response(200, json=raw)

    install_fallback(monkeypatch, transport)
    bound = await prepare_chat_turn(store, "alice", rt.session, "Write the fixture.")
    _, result = await rt.dispatch(bound)
    assert result["exit_code"] == 0 and len(calls) == len(rt.effects) == 1
    identity = result["approval_review"]["provider_identity"]
    assert identity["model"] == selected and identity["reviewed_model"] == canonical
    [record] = rt.attempts.records_for_run("alice", "a", bound.run_id)
    assert record["provider_identity"]["model"] == selected
    assert record["provider_identity"]["canonical_model"] == canonical
    assert record["provider_identity"]["accepted_response_models"] == [selected, canonical]
    assert "reviewed_model" not in record["provider_identity"]
    from src.decisions.observability import default_observability
    [item] = default_observability(store.session_factory).history("alice", "a")["items"]
    assert item["provider"]["model"] == selected
    assert item["provider"]["reviewed_model"] == canonical


@pytest.mark.parametrize("url", [
    "https://proxy.invalid/v1/chat/completions",
    "https://api.openai.com.evil.invalid/v1/chat/completions",
    "https://other.api.openai.com/v1/chat/completions",
    "http://api.openai.com/v1/chat/completions",
    "https://api.openai.com:8443/v1/chat/completions",
])
def test_alias_and_schema_capability_do_not_cross_official_origin(url):
    endpoint = ConversationEndpoint("selected", "gpt-5", "approval_openai", url, {})
    assert ConversationReviewProvider._same_response_model(endpoint, "gpt-5")
    assert not ConversationReviewProvider._same_response_model(endpoint, "gpt-5-2025-08-07")
    contract = approval_provider_contract(endpoint.protocol, url, endpoint.model)
    assert apply_output_contract({}, contract) == {}


@pytest.mark.parametrize("selected,reported", [
    ("gpt-4o", "gpt-4o-2024-11-20"),  # A real sibling snapshot is not its alias binding.
    ("gpt-5", "gpt-5-2026-10-28"),
    ("gpt-5-mini", "gpt-5-2025-08-07"),
    ("gpt-5-2025-08-07", "gpt-5"),
    ("gpt-5-latest", "gpt-5-2025-08-07"),
])
def test_no_prefix_date_sibling_or_reverse_alias_equivalence(selected, reported):
    endpoint = ConversationEndpoint("selected", selected, "approval_openai", OPENAI, {})
    assert not ConversationReviewProvider._same_response_model(endpoint, reported)


@pytest.mark.parametrize("url", [
    "https://generativelanguage.googleapis.com/v1/chat/completions",
    "https://generativelanguage.googleapis.com:8443/v1beta/openai/chat/completions",
    "http://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
])
def test_google_models_spelling_requires_exact_compatibility_origin_and_path(url):
    endpoint = ConversationEndpoint("selected", "gemini-2.5-flash", "approval_openai", url, {})
    assert not ConversationReviewProvider._same_response_model(endpoint, "models/gemini-2.5-flash")


@pytest.mark.parametrize("protocol,url,expected", [
    ("approval_openai", URLS["openai"], None),
    ("approval_anthropic", ANTHROPIC, "output_config"),
    ("approval_ollama", URLS["ollama"], "format"),
    ("approval_ollama", "https://ollama.com/api/chat", None),
    ("approval_responses", URLS["responses"] + "/responses", None),
])
def test_only_contracted_transports_receive_schema(protocol, url, expected):
    contract = approval_provider_contract(protocol, url, "manual")
    payload = apply_output_contract({"model": "manual"}, contract)
    assert set(payload) == ({"model", expected} if expected else {"model"})


@pytest.mark.asyncio
async def test_known_unsupported_path_is_unavailable_without_dns_or_inference(
        store, dispatch_runtime, monkeypatch):
    import socket
    rt = dispatch_runtime
    select(store, rt, "manual", "https://api.openai.com/v1/responses")
    store.update_configuration("alice", {"decision_provider": None}, 1)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: pytest.fail("Readiness must not resolve DNS"))
    with store.transaction() as db:
        readiness = fallback.auto_approve_availability(None, "alice", db=db, session_id="a")
    assert not readiness["available"]
    assert readiness["reason_code"] == "decision_approval_contract_unsupported"
    assert "Always Ask" in readiness["reason"] and "configure Jev" in readiness["reason"]
    assert not rt.attempts.records_for_run("alice", "a", "anything")


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,held", [("substitution", False), ("missing_usage", True), ("duplicate_json", False)])
async def test_incompatible_binding_stops_repeated_spend_and_readiness_explains_recovery(
        store, dispatch_runtime, monkeypatch, fault, held):
    rt = dispatch_runtime
    store.update_configuration("alice", {"decision_provider": None}, 1)
    store.update_chat("alice", "a", {"approval_mode": "auto_approve"}, 0)
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(200, json=answer("openai", fault))

    install_fallback(monkeypatch, transport)
    first = await prepare_chat_turn(store, "alice", rt.session, "First exact action")
    assert (await rt.dispatch(first))[1]["blocked"]
    [record] = rt.attempts.records_for_run("alice", "a", first.run_id)
    assert record["usage"] == (None if held else {"input_tokens": 30, "output_tokens": 10})
    assert record["status"] == ("unresolved" if held else "rejected")
    with store.transaction() as db:
        ready = fallback.auto_approve_availability(None, "alice", db=db, session_id="a")
    assert ready["reason_code"] == "decision_approval_contract_incompatible"
    assert "five minutes" in ready["reason"]
    second = await prepare_chat_turn(store, "alice", rt.session, "Another exact action")
    assert (await rt.dispatch(second))[1]["blocked"]
    assert len(calls) == 1 and not rt.effects
    assert not rt.attempts.records_for_run("alice", "a", second.run_id)
    # The first reservation is never rewritten or released by the RAM memo.
    assert rt.attempts.records_for_run("alice", "a", first.run_id)[0] == record
    clock = fallback._memo_clock()
    monkeypatch.setattr(fallback, "_memo_clock", lambda: clock + 301)
    with store.transaction() as db:
        assert fallback.auto_approve_availability(None, "alice", db=db, session_id="a")["available"]
    third = await prepare_chat_turn(store, "alice", rt.session, "Action after recovery window")
    assert (await rt.dispatch(third))[1]["blocked"] and len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["denial", "uncertainty", "refusal", "truncation", "http"])
async def test_semantic_stop_and_transient_failures_never_memoize_model_denial(
        store, dispatch_runtime, monkeypatch, outcome):
    rt = dispatch_runtime
    store.update_configuration("alice", {"decision_provider": None}, 1)
    store.update_chat("alice", "a", {"approval_mode": "auto_approve"}, 0)

    def transport(_request):
        raw = openai_answer(**({"risk_level": "critical"} if outcome == "denial" else
                              {"uncertain": True} if outcome == "uncertainty" else {}))
        if outcome == "refusal": raw["choices"][0]["message"]["refusal"] = "Refused"
        if outcome == "truncation": raw["choices"][0]["finish_reason"] = "length"
        return httpx.Response(503 if outcome == "http" else 200, json=raw)

    install_fallback(monkeypatch, transport)
    bound = await prepare_chat_turn(store, "alice", rt.session, "Write fixture")
    result = (await rt.dispatch(bound))[1]
    assert result["blocked"] and not rt.effects
    with store.transaction() as db:
        assert fallback.auto_approve_availability(None, "alice", db=db, session_id="a")["available"]


@pytest.mark.asyncio
async def test_memo_is_bounded_and_resets_on_saved_selection_registration_owner_or_chat_change(
        store, dispatch_runtime, monkeypatch):
    rt = dispatch_runtime
    with store.transaction() as db:
        row = db.get(d.Session, "a")
        original = fallback._binding_key(db, row, "alice", "manual", "reviewer")
        fallback._incompatibility(original, remember="decision_approval_model_mismatch")
        row.headers = {"X-Route-Revision": "changed"}
        assert not fallback._incompatibility(fallback._binding_key(db, row, "alice", "manual", "reviewer"))
        row.headers = None
        assert not fallback._incompatibility(fallback._binding_key(db, row, "bob", "manual", "reviewer"))
        other = db.get(d.Session, "b")
        assert not fallback._incompatibility(fallback._binding_key(db, other, "alice", "manual", "reviewer"))
        db.get(d.ModelEndpoint, "reviewer").base_url = "https://changed.invalid/v1"
        assert not fallback._incompatibility(fallback._binding_key(db, row, "alice", "manual", "reviewer"))
    monkeypatch.setattr(fallback, "_INCOMPATIBLE_LIMIT", 3)
    for number in range(5):
        fallback._incompatibility(("fixture-scope", str(number)), remember="decision_invalid_json")
    assert len(fallback._incompatible) == 3


@pytest.mark.parametrize("protocol", URLS)
@pytest.mark.parametrize("fault", ["tools", "bad_usage", "bad_shape"])
def test_all_transports_reject_tools_malformed_usage_and_shapes(protocol, fault):
    raw = answer(protocol, None)
    client = object.__new__(ConversationReviewProvider)
    endpoint = ConversationEndpoint("selected", "manual", "approval_" + protocol, URLS[protocol], {})
    if fault == "bad_usage":
        if protocol == "ollama": raw["eval_count"] = True
        else: raw["usage"] = []
    elif protocol == "openai":
        if fault == "tools": raw["choices"][0]["message"]["tool_calls"] = [{"name": "write_file"}]
        else: raw["choices"] = [None]
    elif protocol == "anthropic":
        raw["content"] = [{"type": "tool_use"}] if fault == "tools" else [None]
    elif protocol == "ollama":
        if fault == "tools": raw["message"]["tool_calls"] = [{"name": "write_file"}]
        else: raw["message"] = None
    else:
        raw["output"] = [{"type": "function_call"}] if fault == "tools" else [None]
    with pytest.raises(DecisionError):
        client._answers(raw, {}, endpoint)


@pytest.mark.parametrize("fault", ["missing_terminal", "duplicate_terminal", "failed", "incomplete", "unterminated", "event_mismatch", "tool_event", "refusal_event"])
def test_responses_requires_one_complete_tool_free_terminal_event(fault):
    client = object.__new__(ConversationReviewProvider)
    endpoint = ConversationEndpoint("selected", "manual", "approval_responses", URLS["responses"], {})
    completed = {"type": "response.completed", "response": answer("responses", None)}
    def frame(value): return "data: " + json.dumps(value) + "\n\n"
    wire = frame(completed)
    if fault == "missing_terminal": wire = frame({"type": "response.created"})
    elif fault == "duplicate_terminal": wire += frame(completed)
    elif fault in {"failed", "incomplete"}: wire += frame({"type": "response." + fault})
    elif fault == "unterminated": wire = wire.rstrip("\n")
    elif fault == "event_mismatch": wire = "event: response.failed\n" + wire
    elif fault == "tool_event": wire = frame({"type": "response.output_item.added", "item": {"type": "function_call"}}) + wire
    else: wire = frame({"type": "response.refusal.delta", "delta": "Refused"}) + wire
    with pytest.raises(DecisionError):
        client._read_response(wire.encode(), endpoint)


def test_responses_multiline_sse_and_cache_tokens_are_not_double_counted():
    client = object.__new__(ConversationReviewProvider)
    endpoint = ConversationEndpoint("selected", "manual", "approval_responses", URLS["responses"], {})
    raw = answer("responses", None)
    raw["usage"]["input_tokens_details"] = {"cached_tokens": 9}
    wire = "event: response.completed\n" + "\n".join("data: " + line for line in json.dumps(
        {"type": "response.completed", "response": raw}, indent=2).splitlines()) + "\n\n"
    parsed = client._read_response(wire.encode(), endpoint)
    assert client._usage(parsed, endpoint) == {"input_tokens": 30, "output_tokens": 10}
    assert client._answers(parsed, {}, endpoint)["uncertain"] is False


def test_actual_reviewer_identity_is_omitted_from_redacted_capture_exports():
    from src.decisions.observability import _packet
    public = {"attempt_id": "a", "run_id": "run", "purpose": "approval", "provider": {
        "model": "private-selected-id", "canonical_model": "private-canonical-id",
        "reviewed_model": "private-reviewed-id", "endpoint_id": "private-endpoint",
        "name": "private-provider", "identity_contract": "private-contract",
        "output_contract": "private-output-contract", "protocol": "approval_openai"}}
    packet = _packet(public, {})
    assert "private-" not in json.dumps(packet)
    assert public["provider"]["reviewed_model"] == "private-reviewed-id"


def test_ollama_complete_schema_and_state_obey_the_explicit_context_bound():
    from types import SimpleNamespace
    client = object.__new__(ConversationReviewProvider)
    endpoint = ConversationEndpoint("selected", "manual", "approval_ollama", URLS["ollama"], {})
    payload = client._payload(SimpleNamespace(state={"complete_state": "x" * 1000}), {}, endpoint)
    assert payload["options"]["num_ctx"] == 16384
    assert "x" * 1000 in payload["messages"][1]["content"]
    assert payload["format"]["properties"]["uncertain"] == {"type": "boolean"}
    with pytest.raises(DecisionError, match="decision_request_too_large"):
        client._payload(SimpleNamespace(state={"complete_state": "x" * 20000}), {}, endpoint)


@pytest.mark.parametrize("failure", ["approval_context_checkpoint_required", "approval_private_context_unavailable",
    "approval_private_process_changed", "approval_sensitive_input", "unknown_untrusted_reason"])
def test_context_recovery_observation_has_only_an_allowlisted_renewal_reason(store, failure):
    from src.decisions.observability import default_observability
    from src.approval_policy import REASONS
    observer = default_observability(store.session_factory)
    observer.record_approval("alice", "a", "checkpoint-run", {"decision_source": "conversation_model",
        "status": "uncertain", "applied_rule": "insufficient_evidence", "failure_kind": failure,
        "reason": "UNTRUSTED arbitrary reason must not enter history"})
    [item] = observer.history("alice", "a")["items"]
    assert item["approval"]["reason"] == (REASONS["insufficient_evidence"] if failure == "unknown_untrusted_reason"
                                           else "Renew review context in Chat settings.")
    assert "UNTRUSTED" not in json.dumps(item)


def test_memo_identity_uses_a_process_key_without_retaining_credentials(store, dispatch_runtime, monkeypatch):
    with store.transaction() as db:
        row = db.get(d.Session, "a")
        row.headers = {"Authorization": "Bearer fixture"}
        first = fallback._binding_key(db, row, "alice", "manual", "reviewer")
        assert first == fallback._binding_key(db, row, "alice", "manual", "reviewer")
        monkeypatch.setattr(fallback, "_BINDING_KEY", b"different-process-key" * 2)
        second = fallback._binding_key(db, row, "alice", "manual", "reviewer")
        assert first[0] is second[0] and first[1] != second[1]
        assert len(first[1]) == len(second[1]) == 64
        assert "fixture" not in repr((first, second))


@pytest.mark.asyncio
@pytest.mark.parametrize("surface,legacy", [
    ("tool_result", False), ("history", False), ("capture", False),
    ("history", True), ("capture", True),
])
async def test_secret_route_fingerprint_stays_internal(
        store, dispatch_runtime, monkeypatch, surface, legacy):
    import copy
    import hashlib
    from src.chat_automation import digest
    from src.decisions.config import ResolvedDecisionEndpoint
    from src.decisions.contracts import encode
    from src.decisions.observability import (
        DecisionObservability, default_observability, validate_capture,
    )

    rt = dispatch_runtime
    secret = "pin-0420"
    headers = {"Authorization": "Bearer " + secret}
    rt.session.headers = dict(headers)
    with store.transaction() as db:
        row = db.get(d.Session, "a")
        row.endpoint_url = rt.session.endpoint_url
        row.headers = dict(headers)
        saved = {key: getattr(row, key) for key in (
            "model", "endpoint_id", "endpoint_url", "headers",
        )}
    store.update_configuration("alice", {"decision_provider": None}, 1)
    store.update_chat("alice", "a", {"approval_mode": "auto_approve"}, 0)
    observer = default_observability(store.session_factory)
    observer.consent("alice", "a", {"purposes": ["approval"]})
    install_fallback(monkeypatch, lambda _request: httpx.Response(200, json=openai_answer()))
    bound = await prepare_chat_turn(store, "alice", rt.session, "Write the fixture.",
                                    submission_id="known-submission")
    _, result = await rt.dispatch(bound)
    assert result["exit_code"] == 0 and len(rt.effects) == 1
    [record] = rt.attempts.records_for_run("alice", "a", bound.run_id)
    identity = record["provider_identity"]
    fingerprint = identity["configuration_digest"]
    assert record["approval_route_origin"] == fallback.model_snapshot(bound)
    target, _, protocol, _ = fallback._conversation_route(bound.endpoint_url, {})

    def guess_fingerprint(candidate):
        guessed_headers = {"Authorization": "Bearer " + candidate}
        origin = digest({"saved": {**saved, "headers": guessed_headers},
            "resolved": bound.descriptor, "url": bound.endpoint_url,
            "headers": guessed_headers, "route_revision": bound.review_route_revision})
        endpoint = ResolvedDecisionEndpoint(identity["endpoint_id"], identity["model"],
            protocol, target, {}, registration_revision=origin)
        return endpoint.public_identity()["configuration_digest"]

    assert [candidate for candidate in ("pin-0000", secret, "pin-1234")
            if guess_fingerprint(candidate) == fingerprint] == [secret]

    if legacy:
        # Reproduce already-persisted observations from before boundary redaction.
        with observer._transaction(write=True) as db:
            activity = db.query(observer.models.activity).filter_by(
                attempt_id=record["attempt_id"]).one()
            projection = copy.deepcopy(activity.projection)
            projection["provider"]["configuration_digest"] = fingerprint
            activity.projection = projection
            captured = db.query(observer.models.capture).filter_by(run_id=bound.run_id).one()
            entries = copy.deepcopy(captured.captures)
            for entry in entries:
                entry["packet"]["observation"]["provider"]["configuration_digest"] = fingerprint
                entry["sanitized_sha256"] = hashlib.sha256(encode(entry["packet"])).hexdigest()
            captured.captures = entries

    # Reopening reads the persisted observations; no live ticket is needed.
    reopened = DecisionObservability(store.session_factory)
    if surface == "tool_result":
        public = result["approval_review"]
        provider = public["provider_identity"]
        assert provider["model"] == "manual" and provider["reviewed_model"] == "manual"
    elif surface == "history":
        [public] = reopened.history("alice", "a")["items"]
        provider = public["provider"]
        assert provider["model"] == "manual" and provider["reviewed_model"] == "manual"
    else:
        public = reopened.capture("alice", "a", bound.run_id)
        assert validate_capture(encode(public))["valid"]
        [entry] = public["records"]
        provider = entry["packet"]["observation"]["provider"]
    assert provider["protocol"] == "approval_openai"
    assert "configuration_digest" not in provider
    assert fingerprint.encode() not in encode(public)
    assert secret.encode() not in encode(public)
    # Redaction does not rewrite the durable authority/accounting record.
    assert rt.attempts.records_for_run("alice", "a", bound.run_id) == [record]
    assert fallback.model_snapshot(bound) == record["approval_route_origin"]


def test_non_secret_decision_fingerprint_remains_public():
    from dataclasses import replace
    from src.decisions.config import ResolvedDecisionEndpoint
    from src.decisions.contracts import DecisionRequest
    from src.decisions.observability import _packet, _projection

    endpoint = ResolvedDecisionEndpoint("reviewer", "decision", "systemone",
        "https://review.invalid/v1/systemone", {"Authorization": "Bearer first"},
        registration_revision="2026-10-30T00:00:00")
    identity = endpoint.public_identity()
    assert replace(endpoint, headers={"Authorization": "Bearer second"}).public_identity() == identity
    request = DecisionRequest("tools", {}, {"yes": "Allow", "no": "Deny"})
    record = {"attempt_id": "public-attempt", "run_id": "public-run",
              "provider_identity": identity}
    public, _, offers = _projection(request, record)
    assert public["provider"]["protocol"] == "systemone"
    assert public["provider"]["configuration_digest"] == identity["configuration_digest"]
    packet = _packet(public, offers)
    assert packet["packet"]["observation"]["provider"]["configuration_digest"] == identity["configuration_digest"]
    assert identity == endpoint.public_identity()
