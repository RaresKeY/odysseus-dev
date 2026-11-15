"""Request-control evidence must apply only to its serving route."""

from dataclasses import replace

import pytest

from src import model_capabilities as mc
from src.model_capability_readers.base import ModelCapabilityRecord, stable_model_id_for
from src.model_request_controls import (
    DIALECT_CODEX_RESPONSES,
    DIALECT_OPENAI_CHAT,
    RequestControls,
)


def record_for(vendor="chatgpt_subscription", model="future-model", scope="endpoint-a", **control):
    return ModelCapabilityRecord(
        vendor=vendor,
        model_id=model,
        stable_model_id=stable_model_id_for(vendor, model, endpoint_id=scope),
        capability=mc.unknown_capability(),
        deterministic_controls=(mc.DeterministicControl.build(
            control=mc.CONTROL_TEMPERATURE, **control
        ),),
    )


@pytest.mark.parametrize("status", [mc.ASSERTION_CLAIMED, mc.ASSERTION_VERIFIED])
def test_transport_prohibition_wins_over_positive_model_evidence(status):
    record = record_for(status=status, source=mc.SOURCE_PROVIDER_READER)
    controls = RequestControls(
        "chatgpt_subscription", DIALECT_CODEX_RESPONSES, "future-model", "endpoint-a", record
    )
    assert not controls.permits(mc.CONTROL_TEMPERATURE, 0.2)
    evidence = controls.evidence_for(mc.CONTROL_TEMPERATURE)
    assert evidence[0].source == mc.SOURCE_PROVIDER_CONTRACT
    assert evidence[0].status == mc.ASSERTION_UNSUPPORTED
    assert evidence[1].status == status


@pytest.mark.parametrize("vendor,dialect", [
    ("openai", DIALECT_CODEX_RESPONSES),
    ("chatgpt_subscription", DIALECT_OPENAI_CHAT),
    ("openrouter", DIALECT_OPENAI_CHAT),
    ("unknown", DIALECT_OPENAI_CHAT),
])
def test_vendor_identity_alone_does_not_select_codex_contract(vendor, dialect):
    controls = RequestControls(vendor, dialect, "gpt-6-astra")
    assert controls.permits(mc.CONTROL_TEMPERATURE, 0.2)


@pytest.mark.parametrize("mutation", [
    {"vendor": "openai"},
    {"model_id": "Future-model"},
    {"stable_model_id": stable_model_id_for("chatgpt_subscription", "future-model", endpoint_id="other")},
    {"stable_model_id": stable_model_id_for("chatgpt_subscription", "future-model")},
])
def test_mismatched_or_unscoped_record_cannot_constrain_request(mutation):
    record = replace(record_for(status=mc.ASSERTION_UNSUPPORTED), **mutation)
    controls = RequestControls(
        "chatgpt_subscription", DIALECT_OPENAI_CHAT, "future-model", "endpoint-a", record
    )
    assert controls.permits(mc.CONTROL_TEMPERATURE, 0.2)


def test_absent_and_unknown_controls_preserve_existing_behavior():
    record = record_for(status=mc.ASSERTION_UNKNOWN, evidence={"allowed_values": []})
    controls = RequestControls(
        "chatgpt_subscription", DIALECT_OPENAI_CHAT, "future-model", "endpoint-a", record
    )
    assert controls.permits(mc.CONTROL_TEMPERATURE, 0.2)
    assert controls.permits(mc.CONTROL_REASONING_EFFORT, "high")


def test_exact_supported_values_are_enforced_without_inserting_defaults():
    record = record_for(status=mc.ASSERTION_CLAIMED, evidence={"allowed_values": [0.2], "default": 0.2})
    controls = RequestControls(
        "chatgpt_subscription", DIALECT_OPENAI_CHAT, "future-model", "endpoint-a", record
    )
    assert controls.permits(mc.CONTROL_TEMPERATURE, 0.2)
    assert not controls.permits(mc.CONTROL_TEMPERATURE, 0.3)
    assert not controls.permits(mc.CONTROL_TEMPERATURE, "0.2")
    assert not controls.permits(mc.CONTROL_TEMPERATURE, True)


@pytest.mark.parametrize("allowed", [[], "high", None, {"high": True}])
def test_malformed_positive_constraint_cannot_authorize_override(allowed):
    record = record_for(status=mc.ASSERTION_CLAIMED, evidence={"allowed_values": allowed})
    controls = RequestControls(
        "chatgpt_subscription", DIALECT_OPENAI_CHAT, "future-model", "endpoint-a", record
    )
    assert not controls.permits(mc.CONTROL_TEMPERATURE, 0.2)


def test_negative_model_evidence_wins_over_conflicting_positive_claim():
    record = record_for(status=mc.ASSERTION_UNSUPPORTED)
    positive = mc.DeterministicControl.build(
        control=mc.CONTROL_TEMPERATURE, status=mc.ASSERTION_CLAIMED
    )
    record = replace(record, deterministic_controls=(positive, *record.deterministic_controls))
    controls = RequestControls(
        "chatgpt_subscription", DIALECT_OPENAI_CHAT, "future-model", "endpoint-a", record
    )
    assert not controls.permits(mc.CONTROL_TEMPERATURE, 0.2)
