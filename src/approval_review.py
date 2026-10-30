"""Independent second-call review over canonical exact-action proposals.

This service proposes no tools and invokes no executor. Only an already
admitted action may receive an automatic single-action grant from the existing
approval store. The dispatcher still owns current resource/policy enforcement.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import inspect
import re
import time
from typing import Any, Awaitable, Callable, Mapping

from src.chat_policy import ChatPolicy, PolicyError, ReviewRequirement, review_requirement
from src.decisions import DecisionContext, DecisionError, DecisionRequest, DecisionResult
from src.decisions.contracts import validate_answers, valid_usage
from src.tool_approvals import ExactToolApproval, PendingToolApproval, ToolApprovalStore


from src.approval_policy import (
    APPROVAL_REVIEW_POLICY, INSTRUCTIONS, QUESTIONS, combine_assessment,
)
from src.approval_context import assemble
from src.decisions.store import OUTAGE_FAILURES


# Configuration absence may use the exact selected conversation reviewer. Wire
# contract failures, unknown exceptions and identity mismatches are final.
_FALLBACK_FAILURES = OUTAGE_FAILURES | frozenset({
    "decision_provider_not_configured", "decision_endpoint_unavailable",
    "decision_endpoint_protocol_unavailable", "decision_model_unavailable",
    "decision_credentials_missing",
})


@dataclass(frozen=True)
class ReviewOutcome:
    status: str
    pending: PendingToolApproval
    approval: ExactToolApproval | None = None
    reason: str = ""
    decision: dict[str, Any] | None = None

    def tool_result(self) -> dict[str, Any]:
        """Always Ask pauses; unsuccessful Auto-Approve stops without a card."""
        if self.status in {"approved", "bypass"}:
            raise ValueError("An allowed review is not a paused tool result")
        result = {"output": self.reason or "This action needs human review.",
                  "exit_code": None, "executed": False,
                  "approval_review_status": self.status}
        if self.status == "manual":
            result.update(approval_required=True,
                          ask_user=self.pending.public_payload(reason=self.reason))
        else:
            result.update(error=self.reason or 'Automatic review stopped this action.', exit_code=1,
                          blocked=True, failure_kind="approval_" + self.status)
        if self.decision is not None:
            result["approval_review"] = self.decision
        return result


async def _cancelled(callback: Callable | None) -> bool:
    if callback is None:
        return False
    try:
        value = callback()
        if inspect.isawaitable(value):
            value = await value
        # A malformed cancellation answer fails closed.
        return value is not False
    except Exception:
        return True


def _decision_projection(result: Any, request: DecisionRequest) -> dict[str, Any]:
    """Validate even injected evaluators; a high-confidence label is not enough."""
    from src.approval_fallback import ConversationAssessment, FALLBACK_PROMPT_SHA256
    if isinstance(result, ConversationAssessment):
        from src.approval_policy import combine_values
        if (not isinstance(result.attempt_id, str) or not 1 <= len(result.attempt_id) <= 160
                or not isinstance(result.provider_identity, dict)
                or not result.provider_identity.get("model") or valid_usage(result.usage) is None):
            raise DecisionError("decision_invalid_response")
        return {**combine_values(result.labels, uncertain=result.uncertain),
                "decision_source": "conversation_model", "confidence": None, "probabilities": None,
                "assessment_contract": "categorical_uncertainty/v1",
                "prompt_sha256": FALLBACK_PROMPT_SHA256,
                "attempt_id": result.attempt_id, "provider_identity": result.provider_identity,
                "usage": valid_usage(result.usage),
                "evidence": {"user_items": len(request.state["server_context"]["user_evidence"]),
                             "omissions": len(request.state["omissions"]), "script_items": len(request.state["declared_scripts"])}}
    if not isinstance(result, DecisionResult):
        raise DecisionError("decision_invalid_response")
    identity = result.provider_identity
    if (not isinstance(identity, dict) or not isinstance(identity.get("model"), str)
            or not identity["model"] or not isinstance(result.attempt_id, str)
            or not 1 <= len(result.attempt_id) <= 160):
        raise DecisionError("decision_invalid_response")
    answers = validate_answers({"model": identity["model"], "answers": result.answers,
                                "usage": result.usage}, request.questions(), model=identity["model"])
    answer = answers[request.question_id]
    if (result.choice != answer["choice"] or result.confidence != answer["confidence"]
            or result.probabilities != answer["probabilities"]):
        raise DecisionError("decision_projection_mismatch")
    return {**combine_assessment(answers), "choice": answer["choice"],
            "answers": answers,
            "evidence": {"user_items": len(request.state["server_context"]["user_evidence"]),
                         "omissions": len(request.state["omissions"]),
                         "script_items": len(request.state["declared_scripts"])},
            "confidence": answer["confidence"],
            "probabilities": dict(answer["probabilities"]),
            "attempt_id": result.attempt_id,
            "provider_identity": {key: identity[key] for key in (
                "model", "endpoint_id", "protocol", "distribution_source", "configuration_digest"
            ) if key in identity}, "usage": valid_usage(result.usage)}


def _authority_admits(pending: PendingToolApproval) -> bool:
    """A reviewer cannot manufacture missing deterministic request authority."""
    try:
        from src.agent_runtime.authority import ExactOperation
        authority = pending.request_authority
        return (authority is not None and pending.backend_operation is not None
                and authority.bound_to(owner=pending.owner, session_id=pending.session_id,
                                       workspace=pending.workspace)
                and authority.permits(ExactOperation.normalize(pending.tool_name, pending.content)))
    except (ValueError, TypeError, AttributeError, RuntimeError):
        return False


class ApprovalReviewService:
    """One review with at most one technical fallback; never a broader grant."""

    def __init__(self, store: ToolApprovalStore, *, clock: Callable[[], float] = time.time):
        self.store, self.clock = store, clock

    def _retire(self, pending: PendingToolApproval) -> None:
        self.store.discard(pending.approval_id, owner=pending.owner, session_id=pending.session_id)

    def _current(self, pending: PendingToolApproval, policy: ChatPolicy) -> bool:
        return (self.store.peek(pending.approval_id) is pending
                and pending.expires_at > self.clock()
                and policy.matches_binding(pending.policy_binding)
                and pending.policy_is_current())

    async def review(
        self,
        pending: PendingToolApproval,
        policy_record: Mapping[str, Any],
        *,
        deterministically_admitted: bool,
        evaluator: Callable[[DecisionRequest, DecisionContext], Awaitable[DecisionResult]] | None = None,
        fallback_evaluator: Callable | None = None,
        cancelled: Callable | None = None,
        deadline: float | None = None,
        max_calls: int = 32,
        evidence: dict | None = None,
        hard_denied: bool = False,
    ) -> ReviewOutcome:
        try:
            policy = ChatPolicy.from_record(policy_record, owner=pending.owner,
                                            session_id=pending.session_id)
        except (PolicyError, TypeError, ValueError):
            self._retire(pending)
            return ReviewOutcome("stale", pending, reason="Chat policy changed. Request this action again.")
        if await _cancelled(cancelled):
            self._retire(pending)
            return ReviewOutcome("cancelled", pending, reason="The action was cancelled before review.")
        if not self._current(pending, policy):
            self._retire(pending)
            return ReviewOutcome("stale", pending, reason="The action or its approval policy is no longer current.")
        requirement = review_requirement(policy, tool_name=pending.tool_name,
                                         deterministically_admitted=(deterministically_admitted
                                                                     and _authority_admits(pending)),
                                         hard_denied=hard_denied)
        if requirement is ReviewRequirement.DENY:
            self._retire(pending)
            return ReviewOutcome("blocked", pending, reason="This action is outside current permissions.")
        if requirement is ReviewRequirement.HUMAN:
            return ReviewOutcome("manual", pending, reason="Review this exact action before it runs.")
        if requirement is ReviewRequirement.NONE:
            self._retire(pending)
            return ReviewOutcome("bypass", pending)
        if evaluator is None and fallback_evaluator is None:
            self._retire(pending)
            return ReviewOutcome("unavailable", pending,
                                 reason="Auto-Approve review is unavailable. This action was stopped.")
        if not self.store.begin_review(pending):
            self._retire(pending)
            return ReviewOutcome("unavailable", pending,
                                 reason="This action already has a review attempt. It was stopped; no retry was made.")

        try:
            state = assemble(pending, policy, evidence)
            request = DecisionRequest(
                "approval", state, QUESTIONS["risk_level"], question_id="risk_level",
                additional_questions={key: value for key, value in QUESTIONS.items() if key != "risk_level"},
                instructions=INSTRUCTIONS, server_user_evidence=True,
            )
            request.questions()  # Validate the full policy; never truncate it.
        except DecisionError as error:
            self._retire(pending)
            return ReviewOutcome("error", pending, reason="Auto-Approve context is unavailable or exceeds its safe input budget.",
                decision={"policy": APPROVAL_REVIEW_POLICY, "decision_source": "deterministic",
                          "assessed": False, "failure_kind": error.code})
        shared_deadline = min(deadline if deadline is not None else float("inf"), time.monotonic() + 60)
        lineage = tuple(getattr(pending.bound_turn, "review_route_lineage", ()))
        if lineage:
            from src.approval_fallback import model_snapshot
            if (len(lineage) > 16 or any(not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item) for item in lineage)
                    or lineage[-1] != pending.bound_turn.review_model_origin
                    or model_snapshot(pending.bound_turn) != lineage[-1]):
                self._retire(pending)
                return ReviewOutcome("stale", pending, reason="The originating review route changed.")
        context = DecisionContext(pending.owner, pending.session_id, pending.origin_run_id,
                                  policy.policy_revision, policy.authority_epoch,
                                  deadline=shared_deadline, cancelled=cancelled, max_calls=max_calls,
                                  approval_route_lineage=lineage)
        source, fallback_reason, primary_attempt_id = "jev", None, None

        async def assess(callback, attempt_context):
            remaining = attempt_context.deadline - time.monotonic()
            if remaining <= 0:
                raise DecisionError("decision_deadline_exceeded")
            async with asyncio.timeout(remaining):
                return _decision_projection(await callback(request, attempt_context), request)

        try:
            # Separate invocation: never reuse an execution-model completion,
            # response rationale or browser-action selection as its own review.
            try:
                if evaluator is None:
                    raise DecisionError("decision_provider_not_configured")
                primary_deadline = (min(shared_deadline, time.monotonic() + max(0, (shared_deadline - time.monotonic()) / 2))
                                    if fallback_evaluator else shared_deadline)
                projection = await assess(evaluator, replace(context, deadline=primary_deadline))
            except Exception as primary_error:
                terminal_failure = (getattr(primary_error, "approval_terminal_failure", None)
                                    or getattr(primary_error.__cause__, "approval_terminal_failure", None))
                if terminal_failure:
                    raise DecisionError(terminal_failure, attempt_id=(getattr(primary_error, "attempt_id", None)
                        or getattr(primary_error.__cause__, "attempt_id", None))) from primary_error
                code = (primary_error.code if isinstance(primary_error, DecisionError)
                        else "decision_deadline_exceeded" if isinstance(primary_error, TimeoutError) else "decision_failed")
                # Accounting, capture and cancellation failures are not
                # permission to spend another attempt or discard a restriction.
                response_received = bool(getattr(primary_error, "approval_response_received", False)
                    or getattr(primary_error.__cause__, "approval_response_received", False))
                if (fallback_evaluator is None or code not in _FALLBACK_FAILURES
                        or response_received
                        or await _cancelled(cancelled) or not self._current(pending, policy)):
                    raise
                source, fallback_reason = "conversation_model", code
                primary_attempt_id = getattr(primary_error, "attempt_id", None)
                if isinstance(primary_error, TimeoutError):
                    primary_attempt_id = getattr(primary_error.__cause__, "attempt_id", None)
                projection = await assess(fallback_evaluator, replace(context, fallback_of=primary_attempt_id))
                projection.update(fallback_reason=fallback_reason, primary_attempt_id=primary_attempt_id)
                if getattr(primary_error, "approval_outage", False):
                    projection["approval_outage_of"] = primary_attempt_id
        except asyncio.CancelledError:
            self._retire(pending)
            raise
        except Exception as error:
            if await _cancelled(cancelled):
                self._retire(pending)
                return ReviewOutcome("cancelled", pending, reason="The action was cancelled during review.")
            if not self._current(pending, policy):
                self._retire(pending)
                return ReviewOutcome("stale", pending, reason="The action changed while review was pending.")
            self._retire(pending)
            return ReviewOutcome("error", pending,
                                 reason="Auto-Approve review could not be verified. This action was stopped.",
                                 decision={"policy": APPROVAL_REVIEW_POLICY, "decision_source": source,
                                           "fallback_reason": fallback_reason, "primary_attempt_id": primary_attempt_id,
                                           "attempt_id": error.attempt_id if isinstance(error, DecisionError) else None,
                                           "assessed": False, "failure_kind": error.code if isinstance(error, DecisionError)
                                           else "decision_deadline_exceeded" if isinstance(error, TimeoutError) else "decision_failed"})

        if await _cancelled(cancelled):
            self._retire(pending)
            return ReviewOutcome("cancelled", pending, reason="The action was cancelled during review.",
                                 decision=projection)
        if not self._current(pending, policy):
            self._retire(pending)
            return ReviewOutcome("stale", pending, reason="The action changed while review was pending.",
                                 decision=projection)
        if projection["status"] != "approved":
            self._retire(pending)
            return ReviewOutcome(projection["status"], pending,
                                 reason=projection["reason"] + " This action was stopped.", decision=projection)
        grant = self.store.consume_reviewed(pending, owner=pending.owner,
                                           session_id=pending.session_id,
                                           decision_attempt_id=projection["attempt_id"])
        if grant is None:
            return ReviewOutcome("stale", pending, reason="This action no longer has a current approval.",
                                 decision=projection)
        grant.review_decision = projection
        return ReviewOutcome("approved", pending, approval=grant, decision=projection)
