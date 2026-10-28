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
EFFORTS = frozenset({None, "", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"})
FEATURE_DEFAULTS = {"tool_routing": "existing", "effort_mode": "mapped", "followup_enabled": False,
                    "preparation_enabled": False, "completion_advice_enabled": False}
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
            # The owner's last explicit mode is history, not consent for a new chat.
            row = d.ChatAutomationState(session_id=str(session_id), owner_key=owner_key(owner),
                approval_mode="always_ask",
                policy_revision=0, route_mode="manual", elevation_grant=None)
            db.add(row)
            db.flush()
        if row.owner_key != owner_key(owner):
            raise HTTPException(409, "Chat policy ownership changed")
        self._materialize_auto_models(prefs, row)
        return prefs, row

    def _materialize_auto_models(self, prefs, row):
        if (row.route_mode != 'auto' or 'auto_models' in (row.feature_settings or {})
                or 'auto_categories' in (row.feature_settings or {})):
            return
        from src.model_auto import legacy_candidates
        from src.decisions import DecisionError
        try:
            candidates, source = legacy_candidates(prefs.configuration or {}), 'legacy_approaches'
        except DecisionError:
            candidates, source = [], 'legacy_invalid'
        row.feature_settings = {**(row.feature_settings or {}), 'auto_models': candidates,
                                'auto_models_source': source}

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
            target.approval_mode, target.route_mode = "always_ask", source.route_mode
            target.feature_settings = copy.deepcopy(source.feature_settings or {})
            target.elevation_grant = None
            target.policy_revision += 1

    def revoke_owner(self, owner):
        with self.transaction() as db:
            prefs = self.owner(db, owner)
            prefs.authority_epoch += 1
            prefs.revision += 1

    def _policy(self, p, s, owner, entrypoint="interactive"):
        features = {**FEATURE_DEFAULTS, **copy.deepcopy(s.feature_settings or {})}
        features.setdefault('auto_models', [])
        features.setdefault('auto_models_source', 'explicit')
        features.setdefault('auto_categories', {})
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
                **features,
                "route_mode": s.route_mode, "entrypoint": entrypoint,
                "execution_scope": "expanded" if grant else "workspace",
                "restriction": restriction or ('Assign a model to Complex tasks and Straightforward tasks before using Auto.'
                    if s.route_mode == 'auto' and (not isinstance(features['auto_categories'], dict)
                        or set(features['auto_categories']) != {'complex_tasks', 'straightforward_tasks'}) else
                    'Previous Auto selection is invalid. Choose Auto models again.'
                    if features['auto_models_source'] == 'legacy_invalid' and not features['auto_categories'] else
                    None if grant or not p.elevation_ceiling else "No additional scope is granted")}

    def policy(self, owner, session_id, entrypoint="interactive"):
        with self.transaction() as db:
            p, s = self.state(db, owner, session_id)
            return self._policy(p, s, owner, entrypoint)

    def update_chat(self, owner, session_id, changes, expected_revision):
        if set(changes) - {"approval_mode", "route_mode", "auto_models", "auto_categories", "elevation_roots", *FEATURE_DEFAULTS}:
            raise HTTPException(422, "Unsupported chat setting")
        with self.transaction() as db:
            p, s = self.state(db, owner, session_id)
            if type(expected_revision) is not int or expected_revision != s.policy_revision:
                raise HTTPException(409, "Chat settings changed; refresh and try again")
            mode = changes.get("approval_mode", s.approval_mode)
            route = changes.get("route_mode", s.route_mode)
            if mode not in MODES or route not in {"manual", "auto"}:
                raise HTTPException(422, "Invalid chat mode")
            if changes.get("approval_mode") == "auto_approve":
                from src.approval_fallback import auto_approve_availability
                availability = auto_approve_availability(
                    (p.configuration or {}).get("decision_provider"), owner, db=db,
                    session_id=str(session_id), policy={**self._policy(p, s, owner), **changes})
                if not availability["available"]:
                    raise HTTPException(422, availability["reason"],
                                        headers={"X-Decision-Failure": availability["reason_code"]})
            features = {**FEATURE_DEFAULTS, **copy.deepcopy(s.feature_settings or {})}
            from src.model_auto import validate_candidates, validate_registered_candidates, validate_registered_categories
            from src.decisions import DecisionError
            try:
                if 'auto_models' in changes:
                    candidates = validate_candidates(changes['auto_models'])
                    if candidates:
                        validate_registered_candidates(owner, candidates, db=db)
                    features['auto_models'] = candidates
                    features['auto_models_source'] = 'explicit'
                if 'auto_categories' in changes:
                    features['auto_categories'] = validate_registered_categories(owner,
                        changes['auto_categories'], db=db, require_complete=route == 'auto')
                if route == 'auto' and ('auto_categories' in changes or 'auto_models' in changes
                                        or changes.get('route_mode') == 'auto'):
                    validate_registered_categories(owner, features.get('auto_categories', {}), db=db)
            except DecisionError as error:
                messages = {'auto_categories_incomplete': 'Assign a model to Complex tasks and Straightforward tasks before using Auto.',
                    'auto_categories_invalid': 'Assign one exact registered endpoint/model to each task category.',
                    'auto_models_empty': 'Choose at least one available model to use Auto.',
                    'auto_models_invalid': 'Choose up to 32 exact registered endpoint/model identities.',
                    'auto_models_duplicate': 'Each Auto model may be selected only once.',
                    'auto_model_not_allowed': 'A selected Auto model is not allowed for your account.'}
                raise HTTPException(422, messages.get(error.code, 'An assigned Auto model is unavailable. Edit the category assignments.'),
                                    headers={'X-Decision-Failure': error.code}) from None
            for key in FEATURE_DEFAULTS:
                if key in changes:
                    value = changes[key]
                    if (key == 'tool_routing' and value not in {'existing', 'jev', 'all'}
                            or key == 'effort_mode' and value not in {'mapped', 'auto'}
                            or key.endswith('_enabled') and type(value) is not bool):
                        raise HTTPException(422, "Invalid chat feature setting")
                    features[key] = value
            changed = features != {**FEATURE_DEFAULTS, **(s.feature_settings or {})} or mode != s.approval_mode or route != s.route_mode or "elevation_roots" in changes
            if changed:
                s.policy_revision += 1
                s.elevation_grant = None
                s.approval_mode, s.route_mode = mode, route
                s.feature_settings = features
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
            if 'approaches' in changes:
                # Freeze old chats against the old mapping before replacing
                # owner configuration, even if those chats were never opened.
                for state in db.query(_models().ChatAutomationState).filter_by(
                        owner_key=p.owner_key, route_mode='auto').all():
                    self._materialize_auto_models(p, state)
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

    def acquire(self, owner, session_id, raw_ask, entrypoint, submission_id=None, *, ephemeral_semantics=False, input_card_id=None):
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
            # Snapshot semantic evidence BEFORE any earlier admission is superseded.
            previous = db.query(d.ChatAutomationSubmission).filter_by(
                session_id=sid, owner_key=owner_key(owner)).order_by(
                    d.ChatAutomationSubmission.created_at.desc(), d.ChatAutomationSubmission.submission_id.desc()).first()
            reply_card = None
            if input_card_id is not None:
                if not isinstance(input_card_id, str) or not 1 <= len(input_card_id) <= 128:
                    raise HTTPException(422, "Invalid clarification card")
                reply_card = db.query(d.ChatAutomationInputCard).filter_by(card_id=input_card_id,
                    owner_key=owner_key(owner), session_id=sid).with_for_update().first()
                if (reply_card is None or previous is None or previous.status != 'awaiting_input'
                        or reply_card.submission_id != previous.submission_id
                        or reply_card.status != 'pending' or reply_card.expires_at <= self.clock()):
                    raise HTTPException(409, "Clarification expired; send a new message")
            chat_name = db.query(d.Session.name).filter_by(id=sid).scalar()
            ephemeral_semantics = ephemeral_semantics or str(chat_name or '').strip().casefold() in {'nobody', 'incognito'}
            if reply_card is not None and ephemeral_semantics:
                raise HTTPException(409, "Reply to this question outside private mode, or send a new message")
            prior_task = self._task_projection(db, previous) if previous and not ephemeral_semantics else {}
            # A new accepted ask supersedes old pending control cards as well as
            # an expired preparation. Late finally blocks may not revive them.
            db.query(d.ChatAutomationSubmission).filter(
                d.ChatAutomationSubmission.session_id == sid,
                d.ChatAutomationSubmission.owner_key == owner_key(owner),
                d.ChatAutomationSubmission.status.in_(["preparing", "bound", "running", "awaiting_approval", "awaiting_input"]),
            ).update({"status": "cancelled"}, synchronize_session=False)
            db.query(d.ChatAutomationInputCard).filter_by(session_id=sid, owner_key=owner_key(owner),
                status='pending').update({'status': 'superseded'}, synchronize_session=False)
            if reply_card is not None:
                reply_card.status, reply_card.answer, reply_card.resolved_by_submission_id = 'resolved', raw_ask, identifier
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
                intent_digest=digest(raw_ask), descriptor={}, status="preparing", created_at=self.clock(),
                current_ask=None if ephemeral_semantics else raw_ask,
                task_lineage_id=previous.task_lineage_id if reply_card is not None else identifier,
                original_submission_id=previous.original_submission_id if reply_card is not None else identifier,
                previous_submission_id=previous.submission_id if previous else None,
                prior_task=None if ephemeral_semantics else prior_task))
            bound = BoundChatTurn(self, owner, sid, identifier, token, raw_ask, policy,
                                 {**copy.deepcopy(p.configuration or {}), **{key: policy[key] for key in FEATURE_DEFAULTS}},
                                 prior_task=prior_task, ephemeral_semantics=bool(ephemeral_semantics))
            if bound.ephemeral_semantics:
                from src.approval_context import retain_private_submission
                retain_private_submission(bound)
            return bound

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
            if status == 'completed' and row.status == 'awaiting_input':
                pending = db.query(d.ChatAutomationInputCard).filter_by(submission_id=bound.submission_id,
                    owner_key=owner_key(bound.owner), session_id=bound.session_id, status='pending').first()
                if pending is not None and pending.expires_at > self.clock():
                    status = 'awaiting_input'
            next_status = transition_status(row.status, status)
            if row.status in {"completed", "failed", "cancelled", "detached"}:
                return
            row.status = next_status
            if next_status in {'failed', 'cancelled', 'detached', 'completed'}:
                db.query(d.ChatAutomationInputCard).filter_by(submission_id=bound.submission_id,
                    owner_key=owner_key(bound.owner), status='pending').update(
                        {'status': 'cancelled' if next_status != 'completed' else 'superseded'}, synchronize_session=False)
            if status == 'awaiting_approval' and bound.pending_approval_id:
                row.routing = {**(row.routing or {}), 'pending_action_card_id': bound.pending_approval_id}
            if descriptor is not None:
                row.descriptor = copy.deepcopy(descriptor)
            lease = db.query(d.ChatAutomationLease).filter_by(session_id=bound.session_id, token=bound.token).first()
            if lease:
                if status == "preparing":
                    lease.expires_at = self.clock() + 90
                else:
                    db.delete(lease)


    def _task_projection(self, db, row):
        if row is None:
            return {}
        from src.automation_task_state import pending_task_projection
        d = _models()
        if row.current_ask is None:
            return {'submission_id': row.submission_id, 'previous_status': 'unknown'}
        card = db.query(d.ChatAutomationInputCard).filter_by(submission_id=row.submission_id,
            owner_key=row.owner_key, session_id=row.session_id, status='pending').first()
        pending = ({**copy.deepcopy(card.payload or {}), 'card_id': card.card_id, 'question': card.question,
                    'kind': 'clarification', 'session_id': row.session_id, 'input_card_id': card.card_id,
                    'expires_at': card.expires_at, 'resolved': False} if card else None)
        status = row.status
        pending_expiry = card.expires_at if card else None
        if status == 'awaiting_approval':
            reference = (row.routing or {}).get('pending_action_card_id')
            if reference:
                from src.tool_approvals import tool_approval_store
                action_card = tool_approval_store.peek(reference)
                if (action_card is not None and owner_key(action_card.owner) == row.owner_key
                        and action_card.session_id == row.session_id and action_card.submission_id == row.submission_id
                        and action_card.expires_at > self.clock()):
                    pending = {'card_id': action_card.approval_id, 'question': 'Exact action awaiting structured approval',
                        'kind': 'tool_approval', 'resolved': False, 'reference_only': True}
                    pending_expiry = action_card.expires_at
        if status in {'awaiting_input', 'awaiting_approval'} and pending is None:
            result = {'previous_request': row.current_ask, 'previous_answer': row.previous_answer,
                      'previous_status': status, 'active_task': row.current_ask, 'pending_question': None}
        else:
            result = pending_task_projection(row.current_ask, status, previous_answer=row.previous_answer,
                pending_question=pending, pending_expires_at=pending_expiry, now=self.clock())
        original = db.query(d.ChatAutomationSubmission).filter_by(submission_id=row.original_submission_id,
            owner_key=row.owner_key, session_id=row.session_id).first() if row.original_submission_id else None
        return {**result, 'submission_id': row.submission_id, 'task_lineage_id': row.task_lineage_id,
                'original_submission_id': row.original_submission_id,
                'original_task': original.current_ask if original else row.current_ask,
                'answer_message_id': row.answer_message_id, 'preparation': copy.deepcopy(row.preparation),
                'routing': copy.deepcopy(row.routing)}

    def task_state(self, owner, session_id):
        """Reloadable owner-scoped semantic facts, with no inferred permissions."""
        d = _models()
        with self.transaction() as db:
            self.state(db, owner, session_id)
            row = db.query(d.ChatAutomationSubmission).filter_by(session_id=str(session_id),
                owner_key=owner_key(owner)).order_by(d.ChatAutomationSubmission.created_at.desc(),
                    d.ChatAutomationSubmission.submission_id.desc()).first()
            return self._task_projection(db, row)

    def record_preparation(self, bound):
        d = _models()
        if not bound.current():
            raise HTTPException(409, "Turn changed during preparation")
        with self.transaction() as db:
            row = db.query(d.ChatAutomationSubmission).filter_by(submission_id=bound.submission_id,
                owner_key=owner_key(bound.owner), session_id=bound.session_id).one()
            row.preparation = bound.preparation.public() if bound.preparation else None
            row.routing = {'tools': bound.tool_selection.public() if bound.tool_selection else None,
                'followup': bound.followup.public() if bound.followup else None,
                'diagnostics': copy.deepcopy(bound.diagnostics), 'approach': copy.deepcopy(bound.approach_selection),
                'effort': copy.deepcopy(bound.effort_selection)}
            if bound.followup and bound.followup.relation in {'continuation', 'confirmation'}:
                prior = row.prior_task or {}
                row.task_lineage_id = prior.get('task_lineage_id') or row.task_lineage_id
                row.original_submission_id = prior.get('original_submission_id') or row.original_submission_id

    def record_input(self, bound, payload, *, ttl=3600):
        """Persist actual ordinary ask_user events; approvals retain their owner."""
        if bound.ephemeral_semantics:
            return None
        if (not isinstance(payload, dict) or payload.get('kind') == 'tool_approval'
                or payload.get('approval_id')):
            return None
        question = payload.get('question')
        if not isinstance(question, str) or not question.strip() or len(question) > 8000:
            return None
        if not bound.current():
            return None
        d = _models()
        with self.transaction() as db:
            row = db.query(d.ChatAutomationSubmission).filter_by(submission_id=bound.submission_id,
                owner_key=owner_key(bound.owner), session_id=bound.session_id).one()
            if row.status in {'completed', 'failed', 'cancelled', 'detached'}:
                return None
            existing = db.query(d.ChatAutomationInputCard).filter_by(submission_id=bound.submission_id,
                owner_key=owner_key(bound.owner), session_id=bound.session_id, status='pending').first()
            if existing:
                identifier = existing.card_id
            else:
                identifier = uuid.uuid4().hex
                db.add(d.ChatAutomationInputCard(card_id=identifier, owner_key=owner_key(bound.owner),
                    session_id=bound.session_id, submission_id=bound.submission_id, question=question,
                    payload=_clarification_payload(payload), status='pending',
                    created_at=self.clock(), expires_at=self.clock() + min(max(ttl, 1), 3600)))
            row.status = transition_status(row.status, 'awaiting_input')
            bound.pending_input_id = identifier
            return identifier

    def resolve_input(self, owner, session_id, card_id, answer, *, submission_id):
        """Explicit opaque clarification reply; no permission or old lease revival."""
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 50000:
            raise HTTPException(422, "Enter a clarification reply")
        d = _models()
        with self.transaction() as db:
            self.state(db, owner, session_id)
            card = db.query(d.ChatAutomationInputCard).filter_by(card_id=card_id,
                owner_key=owner_key(owner), session_id=str(session_id)).first()
            current = db.query(d.ChatAutomationSubmission).filter_by(submission_id=submission_id,
                owner_key=owner_key(owner), session_id=str(session_id)).first()
            if (card is None or current is None or current.status not in {'preparing', 'bound'}
                    or current.previous_submission_id != card.submission_id
                    or card.status not in {'pending', 'superseded'} or card.expires_at <= self.clock()):
                raise HTTPException(409, "Clarification expired; send a new message")
            card.status, card.answer, card.resolved_by_submission_id = 'resolved', answer, submission_id
            return {'card_id': card.card_id, 'kind': 'clarification', 'resolved': True}

    def record_answer(self, bound, answer, *, message_id=None):
        if bound.ephemeral_semantics:
            return
        if not isinstance(answer, str) or len(answer.encode('utf-8')) > 256 * 1024:
            return
        d = _models()
        with self.transaction() as db:
            row = db.query(d.ChatAutomationSubmission).filter_by(submission_id=bound.submission_id,
                owner_key=owner_key(bound.owner), session_id=bound.session_id).first()
            if row is None or row.status in {'cancelled', 'detached', 'failed'}:
                return
            if message_id is not None:
                message = db.query(d.ChatMessage).filter_by(id=message_id, session_id=bound.session_id,
                    role='assistant').first()
                if message is None:
                    raise HTTPException(409, "Answer does not belong to this turn")
            row.previous_answer, row.answer_message_id = answer, message_id



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
        from src.approach_routing import validate_approaches
        from src.decisions.contracts import DecisionError
        try:
            validate_approaches(mappings)
        except DecisionError:
            raise HTTPException(422, "Invalid approach model selection or supported efforts") from None


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
    from src.approval_context import forget_private_evidence
    forget_private_evidence(old_owner)
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
    db.query(d.ChatAutomationInputCard).filter_by(owner_key=old_key).update(
        {'owner_key': new_key, 'status': 'cancelled'}, synchronize_session=False)
    for attempt in db.query(d.DecisionAttempt).filter_by(owner_key=old_key):
        attempt.owner_key = new_key
        if attempt.status == 'reserved':
            attempt.status = 'unresolved'
            attempt.record = {**attempt.record, 'status': 'unresolved', 'failure_kind': 'owner_changed', 'usage': None}


def retire_automation_owner(db, owner):
    from src.approval_context import forget_private_evidence
    forget_private_evidence(owner)
    d = _models()
    key = owner_key(owner)
    # A later account with the same spelling must not inherit these settings.
    db.query(d.OwnerChatAutomation).filter_by(owner_key=key).delete(synchronize_session=False)
    for state in db.query(d.ChatAutomationState).filter_by(owner_key=key):
        state.approval_mode, state.route_mode, state.elevation_grant = 'always_ask', 'manual', None
        state.policy_revision += 1
        state.feature_settings = {}
    db.query(d.ChatAutomationLease).filter_by(owner_key=key).delete(synchronize_session=False)
    retired = 'retired:' + uuid.uuid4().hex
    db.query(d.ChatAutomationSubmission).filter_by(owner_key=key).update(
        {'owner_key': retired, 'status': 'cancelled', 'current_ask': None, 'prior_task': None,
         'previous_answer': None, 'answer_message_id': None, 'preparation': None, 'routing': None},
        synchronize_session=False)
    db.query(d.ChatAutomationInputCard).filter_by(owner_key=key).delete(synchronize_session=False)
    db.query(d.DecisionAttempt).filter_by(owner_key=key).update({'owner_key': retired}, synchronize_session=False)


def retire_automation_session(db, owner, session_id):
    """Delete private task/card content with its owned chat, in the same transaction."""
    d = _models()
    sid, key = str(session_id), owner_key(owner)
    session = db.query(d.Session).filter_by(id=sid).first()
    if session is None or owner_key(session.owner) != key:
        return False
    from src.approval_context import forget_private_evidence
    forget_private_evidence(owner, sid)
    for model in (d.ChatAutomationInputCard, d.ChatAutomationSubmission, d.ChatAutomationLease,
                  d.ChatAutomationState):
        db.query(model).filter_by(owner_key=key, session_id=sid).delete(synchronize_session=False)
    return True


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
            from src.decisions.store import fallback_exemption, reservation_limit
            exempt = fallback_exemption(record, [r.record for r in previous])
            if (db.query(d.DecisionAttempt).filter_by(attempt_id=record["attempt_id"]).first()
                    or len(previous) >= reservation_limit(record, [r.record for r in previous], max_calls)
                    or any((r.status in {"reserved", "unresolved"} and r.attempt_id != exempt)
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
    pending_input_id: str | None = None
    prior_task: dict = field(default_factory=dict)
    ephemeral_semantics: bool = False
    approval_evidence: list = field(default_factory=list, repr=False)
    review_observations: list = field(default_factory=list, repr=False)
    review_observations_omitted: bool = False
    review_model_origin: str = ""
    review_route_revision: int = 0
    preparation: Any = None
    tool_selection: Any = None
    followup: Any = None
    diagnostics: dict = field(default_factory=dict)
    approach_selection: dict = field(default_factory=dict)
    effort_selection: dict = field(default_factory=dict)

    @property
    def no_tools(self):
        return self.tool_selection is not None and self.tool_selection.kind == 'no_tools'


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
        from src.approval_fallback import model_snapshot
        if not self.review_model_origin or model_snapshot(self) != self.review_model_origin:
            raise RuntimeError("The originating conversation route changed")
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
        # Retire old-route cards before publishing the authorized new binding.
        # Revision also prevents route A -> B -> A from reviving old grants.
        from src.tool_approvals import tool_approval_store
        tool_approval_store.retire_for_session(owner=self.owner, session_id=self.session_id)
        self.pending, self.pending_approval_id = False, None
        self.review_route_revision += 1
        descriptor = {**self.descriptor, "endpoint_id": chosen[1]["endpoint_id"],
            "model": candidate[1], "reasoning_effort": reasoning_effort or "",
            "source": "explicit_fallback"}
        self.store.record(self, "bound", descriptor)
        self.endpoint_url, self.headers = candidate[0], dict(candidate[2] or {})
        self.descriptor = descriptor
        self.review_model_origin = model_snapshot(self)


def current_turn():
    return _BOUND.get()


def bound_session_view(session):
    bound = current_turn()
    return bound.view(session) if bound else session


async def prepare_chat_turn(store, owner, session, raw_ask, *, entrypoint="interactive", submission_id=None,
                            reasoning_effort=None, allow_empty=False, tool_inventory=None,
                            preparation=None, decision_provider=None, supported_efforts=None, ephemeral_semantics=False, input_card_id=None):
    if not isinstance(raw_ask, str) or (not raw_ask.strip() and not allow_empty) or len(raw_ask) > 50000:
        raise HTTPException(422, "Enter a nonblank message of at most 50000 characters")
    bound = store.acquire(owner, session.id, raw_ask, entrypoint, submission_id,
        ephemeral_semantics=ephemeral_semantics or str(getattr(session, 'name', '') or '').strip().casefold() in {'nobody', 'incognito'},
        input_card_id=input_card_id)
    owner = bound.owner
    try:
        from src.decision_routing import preparation_receipt
        if bound.ephemeral_semantics:
            bound.preparation = preparation_receipt(bound, unknowns=['ephemeral_context_unavailable'])
        elif isinstance(preparation, dict):
            bound.preparation = preparation_receipt(bound, preparation.get('sources', ()),
                unknowns=preparation.get('unknowns', ()))
        else:
            bound.preparation = preparation or preparation_receipt(bound, unknowns=['canonical_context_not_prepared'])
        bound.preparation.validate(bound)
        try:
            from src.decisions.observability import default_observability, DecisionObservability
        except ModuleNotFoundError as error:
            if error.name != 'src.decisions.observability':
                raise
            # The optional module is absent in this pre-integration source.
        else:
            observer = default_observability(store.session_factory)
            observer.bind_turn(owner, session.id, bound.run_id, ephemeral=bound.ephemeral_semantics)

        effort = ""
        mode = str(getattr(session, "thinking_mode", "") or "")
        if mode.startswith("effort:"):
            effort = mode[7:]
        if bound.policy["route_mode"] == "auto":
            from src.model_auto import select_model_and_effort
            from src.decisions import DecisionProvider
            from src.decisions.contracts import DecisionContext
            selected = await select_model_and_effort(raw_ask, bound.policy.get('auto_categories', {}), owner=owner,
                provider=decision_provider or DecisionProvider(store=SqlAttemptStore(store)),
                config=bound.configuration.get("decision_provider", {}),
                context=DecisionContext(owner, session.id, bound.run_id,
                    policy_revision=bound.policy["policy_revision"], authority_epoch=bound.policy["authority_epoch"],
                    cancelled=lambda: not bound.current()), evidence=bound.preparation.evidence())
            endpoint_id, model = selected["endpoint_id"], selected["model"]
            effort = selected.get("reasoning_effort") or ""
            bound.approach_selection = copy.deepcopy(selected.get('selection') or {})
            bound.effort_selection = copy.deepcopy(selected['effort_selection'])
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
                raise HTTPException(409, "Choose registered API models for Auto")
            bound.endpoint_url = session.endpoint_url
            bound.headers = dict(session.headers or {})
        bound.descriptor = {"endpoint_id": endpoint_id, "model": model, "reasoning_effort": effort,
            "route_mode": bound.policy["route_mode"], "submission_id": bound.submission_id}
        if bound.policy['route_mode'] == 'auto':
            bound.descriptor['supported_efforts'] = selected['supported_efforts']
            bound.descriptor['auto_category'] = selected['category']
            bound.descriptor['auto_category_snapshot'] = selected['category_snapshot']
            bound.descriptor['effort_support_source'] = 'registered_runtime'
        if not bound.current():
            raise HTTPException(409, "Chat settings changed before submission")
        if (tool_inventory is not None or bound.policy.get('followup_enabled') or bound.policy.get('preparation_enabled')):
            from src.decision_routing import prepare_tool_routing
            await prepare_tool_routing(bound, tool_inventory if tool_inventory is not None else [],
                                       provider=decision_provider, semantic_only=tool_inventory is None)
        store.record_preparation(bound)
        bind_prepared_browser_scope(bound)
        from src.approval_fallback import model_snapshot
        bound.review_model_origin = model_snapshot(bound)
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
    if bound.policy.get('route_mode') == 'auto':
        from src.model_auto import category_snapshot, public_category_snapshot
        snapshot = category_snapshot(bound.owner, bound.policy.get('auto_categories', {}))
        assigned = bound.policy['auto_categories'].get(bound.descriptor.get('auto_category'))
        if (public_category_snapshot(snapshot) != bound.descriptor.get('auto_category_snapshot')
                or assigned != {'endpoint_id': bound.descriptor['endpoint_id'], 'model': model}):
            raise RuntimeError('Auto category assignments changed before provider dispatch')
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
    supported = bound.descriptor.get('supported_efforts')
    if supported is not None and (reasoning_effort or None) not in supported:
        raise RuntimeError("Execution effort is outside the committed supported set")


async def prepare_chat_submission(*args, **kwargs):
    """Map bounded selection failures to an actionable ingress response."""
    from src.decisions import DecisionError
    try:
        return await prepare_chat_turn(*args, **kwargs)
    except DecisionError as error:
        model_errors = {
            'auto_categories_incomplete': 'Assign a model to Complex tasks and Straightforward tasks before using Auto.',
            'auto_categories_invalid': 'Auto category assignments are invalid. Choose the models again.',
            'auto_models_empty': 'Choose at least one available model to use Auto.',
            'auto_model_unavailable': 'A selected Auto model is unavailable. Edit Auto models before sending.',
            'auto_model_not_allowed': 'A selected Auto model is no longer allowed for your account.',
            'auto_effort_unsupported': 'Jev selected unsupported reasoning effort. Your message was not sent.',
            'auto_models_changed': 'Auto models changed during selection. Your message was not sent.',
            'auto_selection_uncertain': 'Jev declined to choose a supported model and effort. Edit Auto models or choose a model manually.',
        }
        raise HTTPException(409,
            model_errors.get(error.code, "Jev could not select a supported model and effort. Your message was not sent. Check Auto models and the decision provider."),
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


def session_preparation_sources(session, raw_ask):
    """Bounded already loaded owned history; no transcript authority inference."""
    history = list(getattr(session, 'history', ()) or ())[-6:]
    sources = []
    for index, message in enumerate(history):
        role = message.get('role') if isinstance(message, dict) else getattr(message, 'role', None)
        content = message.get('content') if isinstance(message, dict) else getattr(message, 'content', None)
        metadata = message.get('metadata') if isinstance(message, dict) else getattr(message, 'metadata', None)
        if role not in {'user', 'assistant'} or not isinstance(content, str) or not content:
            continue
        if index == len(history) - 1 and role == 'user' and content == raw_ask:
            continue
        raw = content.encode('utf-8')
        excerpt = raw[:2048].decode('utf-8', errors='ignore')
        if not excerpt:
            continue
        version = hashlib.sha256(raw).hexdigest()
        message_id = metadata.get('_db_id') if isinstance(metadata, dict) else None
        sources.append({'source_id': str(message_id) if message_id else 'owned_history:' + str(index) + ':' + version[:16],
            'version': version, 'excerpt': excerpt,
            'coverage': 'complete' if len(raw) <= 2048 else 'prefix', 'trust': 'untrusted_history'})
    return {'sources': sources, 'unknowns': ['history_is_evidence_not_pending_state', 'external_sources_not_returned']}


def _clarification_payload(payload):
    result = {'question': payload['question'], 'kind': 'clarification'}
    options = payload.get('options')
    if isinstance(options, list) and len(options) <= 6:
        result['options'] = [{'label': item['label'],
            **({'description': item['description']} if isinstance(item.get('description'), str)
               and len(item['description']) <= 1000 else {})}
            for item in options if isinstance(item, dict) and isinstance(item.get('label'), str)
            and 0 < len(item['label']) <= 300]
    if type(payload.get('multi')) is bool:
        result['multi'] = payload['multi']
    return result


class TurnEventObserver:
    """Observe and annotate complete SSE frames, including arbitrary splits."""
    def __init__(self, bound):
        import codecs
        self.bound, self.buffer, self.answer = bound, '', ''
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')

    def feed(self, chunk):
        import re
        if isinstance(chunk, bytes):
            chunk = self.decoder.decode(chunk)
        if not isinstance(chunk, str):
            return ''
        self.buffer += chunk
        output = []
        while True:
            delimiter = re.search(r'\r?\n\r?\n', self.buffer)
            if delimiter is None:
                break
            frame, separator = self.buffer[:delimiter.start()], self.buffer[delimiter.start():delimiter.end()]
            self.buffer = self.buffer[delimiter.end():]
            lines = frame.splitlines()
            data_lines = [line[5:].lstrip(' ') for line in lines if line.startswith('data:')]
            try:
                event = json.loads('\n'.join(data_lines))
            except (ValueError, TypeError):
                event = None
            if isinstance(event, dict):
                if event.get('type') == 'ask_user' and isinstance(event.get('data'), dict):
                    identifier = self.bound.store.record_input(self.bound, event['data'])
                    if identifier:
                        event['data'].update(input_card_id=identifier, session_id=self.bound.session_id)
                        replacement = 'data: ' + json.dumps(event, ensure_ascii=False)
                        replaced, updated = False, []
                        for line in lines:
                            if line.startswith('data:'):
                                if not replaced:
                                    updated.append(replacement)
                                    replaced = True
                            else:
                                updated.append(line)
                        frame = '\n'.join(updated)
                if event.get('type') == 'final_response' and isinstance(event.get('content'), str):
                    self.answer = event['content']
                elif isinstance(event.get('delta'), str) and not event.get('thinking'):
                    if len((self.answer + event['delta']).encode('utf-8')) <= 256 * 1024:
                        self.answer += event['delta']
            output.append(frame + separator)
        # Oversize observational frames remain byte-for-byte visible, without
        # accumulating unbounded parser state or inferring a partial card.
        if len(self.buffer) > 512 * 1024:
            output.append(self.buffer)
            self.buffer = ''
        return ''.join(output)

    def finish(self):
        tail, self.buffer = self.buffer + self.decoder.decode(b'', final=True), ''
        if self.answer and not getattr(self.bound, '_canonical_answer_saved', False):
            self.bound.store.record_answer(self.bound, self.answer)
        return tail


def observe_json_response(bound, response):
    body = getattr(response, 'body', None)
    if isinstance(response, dict):
        value = response
    elif isinstance(body, (bytes, str)) and len(body) <= 512 * 1024:
        try:
            value = json.loads(body)
        except (ValueError, TypeError):
            return
    else:
        return
    if not isinstance(value, dict):
        return
    for event in value.get('tool_events') or []:
        if isinstance(event, dict):
            bound.store.record_input(bound, event.get('ask_user'))
    answer = value.get('response', value.get('content'))
    if isinstance(answer, str):
        bound.store.record_answer(bound, answer)


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
            if data.get('input_card_id') and (data.get('tool_approval_id') or not str(data.get('message') or '').strip()):
                raise HTTPException(422, "A clarification needs a nonblank reply and cannot also approve an action")
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
            ephemeral_semantics = (str(data.get('incognito', '')).casefold() == 'true'
                or str(getattr(session, 'name', '') or '').strip().casefold() in {'nobody', 'incognito'})
            preparation = None if ephemeral_semantics else session_preparation_sources(session, data.get('message') or '')
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
                    reasoning_effort=data.get("reasoning_effort"), allow_empty=True, preparation=preparation, ephemeral_semantics=ephemeral_semantics)
            else:
                bound = await prepare_chat_submission(store, owner, session, data["message"],
                    entrypoint="delegated" if is_delegated_credential(request) else "interactive",
                    submission_id=request.headers.get("X-Chat-Submission-Id"),
                    reasoning_effort=data.get("reasoning_effort"), preparation=preparation, ephemeral_semantics=ephemeral_semantics,
                    input_card_id=data.get('input_card_id'))
            try:
                with bound.activate():
                    response = await func(request, *args, **kwargs)
                if hasattr(response, "body_iterator"):
                    original = response.body_iterator
                    async def body():
                        observer = TurnEventObserver(bound)
                        ended = False
                        try:
                            with bound.activate():
                                async for item in original:
                                    observed = observer.feed(item)
                                    if observed:
                                        yield observed
                            tail = observer.finish()
                            if tail:
                                yield tail
                            ended = True
                        finally:
                            if not ended:
                                observer.finish()
                            if not response.headers.get("X-Odysseus-Run-Id"):
                                store.record(bound, 'awaiting_input' if bound.pending_input_id else
                                    'awaiting_approval' if bound.pending else 'completed' if ended else 'detached')
                    response.body_iterator = body()
                else:
                    observe_json_response(bound, response)
                    store.record(bound, 'awaiting_input' if bound.pending_input_id else
                                 'awaiting_approval' if bound.pending else 'completed')
                return response
            except BaseException:
                store.record(bound, "awaiting_approval" if data.get("tool_approval_id") and bound.pending else "failed")
                raise
        return wrapped
    return decorate
