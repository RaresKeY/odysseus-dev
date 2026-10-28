"""Attempt-store contract. Production callers supply their canonical SQL store."""
from __future__ import annotations

from copy import deepcopy
import re
from threading import RLock
from typing import Protocol

from .contracts import DecisionError


class AttemptStore(Protocol):
    def reserve(self, record: dict, *, max_calls: int) -> None: ...
    def settle(self, attempt_id: str, *, status: str, usage: dict | None = None,
               failure_kind: str | None = None) -> None: ...
    def records_for_run(self, owner: str | None, session_id: str, run_id: str) -> list[dict]: ...


# Explicit technical response failures only. A valid verdict never enters this
# set; local context, accounting, cancellation and identity changes do not either.
OUTAGE_FAILURES = frozenset({"decision_http_error", "decision_transport_failed",
    "decision_deadline_exceeded", "decision_invalid_json", "decision_invalid_response",
    "decision_invalid_answer", "decision_invalid_answers", "decision_invalid_distribution",
    "decision_choice_mismatch", "decision_model_mismatch", "decision_usage_unavailable",
    "decision_encoding_refused", "decision_response_too_large", "decision_incomplete_response",
    "decision_redirect_refused"})
_BINDING = ("owner", "session_id", "run_id", "policy_revision", "authority_epoch")


def _failed_primary(parent):
    failure = parent.get("failure_kind", "") if parent else ""
    return (parent and parent.get("status") in {"unresolved", "rejected", "cancelled"}
            and parent.get("purpose") == "approval"
            and parent.get("provider_identity", {}).get("protocol") in {"systemone", "openai_json"}
            and isinstance(failure, str) and failure.startswith("decision_")
            and not any(part in failure for part in
                        ("budget", "attempt_store", "capture_", "usage_exceeded", "cancelled", "endpoint_changed")))


def approval_outage(previous, *, identity, configuration_sha256, context, action_digest):
    """Find a confirmed run-local outage without making another primary call.

    Only a settled independent review establishes the conversation route. Its
    next reservation rechecks this chain atomically and retains the primary hold.
    """
    roots = [r for r in previous if _failed_primary(r)
             and r.get("failure_kind") in OUTAGE_FAILURES
             and r.get("provider_identity") == identity
             and r.get("configuration_sha256") == configuration_sha256
             and all(r.get(key) == getattr(context, key) for key in _BINDING)]
    confirmed = [(root, linked) for root in roots
                 for linked in previous if linked.get("fallback_of") == root["attempt_id"]
                 and linked.get("status") == "settled"]
    if len(confirmed) != 1:
        return None
    root, _ = confirmed[0]
    if any(r.get("approval_action_digest") == action_digest for r in previous
           if r["attempt_id"] == root["attempt_id"]
           or r.get("fallback_of") == root["attempt_id"]
           or r.get("approval_outage_of") == root["attempt_id"]):
        raise DecisionError("decision_budget_unavailable")
    return root


def _route_continues(record, prior):
    """Only a canonical registered handoff can change an established reviewer.

    The route lineage comes from typed server context, not model/request state.
    Its final origin must equal the actual provider resolver's registration seal.
    """
    old, new = prior.get("approval_route_lineage", []), record.get("approval_route_lineage", [])
    if record.get("provider_identity") == prior.get("provider_identity"):
        return new == old
    return (isinstance(old, list) and isinstance(new, list) and 1 <= len(old) < len(new) <= 16
            and all(isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item) for item in new)
            and new[:len(old)] == old
            and prior.get("approval_route_origin") == old[-1]
            and record.get("approval_route_origin") == new[-1])


def fallback_exemption(record, previous):
    """Exempt only the original primary hold for a bound approval reservation."""
    parent_id = record.get("fallback_of") or record.get("approval_outage_of")
    if not parent_id:
        return None
    parent = next((r for r in previous if r["attempt_id"] == parent_id), None)
    if (not _failed_primary(parent) or record.get("purpose") != "approval"
            or not record.get("provider_identity", {}).get("protocol", "").startswith("approval_")
            or not record.get("approval_action_digest")
            or any(record.get(k) != parent.get(k) for k in _BINDING)):
        raise DecisionError("decision_budget_unavailable")
    linked = [r for r in previous if r.get("fallback_of") == parent_id]
    if record.get("approval_outage_of"):
        established = [r for r in previous if r.get("status") == "settled"
                       and (r.get("fallback_of") == parent_id or r.get("approval_outage_of") == parent_id)]
        latest = max(established, key=lambda r: len(r.get("approval_route_lineage", [])), default={})
        if (record.get("fallback_of") or parent.get("failure_kind") not in OUTAGE_FAILURES
                or type(parent.get("call_limit")) is not int or not 1 <= parent["call_limit"] <= 128
                or len(linked) != 1 or linked[0].get("status") != "settled"
                or linked[0].get("approval_action_digest") != parent.get("approval_action_digest")
                or any(linked[0].get(k) != parent.get(k) for k in _BINDING)
                or not _route_continues(record, latest)
                or record["approval_action_digest"] == parent.get("approval_action_digest")
                or any(r.get("approval_action_digest") == record["approval_action_digest"]
                       for r in previous if r.get("approval_outage_of") == parent_id)):
            raise DecisionError("decision_budget_unavailable")
    elif record["approval_action_digest"] != parent.get("approval_action_digest") or linked:
        raise DecisionError("decision_budget_unavailable")
    return parent_id


def reservation_limit(record, previous, max_calls):
    parent_id = record.get("fallback_of") or record.get("approval_outage_of")
    parent = next((r for r in previous if r["attempt_id"] == parent_id), {})
    limit = parent.get("call_limit", max_calls)
    return min(max_calls, limit) if type(limit) is int and 1 <= limit <= 128 else max_calls


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
            if (record["attempt_id"] in self.records or len(previous) >= reservation_limit(record, previous, max_calls)
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
