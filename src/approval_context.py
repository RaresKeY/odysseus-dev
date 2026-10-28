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
import time
import threading
import weakref
from collections import OrderedDict

from src.decisions.contracts import DecisionError, encode

CONTEXT_VERSION = "odysseus-approval-context/v2"
MAX_USER_BYTES = 24 * 1024
MAX_USER_ITEMS = 4096
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


# Volatile, bounded by SQL factory AND authenticated owner/chat. Restart,
# eviction and expiry never reconstruct a private ask from a transcript.
_PRIVATE = weakref.WeakKeyDictionary()
_PRIVATE_LOCK = threading.RLock()
PRIVATE_TTL = 3600
PRIVATE_MAX_ITEMS = 1024
PRIVATE_MAX_BYTES = 2 * 1024 * 1024


def forget_private_evidence(owner, session_id=None):
    """Deletion/rename invalidates volatile evidence as well as durable state."""
    from src.chat_automation import owner_key
    key = owner_key(owner)
    with _PRIVATE_LOCK:
        for cache in list(_PRIVATE.values()):
            for identity in list(cache):
                if identity[0] == key and (session_id is None or identity[1] == str(session_id)):
                    del cache[identity]


def _private_namespace(bound):
    from src.chat_automation import _models
    return bound.store.session_factory or _models().SessionLocal


def _prune_private(cache):
    now = time.monotonic()
    for old in list(cache):
        if cache[old]["expires"] <= now:
            del cache[old]
    while (len(cache) > PRIVATE_MAX_ITEMS or
           sum(len(encode(v)) for v in cache.values()) > PRIVATE_MAX_BYTES):
        cache.popitem(last=False)


def retain_private_submission(bound):
    from src.chat_automation import owner_key
    key = (owner_key(bound.owner), bound.session_id, bound.submission_id)
    with _PRIVATE_LOCK:
        cache = _PRIVATE.setdefault(_private_namespace(bound), OrderedDict())
        _prune_private(cache)
        expires = time.monotonic() + PRIVATE_TTL
        # An active private conversation keeps its complete retained evidence;
        # idle expiry remains explicit, and missing entries are never revived.
        for identity, item in cache.items():
            if identity[:2] == key[:2]:
                item["expires"] = expires
        cache[key] = {"text": bound.raw_ask, "events": [], "expires": expires}
        _prune_private(cache)


def _private_item(bound, row):
    from src.chat_automation import owner_key
    key = (owner_key(bound.owner), bound.session_id, row.submission_id)
    if row.submission_id == bound.submission_id and bound.ephemeral_semantics:
        return bound.raw_ask, list(bound.approval_evidence)
    with _PRIVATE_LOCK:
        cache = _PRIVATE.get(_private_namespace(bound), {})
        _prune_private(cache)
        item = cache.get(key)
        if item is None or item["expires"] <= time.monotonic():
            raise DecisionError("approval_private_context_unavailable")
        return item["text"], list(item["events"])


def _pack(items):
    # Lossless framing, not a summary or relevance classifier. Retain chronology,
    # every exact ask and every exact human decision. A digest names the ordered
    # archive; its provenance comes from SQL validation, never that hash alone.
    if len(items) <= 32:
        return items
    older, recent = items[:-8], items[-8:]
    archive = [[item["text"], item["action_decisions"]] for item in older]
    return [{"source": "authenticated_submission_archive", "id": hashlib.sha256(encode(older)).hexdigest(),
             "format": "ordered_exact_asks_and_human_decisions", "count": len(older),
             "text": encode(archive).decode(), "action_decisions": []}, *recent]


def user_snapshot(bound):
    """One SQL read, exact chain verification and lossless bounded framing.

    No last-N truncation: if all material user evidence cannot fit, require an
    explicit new-chat checkpoint or manual review rather than discard a rule.
    """
    from src.chat_automation import _models, owner_key, digest
    d = _models()
    with bound.store.transaction() as db:
        bound.store.state(db, bound.owner, bound.session_id)
        # Fetch only this owned chain's possible rows in one bounded query.
        rows = db.query(d.ChatAutomationSubmission).filter_by(
            owner_key=owner_key(bound.owner), session_id=bound.session_id).order_by(
                d.ChatAutomationSubmission.created_at.desc(),
                d.ChatAutomationSubmission.submission_id.desc()).limit(MAX_USER_ITEMS + 1).all()
        by_id = {row.submission_id: row for row in rows}
        items, seen, identifier, private = [], set(), bound.submission_id, False
        while identifier:
            if identifier in seen or len(items) >= MAX_USER_ITEMS:
                raise DecisionError("approval_context_checkpoint_required")
            seen.add(identifier)
            row = by_id.get(identifier)
            if row is None:
                raise DecisionError("approval_context_unavailable")
            if row.current_ask is None:
                text, events = _private_item(bound, row)
                private = True
            else:
                text, events = row.current_ask, row.approval_evidence or []
            if not isinstance(text, str) or digest(text) != row.intent_digest:
                raise DecisionError("approval_context_unavailable")
            if sensitive(text):
                raise DecisionError("approval_sensitive_input")
            if not isinstance(events, list) or len(events) > 64:
                raise DecisionError("approval_context_budget")
            # Decisions are server-authored audit evidence, never reusable grants.
            for event in events:
                if (not isinstance(event, dict) or set(event) != {"source", "id", "decision", "operation_sha256"}
                        or event["source"] != "human_exact_action"
                        or event["decision"] not in {"approve_once", "deny"}
                        or not isinstance(event["id"], str)
                        or not isinstance(event["operation_sha256"], str)
                        or not re.fullmatch(r"[0-9a-f]{64}", event["operation_sha256"])):
                    raise DecisionError("approval_context_unavailable")
            items.append({"source": "current_private_submission" if row.current_ask is None else "authenticated_submission",
                          "id": row.submission_id, "text": text, "action_decisions": events})
            identifier = row.previous_submission_id
    if private and len(seen) != len(rows):
        raise DecisionError("approval_private_context_unavailable")
    items.reverse()
    snapshot = {"version": CONTEXT_VERSION,
                "coverage": "complete_volatile_private_chain" if private else "complete_server_submission_chain",
                "user_evidence": _pack(items)}
    if len(encode(snapshot)) > MAX_USER_BYTES:
        raise DecisionError("approval_context_checkpoint_required")
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
        with _PRIVATE_LOCK:
            item = _PRIVATE.get(_private_namespace(bound), {}).get((owner_key(bound.owner), bound.session_id, bound.submission_id))
            if item is not None:
                item["events"] = list(bound.approval_evidence)
                _prune_private(_PRIVATE[_private_namespace(bound)])
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
