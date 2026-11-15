import pytest

from src import model_capabilities as mc
from src import model_capability_readers as readers
from src.model_capability_readers import chatgpt_subscription as reader


def _claims(record):
    return {claim.control: claim.to_dict() for claim in record.deterministic_controls}


def test_native_control_evidence_preserves_unknown_model_capabilities():
    record = reader.record_from_model({
        "slug": "Future-Model",
        "supported_reasoning_levels": [{"effort": "low"}, {"effort": "ultra"}],
        "default_reasoning_level": "ultra",
        "support_verbosity": True,
        "default_verbosity": "high",
    }, endpoint_id="account-route")
    assert record.model_id == "Future-Model"
    assert record.stable_model_id == "chatgpt_subscription|endpoint:account-route|future-model"
    assert record.capability.family == mc.FAMILY_UNKNOWN
    assert record.capability.capabilities == ()
    claims = _claims(record)
    assert claims[mc.CONTROL_REASONING_EFFORT]["evidence"] == {
        "allowed_values": ["low", "ultra"], "default": "ultra",
    }
    assert claims[mc.CONTROL_VERBOSITY]["evidence"] == {
        "allowed_values": ["low", "medium", "high"], "default": "high",
    }
    assert all(claim["status"] == mc.ASSERTION_CLAIMED for claim in claims.values())
    assert all(claim["source"] == mc.SOURCE_PROVIDER_READER for claim in claims.values())
    assert all(claim["confidence"] == mc.CONFIDENCE_PROVIDER_REPORTED for claim in claims.values())


def test_runtime_native_aliases_and_safe_future_values_are_preserved():
    record = reader.record_from_model({
        "slug": "future",
        "supportedReasoningEfforts": [
            " LOW ", {"level": "x-high"}, {"name": "persistent"}, "low",
            None, True, 1, {}, {"effort": False}, "unsafe value", "a" * 33,
        ],
        "defaultReasoningEffort": "x_high",
    })
    assert _claims(record)[mc.CONTROL_REASONING_EFFORT]["evidence"] == {
        "allowed_values": ["low", "x-high", "persistent"],
    }


@pytest.mark.parametrize("levels", [None, False, 1, "high", {"effort": "high"}, [False, 1, {}]])
def test_malformed_metadata_and_defaults_alone_do_not_establish_support(levels):
    record = reader.record_from_model({
        "slug": "gpt-6-reasoning-vision",
        "supported_reasoning_levels": levels,
        "default_reasoning_level": "high",
        "support_verbosity": "true",
        "default_verbosity": "high",
        "supports_reasoning": True,
        "capabilities": {"reasoning": True, "vision": True},
    })
    assert record.deterministic_controls == ()
    assert record.capability.capabilities == ()


def test_unadvertised_default_is_omitted_without_discarding_supported_values():
    record = reader.record_from_model({
        "slug": "future", "supported_reasoning_levels": ["low"],
        "default_reasoning_level": "high", "support_verbosity": True,
        "default_verbosity": {"value": "high"},
    })
    assert _claims(record)[mc.CONTROL_REASONING_EFFORT]["evidence"] == {"allowed_values": ["low"]}
    assert "default" not in _claims(record)[mc.CONTROL_VERBOSITY]["evidence"]


@pytest.mark.parametrize("factory", [reader.records_from_payload, readers.records_from_payload])
def test_duplicate_conflicts_cannot_select_control_truth_by_priority(factory):
    payload = {"models": [
        {"slug": "same", "priority": 1, "supported_reasoning_levels": ["low"]},
        {"slug": "same", "priority": 2, "supported_reasoning_levels": ["high"]},
        {"slug": "Same", "priority": 3, "supported_reasoning_levels": ["ultra"]},
    ]}
    kwargs = {"vendor": "chatgpt_subscription"} if factory is readers.records_from_payload else {}
    records = factory(payload, **kwargs)
    assert [record.model_id for record in records] == ["same", "Same"]
    assert records[0].deterministic_controls == ()
    assert _claims(records[1])[mc.CONTROL_REASONING_EFFORT]["evidence"] == {"allowed_values": ["ultra"]}


def test_identical_duplicate_claims_and_hidden_rows_preserve_visible_evidence():
    records = reader.records_from_payload({"models": [
        {"slug": "same", "priority": 2, "supported_reasoning_levels": ["low"]},
        {"slug": "same", "priority": 1, "supported_reasoning_levels": ["low"]},
        {"slug": "same", "visibility": "hidden", "supported_reasoning_levels": ["high"]},
        {"slug": 123, "supported_reasoning_levels": ["high"]},
    ]})
    assert len(records) == 1
    assert _claims(records[0])[mc.CONTROL_REASONING_EFFORT]["evidence"] == {"allowed_values": ["low"]}


def test_generic_inventory_does_not_promote_native_looking_control_fields():
    records = readers.records_from_payload({"data": [{
        "id": "gpt-6", "supported_reasoning_levels": ["ultra"], "support_verbosity": True,
    }]}, base_url="https://unknown.test/v1")
    assert records[0].deterministic_controls == ()
