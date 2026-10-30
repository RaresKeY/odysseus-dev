"""Approval mode is a reviewer choice, never execution or elevation authority."""
from copy import deepcopy

import pytest

from src.chat_policy import (ChatPolicy, PolicyError, ReviewRequirement,
                             policy_matches_binding, review_requirement)


def record(**changes):
    return {"contract_version": 1, "owner": "Alice", "session_id": "chat",
            "approval_mode": "auto_approve", "policy_revision": 2,
            "authority_epoch": 3, "elevation_ceiling": False,
            "elevation_grant": None, "effective_roots": ["workspace"], **changes}


@pytest.mark.parametrize("mode,expected", [
    ("always_ask", ReviewRequirement.HUMAN),
    ("auto_approve", ReviewRequirement.INDEPENDENT_MODEL),
    ("yolo", ReviewRequirement.NONE),
])
@pytest.mark.parametrize("ceiling", [False, True])
@pytest.mark.parametrize("grant", [None, {"root": "explicit-scoped-root"}])
def test_mode_selects_reviewer_without_conferring_execution_authority(mode, expected, ceiling, grant):
    policy = ChatPolicy.from_record(record(approval_mode=mode, elevation_ceiling=ceiling,
                                          elevation_grant=grant))
    assert review_requirement(policy, tool_name="write_file", deterministically_admitted=True) is expected
    assert review_requirement(policy, tool_name="write_file", deterministically_admitted=False) is (ReviewRequirement.DENY if mode == 'auto_approve' else ReviewRequirement.HUMAN)
    assert review_requirement(policy, tool_name="write_file", deterministically_admitted=True,
                              hard_denied=True) is ReviewRequirement.DENY


@pytest.mark.parametrize("entrypoint", ["scheduled", "audit", "api_token", "worker"])
def test_unattended_entrypoints_do_not_inherit_saved_yolo(entrypoint):
    policy = ChatPolicy.from_record(record(approval_mode="yolo", entrypoint=entrypoint))
    assert review_requirement(policy, tool_name="bash", deterministically_admitted=True) is ReviewRequirement.HUMAN


@pytest.mark.parametrize("changes", [
    {"contract_version": True}, {"contract_version": 2}, {"approval_mode": []},
    {"approval_mode": "full-auto"}, {"policy_revision": True}, {"policy_revision": -1},
    {"authority_epoch": None}, {"elevation_ceiling": 1}, {"session_id": ""},
    {"entrypoint": []}, {"effective_roots": [float("nan")]},
])
def test_invalid_policy_fails_closed(changes):
    with pytest.raises(PolicyError):
        ChatPolicy.from_record(record(**changes))


def test_binding_freezes_owner_session_revision_epoch_and_scope():
    original = record(elevation_grant={"roots": ["narrow"]})
    binding = ChatPolicy.from_record(original, owner="ALICE", session_id="chat").binding()
    assert policy_matches_binding(binding, original, owner="alice", session_id="chat")
    for changes in ({"owner": "bob"}, {"session_id": "other"}, {"policy_revision": 3},
                    {"authority_epoch": 4}, {"approval_mode": "yolo"},
                    {"elevation_ceiling": True}, {"elevation_grant": None},
                    {"effective_roots": ["other"]}, {"entrypoint": "scheduled"}):
        assert not policy_matches_binding(binding, {**original, **changes}, owner="alice", session_id="chat")
    changed = deepcopy(original)
    changed["elevation_grant"]["roots"].append("wider")
    assert not policy_matches_binding(binding, changed)
    # Turning the ceiling off and back on cannot revive a token: SQL increments
    # epoch/revision, and both are part of the immutable binding.
    assert not policy_matches_binding(binding, {**original, "authority_epoch": 5})
