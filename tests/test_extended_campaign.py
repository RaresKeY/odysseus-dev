"""Network-none accounting regression tests; all usage is synthetic."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import multiprocessing
import os
import sqlite3

import httpx
import pytest

from src.decisions import DecisionContext, DecisionError, DecisionProvider, DecisionRequest, MemoryAttemptStore, ResolvedDecisionEndpoint
from src.decisions.campaign import BillingContract, CampaignGovernor, CampaignLedger, HARD_LIMIT, digest
from src.decisions.contracts import encode

AUTH = {"authorization": "synthetic offline tests", "initial_dispatched": 0}
CONTRACT = BillingContract(100, 100, "1" * 64, verified=True)
IDENTITY = {"source": "source-v1", "suite": "suite-v1", "policy": "policy-v1", "config": "config-v1", "contract": "1" * 64}
ENDPOINT = ResolvedDecisionEndpoint("synthetic", "synthetic", "systemone", "https://synthetic.invalid/v1/systemone", {})
REQUEST = DecisionRequest("completion", {"synthetic": True, "receipt": "verified"}, {"complete": "Receipts prove completion", "unknown": "Evidence missing"})
CONFIG = {"endpoint_id": "synthetic", "model": "synthetic", "protocol": "systemone"}


def setup(tmp_path):
    ledger = CampaignLedger(tmp_path / "budget" / "campaign.sqlite3", authorization=AUTH)
    version = ledger.admit_version(IDENTITY)
    ledger.advance("smoke", evidence="offline-regressions")
    return ledger, version


def reserve(ledger, version, identity="one", contract=CONTRACT):
    ledger.reserve(attempt_id=identity, case_key=identity, version=version, stage="smoke", binding={"wire": identity}, contract=contract)


def response(usage=None):
    return {"model": "synthetic", "answers": {"decision": {"type": "choice", "choice": "complete", "confidence": .99,
        "probabilities": {"complete": .99, "unknown": .01}}}, "usage": usage or {"input_tokens": 20, "output_tokens": 10}}


def provider(tmp_path, transport, *, store=None, observer=None, contract=CONTRACT):
    ledger, version = setup(tmp_path)
    governor = CampaignGovernor(ledger, version=version, stage="smoke", case_id="fixture-one", contract=contract, allow_synthetic=True)
    store = store or MemoryAttemptStore()
    client = DecisionProvider(store, resolver=lambda *_: ENDPOINT, campaign=governor, observer=observer,
        client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(transport), **kw))
    return client, ledger, store


def test_immutable_campaign_and_append_only_versions(tmp_path):
    ledger, version = setup(tmp_path)
    assert CampaignLedger(ledger.path, authorization=AUTH).admit_version(IDENTITY) == version
    with pytest.raises(DecisionError, match="identity_mismatch"):
        CampaignLedger(ledger.path, authorization={"new": "allowance"})
    second = ledger.admit_version({**IDENTITY, "source": "fixed-source"})
    assert second != version and len(ledger.report()["versions"]) == 2
    with sqlite3.connect(ledger.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE versions SET identity='rewrite'")
    assert ledger.report()["limit"] == HARD_LIMIT


def test_committed_reservation_blocks_restart_and_same_case(tmp_path):
    ledger, version = setup(tmp_path)
    reserve(ledger, version)
    restarted = CampaignLedger(ledger.path, authorization=AUTH)
    with pytest.raises(DecisionError, match="unresolved_attempt"):
        reserve(restarted, version, "two")
    ledger.finish("one", status="cancelled", dispatched=False)
    with pytest.raises(DecisionError, match="already_exists"):
        reserve(restarted, version)
    assert restarted.report()["charged_or_held"] == 0


def test_concurrent_atomic_admission_has_one_winner(tmp_path):
    ledger, version = setup(tmp_path)
    def admit(i):
        try:
            reserve(ledger, version, str(i))
            return True
        except DecisionError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(admit, range(16))) == 1
    assert ledger.report()["charged_or_held"] == 200


def _crash_child(path, version):
    ledger = CampaignLedger(path, authorization=AUTH)
    reserve(ledger, version)
    ledger.dispatch("one", {"wire": "one"})
    os._exit(29)


def test_hard_exit_preserves_dispatch_marker(tmp_path):
    ledger, version = setup(tmp_path)
    child = multiprocessing.get_context("spawn").Process(target=_crash_child, args=(str(ledger.path), version))
    child.start()
    child.join(20)
    assert child.exitcode == 29
    assert ledger.report()["attempts"][0]["status"] == "dispatching"
    with pytest.raises(DecisionError, match="unresolved_attempt"):
        reserve(CampaignLedger(ledger.path, authorization=AUTH), version, "two")


def test_stage_and_global_caps_are_admitted_before_dispatch(tmp_path):
    ledger, version = setup(tmp_path)
    with pytest.raises(DecisionError, match="budget_unavailable"):
        reserve(ledger, version, contract=BillingContract(150_000, 1, "1" * 64, True))
    with pytest.raises(DecisionError, match="stage_order"):
        ledger.advance("heldout", evidence="skip")
    for stage in ("small", "development", "heldout", "confirmation"):
        ledger.advance(stage, evidence="reviewed-offline")
    # Seed only through real reservations/known overrun: actual spend is persisted
    # even when it breaches the contract and exceeds the whole campaign ceiling.
    reserve(ledger, version)
    ledger.dispatch("one", {"wire": "one"})
    with pytest.raises(DecisionError, match="exceeded_reservation"):
        ledger.response("one", {"input_tokens": HARD_LIMIT + 1, "output_tokens": 4}, "2" * 64)
    assert ledger.report()["charged_or_held"] == HARD_LIMIT + 5
    assert ledger.report()["halted"] and ledger.report()["remaining"] == 0


@pytest.mark.parametrize("usage,known", [({"input_tokens": 7}, (7, None)),
    ({"input_tokens": True, "output_tokens": 5}, (None, 5)),
    ({"input_tokens": -1, "output_tokens": 5}, (None, 5)),
    ({"input_tokens": 7, "output_tokens": "8"}, (7, None)), (None, (None, None))])
def test_partial_known_usage_is_preserved_and_whole_reservation_held(tmp_path, usage, known):
    ledger, version = setup(tmp_path)
    reserve(ledger, version)
    ledger.dispatch("one", {"wire": "one"})
    ledger.response("one", usage, "2" * 64)
    row = ledger.report()["attempts"][0]
    assert (row["input_tokens"], row["output_tokens"]) == known
    assert row["charge"] == 200 and ledger.report()["halted"]
    with pytest.raises(DecisionError, match="halted"):
        reserve(ledger, version, "two")


def test_arbitrarily_large_known_usage_does_not_overflow_or_disappear(tmp_path):
    ledger, version = setup(tmp_path)
    reserve(ledger, version)
    ledger.dispatch("one", {"wire": "one"})
    huge = 10 ** 100
    with pytest.raises(DecisionError):
        ledger.response("one", {"input_tokens": huge, "output_tokens": huge}, "2" * 64)
    assert ledger.report()["charged_or_held"] == 2 * huge


def test_partial_overrun_keeps_unknown_component_and_known_spend(tmp_path):
    ledger, version = setup(tmp_path)
    reserve(ledger, version)
    ledger.dispatch("one", {"wire": "one"})
    with pytest.raises(DecisionError, match="exceeded_reservation"):
        ledger.response("one", {"input_tokens": 1000}, "2" * 64)
    row = ledger.report()["attempts"][0]
    assert row["input_tokens"] == 1000 and row["output_tokens"] is None
    assert row["charge"] == 1100 and ledger.report()["halted"]


def test_stage_call_cap_and_version_append_never_reset_charges(tmp_path):
    ledger, version = setup(tmp_path)
    for identity in ("one", "two"):
        reserve(ledger, version, identity)
        ledger.dispatch(identity, {"wire": identity})
        ledger.response(identity, {"input_tokens": 10, "output_tokens": 2}, "2" * 64)
        ledger.finish(identity, status="settled", dispatched=True)
    changed = ledger.admit_version({**IDENTITY, "source": "fixed-source"})
    with pytest.raises(DecisionError, match="budget_unavailable"):
        reserve(ledger, changed, "new-source-case")
    assert ledger.report()["charged_or_held"] == 24


@pytest.mark.asyncio
async def test_cancellation_during_transport_holds_and_halts(tmp_path):
    entered = asyncio.Event()
    async def transport(req):
        entered.set()
        await asyncio.Event().wait()
    client, ledger, _ = provider(tmp_path, transport)
    task = asyncio.create_task(client.evaluate(REQUEST, DecisionContext("synthetic-owner", "chat", "run"), config=CONFIG))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(100):
        if ledger.report()["halted"]:
            break
        await asyncio.sleep(.005)
    assert ledger.report()["halted"] and ledger.report()["held"] == 200


@pytest.mark.asyncio
async def test_observer_receives_validated_result_after_both_settlements(tmp_path):
    events = []
    class Observer:
        def before_dispatch(self, request, context, record):
            events.append("before")
            return record["attempt_id"]
        def after_attempt(self, ticket, **kwargs):
            row = ledger.report()["attempts"][0]
            assert row["status"] == "settled" and store.records[ticket]["status"] == "settled"
            assert kwargs["result"].choice == "complete"
            events.append("after")
    client, ledger, store = provider(tmp_path, lambda req: httpx.Response(200, json=response()), observer=Observer())
    await client.evaluate(REQUEST, DecisionContext("synthetic-owner", "chat", "run"), config=CONFIG)
    assert events == ["before", "after"]


@pytest.mark.asyncio
async def test_exact_wire_and_provider_committed_before_actual_post(tmp_path):
    def transport(req):
        row = ledger.report()["attempts"][0]
        binding = __import__("json").loads(row["binding"])
        assert row["status"] == "dispatching"
        assert binding["wire_sha256"] == hashlib.sha256(req.content).hexdigest()
        assert binding["url"] == str(req.url)
        assert "expected" not in req.content.decode()
        return httpx.Response(200, json=response())
    client, ledger, _ = provider(tmp_path, transport)
    result = await client.evaluate(REQUEST, DecisionContext("synthetic-owner", "chat", "run"), config=CONFIG)
    assert result.choice == "complete"
    row = ledger.report()["attempts"][0]
    assert row["status"] == "settled" and row["charge"] == 30
    assert row["response_sha256"] and ledger.report()["held"] == 0
    before = ledger.path.read_bytes()
    assert ledger.report()["known_input_tokens"] == 20
    assert ledger.path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["answer", "model", "distribution", "overrun", "partial"])
async def test_malformed_answer_still_charges_known_usage_before_parser(tmp_path, mutation):
    raw = response()
    if mutation == "answer": raw["answers"] = {}
    if mutation == "model": raw["model"] = "wrong"
    if mutation == "distribution": raw["answers"]["decision"]["probabilities"] = {}
    if mutation == "overrun": raw["usage"] = {"input_tokens": 101, "output_tokens": 6}; raw["answers"] = {}
    if mutation == "partial": raw["usage"] = {"input_tokens": 9}
    client, ledger, _ = provider(tmp_path, lambda _: httpx.Response(200, json=raw))
    with pytest.raises(DecisionError) as error:
        await client.evaluate(REQUEST, DecisionContext("synthetic-owner", "chat", "run"), config=CONFIG)
    assert error.value.dispatched is True
    row = ledger.report()["attempts"][0]
    assert row["input_tokens"] == (101 if mutation == "overrun" else 9 if mutation == "partial" else 20)
    assert row["charge"] == (107 if mutation == "overrun" else 200 if mutation == "partial" else 30)
    assert ledger.report()["halted"] is (mutation in {"overrun", "partial"})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["redirect", "timeout", "http_error", "bad_json"])
async def test_unknown_transport_halts_without_retry_across_new_run(tmp_path, kind):
    calls = []
    def transport(req):
        calls.append(req)
        if kind == "timeout": raise httpx.ReadTimeout("synthetic")
        return httpx.Response(307 if kind == "redirect" else 503 if kind == "http_error" else 200, content=b"invalid")
    client, ledger, _ = provider(tmp_path, transport)
    for run in ("run", "new-run"):
        with pytest.raises(DecisionError):
            await client.evaluate(REQUEST, DecisionContext("synthetic-owner", "chat", run), config=CONFIG)
    assert len(calls) == 1 and ledger.report()["halted"]
    assert ledger.report()["held"] == 200


@pytest.mark.asyncio
async def test_canonical_admission_failure_cannot_post_or_reset_identity(tmp_path):
    class Broken(MemoryAttemptStore):
        def reserve(self, *args, **kwargs): raise RuntimeError("synthetic")
    calls = []
    client, ledger, _ = provider(tmp_path, lambda req: calls.append(req), store=Broken())
    with pytest.raises(DecisionError):
        await client.evaluate(REQUEST, DecisionContext("synthetic-owner", "chat", "run"), config=CONFIG)
    assert calls == [] and ledger.report()["attempts"][0]["status"] == "no_dispatch"


@pytest.mark.asyncio
async def test_canonical_settlement_failure_blocks_future_dispatch(tmp_path):
    class Broken(MemoryAttemptStore):
        def settle(self, *args, **kwargs): raise RuntimeError("synthetic")
    calls = []
    client, ledger, _ = provider(tmp_path, lambda req: (calls.append(req), httpx.Response(200, json=response()))[1], store=Broken())
    with pytest.raises(DecisionError):
        await client.evaluate(REQUEST, DecisionContext("synthetic-owner", "chat", "run"), config=CONFIG)
    await asyncio.sleep(.05)
    assert len(calls) == 1 and ledger.report()["charged_or_held"] == 30
    with pytest.raises(DecisionError):
        await client.evaluate(REQUEST, DecisionContext("synthetic-owner", "chat", "other"), config=CONFIG)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_required_observer_failure_is_proved_no_dispatch(tmp_path):
    class Observer:
        def before_dispatch(self, *args): raise DecisionError("decision_capture_unavailable")
        def after_attempt(self, *args, **kwargs): raise RuntimeError("optional observer")
    calls = []
    client, ledger, _ = provider(tmp_path, lambda req: calls.append(req), observer=Observer())
    with pytest.raises(DecisionError) as error:
        await client.evaluate(REQUEST, DecisionContext("synthetic-owner", "chat", "run"), config=CONFIG)
    assert error.value.dispatched is False
    assert calls == [] and ledger.report()["attempts"][0]["status"] == "no_dispatch"


@pytest.mark.asyncio
async def test_unverified_contract_refuses_before_post(tmp_path):
    calls = []
    client, ledger, _ = provider(tmp_path, lambda req: calls.append(req), contract=replace(CONTRACT, verified=False))
    with pytest.raises(DecisionError, match="billing_bound_unverified"):
        await client.evaluate(REQUEST, DecisionContext("synthetic-owner", "chat", "run"), config=CONFIG)
    assert calls == [] and ledger.report()["attempts"] == []


def test_dispatch_wire_change_refused_and_reservation_retained(tmp_path):
    ledger, version = setup(tmp_path)
    reserve(ledger, version)
    with pytest.raises(DecisionError, match="identity_mismatch"):
        ledger.dispatch("one", {"wire": "changed"})
    assert ledger.report()["charged_or_held"] == 200


def test_symlink_ledger_and_writable_directory_refused(tmp_path):
    ledger, _ = setup(tmp_path)
    alias = ledger.path.parent / "alias.sqlite3"
    alias.symlink_to(ledger.path)
    with pytest.raises(OSError): CampaignLedger(alias, authorization=AUTH)
    ledger.path.parent.chmod(0o755)
    with pytest.raises(DecisionError, match="unsafe_ledger_directory"):
        CampaignLedger(ledger.path, authorization=AUTH)


def test_host_stop_sentinel_blocks_existing_and_restarted_owner(tmp_path):
    ledger, version = setup(tmp_path)
    (ledger.path.parent / "HALT_REQUESTED.json").write_text('{"campaign_id":"odysseus-jev-extended-20261024-v1"}')
    with pytest.raises(DecisionError, match="halted"):
        reserve(ledger, version)
    assert CampaignLedger(ledger.path, authorization=AUTH).report()["halted"]


def test_host_stop_sentinel_between_reserve_and_dispatch_retains_hold(tmp_path):
    ledger, version = setup(tmp_path)
    reserve(ledger, version)
    (ledger.path.parent / "HALT_REQUESTED.json").write_text('{}')
    with pytest.raises(DecisionError, match="halted"):
        ledger.dispatch("one", {"wire": "one"})
    assert ledger.report()["held"] == 200 and ledger.report()["halted"]


@pytest.mark.parametrize("verified", ["false", "true", 1, None])
def test_verified_contract_requires_exact_boolean(verified):
    with pytest.raises(DecisionError, match="invalid_billing_contract"):
        replace(CONTRACT, verified=verified).checked()


# Planning allowances are deliberately separate from verified vendor bounds.
def planning_profile(**changes):
    from src.decisions.campaign import RootApprovedChoiceProfile
    return RootApprovedChoiceProfile("2" * 64, root_approved=True, **changes)


def test_planning_profile_is_distinct_and_requires_exact_root_approval(tmp_path):
    from src.decisions.campaign import RootApprovedChoiceProfile
    ledger, version = setup(tmp_path)
    profile = planning_profile()
    assert not hasattr(profile, "verified")
    assert "not a published vendor maximum" in profile.basis
    reserve(ledger, version, contract=profile)
    assert ledger.report()["held"] == 131072
    other, identity = setup(tmp_path / "other")
    with pytest.raises(DecisionError, match="profile_not_approved"):
        reserve(other, identity, contract=RootApprovedChoiceProfile("2" * 64))


@pytest.mark.parametrize("field,value", [("root_approved", 1), ("root_approved", "true"),
    ("input_tokens", 65535), ("output_tokens", 0), ("basis", "published vendor maximum"),
    ("service_assumption", "guaranteed zero output"), ("evidence_sha256", "0" * 64)])
def test_planning_profile_rejects_changed_allowance_or_provenance(field, value):
    with pytest.raises(DecisionError, match="invalid_planning_profile"):
        replace(planning_profile(), **{field: value}).checked()


def test_operational_stop_is_persisted_without_changing_authorization(tmp_path):
    from src.decisions.campaign import OPERATIONAL_STOP
    ledger, version = setup(tmp_path)
    for stage in ("small", "development"):
        ledger.advance(stage, evidence="offline")
    contract = BillingContract(50000, 50000, "3" * 64, True)
    for i in range(10):
        key = str(i)
        ledger.reserve(attempt_id=key, case_key=key, version=version, stage="development", binding={}, contract=contract)
        ledger.dispatch(key, {})
        ledger.response(key, {"input_tokens": 50000, "output_tokens": 50000}, "4" * 64)
        ledger.finish(key, status="settled", dispatched=True)
    report = ledger.report()
    assert report["limit"] == 5000000 and report["operational_stop"] == OPERATIONAL_STOP == 1000000
    assert report["halted"] and report["charged_or_held"] == 1000000
    assert report["remaining"] == 4000000 and report["operational_remaining"] == 0
    assert CampaignLedger(ledger.path, authorization=AUTH).report()["halted"]
    with sqlite3.connect(ledger.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE operational_policy SET stop=5000000")


def test_projected_operational_stop_counts_held_reservation(tmp_path):
    ledger, version = setup(tmp_path)
    for stage in ("small", "development"):
        ledger.advance(stage, evidence="offline")
    contract = BillingContract(50000, 50000, "3" * 64, True)
    for i in range(9):
        key = str(i)
        ledger.reserve(attempt_id=key, case_key=key, version=version, stage="development", binding={}, contract=contract)
        ledger.dispatch(key, {})
        ledger.response(key, {"input_tokens": 50000, "output_tokens": 50000}, "4" * 64)
        ledger.finish(key, status="settled", dispatched=True)
    with pytest.raises(DecisionError, match="budget_unavailable"):
        ledger.reserve(attempt_id="new", case_key="new", version=version, stage="development", binding={}, contract=planning_profile())
    assert len(ledger.report()["attempts"]) == 9
    assert ledger.report()["charged_or_held"] == 900000
    assert ledger.report()["halt_reason"] == "operational_reservation_unavailable"
    assert CampaignLedger(ledger.path, authorization=AUTH).report()["halted"]


@pytest.mark.parametrize("mutation", ["model", "url", "header", "state", "free_text", "option_count", "question_count"])
def test_guarded_profile_rejects_unsupported_wire_before_reservation(tmp_path, mutation):
    from src.decisions.campaign import MODEL, URL
    endpoint = replace(ENDPOINT, model=MODEL, url=URL)
    payload = {"model": MODEL, "state": {"effects": "mocked"}, "questions": REQUEST.questions()}
    if mutation == "model": payload["model"] = "other"
    if mutation == "url": endpoint = replace(endpoint, url=URL + "?redirect=1")
    if mutation == "header": endpoint = replace(endpoint, headers={"Typesafe-danger-zone-long-context": "true"})
    if mutation == "state": payload["state"]["text"] = "x" * 4096
    if mutation == "free_text": payload["max_tokens"] = 100
    if mutation == "option_count": payload["questions"]["decision"]["criteria"] = {str(i): "a" for i in range(17)}
    if mutation == "question_count": payload["questions"]["another"] = payload["questions"]["decision"]
    with pytest.raises(DecisionError, match="profile_refused"):
        planning_profile().check_wire(endpoint, encode(payload), smoke=True)


def test_lower_stop_migration_preserves_old_campaign_identity_and_charges(tmp_path):
    ledger, version = setup(tmp_path)
    reserve(ledger, version)
    ledger.dispatch("one", {"wire": "one"})
    ledger.response("one", {"input_tokens": 10, "output_tokens": 5}, "2" * 64)
    ledger.finish("one", status="settled", dispatched=True)
    with sqlite3.connect(ledger.path) as db:
        identity = db.execute("SELECT identity FROM campaign").fetchone()[0]
        db.execute("DROP TRIGGER operational_policy_no_update")
        db.execute("DROP TRIGGER operational_policy_no_delete")
        db.execute("DROP TABLE operational_policy")
    # Simulate the prior reviewed schema: its immutable campaign row is untouched.
    reopened = CampaignLedger(ledger.path, authorization=AUTH)
    assert reopened.report()["charged_or_held"] == 15
    assert reopened.report()["operational_stop"] == 1000000
    with sqlite3.connect(ledger.path) as db:
        assert db.execute("SELECT identity FROM campaign").fetchone()[0] == identity
