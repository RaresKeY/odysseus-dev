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
            if (record["attempt_id"] in self.records or len(previous) >= max_calls
                    or any(r["status"] in {"reserved", "unresolved"} for r in previous)):
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
