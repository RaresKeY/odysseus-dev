"""Bounded server-provenance evidence for synchronous action review.

No transcript role guessing, filesystem traversal, credential lookup or model
summarization. Missing authorization history fails closed instead of dropping
an old restriction. Optional observations cannot displace the exact action.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid

from src.decisions.contracts import DecisionError, encode

CONTEXT_VERSION = "odysseus-approval-context/v1"
MAX_USER_BYTES = 16 * 1024
MAX_USER_ITEMS = 128
MAX_STATE_BYTES = 32 * 1024
MAX_OBSERVATION_BYTES = 4096

# Deliberately no arbitrary secret-store lookup. Recognizable literal secrets
# cause review to stop, never approval of a redacted approximation of an action.
_SECRET = re.compile(r"(?i)(?:bearer\s+[a-z0-9._~+/=-]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
                     r"(?:password|api[_-]?key|access[_-]?token|client[_-]?secret)[\"']?\s*[=:]\s*[^\s,;]{4,})")


def sensitive(value):
    if isinstance(value, str):
        return bool(_SECRET.search(value))
    if isinstance(value, dict):
        return any(sensitive(key + "=" + str(item)) if key.casefold() in {
            "password", "api_key", "apikey", "access_token", "client_secret"} else sensitive(item)
            for key, item in value.items())
    if isinstance(value, list):
        return any(sensitive(item) for item in value)
    return False


def remember_observation(bound, tool, result):
    """Keep bounded tool evidence in the active turn only, never as consent."""
    try:
        _remember_observation(bound, tool, result)
    except Exception:
        # A malformed diagnostic must not turn a completed effect into an
        # apparent failure and invite an execution retry.
        bound.review_observations_omitted = True


def _remember_observation(bound, tool, result):
    if not isinstance(result, dict):
        return
    item = {"id": uuid.uuid4().hex, "source": "tool_result", "trusted": False,
            "tool": str(tool)[:160], "fields": {}, "omitted": False}
    for name in ("output", "error", "failure_kind", "exit_code", "executed"):
        value = result.get(name)
        if isinstance(value, str):
            if sensitive(value):
                item["omitted"] = True
                continue
            # Budget is UTF-8 bytes; no partial codepoint or silent truncation.
            raw = value.encode("utf-8", errors="replace")
            item["fields"][name] = raw[:1024].decode("utf-8", errors="ignore")
            item["omitted"] |= len(raw) > 1024
        elif value is None or type(value) in (bool, int):
            item["fields"][name] = value
    previous = list(getattr(bound, "review_observations", []))[-7:]
    retained = [*previous, item]
    omitted = bool(getattr(bound, "review_observations_omitted", False)) or len(previous) < len(getattr(bound, "review_observations", []))
    while len(encode(retained)) > MAX_OBSERVATION_BYTES - 256 and len(retained) > 1:
        retained.pop(0)
        omitted = True
    bound.review_observations = retained
    bound.review_observations_omitted = omitted


def fingerprint(tool, content):
    from src.agent_runtime.authority import ExactOperation
    operation = ExactOperation.normalize(tool, content)
    return hashlib.sha256(encode({"tool": operation.transport_tool, "input": operation.input})).hexdigest()


def user_snapshot(bound):
    """Read the exact server submission chain, never a mutable model history."""
    from src.chat_automation import _models, owner_key, digest
    d = _models()
    if bound.ephemeral_semantics:
        if len(bound.approval_evidence) > 64:
            raise DecisionError("approval_context_budget")
        # Private turns deliberately have no durable earlier evidence. A claim
        # about prior consent is therefore unavailable, not reconstructed.
        with bound.store.transaction() as db:
            bound.store.state(db, bound.owner, bound.session_id)
            if db.query(d.ChatAutomationSubmission.submission_id).filter(
                    d.ChatAutomationSubmission.owner_key == owner_key(bound.owner),
                    d.ChatAutomationSubmission.session_id == bound.session_id,
                    d.ChatAutomationSubmission.submission_id != bound.submission_id).first():
                raise DecisionError("approval_context_unavailable")
        items = [{"source": "current_private_submission", "id": bound.submission_id,
                  "text": bound.raw_ask, "action_decisions": list(bound.approval_evidence)}]
        coverage = "current_private_turn_only"
    else:
        items, seen, identifier = [], set(), bound.submission_id
        with bound.store.transaction() as db:
            bound.store.state(db, bound.owner, bound.session_id)
            while identifier:
                if identifier in seen or len(items) >= MAX_USER_ITEMS:
                    raise DecisionError("approval_context_budget")
                seen.add(identifier)
                row = db.query(d.ChatAutomationSubmission).filter_by(
                    submission_id=identifier, owner_key=owner_key(bound.owner),
                    session_id=bound.session_id).first()
                if (row is None or row.current_ask is None
                        or digest(row.current_ask) != row.intent_digest):
                    raise DecisionError("approval_context_unavailable")
                events = row.approval_evidence or []
                if not isinstance(events, list) or len(events) > 64:
                    raise DecisionError("approval_context_budget")
                items.append({"source": "authenticated_submission", "id": row.submission_id,
                              "text": row.current_ask, "action_decisions": events})
                if len(encode(items)) > MAX_USER_BYTES:
                    raise DecisionError("approval_context_budget")
                identifier = row.previous_submission_id
        items.reverse()
        coverage = "complete_server_submission_chain"
    snapshot = {"version": CONTEXT_VERSION, "coverage": coverage, "user_evidence": items}
    if len(encode(snapshot)) > MAX_USER_BYTES:
        raise DecisionError("approval_context_budget")
    return encode(snapshot).decode()


def record_human_decision(pending, decision):
    """Retain exact human choices, not a reusable grant or model approval."""
    bound = pending.bound_turn
    if bound is None:
        return
    from src.chat_automation import _models, owner_key
    event = {"source": "human_exact_action", "id": pending.approval_id,
             "decision": decision, "operation_sha256": fingerprint(pending.tool_name, pending.content)}
    if bound.ephemeral_semantics:
        bound.approval_evidence = [*bound.approval_evidence[:64], event]
    else:
        with bound.store.transaction() as db:
            row = db.query(_models().ChatAutomationSubmission).filter_by(
                submission_id=bound.submission_id, owner_key=owner_key(bound.owner),
                session_id=bound.session_id).with_for_update().one()
            # Overflow is retained as a 65th marker and blocks later Auto review.
            # It never prevents the user from using Always Ask.
            row.approval_evidence = [*(row.approval_evidence or [])[:64], event]
    try:
        from src.decisions.observability import default_observability
        default_observability(bound.store.session_factory).record_approval(bound.owner, bound.session_id,
            bound.run_id, {"decision_source": "human", "assessed": False,
                           "status": "approved" if decision == "approve_once" else "denied"})
    except Exception:
        pass


def assemble(pending, policy, evidence=None):
    """Freeze mandatory exact inputs; omit only optional observations explicitly."""
    if pending.review_context_json:
        user = json.loads(pending.review_context_json)
    else:
        # Non-chat service callers already supply a server-owned pending ask.
        user = {"version": CONTEXT_VERSION, "coverage": "current_submission_only",
                "user_evidence": [{"source": "server_pending_request", "id": pending.submission_id,
                                   "text": pending.request_text, "action_decisions": []}]}
    # Resource owners use dataclass tuples/string enums in their serializers.
    # Normalize only those typed server snapshots, not arbitrary decision input.
    def snapshot(value):
        return json.loads(json.dumps(value.to_dict(), allow_nan=False)) if value is not None else None

    bindings = {name: snapshot(value) for name, value in (
        ("filesystem", pending.resource_operation), ("backend", pending.backend_operation),
        ("owned", pending.owned_operation), ("process", pending.process_operation),
        ("browser", pending.browser_operation))}
    state = {
        "server_context": {**user, "policy": {**policy.binding(), **json.loads(policy.scope_json)},
            "owner": pending.owner, "session_id": pending.session_id,
            "run_id": pending.origin_run_id, "submission_id": pending.submission_id,
            "action_digest": pending.digest, "workspace": pending.workspace,
            "authority": snapshot(pending.request_authority), "resources": bindings,
            "effects": list(pending.effects)},
        "proposed_operation": {"tool": pending.tool_name, "input": pending.content,
                               "operation_sha256": fingerprint(pending.tool_name, pending.content)},
        "untrusted_evidence": {},
        "declared_scripts": [item.to_dict() for item in pending.review_files],
        "omissions": ["Only directly declared simple shell script operands are inspected. No transitive dependency inspection, external history resolver, inherited project instructions or worker/root delegation evidence. Do not infer those facts."],
    }
    backend = pending.backend_operation.resource
    if backend.namespace == "mcp":
        from src.tool_execution import get_mcp_manager
        manager = get_mcp_manager()
        try:
            state["mcp_metadata"] = manager.review_annotations(backend)
        except (ValueError, AttributeError, StopIteration):
            raise DecisionError("approval_context_unavailable") from None
    mandatory = encode(state)
    if len(mandatory) > MAX_STATE_BYTES:
        raise DecisionError("approval_context_budget")
    if sensitive(state):
        raise DecisionError("approval_sensitive_input")
    if evidence:
        optional = encode(evidence)
        if len(optional) <= MAX_OBSERVATION_BYTES and not sensitive(evidence):
            state["untrusted_evidence"] = json.loads(optional)
        else:
            state["omissions"].append("Optional observations omitted for size or sensitive content; material missing facts require insufficient_evidence.")
    if len(encode(state)) > MAX_STATE_BYTES:
        state["untrusted_evidence"] = {}
        state["omissions"].append("Optional observations omitted for total context budget.")
    if len(encode(state)) > MAX_STATE_BYTES:
        raise DecisionError("approval_context_budget")
    return state
