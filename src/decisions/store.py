"""Attempt-store contract. Production callers supply their canonical SQL store."""
from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Protocol

from .contracts import DecisionError


class AttemptStore(Protocol):
    def reserve(self, record: dict, *, max_calls: int) -> None: ...
    def settle(self, attempt_id: str, *, status: str, usage: dict | None = None,
               failure_kind: str | None = None) -> None: ...
    def records_for_run(self, owner: str | None, session_id: str, run_id: str) -> list[dict]: ...


def fallback_exemption(record, previous):
    """One extra reserved budget for the same failed approval, never a retry.

    The primary's unknown usage stays held. Subsequent ordinary decisions still
    stop on it. A linked fallback cannot exempt another pending call or a deny.
    """
    parent_id = record.get("fallback_of")
    if not parent_id:
        return None
    parent = next((r for r in previous if r["attempt_id"] == parent_id), None)
    failure = parent.get("failure_kind", "") if parent else ""
    if (not parent or parent["status"] not in {"unresolved", "rejected", "cancelled"}
            or record.get("purpose") != "approval" or parent.get("purpose") != "approval"
            or not record.get("provider_identity", {}).get("protocol", "").startswith("approval_")
            or parent.get("provider_identity", {}).get("protocol") not in {"systemone", "openai_json"}
            or not isinstance(failure, str) or not failure.startswith("decision_")
            or any(part in failure for part in ("budget", "attempt_store", "capture_", "usage_exceeded", "cancelled"))
            or not record.get("approval_action_digest")
            or any(record.get(k) != parent.get(k) for k in ("approval_action_digest", "policy_revision", "authority_epoch"))
            or any(r.get("fallback_of") == parent_id for r in previous)):
        raise DecisionError("decision_budget_unavailable")
    return parent_id


class MemoryAttemptStore:
    """Thread-safe injected store for offline tests; never a production default."""

    def __init__(self):
        self.records: dict[str, dict] = {}
        self._lock = RLock()

    def records_for_run(self, owner, session_id, run_id):
        with self._lock:
            return deepcopy([r for r in self.records.values()
                             if (r["owner"], r["session_id"], r["run_id"])
                             == (owner, session_id, run_id)])

    def reserve(self, record, *, max_calls):
        with self._lock:
            previous = self.records_for_run(record["owner"], record["session_id"], record["run_id"])
            exempt = fallback_exemption(record, previous)
            if (record["attempt_id"] in self.records or len(previous) >= max_calls
                    or any((r["status"] in {"reserved", "unresolved"} and r["attempt_id"] != exempt)
                           or r.get("failure_kind") == "decision_usage_exceeded_reservation" for r in previous)):
                raise DecisionError("decision_budget_unavailable")
            self.records[record["attempt_id"]] = deepcopy(record)

    def settle(self, attempt_id, *, status, usage=None, failure_kind=None):
        with self._lock:
            record = self.records[attempt_id]
            if record["status"] != "reserved":
                raise DecisionError("decision_attempt_already_settled")
            if status not in {"settled", "rejected", "unresolved", "cancelled"}:
                raise DecisionError("decision_invalid_attempt_status")
            record.update(status=status, usage=deepcopy(usage), failure_kind=failure_kind)
