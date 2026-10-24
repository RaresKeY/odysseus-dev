"""Server lifecycle facts; historical text is never a pending permission.

Adapted from the task-state separation in Odysseus's earlier Jev concern
series. The canonical SQL store owns these facts; this module does not read
transcripts, classify intent, create grants, or choose execution policy.
"""
from __future__ import annotations

import copy
import math

ACTIVE_STATUSES = frozenset({'preparing', 'bound', 'running', 'awaiting_approval'})
TERMINAL_STATUSES = frozenset({'completed', 'failed', 'cancelled'})
STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES


class LifecycleError(ValueError):
    pass


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def transition_status(current: str, requested: str) -> str:
    """Return the committed status, with terminal states never revived.

    Late stream-finally blocks are harmless no-ops after replacement or
    cancellation. Preparing cannot be re-entered after its lease is released.
    Exact human continuation may move an awaiting action back to bound/running.
    """
    if current not in STATUSES or requested not in STATUSES:
        raise LifecycleError('automation_lifecycle_invalid_status')
    if current in TERMINAL_STATUSES:
        return current
    if current != 'preparing' and requested == 'preparing':
        raise LifecycleError('automation_preparation_cannot_restart')
    if current == 'preparing' and requested not in {'preparing', 'bound', 'failed', 'cancelled'}:
        raise LifecycleError('automation_preparation_not_admitted')
    return requested


def submission_is_current(status: str, *, lease_token=None, expected_token=None,
                          lease_expires_at=None, now: float) -> bool:
    """A preparation requires its live lease; admitted turns release it."""
    if status not in ACTIVE_STATUSES or not _finite(now):
        return False
    if status != 'preparing':
        return True
    return (isinstance(lease_token, str) and bool(lease_token)
            and isinstance(expected_token, str) and lease_token == expected_token
            and _finite(lease_expires_at) and lease_expires_at > now)


def pending_task_projection(raw_ask: str, status: str, *, previous_answer=None,
                            pending_question=None, pending_expires_at=None,
                            now: float) -> dict:
    """Separate current/pending work from historical request/answer evidence.

    Only the canonical server card can populate pending_question. A completed
    answer, even one ending in a question mark or "yes", remains historical.
    Resolved/expired cards cannot become pending again through projection.
    Nothing in this projection grants exact approval or inherited authority.
    """
    if (not isinstance(raw_ask, str) or status not in STATUSES or not _finite(now)
            or previous_answer is not None and not isinstance(previous_answer, str)):
        raise LifecycleError('automation_lifecycle_invalid_projection')
    result = {'previous_request': raw_ask, 'previous_answer': previous_answer,
              'previous_status': status, 'active_task': None, 'pending_question': None}
    if status in {'preparing', 'bound', 'running'}:
        result['active_task'] = raw_ask
    elif status == 'awaiting_approval':
        if (not isinstance(pending_question, dict)
                or not isinstance(pending_question.get('question'), str)
                or not pending_question['question'].strip()
                or not _finite(pending_expires_at)):
            raise LifecycleError('automation_lifecycle_invalid_pending_card')
        if pending_question.get('resolved') is True:
            result['previous_status'] = 'resolved'
        elif pending_expires_at <= now:
            result['previous_status'] = 'expired'
        else:
            result.update(active_task=raw_ask, pending_question=copy.deepcopy(pending_question))
    return result
