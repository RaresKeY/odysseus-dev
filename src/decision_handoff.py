"""Persist observation receipts from the actual execution completion owner."""
from __future__ import annotations


def completion_checkpoint(bound, decision, ledger, journal):
    if bound is None:
        return None
    from src.agent_evidence import CompletionStatus
    from src.chat_automation import SqlAttemptStore
    from src.decisions.observability import default_observability
    reason = ("cancelled" if not bound.current() else
        "needs_input" if decision.status == CompletionStatus.AWAITING_USER else
        "budget" if decision.status == CompletionStatus.EXHAUSTED else
        "completed" if decision.can_complete else "review_required")
    events = ledger.to_list()
    attempts = SqlAttemptStore(bound.store).records_for_run(bound.owner, bound.session_id, bound.run_id)
    def refs(values):
        return tuple(dict.fromkeys(value for value in values if isinstance(value, str) and value))[:64]
    return default_observability(bound.store.session_factory).record_handoff(bound.owner, bound.session_id, bound.run_id,
        attempt_ids=refs(item['attempt_id'] for item in attempts),
        action_ids=refs(action.action_id for action in journal.actions),
        check_ids=refs(item.get('verification_id') for item in events),
        artifact_ids=refs(item.get('artifact_id') for item in events), stop_reason=reason,
        completion_verified=decision.status == CompletionStatus.VERIFIED and decision.can_complete)
