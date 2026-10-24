"""Transactional chat preferences and server-bound turn admission.

This module owns neither streaming nor tools. It binds their existing owners to
one committed policy and execution descriptor before a user turn can run.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import Any
from src.automation_task_state import transition_status, submission_is_current

from fastapi import HTTPException

MODES = frozenset({"always_ask", "auto_approve", "yolo"})
APPROACHES = frozenset({"established_procedure", "bounded_domain", "approach_discovery"})
EFFORTS = frozenset({None, "", "none", "minimal", "low", "medium", "high", "xhigh", "max"})
_BOUND = ContextVar("chat_automation_bound_turn", default=None)


def owner_key(owner):
    from src.owner_identity import DEFAULT_LOCAL_OWNER
    if not owner or owner == DEFAULT_LOCAL_OWNER:
        return "local:auth-disabled"
    return "user:" + str(owner).strip().casefold() if owner else "local:auth-disabled"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _models():
    from core import database as d
    return d


class AutomationStore:
    def __init__(self, session_factory=None, clock=time.time):
        self.session_factory = session_factory
        self.clock = clock

    @contextmanager
    def transaction(self):
        d = _models()
        db = (self.session_factory or d.SessionLocal)()
        try:
            # SQLite ignores SELECT FOR UPDATE. Reserve the write transaction
            # before reading defaults/revisions, including first-use creation.
            if db.get_bind().dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def owner(self, db, owner):
        from src.owner_identity import effective_storage_owner
        owner = effective_storage_owner(owner)
        if owner is None:
            raise HTTPException(401, "A chat owner is required")
        d = _models()
        key = owner_key(owner)
        row = db.query(d.OwnerChatAutomation).filter_by(owner_key=key).with_for_update().first()
        if row is None:
            if db.get_bind().dialect.name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert
                db.execute(insert(d.OwnerChatAutomation).values(owner_key=key,
                    last_explicit_mode="always_ask", elevation_ceiling=False,
                    authority_epoch=0, revision=0, configuration={}).on_conflict_do_nothing())
                return db.query(d.OwnerChatAutomation).filter_by(owner_key=key).with_for_update().one()
            row = d.OwnerChatAutomation(owner_key=key, last_explicit_mode="always_ask",
                elevation_ceiling=False, authority_epoch=0, revision=0, configuration={})
            db.add(row)
            db.flush()
        return row

    def state(self, db, owner, session_id, *, new=False):
        d = _models()
        session = db.query(d.Session).filter_by(id=str(session_id)).first()
        if session is None or owner_key(session.owner) != owner_key(owner):
            raise HTTPException(404, "Chat not found")
        prefs = self.owner(db, owner)
        row = db.query(d.ChatAutomationState).filter_by(session_id=str(session_id)).first()
        if row is None:
            row = d.ChatAutomationState(session_id=str(session_id), owner_key=owner_key(owner),
                approval_mode=prefs.last_explicit_mode if new else "always_ask",
                policy_revision=0, route_mode="manual", elevation_grant=None)
            db.add(row)
            db.flush()
        if row.owner_key != owner_key(owner):
            raise HTTPException(409, "Chat policy ownership changed")
        return prefs, row

    def configuration(self, owner):
        with self.transaction() as db:
            p = self.owner(db, owner)
            return {"revision": p.revision, "authority_epoch": p.authority_epoch,
                    "last_explicit_mode": p.last_explicit_mode,
                    "elevation_ceiling": p.elevation_ceiling,
                    **copy.deepcopy(p.configuration or {})}

    def fork(self, owner, source_id, target_id):
        with self.transaction() as db:
            _, source = self.state(db, owner, source_id)
            _, target = self.state(db, owner, target_id)
            target.approval_mode, target.route_mode = source.approval_mode, source.route_mode
            target.elevation_grant = None
            target.policy_revision += 1

    def revoke_owner(self, owner):
        with self.transaction() as db:
            prefs = self.owner(db, owner)
            prefs.authority_epoch += 1
            prefs.revision += 1

    def _policy(self, p, s, owner, entrypoint="interactive"):
        grant = copy.deepcopy(s.elevation_grant)
        requested_grant = copy.deepcopy(grant)
        restriction = None
        if (not p.elevation_ceiling or not grant
                or grant.get("authority_epoch") != p.authority_epoch
                or grant.get("policy_revision") != s.policy_revision
                or grant.get("expires_at", 0) <= self.clock()
                or entrypoint != "interactive"):
            grant = None
        if grant:
            try:
                validate_elevation_roots(owner, [{'path':r['path'],'access':r['access']} for r in grant['roots']])
                from src.agent_runtime.resources import FilesystemRoot
                for item in grant['roots']:
                    FilesystemRoot.from_dict(item['identity']).validate()
            except (HTTPException, ValueError, OSError):
                grant = None
                restriction = 'Additional folder access is unavailable under the current deployment policy'
        return {"contract_version": 1, "owner": owner, "session_id": s.session_id,
                "approval_mode": s.approval_mode, "policy_revision": s.policy_revision,
                "authority_epoch": p.authority_epoch, "configuration_revision": p.revision,
                "configuration_digest": digest(p.configuration or {}),
                "elevation_ceiling": bool(p.elevation_ceiling), "elevation_grant": grant,
                "requested_elevation_grant": requested_grant, "sandbox_state": "required_at_execution",
                "effective_roots": (grant or {}).get("roots", []),
                "route_mode": s.route_mode, "entrypoint": entrypoint,
                "execution_scope": "expanded" if grant else "workspace",
                "restriction": restriction or (None if grant or not p.elevation_ceiling else "No additional scope is granted")}

    def policy(self, owner, session_id, entrypoint="interactive"):
        with self.transaction() as db:
            p, s = self.state(db, owner, session_id)
            return self._policy(p, s, owner, entrypoint)

    def update_chat(self, owner, session_id, changes, expected_revision):
        if set(changes) - {"approval_mode", "route_mode", "elevation_roots"}:
            raise HTTPException(422, "Unsupported chat setting")
        with self.transaction() as db:
            p, s = self.state(db, owner, session_id)
            if type(expected_revision) is not int or expected_revision != s.policy_revision:
                raise HTTPException(409, "Chat settings changed; refresh and try again")
            mode = changes.get("approval_mode", s.approval_mode)
            route = changes.get("route_mode", s.route_mode)
            if mode not in MODES or route not in {"manual", "auto"}:
                raise HTTPException(422, "Invalid chat mode")
            changed = mode != s.approval_mode or route != s.route_mode or "elevation_roots" in changes
            if changed:
                s.policy_revision += 1
                s.elevation_grant = None
                s.approval_mode, s.route_mode = mode, route
            if "approval_mode" in changes and p.last_explicit_mode != mode:
                p.last_explicit_mode = mode
                p.revision += 1
            if "elevation_roots" in changes:
                roots = validate_elevation_roots(owner, changes["elevation_roots"])
                if roots and not p.elevation_ceiling:
                    raise HTTPException(409, "Enable the elevation ceiling first")
                if roots:
                    s.elevation_grant = {"grant_id": uuid.uuid4().hex, "roots": roots,
                        "authority_epoch": p.authority_epoch, "policy_revision": s.policy_revision,
                        "expires_at": self.clock() + 3600}
            return self._policy(p, s, owner)

    def update_configuration(self, owner, changes, expected_revision):
        if set(changes) - {"elevation_ceiling", "decision_provider", "approaches"}:
            raise HTTPException(422, "Unsupported automation setting")
        validate_configuration(changes)
        with self.transaction() as db:
            p = self.owner(db, owner)
            if type(expected_revision) is not int or expected_revision != p.revision:
                raise HTTPException(409, "Settings changed; refresh and try again")
            config = copy.deepcopy(p.configuration or {})
            for key in ("decision_provider", "approaches"):
                if key in changes:
                    config[key] = changes[key]
            ceiling = changes.get("elevation_ceiling", p.elevation_ceiling)
            if type(ceiling) is not bool:
                raise HTTPException(422, "Elevation ceiling must be a boolean")
            if ceiling != p.elevation_ceiling:
                p.authority_epoch += 1
            if config != (p.configuration or {}) or ceiling != p.elevation_ceiling:
                p.revision += 1
                p.configuration, p.elevation_ceiling = config, ceiling
            return {"revision": p.revision, "authority_epoch": p.authority_epoch,
                    "last_explicit_mode": p.last_explicit_mode,
                    "elevation_ceiling": p.elevation_ceiling, **copy.deepcopy(config)}

    def acquire(self, owner, session_id, raw_ask, entrypoint, submission_id=None):
        from src.owner_identity import effective_storage_owner
        owner = effective_storage_owner(owner)
        if owner is None:
            raise HTTPException(401, "A chat owner is required")
        d = _models()
        sid = str(session_id)
        identifier = submission_id or uuid.uuid4().hex
        if not isinstance(identifier, str) or len(identifier) > 128 or not identifier:
            raise HTTPException(422, "Invalid submission identifier")
        with self.transaction() as db:
            p, s = self.state(db, owner, sid)
            prior = db.query(d.ChatAutomationSubmission).filter_by(submission_id=identifier).first()
            if prior is not None:
                raise HTTPException(409, "Submission already exists; check the chat before resending")
            lease = db.query(d.ChatAutomationLease).filter_by(session_id=sid).first()
            if lease is not None and lease.expires_at > self.clock():
                raise HTTPException(409, "This chat is already preparing a message")
            token = uuid.uuid4().hex
            # A new accepted ask supersedes old pending control cards as well as
            # an expired preparation. Late finally blocks may not revive them.
            db.query(d.ChatAutomationSubmission).filter(
                d.ChatAutomationSubmission.session_id == sid,
                d.ChatAutomationSubmission.owner_key == owner_key(owner),
                d.ChatAutomationSubmission.status.in_(["preparing", "bound", "running", "awaiting_approval"]),
            ).update({"status": "cancelled"}, synchronize_session=False)
            if lease is None:
                lease = d.ChatAutomationLease(session_id=sid)
                db.add(lease)
            lease.owner_key, lease.token, lease.submission_id = owner_key(owner), token, identifier
            lease.expires_at = self.clock() + 90
            policy = self._policy(p, s, owner, entrypoint)
            from src.model_admission import owner_policy_fingerprint
            policy["owner_policy_fingerprint"] = owner_policy_fingerprint(owner)
            policy["original_intent_digest"] = digest(raw_ask)
            db.add(d.ChatAutomationSubmission(submission_id=identifier, owner_key=owner_key(owner),
                session_id=sid, policy_revision=s.policy_revision, authority_epoch=p.authority_epoch,
                intent_digest=digest(raw_ask), descriptor={}, status="preparing", created_at=self.clock()))
            return BoundChatTurn(self, owner, sid, identifier, token, raw_ask, policy,
                                 copy.deepcopy(p.configuration or {}))

    def current(self, bound):
        d = _models()
        from src.model_admission import owner_policy_fingerprint
        if owner_policy_fingerprint(bound.owner) != bound.policy.get('owner_policy_fingerprint'):
            return False
        try:
            with self.transaction() as db:
                p, s = self.state(db, bound.owner, bound.session_id)
                row = db.query(d.ChatAutomationSubmission).filter_by(submission_id=bound.submission_id).first()
                lease = db.query(d.ChatAutomationLease).filter_by(session_id=bound.session_id,
                    token=bound.token, submission_id=bound.submission_id, owner_key=owner_key(bound.owner)).first() if row and row.status == 'preparing' else None
                fresh = self._policy(p, s, bound.owner, bound.policy.get('entrypoint', 'interactive'))
                return bool(row and row.owner_key == owner_key(bound.owner)
                    and submission_is_current(row.status, lease_token=lease.token if lease else None,
                        expected_token=bound.token, lease_expires_at=lease.expires_at if lease else None, now=self.clock())
                    and row.intent_digest == digest(bound.raw_ask)
                    and p.authority_epoch == bound.policy["authority_epoch"]
                    and digest(p.configuration or {}) == bound.policy["configuration_digest"]
                    and digest(fresh['effective_roots']) == digest(bound.policy.get('effective_roots', []))
                    and s.policy_revision == bound.policy["policy_revision"])
        except HTTPException:
            return False

    def record(self, bound, status, descriptor=None):
        d = _models()
        with self.transaction() as db:
            row = db.query(d.ChatAutomationSubmission).filter_by(submission_id=bound.submission_id).first()
            if row is None or row.owner_key != owner_key(bound.owner):
                raise HTTPException(409, "Turn binding is unavailable")
            next_status = transition_status(row.status, status)
            if row.status in {"completed", "failed", "cancelled"}:
                return
            row.status = next_status
            if descriptor is not None:
                row.descriptor = copy.deepcopy(descriptor)
            lease = db.query(d.ChatAutomationLease).filter_by(session_id=bound.session_id, token=bound.token).first()
            if lease:
                if status == "preparing":
                    lease.expires_at = self.clock() + 90
                else:
                    db.delete(lease)


def validate_configuration(changes):
    provider = changes.get("decision_provider")
    if provider is not None:
        if (not isinstance(provider, dict) or set(provider) - {"endpoint_id", "model", "protocol", "timeout_seconds", "max_calls"}
                or not all(isinstance(provider.get(k), str) and provider[k] for k in ("endpoint_id", "model"))
                or provider.get("protocol") not in {"systemone", "openai_json"}):
            raise HTTPException(422, "Choose a registered decision endpoint, model and protocol")
        from src.decisions.config import validate_config
        from src.decisions.contracts import DecisionError
        try:
            validate_config(provider)
        except DecisionError:
            raise HTTPException(422, "Decision limits are out of range") from None
    mappings = changes.get("approaches")
    if mappings is not None:
        if not isinstance(mappings, dict) or set(mappings) != APPROACHES:
            raise HTTPException(422, "Configure all three approaches")
        for item in mappings.values():
            if (not isinstance(item, dict) or set(item) != {"endpoint_id", "model", "reasoning_effort"}
                    or not all(isinstance(item[k], str) and item[k] for k in ("endpoint_id", "model"))
                    or item["reasoning_effort"] not in EFFORTS):
                raise HTTPException(422, "Invalid approach model selection")


def validate_elevation_roots(owner, roots):
    from pathlib import Path
    from src.settings import get_setting
    from src.tool_execution import vet_workspace
    allowed = get_setting("agent_elevation_roots", [])
    if not isinstance(roots, list) or len(roots) > 8:
        raise HTTPException(422, "Choose at most eight additional folders")
    if not roots:
        return []
    if not get_setting("agent_elevation_enabled", False) or not isinstance(allowed, list):
        raise HTTPException(403, "Elevation is disabled by the deployment")
    result = []
    for entry in roots:
        if not isinstance(entry, dict) or set(entry) != {"path", "access"} or entry["access"] not in {"read", "write"}:
            raise HTTPException(422, "Invalid folder permission")
        path = vet_workspace(entry["path"])
        if not path or not any(Path(path).is_relative_to(Path(p).resolve()) for p in allowed if isinstance(p, str)):
            raise HTTPException(403, "Folder is outside the deployment elevation scope")
        from src.agent_runtime.resources import FilesystemRoot
        sealed = FilesystemRoot.seal(path, owner=str(owner or "").strip().casefold())
        result.append({"path": path, "access": entry["access"], "identity": sealed.to_dict()})
    return result


def rename_automation_owner(db, old_owner, new_owner):
    """Move preferences with account data, never live capabilities."""
    d = _models()
    old_key, new_key = owner_key(old_owner), owner_key(new_owner)
    existing = db.query(d.OwnerChatAutomation).filter_by(owner_key=new_key).first()
    if existing is not None:
        raise ValueError('Destination already has chat settings')
    prefs = db.query(d.OwnerChatAutomation).filter_by(owner_key=old_key).first()
    if prefs is not None:
        prefs.owner_key = new_key
        prefs.authority_epoch += 1
        prefs.revision += 1
    for state in db.query(d.ChatAutomationState).filter_by(owner_key=old_key):
        state.owner_key = new_key
        state.policy_revision += 1
        state.elevation_grant = None
    db.query(d.ChatAutomationLease).filter_by(owner_key=old_key).delete(synchronize_session=False)
    db.query(d.ChatAutomationSubmission).filter_by(owner_key=old_key).update(
        {'owner_key': new_key, 'status': 'cancelled'}, synchronize_session=False)
    for attempt in db.query(d.DecisionAttempt).filter_by(owner_key=old_key):
        attempt.owner_key = new_key
        if attempt.status == 'reserved':
            attempt.status = 'unresolved'
            attempt.record = {**attempt.record, 'status': 'unresolved', 'failure_kind': 'owner_changed', 'usage': None}


def retire_automation_owner(db, owner):
    d = _models()
    key = owner_key(owner)
    # A later account with the same spelling must not inherit these settings.
    db.query(d.OwnerChatAutomation).filter_by(owner_key=key).delete(synchronize_session=False)
    for state in db.query(d.ChatAutomationState).filter_by(owner_key=key):
        state.approval_mode, state.route_mode, state.elevation_grant = 'always_ask', 'manual', None
        state.policy_revision += 1
    db.query(d.ChatAutomationLease).filter_by(owner_key=key).delete(synchronize_session=False)
    retired = 'retired:' + uuid.uuid4().hex
    db.query(d.ChatAutomationSubmission).filter_by(owner_key=key).update(
        {'owner_key': retired, 'status': 'cancelled'}, synchronize_session=False)
    db.query(d.DecisionAttempt).filter_by(owner_key=key).update({'owner_key': retired}, synchronize_session=False)


class SqlAttemptStore:
    def __init__(self, store=None):
        self.store = store or AutomationStore()

    def reserve(self, record, *, max_calls):
        from src.decisions.contracts import DecisionError
        d = _models()
        with self.store.transaction() as db:
            self.store.owner(db, record["owner"])
            previous = db.query(d.DecisionAttempt).filter_by(owner_key=owner_key(record["owner"]),
                session_id=record["session_id"], run_id=record["run_id"]).all()
            if (db.query(d.DecisionAttempt).filter_by(attempt_id=record["attempt_id"]).first()
                    or len(previous) >= max_calls or any(r.status in {"reserved", "unresolved"}
                        or (r.record or {}).get('failure_kind') == 'decision_usage_exceeded_reservation' for r in previous)):
                raise DecisionError("decision_budget_unavailable")
            db.add(d.DecisionAttempt(attempt_id=record["attempt_id"], owner_key=owner_key(record["owner"]),
                session_id=record["session_id"], run_id=record["run_id"], status="reserved", record=copy.deepcopy(record)))

    def settle(self, attempt_id, *, status, usage=None, failure_kind=None):
        from src.decisions.contracts import DecisionError
        d = _models()
        if status not in {"settled", "rejected", "unresolved", "cancelled"}:
            raise DecisionError("decision_invalid_attempt_status")
        with self.store.transaction() as db:
            row = db.query(d.DecisionAttempt).filter_by(attempt_id=attempt_id).with_for_update().first()
            if row is None or row.status != "reserved":
                raise DecisionError("decision_attempt_already_settled")
            row.status = status
            row.record = {**row.record, "status": status, "usage": usage, "failure_kind": failure_kind}

    def records_for_run(self, owner, session_id, run_id):
        d = _models()
        with self.store.transaction() as db:
            return [copy.deepcopy(r.record) for r in db.query(d.DecisionAttempt).filter_by(
                owner_key=owner_key(owner), session_id=session_id, run_id=run_id).all()]


@dataclass
class BoundChatTurn:
    store: AutomationStore
    owner: str | None
    session_id: str
    submission_id: str
    token: str
    raw_ask: str
    policy: dict
    configuration: dict
    descriptor: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict, repr=False)
    endpoint_url: str = ""
    pending: bool = False
    pending_approval_id: str | None = None

    @property
    def run_id(self):
        return self.submission_id

    def current(self):
        return self.store.current(self)

    @contextmanager
    def activate(self):
        token = _BOUND.set(self)
        try:
            yield self
        finally:
            _BOUND.reset(token)

    def view(self, session):
        if session.id != self.session_id or not self.descriptor:
            return session
        view = copy.copy(session)
        view.endpoint_url, view.headers = self.endpoint_url, dict(self.headers)
        view.model, view.endpoint_id = self.descriptor["model"], self.descriptor["endpoint_id"]
        view.thinking_mode = "effort:" + (self.descriptor["reasoning_effort"] or "")
        view._automation_bound = True
        return view

    def rebind_manual(self, candidate, *, status, reasoning_effort=None):
        """Admit only a current owner's explicit availability fallback route."""
        from src.foreground_model_routing import (
            FOREGROUND_AVAILABILITY_STATUSES, resolve_foreground_model_policy,
        )
        if (self.policy.get("route_mode") != "manual" or not self.current()
                or type(status) is not int or status not in FOREGROUND_AVAILABILITY_STATUSES):
            raise RuntimeError("Manual fallback is unavailable for this turn")
        policy = resolve_foreground_model_policy(self.owner)
        chosen = next(((route, descriptor) for route, descriptor in zip(
            policy.fallback_candidates, policy.fallback_descriptors)
            if policy.enabled and tuple(route) == tuple(candidate)), None)
        if chosen is None or not chosen[1].get("endpoint_id"):
            raise RuntimeError("Fallback has no current explicit owner consent")
        from src.endpoint_resolver import resolve_endpoint_by_id
        from src.owner_identity import DEFAULT_LOCAL_OWNER
        resolved = resolve_endpoint_by_id(chosen[1]["endpoint_id"], candidate[1],
            owner=self.owner or DEFAULT_LOCAL_OWNER, require_exact_model=True)
        if resolved is None or tuple(resolved) != tuple(candidate):
            raise RuntimeError("Fallback registration changed before dispatch")
        descriptor = {**self.descriptor, "endpoint_id": chosen[1]["endpoint_id"],
            "model": candidate[1], "reasoning_effort": reasoning_effort or "",
            "source": "explicit_fallback"}
        self.store.record(self, "bound", descriptor)
        self.endpoint_url, self.headers = candidate[0], dict(candidate[2] or {})
        self.descriptor = descriptor


def current_turn():
    return _BOUND.get()


def bound_session_view(session):
    bound = current_turn()
    return bound.view(session) if bound else session


async def prepare_chat_turn(store, owner, session, raw_ask, *, entrypoint="interactive", submission_id=None,
                            reasoning_effort=None, allow_empty=False):
    if not isinstance(raw_ask, str) or (not raw_ask.strip() and not allow_empty) or len(raw_ask) > 50000:
        raise HTTPException(422, "Enter a nonblank message of at most 50000 characters")
    bound = store.acquire(owner, session.id, raw_ask, entrypoint, submission_id)
    owner = bound.owner
    try:
        effort = ""
        mode = str(getattr(session, "thinking_mode", "") or "")
        if mode.startswith("effort:"):
            effort = mode[7:]
        if bound.policy["route_mode"] == "auto":
            from src.approach_routing import route_approach
            from src.decisions import DecisionProvider
            from src.decisions.contracts import DecisionContext
            selection = await route_approach(raw_ask, bound.configuration.get("approaches", {}),
                provider=DecisionProvider(store=SqlAttemptStore(store)),
                config=bound.configuration.get("decision_provider", {}),
                context=DecisionContext(owner, session.id, bound.run_id,
                    policy_revision=bound.policy["policy_revision"], authority_epoch=bound.policy["authority_epoch"]))
            selected = selection.to_dict() if hasattr(selection, "to_dict") else selection
            endpoint_id, model = selected["endpoint_id"], selected["model"]
            effort = selected.get("reasoning_effort") or ""
        else:
            endpoint_id, model = getattr(session, "endpoint_id", None), session.model
            if reasoning_effort is not None:
                if reasoning_effort not in EFFORTS:
                    raise HTTPException(422, "Invalid reasoning effort")
                effort = reasoning_effort or ""
        from src.model_admission import require_owner_model
        try:
            require_owner_model(owner, model)
        except ValueError:
            raise HTTPException(403, "This model is not allowed for your account") from None
        if endpoint_id:
            from src.endpoint_resolver import resolve_endpoint_by_id
            from src.owner_identity import DEFAULT_LOCAL_OWNER
            resolved = resolve_endpoint_by_id(endpoint_id, model, owner=owner or DEFAULT_LOCAL_OWNER, require_exact_model=True)
            if not resolved:
                raise HTTPException(409, "Selected model is no longer available")
            bound.endpoint_url, model, bound.headers = resolved
        else:
            # Existing manual sessions retain their explicit registered/runtime
            # admission in the owning chat route. Auto never selects a raw URL.
            if bound.policy["route_mode"] == "auto":
                raise HTTPException(409, "Choose a registered endpoint for every approach")
            bound.endpoint_url = session.endpoint_url
            bound.headers = dict(session.headers or {})
        bound.descriptor = {"endpoint_id": endpoint_id, "model": model, "reasoning_effort": effort,
            "route_mode": bound.policy["route_mode"], "submission_id": bound.submission_id}
        if not bound.current():
            raise HTTPException(409, "Chat settings changed before submission")
        bind_prepared_browser_scope(bound)
        store.record(bound, "bound", bound.descriptor)
        return bound
    except BaseException:
        store.record(bound, "failed")
        raise


def validate_provider_binding(url, model, headers, session_id, reasoning_effort=None):
    """Called at the real execution provider boundary, not a UI metadata write."""
    bound = current_turn()
    if bound is None or session_id != bound.session_id:
        return
    if not bound.current():
        raise RuntimeError("Chat policy changed before provider dispatch")
    from src.model_admission import require_owner_model
    require_owner_model(bound.owner, model)
    endpoint_id = bound.descriptor["endpoint_id"]
    if endpoint_id:
        from src.endpoint_resolver import resolve_endpoint_by_id
        from src.owner_identity import DEFAULT_LOCAL_OWNER
        resolved = resolve_endpoint_by_id(endpoint_id, bound.descriptor["model"],
            owner=bound.owner or DEFAULT_LOCAL_OWNER, require_exact_model=True)
        if resolved != (bound.endpoint_url, bound.descriptor["model"], bound.headers):
            raise RuntimeError("Selected provider changed before dispatch")
    if (str(url).rstrip("/") != bound.endpoint_url.rstrip("/")
            or model != bound.descriptor["model"] or dict(headers or {}) != bound.headers
            or (reasoning_effort or "") != bound.descriptor["reasoning_effort"]):
        raise RuntimeError("Execution provider does not match the acknowledged turn binding")


async def prepare_chat_submission(*args, **kwargs):
    """Map bounded selection failures to an actionable ingress response."""
    from src.decisions import DecisionError
    try:
        return await prepare_chat_turn(*args, **kwargs)
    except DecisionError as error:
        raise HTTPException(409,
            "Automatic model selection could not be completed. Choose a model manually or check chat settings.",
            headers={'X-Decision-Failure': error.code}) from None


def effective_elevation_roots():
    """Observe the user grant again; neither history nor review can mint it."""
    bound = current_turn()
    if bound is None or not bound.current():
        return []
    policy = bound.store.policy(bound.owner, bound.session_id, bound.policy["entrypoint"])
    roots = policy.get("effective_roots", [])
    if roots:
        # Deployment changes are absolute even before a pending turn finishes.
        validate_elevation_roots(bound.owner, [{"path": r["path"], "access": r["access"]} for r in roots])
        from src.agent_runtime.resources import FilesystemRoot
        for item in roots:
            FilesystemRoot.from_dict(item["identity"]).validate()
    return roots


def validate_elevation_operation(operation, resource_operation):
    if resource_operation is None:
        return
    roots = effective_elevation_roots()
    bound = current_turn()
    originally_expanded = {r['path'] for r in bound.policy.get('effective_roots', [])} if bound else set()
    live = {r['path'] for r in roots}
    if any(binding.resource.root.path in originally_expanded - live for binding in resource_operation.bindings):
        raise ValueError("This additional folder grant expired or was revoked")
    readonly = {r["path"] for r in roots if r["access"] == "read"}
    # Explicit read operations only. Unknown operation classes cannot write
    # through a read-only root merely because a reviewer accepted the action.
    if operation.tool not in {"read_file", "ls", "glob", "grep"}:
        if any(binding.resource.root.path in readonly for binding in resource_operation.bindings):
            raise ValueError("This additional folder is read-only")


def bind_prepared_browser_scope(bound):
    """Consume a human scope for exactly the next admitted submission."""
    try:
        from src.browser_pipe import get
    except ModuleNotFoundError:
        return
    record = get(str(bound.owner or "").strip().casefold(), bound.session_id)
    prepared = getattr(record, "pending_task_grant", None)
    if record is None or prepared is None:
        return
    record.pending_task_grant = None
    grant, revision, epoch = prepared
    if (bound.policy["entrypoint"] != "interactive" or revision != bound.policy["policy_revision"]
            or epoch != bound.policy["authority_epoch"] or grant.expires_at <= time.time()):
        record.task_grant = None
        return
    from dataclasses import replace
    record.task_grant = replace(grant, run_id=bound.run_id)


def current_browser_grant(owner, session_id):
    bound = current_turn()
    if (bound is None or owner_key(owner) != owner_key(bound.owner)
            or str(session_id or "") != bound.session_id or not bound.current()):
        return None
    from src.browser_pipe import get
    record = get(str(owner or "").strip().casefold(), str(session_id or ""))
    grant = getattr(record, "task_grant", None)
    if grant is None or grant.run_id != bound.run_id:
        return None
    try:
        grant.check(str(owner or "").strip().casefold(), str(session_id or ""))
    except ValueError:
        return None
    return grant


def automation_ingress(session_manager):
    """One barrier for existing JSON and SSE chat endpoints, including replay."""
    def decorate(func):
        @wraps(func)
        async def wrapped(request, *args, **kwargs):
            from src.auth_helpers import effective_user, is_delegated_credential, require_api_token_scope
            require_api_token_scope(request, 'chat')
            from routes.chat_routes import _verify_session_owner
            payload = kwargs.get("chat_request") or (args[0] if args else None)
            if payload is not None:
                data = payload.model_dump() if hasattr(payload, "model_dump") else vars(payload)
            elif request.headers.get("content-type", "").startswith("application/json"):
                data = await request.json()
            else:
                data = dict(await request.form())
            if not isinstance(data, dict):
                raise HTTPException(400, "A chat request must be an object")
            sid = data.get("session")
            if not sid:
                return await func(request, *args, **kwargs)
            _verify_session_owner(request, sid)
            session = session_manager.get_session(sid)
            from routes.chat_helpers import _enforce_chat_privileges
            _enforce_chat_privileges(request, session)
            store = AutomationStore()
            from src.owner_identity import effective_storage_owner
            owner = effective_storage_owner(effective_user(request))
            if owner is None:
                raise HTTPException(401, "A chat owner is required")
            if data.get("tool_approval_id"):
                from src.agent_runtime.authority import require_user_approval_request
                require_user_approval_request(request)
                if is_delegated_credential(request):
                    raise HTTPException(403, 'API tokens cannot approve chat actions')
                from src.tool_approvals import tool_approval_store
                pending = tool_approval_store.peek(data["tool_approval_id"])
                bound = getattr(pending, "bound_turn", None)
                if bound is None or bound.owner != owner or bound.session_id != sid or not bound.current():
                    raise HTTPException(409, "Approval expired; request the action again")
            elif not str(data.get("message") or "").strip():
                # Attachment-only requests keep their explicit empty user text;
                # automatic approach choice needs text and cannot guess a task.
                if store.policy(owner, sid)["route_mode"] == "auto":
                    raise HTTPException(422, "Add a message or select a model manually")
                attachments = data.get("attachments")
                if not attachments:
                    raise HTTPException(422, "Add a message or attachment")
                bound = await prepare_chat_submission(store, owner, session, data.get("message") or "",
                    entrypoint="delegated" if is_delegated_credential(request) else "interactive",
                    submission_id=request.headers.get("X-Chat-Submission-Id"),
                    reasoning_effort=data.get("reasoning_effort"), allow_empty=True)
            else:
                bound = await prepare_chat_submission(store, owner, session, data["message"],
                    entrypoint="delegated" if is_delegated_credential(request) else "interactive",
                    submission_id=request.headers.get("X-Chat-Submission-Id"),
                    reasoning_effort=data.get("reasoning_effort"))
            try:
                with bound.activate():
                    response = await func(request, *args, **kwargs)
                if hasattr(response, "body_iterator"):
                    original = response.body_iterator
                    async def body():
                        try:
                            with bound.activate():
                                async for item in original:
                                    yield item
                        finally:
                            if not response.headers.get("X-Odysseus-Run-Id"):
                                store.record(bound, "awaiting_approval" if bound.pending else "completed")
                    response.body_iterator = body()
                else:
                    store.record(bound, "awaiting_approval" if bound.pending else "completed")
                return response
            except BaseException:
                store.record(bound, "awaiting_approval" if data.get("tool_approval_id") and bound.pending else "failed")
                raise
        return wrapped
    return decorate
