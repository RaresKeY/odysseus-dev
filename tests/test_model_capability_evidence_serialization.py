import json

import pytest

from src import model_capabilities as mc
from src.model_capability_readers.base import ModelCapabilityRecord


@pytest.mark.parametrize(
    "status",
    [mc.ASSERTION_CLAIMED, mc.ASSERTION_VERIFIED, mc.ASSERTION_UNSUPPORTED, mc.ASSERTION_UNKNOWN],
)
def test_serialization_preserves_control_status_values_and_provenance(status):
    control = mc.DeterministicControl.build(
        control=mc.CONTROL_TEMPERATURE,
        status=status,
        source=mc.SOURCE_PROVIDER_DOCS_REGISTRY,
        confidence=mc.CONFIDENCE_REGISTRY,
        evidence={"allowed_values": [0, 1], "dialect": "example.responses", "reference": "contract-v1"},
        tested_at="2026-10-28T00:00:00Z",
    )
    record = ModelCapabilityRecord(
        vendor="example",
        model_id="model",
        capability=mc.unknown_capability(),
        deterministic_controls=(control,),
    )

    serialized = json.loads(json.dumps(record.to_dict()))

    # A discovery name is retained even for unknown or unsupported evidence;
    # runtime consumers need the detailed assertion, not a membership test.
    assert serialized["controls"] == ["temperature"]
    restored = mc.DeterministicControl.from_dict(serialized["deterministic_controls"][0])
    assert restored == control
    assert restored.status == status


def test_serialization_preserves_conflicting_control_claims_without_collapsing_them():
    claimed = mc.DeterministicControl.build(
        control=mc.CONTROL_TEMPERATURE,
        status=mc.ASSERTION_CLAIMED,
        source=mc.SOURCE_PROVIDER_READER,
        confidence=mc.CONFIDENCE_PROVIDER_REPORTED,
    )
    unsupported = mc.DeterministicControl.build(
        control=mc.CONTROL_TEMPERATURE,
        status=mc.ASSERTION_UNSUPPORTED,
        source=mc.SOURCE_PROVIDER_DOCS_REGISTRY,
        confidence=mc.CONFIDENCE_REGISTRY,
        evidence={"scope": "transport"},
    )
    record = ModelCapabilityRecord(
        vendor="example",
        model_id="model",
        capability=mc.unknown_capability(),
        deterministic_controls=(claimed, unsupported),
    )

    serialized = record.to_dict()

    assert serialized["controls"] == ["temperature"]
    restored = tuple(mc.DeterministicControl.from_dict(item) for item in serialized["deterministic_controls"])
    assert restored == (claimed, unsupported)


def test_serialization_preserves_negative_feature_evidence_and_keeps_raw_opt_in():
    unsupported = mc.CapabilityAssertion.build(
        capability=mc.CAP_TOOL_CALL,
        status=mc.ASSERTION_UNSUPPORTED,
        source=mc.SOURCE_PROVIDER_READER,
        confidence=mc.CONFIDENCE_PROVIDER_REPORTED,
        evidence={"native_field": "supports_tools", "native_value": False},
    )
    record = ModelCapabilityRecord(
        vendor="example",
        model_id="model",
        capability=mc.unknown_capability(),
        capability_assertions=(unsupported,),
        raw={"private_catalog_field": "not-for-default-projection"},
    )

    serialized = json.loads(json.dumps(record.to_dict()))

    assert serialized["features"] == []
    assert mc.CapabilityAssertion.from_dict(serialized["capability_assertions"][0]) == unsupported
    assert "raw" not in serialized
    assert record.to_dict(include_raw=True)["raw"] == dict(record.raw)
