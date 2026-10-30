"""Offline HTTP contract checks; no credentials, network or classified actions."""
import asyncio
from dataclasses import replace
import json
import threading
import ssl

import httpx
import pytest

from src.decisions import (DecisionContext, DecisionError, DecisionProvider, DecisionRequest,
                           MemoryAttemptStore, ResolvedDecisionEndpoint, resolve_decision_endpoint)
from src.decisions.config import decision_url
from src.decisions.contracts import MAX_RESPONSE_BYTES, decode, encode

CONFIG = {"endpoint_id": "reviewer", "model": "decision-model", "protocol": "systemone"}
CONTEXT = DecisionContext("alice", "chat", "run", policy_revision=2, authority_epoch=3)
REQUEST = DecisionRequest("approval", {"action": "synthetic read", "secret": "DO-NOT-LOG"},
                          {"allow": "Consistent with supplied authority", "review": "Human needed", "deny": "Not permitted"})


@pytest.mark.asyncio
async def test_decision_transport_disables_ambient_proxies_and_keeps_verified_ca_context(monkeypatch):
    from src import tls_overrides
    context=ssl.create_default_context()
    context.check_hostname=True
    monkeypatch.setenv('HTTPS_PROXY','http://proxy.invalid:1')
    monkeypatch.setattr(tls_overrides,'llm_verify',lambda:context)
    options=[]
    def factory(**kwargs):
        options.append(kwargs)
        return httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=reply())),**kwargs)
    client=DecisionProvider(MemoryAttemptStore(),resolver=lambda *_:endpoint(),client_factory=factory,observer=False)
    await client.evaluate(REQUEST,CONTEXT,config=CONFIG)
    assert options[0]['trust_env'] is False and options[0]['follow_redirects'] is False
    assert options[0]['verify'] is context and context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED


def endpoint(protocol="systemone"):
    return ResolvedDecisionEndpoint("reviewer", "decision-model", protocol,
                                    "https://provider.invalid/v1/systemone" if protocol == "systemone" else "http://127.0.0.1:9999/v1/chat/completions",
                                    {"Authorization": "Bearer SYNTHETIC-SECRET"})


def reply(request=REQUEST, model="decision-model"):
    return {"model": model, "answers": {
        name: {"type": "choice", "choice": next(iter(q["criteria"])), "confidence": .99,
               "probabilities": {key: float(i == 0) for i, key in enumerate(q["criteria"])}}
        for name, q in request.questions().items()}, "usage": {"input_tokens": 20, "output_tokens": 10}}


def provider(handler, *, store=None, resolver=None, protocol="systemone"):
    store = store or MemoryAttemptStore()
    return DecisionProvider(store, resolver=resolver or (lambda *_: endpoint(protocol)),
                            client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)), store


@pytest.mark.asyncio
async def test_complete_call_reserves_first_and_returns_only_safe_metadata():
    seen = []
    store = MemoryAttemptStore()

    def transport(request):
        assert len(store.records) == 1
        assert next(iter(store.records.values()))["status"] == "reserved"
        assert request.headers["authorization"] == "Bearer SYNTHETIC-SECRET"
        assert request.headers["accept-encoding"] == "identity"
        assert json.loads(request.content)["state"] == REQUEST.state
        seen.append(request)
        return httpx.Response(200, json=reply())

    client, _ = provider(transport, store=store)
    result = await client.evaluate(REQUEST, CONTEXT, config=CONFIG)
    assert len(seen) == 1 and result.choice == "allow"
    assert result.usage == {"input_tokens": 20, "output_tokens": 10}
    record = store.records[result.attempt_id]
    assert record["status"] == "settled"
    persisted = json.dumps(record)
    assert "DO-NOT-LOG" not in persisted and "SYNTHETIC-SECRET" not in persisted
    assert "provider.invalid" not in persisted
    assert result.provider_identity["distribution_source"] == "provider_reported"


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["https://evil.invalid/steal", "https://provider.invalid/other"])
async def test_all_redirects_are_unresolved_and_never_followed_or_retried(location):
    calls = []
    client, store = provider(lambda request: (calls.append(request), httpx.Response(307, headers={"Location": location}))[1])
    with pytest.raises(DecisionError, match="decision_redirect_refused"):
        await client.evaluate(REQUEST, CONTEXT, config=CONFIG)
    assert len(calls) == 1
    record = next(iter(store.records.values()))
    assert record["status"] == "unresolved" and record["usage"] is None
    with pytest.raises(DecisionError, match="decision_budget_unavailable"):
        await client.evaluate(REQUEST, CONTEXT, config=CONFIG)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mutate,code", [
    (lambda v: v["answers"]["decision"]["probabilities"].update(allow=True), "distribution"),
    (lambda v: v["answers"]["decision"]["probabilities"].update(allow=.8), "distribution"),
    (lambda v: v["answers"]["decision"].update(choice="deny"), "choice_mismatch"),
    (lambda v: v["answers"]["decision"].update(confidence=float("inf")), "invalid_json"),
    (lambda v: v["answers"]["decision"].update(extra="authority"), "invalid_answer"),
    (lambda v: v["answers"].update(extra={}), "invalid_answers"),
    (lambda v: v.update(model="different-model"), "model_mismatch"),
    (lambda v: v["usage"].update(input_tokens=True), "usage_unavailable"),
])
async def test_strict_response_failures_never_become_grants(mutate, code):
    data = reply()
    mutate(data)
    client, store = provider(lambda _: httpx.Response(200, content=json.dumps(data).encode()))
    with pytest.raises(DecisionError, match=code):
        await client.evaluate(REQUEST, CONTEXT, config=CONFIG)
    record = next(iter(store.records.values()))
    assert record["status"] in {"rejected", "unresolved"}
    if code not in {"invalid_json", "usage_unavailable"}:
        assert record["usage"] == {"input_tokens": 20, "output_tokens": 10}


@pytest.mark.asyncio
async def test_duplicate_keys_and_oversized_body_are_unknown_usage():
    for data in [b'{"model":"decision-model","model":"evil"}', b"x" * (MAX_RESPONSE_BYTES + 1)]:
        client, store = provider(lambda _: httpx.Response(200, content=data))
        with pytest.raises(DecisionError):
            await client.evaluate(REQUEST, CONTEXT, config=CONFIG)
        assert next(iter(store.records.values()))["usage"] is None
        assert next(iter(store.records.values()))["status"] == "unresolved"


@pytest.mark.asyncio
async def test_timeout_after_dispatch_is_unresolved_without_retry():
    calls = []

    async def transport(request):
        calls.append(request)
        await asyncio.sleep(2)
        return httpx.Response(200, json=reply())

    client, store = provider(transport, resolver=lambda *_: replace(endpoint(), timeout_seconds=.01))
    with pytest.raises(DecisionError, match="decision_transport_failed"):
        await client.evaluate(REQUEST, CONTEXT, config=CONFIG)
    assert len(calls) == 1 and next(iter(store.records.values()))["status"] == "unresolved"


@pytest.mark.asyncio
async def test_preflight_cancellation_and_size_fail_before_spending():
    calls = []
    client, store = provider(lambda request: calls.append(request))
    with pytest.raises(DecisionError, match="cancelled"):
        await client.evaluate(REQUEST, replace(CONTEXT, cancelled=lambda: True), config=CONFIG)
    with pytest.raises(DecisionError, match="too_large"):
        await client.evaluate(replace(REQUEST, state={"text": "x" * 70000}), CONTEXT, config=CONFIG)
    assert not calls and not store.records


@pytest.mark.asyncio
async def test_endpoint_disable_or_key_change_after_response_rejects_known_usage():
    endpoints = iter([endpoint(), replace(endpoint(), headers={})])
    client, store = provider(lambda _: httpx.Response(200, json=reply()), resolver=lambda *_: next(endpoints))
    with pytest.raises(DecisionError, match="decision_endpoint_changed"):
        await client.evaluate(REQUEST, CONTEXT, config=CONFIG)
    record = next(iter(store.records.values()))
    assert record["status"] == "rejected" and record["usage"]["input_tokens"] == 20


@pytest.mark.asyncio
async def test_openai_compatible_local_complete_path_labels_reported_distribution():
    seen = []

    def transport(request):
        payload = decode(request.content)
        assert payload["messages"][1]["role"] == "user"
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert "tools" not in payload and payload["stream"] is False
        seen.append(payload)
        inner = reply()
        del inner["usage"]
        return httpx.Response(200, json={"model": "decision-model", "choices": [{
            "finish_reason": "stop", "message": {"content": json.dumps(inner)}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 9}})

    client, store = provider(transport, protocol="openai_json")
    result = await client.evaluate(REQUEST, CONTEXT, config={**CONFIG, "protocol": "openai_json"})
    assert len(seen) == 1 and result.provider_identity["distribution_source"] == "model_reported"
    assert result.usage == {"input_tokens": 5, "output_tokens": 9}


@pytest.mark.asyncio
async def test_attempt_store_failure_prevents_transport_and_result():
    class Broken(MemoryAttemptStore):
        def reserve(self, *args, **kwargs):
            raise OSError("PRIVATE DB DETAIL")
    client, _ = provider(lambda _: pytest.fail("dispatch before reservation"), store=Broken())
    with pytest.raises(DecisionError, match="decision_attempt_store_unavailable") as error:
        await client.evaluate(REQUEST, CONTEXT, config=CONFIG)
    assert "PRIVATE" not in str(error.value)


def test_atomic_reservation_prevents_two_parallel_dispatches():
    store = MemoryAttemptStore()
    record = {"attempt_id": "one", "owner": "alice", "session_id": "chat", "run_id": "run", "status": "reserved"}
    outcomes = []
    def reserve(name):
        try:
            store.reserve({**record, "attempt_id": name}, max_calls=32)
            outcomes.append("reserved")
        except DecisionError:
            outcomes.append("blocked")
    threads = [threading.Thread(target=reserve, args=(name,)) for name in ("one", "two")]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert sorted(outcomes) == ["blocked", "reserved"]


def test_registered_resolver_owner_disabled_and_exact_model_gates(monkeypatch):
    from tests.helpers.model_admission import register_model_owners
    register_model_owners(monkeypatch, {"alice": {"decision-model", "unknown"}})
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import core.database as database
    engine = create_engine("sqlite://")
    database.ModelEndpoint.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add_all([database.ModelEndpoint(id="private", name="Private", owner="bob", base_url="http://127.0.0.1:8000", cached_models='["decision-model"]'),
                    database.ModelEndpoint(id="shared", name="Shared", owner=None, base_url="http://127.0.0.1:8000/v1", cached_models='["decision-model"]'),
                    database.ModelEndpoint(id="disabled", name="Disabled", owner="alice", is_enabled=False, base_url="http://127.0.0.1:8000")])
        db.commit()
    monkeypatch.setattr(database, "SessionLocal", sessions)
    for owner in ["alice"]:
        with pytest.raises(DecisionError, match="endpoint_unavailable"):
            resolve_decision_endpoint({**CONFIG, "endpoint_id": "private"}, owner)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with pytest.raises(DecisionError, match="owner_unavailable"):
        resolve_decision_endpoint({**CONFIG, "endpoint_id": "private"}, None)
    result = resolve_decision_endpoint({**CONFIG, "endpoint_id": "shared"}, "alice")
    assert result.url == "http://127.0.0.1:8000/v1/systemone"
    with pytest.raises(DecisionError, match="model_unavailable"):
        resolve_decision_endpoint({**CONFIG, "endpoint_id": "shared", "model": "unknown"}, "alice")
    with pytest.raises(DecisionError, match="endpoint_unavailable"):
        resolve_decision_endpoint({**CONFIG, "endpoint_id": "disabled"}, "alice")


@pytest.mark.parametrize("url", ["https://user:secret@example.org", "https://example.org?key=secret",
                                  "https://example.org/#secret", "file:///etc/passwd", "https://example.org/../v1"])
def test_credential_and_malformed_endpoint_urls_are_rejected(url):
    with pytest.raises(DecisionError):
        decision_url(url, "systemone")


def test_unknown_provider_fields_cannot_override_url_or_credentials():
    from src.decisions.config import validate_config
    for field in ["url", "headers", "api_key", "follow_redirects"]:
        with pytest.raises(DecisionError):
            validate_config({**CONFIG, field: "untrusted"})


@pytest.mark.asyncio
async def test_response_adapter_fault_is_not_a_transport_outage(monkeypatch):
    client, store = provider(lambda _: httpx.Response(200, json=reply()))
    def broken_adapter(*_):
        raise RuntimeError("synthetic decoder fault")
    monkeypatch.setattr(client, "_answers", broken_adapter)
    with pytest.raises(DecisionError, match="decision_invalid_response"):
        await client.evaluate(REQUEST, CONTEXT, config=CONFIG)
    record = next(iter(store.records.values()))
    assert record["status"] == "rejected"
    assert record["usage"] == {"input_tokens": 20, "output_tokens": 10}
    assert record["failure_kind"] == "decision_invalid_response"
