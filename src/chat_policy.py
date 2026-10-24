"""Versioned chat approval policy. Classification never grants authority."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Mapping


AUTOMATION_CONTRACT_VERSION = 1
APPROVAL_MODES = frozenset({"always_ask", "auto_approve", "yolo"})
CONTROL_TOOLS = frozenset({"ask_user", "update_plan"})


class PolicyError(ValueError):
    """Invalid saved policy, never an instruction to fall back to wider access."""


class ReviewRequirement(str, Enum):
    HUMAN = "human"
    INDEPENDENT_MODEL = "independent_model"
    NONE = "none"
    DENY = "deny"


def normalized_owner(value: Any) -> str:
    return str(value or "").strip().casefold()


def _revision(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise PolicyError(f"Invalid {label}")
    return value


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise PolicyError("Invalid policy scope") from None


@dataclass(frozen=True)
class ChatPolicy:
    """A server-loaded policy snapshot, not an execution permission token."""

    owner: str
    session_id: str
    approval_mode: str
    policy_revision: int
    authority_epoch: int
    elevation_ceiling: bool
    scope_json: str
    entrypoint: str = "interactive"
    contract_version: int = AUTOMATION_CONTRACT_VERSION

    @classmethod
    def from_record(cls, record: Mapping[str, Any], *, owner: Any = None,
                    session_id: Any = None) -> "ChatPolicy":
        if not isinstance(record, Mapping):
            raise PolicyError("Chat policy is unavailable")
        version = record.get("contract_version", record.get("automation_contract_version"))
        if type(version) is not int or version != AUTOMATION_CONTRACT_VERSION:
            raise PolicyError("Unsupported chat policy version")
        mode = record.get("approval_mode")
        if not isinstance(mode, str) or mode not in APPROVAL_MODES:
            raise PolicyError("Invalid chat approval mode")
        revision = _revision(record.get("policy_revision"), "chat policy revision")
        epoch = _revision(record.get("authority_epoch"), "owner authority epoch")
        ceiling = record.get("elevation_ceiling", False)
        if type(ceiling) is not bool:
            raise PolicyError("Invalid elevation ceiling")
        actual_owner = normalized_owner(record.get("owner"))
        actual_session = record.get("session_id")
        if not isinstance(actual_session, str) or not actual_session:
            raise PolicyError("Chat policy requires a session")
        if owner is not None and actual_owner != normalized_owner(owner):
            raise PolicyError("Chat policy owner changed")
        if session_id is not None and actual_session != str(session_id):
            raise PolicyError("Chat policy session changed")
        entrypoint = record.get("entrypoint", "interactive")
        if not isinstance(entrypoint, str) or not entrypoint or len(entrypoint) > 80:
            raise PolicyError("Invalid chat policy entrypoint")
        # Freeze scope as canonical JSON. Neither a mutable caller dict nor a
        # later off/on ceiling cycle can change an existing approval binding.
        scope = _json({"elevation_ceiling": ceiling,
                       "elevation_grant": record.get("elevation_grant"),
                       "effective_roots": record.get("effective_roots", [])})
        return cls(actual_owner, actual_session, mode, revision, epoch, ceiling,
                   scope, entrypoint, version)

    def binding(self) -> dict[str, Any]:
        return {"contract_version": self.contract_version,
                "owner": self.owner, "session_id": self.session_id,
                "approval_mode": self.approval_mode,
                "policy_revision": self.policy_revision,
                "authority_epoch": self.authority_epoch,
                "scope_digest": hashlib.sha256(self.scope_json.encode()).hexdigest(),
                "entrypoint": self.entrypoint}

    def matches_binding(self, binding: Any) -> bool:
        return isinstance(binding, dict) and self.binding() == binding


def policy_matches_binding(binding: Any, record: Mapping[str, Any], *,
                           owner: Any = None, session_id: Any = None) -> bool:
    """Compare a fresh canonical record with an approval's frozen policy."""
    try:
        return ChatPolicy.from_record(record, owner=owner,
                                      session_id=session_id).matches_binding(binding)
    except (PolicyError, TypeError, ValueError):
        return False


def review_requirement(policy: ChatPolicy, *, tool_name: str,
                       deterministically_admitted: bool,
                       hard_denied: bool = False) -> ReviewRequirement:
    """Select only the reviewer; never authorize a missing tool/resource grant."""
    if hard_denied:
        return ReviewRequirement.DENY
    if not deterministically_admitted:
        return ReviewRequirement.HUMAN
    if tool_name in CONTROL_TOOLS:
        return ReviewRequirement.NONE
    # A saved interactive preference is not unattended/API-token consent.
    if policy.entrypoint not in {"interactive", "web", "webui"}:
        return ReviewRequirement.HUMAN
    if policy.approval_mode == "always_ask":
        return ReviewRequirement.HUMAN
    if policy.approval_mode == "auto_approve":
        return ReviewRequirement.INDEPENDENT_MODEL
    return ReviewRequirement.NONE
