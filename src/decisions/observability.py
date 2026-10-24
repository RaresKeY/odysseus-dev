"""Bounded decision observations, separate from authority and spend accounting.

The provider invokes the two observer hooks around its *existing* attempt. Reads,
exports and replay never resolve a model or restore executable state. Free text
is deliberately absent: even choice keys may contain browser/private content.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import re
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from functools import lru_cache
from types import SimpleNamespace

from fastapi import HTTPException
from src.decisions.contracts import DecisionError, SCHEMA_VERSION, decode, encode, probability, valid_usage

CAPTURE_SCHEMA = "odysseus-redacted-decision/v1"
REDACTION_POLICY = "omit-all-content/v1"
PURPOSES = frozenset({"approach", "approach_routing", "tools", "tool_selection", "followup",
    "followup_routing", "effort", "execution_effort", "completion_advice", "safety_advice",
    "approval", "approval_review", "browser", "browser_action", "readiness", "diagnostics", "turn_preparation"})
QUESTION_IDS = frozenset({"tools", "followup", "online_search", "freshness", "fresh_information", "project_context",
    "projectcontext", "readiness", "approach", "effort", "execution_effort", "completion", "safety", "permission"})
STATUSES = frozenset({"reserved", "settled", "rejected", "unresolved", "cancelled"})
FAILURES = frozenset({"decision_admission_failed", "decision_attempt_store_unavailable", "decision_cancelled",
    "decision_deadline_exceeded", "decision_encoding_refused", "decision_endpoint_changed", "decision_endpoint_unavailable",
    "decision_http_error", "decision_incomplete_response", "decision_invalid_context", "decision_invalid_request",
    "decision_invalid_response", "decision_model_mismatch", "decision_redirect_refused", "decision_request_too_large",
    "decision_response_too_large", "decision_transport_failed", "decision_usage_exceeded_reservation",
    "decision_choice_mismatch", "decision_invalid_answer", "decision_invalid_answers", "decision_invalid_distribution",
    "decision_invalid_json", "decision_usage_unavailable", "decision_capture_capacity_exceeded", "decision_capture_store_unavailable"})
MAX_RECORD_BYTES = 256 * 1024
MAX_CAPTURE_RECORDS = 32
SESSION_CAPTURE_BYTES = 8 * 1024 * 1024
OWNER_CAPTURE_BYTES = 16 * 1024 * 1024
MAX_HISTORY = 256
MAX_OWNER_HISTORY = 2048
MAX_RETENTION = 24 * 60 * 60
_REF = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_HASH = re.compile(r"^[a-f0-9]{64}$")
_model_lock = threading.Lock()


def _reference(value):
    if not isinstance(value, str) or not _REF.fullmatch(value):
        raise ValueError("Invalid observation reference")
    return value


def _purpose(value):
    # An arbitrary purpose string is also untrusted content.
    return value if value in PURPOSES else "other"


def _owner_key(owner):
    from src.owner_identity import effective_storage_owner
    from src.chat_automation import owner_key
    admitted = effective_storage_owner(owner)
    if admitted is None:
        raise HTTPException(401, "A chat owner is required")
    return owner_key(admitted)


@lru_cache(maxsize=1)
def _register_observation_models():
    """Register additive tables lazily; offline replay does not import a DB."""
    from sqlalchemy import Column, Float, ForeignKey, Integer, JSON, String
    from core.database import Base

    class DecisionActivity(Base):
        __tablename__ = "decision_activity"
        sequence = Column(Integer, primary_key=True, autoincrement=True)
        attempt_id = Column(String, nullable=False, unique=True)
        owner_key = Column(String, nullable=False, index=True)
        session_id = Column(String, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
        run_id = Column(String, nullable=False, index=True)
        created_at = Column(Float, nullable=False)
        projection = Column(JSON, nullable=False)

    class DecisionCaptureConsent(Base):
        __tablename__ = "decision_capture_consents"
        session_id = Column(String, ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True)
        owner_key = Column(String, nullable=False, index=True)
        policy = Column(JSON, nullable=False)

    class DecisionCaptureRun(Base):
        __tablename__ = "decision_capture_runs"
        id = Column(String, primary_key=True)
        owner_key = Column(String, nullable=False, index=True)
        session_id = Column(String, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
        run_id = Column(String, nullable=False, unique=True)
        policy = Column(JSON, nullable=False)
        captures = Column(JSON, nullable=False, default=list)
        bytes_used = Column(Integer, nullable=False, default=0)
        expires_at = Column(Float, nullable=False, index=True)

    class DecisionHandoff(Base):
        __tablename__ = "decision_handoffs"
        id = Column(String, primary_key=True)
        owner_key = Column(String, nullable=False, index=True)
        session_id = Column(String, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
        run_id = Column(String, nullable=False, index=True)
        created_at = Column(Float, nullable=False)
        receipt = Column(JSON, nullable=False)

    return SimpleNamespace(activity=DecisionActivity, consent=DecisionCaptureConsent,
        capture=DecisionCaptureRun, handoff=DecisionHandoff)


def observation_models():
    # functools caching alone does not serialize concurrent first calls.
    with _model_lock:
        return _register_observation_models()


def _projection(request, record):
    questions = request.questions()
    offer = {(name if name in QUESTION_IDS else f"q{i + 1:03d}"): {f"c{j + 1:03d}": original
        for j, original in enumerate(question["criteria"])}
        for i, (name, question) in enumerate(questions.items())}
    mapping = dict(zip(questions, offer))
    identity = record.get("provider_identity", {})
    provider = {"protocol": identity.get("protocol") if identity.get("protocol") in {"systemone", "openai_json"} else "unknown",
        "distribution_source": identity.get("distribution_source") if identity.get("distribution_source") in
        {"provider_reported", "model_reported"} else "unknown"}
    digest = identity.get("configuration_digest")
    if isinstance(digest, str) and _HASH.fullmatch(digest):
        provider["configuration_digest"] = digest
    public = {"attempt_id": _reference(record["attempt_id"]), "run_id": _reference(record["run_id"]),
        "purpose": _purpose(request.purpose), "status": "reserved", "usage": None,
        "usage_provenance": "unknown_held", "provider": provider, "questions": {},
        "failure_kind": None, "duration_ms": None, "capture_state": "not_requested",
        "confidence_meaning": "model_reported_not_task_success",
        "policy_revision": record.get("policy_revision", 0), "authority_epoch": record.get("authority_epoch", 0)}
    # Only the detached, opaque projection is persisted; maps remain local.
    return public, mapping, offer


def _answers(result, mapping, offers):
    if result is None:
        return {}
    answers = {}
    for original, qid in mapping.items():
        answer = result.answers.get(original, {})
        probabilities = answer.get("probabilities", {})
        reverse = {value: key for key, value in offers[qid].items()}
        if (answer.get("choice") not in reverse or not probability(answer.get("confidence"))
                or set(probabilities) != set(reverse) or not all(probability(p) for p in probabilities.values())
                or abs(sum(probabilities.values()) - 1) > 1e-5):
            continue
        answers[qid] = {"choice": reverse[answer["choice"]], "confidence": float(answer["confidence"]),
            "probabilities": {reverse[key]: float(value) for key, value in probabilities.items()}}
    return answers


def _packet(public, offers):
    body = {"schema": CAPTURE_SCHEMA, "source_kind": "captured_live_redacted",
        "redaction_policy": REDACTION_POLICY, "exact_original_replay": False,
        "decision_schema": SCHEMA_VERSION, "implementation_sha256": _implementation_digest(),
        "ordinal": public.get("capture_sequence", 0),
        "expected": None, "label_provenance": "unscored_transformed_input",
        "omitted": ["state", "instructions", "question_names", "choice_names", "choice_labels",
                    "prepared_values", "wire_digest", "raw_response", "provider_labels"],
        "attempt_id": public["attempt_id"], "run_id": public["run_id"], "purpose": public["purpose"],
        "fixture": {"state": {"content_omitted": True}, "questions": {
            qid: list(choices) for qid, choices in offers.items()}},
        "observation": copy.deepcopy(public)}
    return {"packet": body, "sanitized_sha256": hashlib.sha256(encode(body)).hexdigest()}


@lru_cache(maxsize=1)
def _implementation_digest():
    from pathlib import Path
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class DecisionObservability:
    def __init__(self, session_factory=None, *, clock=time.time, initialize=True):
        from core.database import SessionLocal
        self.session_factory = session_factory or SessionLocal
        self.clock = clock
        self.models = observation_models()
        self._required_runs = OrderedDict()
        self._required_lock = threading.Lock()
        if initialize:
            with self.session_factory() as db:
                for model in vars(self.models).values():
                    model.__table__.create(db.get_bind(), checkfirst=True)

    @contextmanager
    def _transaction(self, *, write=False):
        db = self.session_factory()
        try:
            if write and db.get_bind().dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _admit(self, db, owner, session_id):
        from core.database import Session
        key = _owner_key(owner)
        row = db.query(Session).filter_by(id=str(session_id)).first()
        if row is None or _owner_key(row.owner) != key:
            raise HTTPException(404, "Chat not found")
        return key

    def _expire(self, db):
        m = self.models
        db.query(m.capture).filter(m.capture.expires_at <= self.clock()).delete(synchronize_session=False)
        for row in db.query(m.consent).all():
            if row.policy["expires_at"] <= self.clock():
                db.delete(row)

    def consent(self, owner, session_id, policy):
        allowed = {"purposes", "expires_in_seconds", "retention_seconds", "max_records", "max_bytes", "required"}
        if (not isinstance(policy, dict) or set(policy) - allowed or not isinstance(policy.get("purposes"), list)
                or not policy["purposes"] or len(policy["purposes"]) > len(PURPOSES)
                or any(not isinstance(p, str) or p not in PURPOSES for p in policy["purposes"])):
            raise HTTPException(422, "Select supported decision purposes")
        checked = {"purposes": sorted(set(policy["purposes"])), "required": policy.get("required", False)}
        if type(checked["required"]) is not bool:
            raise HTTPException(422, "Invalid capture requirement")
        for name, default, maximum in [("expires_in_seconds", 900, 3600), ("retention_seconds", MAX_RETENTION, MAX_RETENTION),
                ("max_records", MAX_CAPTURE_RECORDS, MAX_CAPTURE_RECORDS), ("max_bytes", SESSION_CAPTURE_BYTES, SESSION_CAPTURE_BYTES)]:
            value = policy.get(name, default)
            if type(value) is not int or not 1 <= value <= maximum:
                raise HTTPException(422, "Invalid capture limit")
            checked[name] = value
        checked["expires_at"] = self.clock() + checked.pop("expires_in_seconds")
        checked["consent_id"] = uuid.uuid4().hex
        checked["redaction_policy"] = REDACTION_POLICY
        with self._transaction(write=True) as db:
            key = self._admit(db, owner, session_id)
            self._expire(db)
            row = db.get(self.models.consent, session_id)
            if row is None:
                db.add(self.models.consent(session_id=session_id, owner_key=key, policy=checked))
            else:
                row.owner_key, row.policy = key, checked
        return copy.deepcopy(checked)

    def bind_turn(self, owner, session_id, run_id):
        """Consume next-turn consent at canonical admission, even for manual turns."""
        _reference(run_id)
        with self._transaction(write=True) as db:
            key = self._admit(db, owner, session_id)
            self._expire(db)
            existing = db.query(self.models.capture).filter_by(run_id=run_id, owner_key=key, session_id=session_id).first()
            if existing is not None:
                policy = copy.deepcopy(existing.policy)
                self._remember_requirement(key, session_id, run_id, policy)
                return policy
            row = db.get(self.models.consent, session_id)
            if row is None or row.owner_key != key:
                return None
            policy = copy.deepcopy(row.policy)
            db.delete(row)
            db.add(self.models.capture(id=uuid.uuid4().hex, owner_key=key, session_id=session_id,
                run_id=run_id, policy=policy, captures=[], bytes_used=0,
                expires_at=self.clock() + policy["retention_seconds"]))
        self._remember_requirement(key, session_id, run_id, policy)
        return policy

    def _remember_requirement(self, owner_key, session_id, run_id, policy):
        # An ephemeral bound-turn snapshot makes required capture fail closed
        # even when the DB becomes unavailable between admission and dispatch.
        # It holds no request content and expires with the capture lifetime.
        with self._required_lock:
            if policy["required"]:
                self._required_runs[(owner_key, session_id, run_id)] = (self.clock() + policy["retention_seconds"], frozenset(policy["purposes"]))
            while len(self._required_runs) > 4096:
                self._required_runs.popitem(last=False)

    def before_dispatch(self, request, context, record):
        """Provider hook. Optional observation failures never request a resend."""
        with self._required_lock:
            expires_at, purposes = self._required_runs.get((_owner_key(context.owner), context.session_id, context.run_id), (0, ()))
            required = expires_at > self.clock() and request.purpose in purposes
        try:
            public, mapping, offers = _projection(request, record)
        except Exception:
            if required:
                raise DecisionError("decision_capture_store_unavailable") from None
            return None
        ticket = {"owner": context.owner, "session_id": context.session_id, "run_id": context.run_id,
            "attempt_id": public["attempt_id"], "public": public, "mapping": mapping, "offers": offers,
            "started": self.clock(), "capture_id": None}
        try:
            with self._transaction(write=True) as db:
                key = self._admit(db, context.owner, context.session_id)
                self._expire(db)
                run = db.query(self.models.capture).filter_by(owner_key=key, session_id=context.session_id,
                    run_id=context.run_id).with_for_update().first()
                if run is not None and request.purpose in run.policy["purposes"]:
                    required = run.policy["required"]
                    packet = _packet(public, offers)
                    # Reserve per-attempt space before spending, including the
                    # bounded final distribution and provenance fields.
                    reserve = min(MAX_RECORD_BYTES, len(encode(packet)) + 48 * sum(len(v) for v in offers.values()) + 4096)
                    total = sum(r.bytes_used for r in db.query(self.models.capture).filter_by(owner_key=key).all())
                    session_total = sum(r.bytes_used for r in db.query(self.models.capture).filter_by(owner_key=key, session_id=context.session_id).all())
                    if (len(run.captures) >= run.policy["max_records"] or run.bytes_used + reserve > run.policy["max_bytes"]
                            or total + reserve > OWNER_CAPTURE_BYTES or session_total + reserve > SESSION_CAPTURE_BYTES):
                        public["capture_state"] = "capacity_exceeded"
                        if required:
                            raise DecisionError("decision_capture_capacity_exceeded")
                    else:
                        public["capture_state"] = "reserved"
                        public["capture_sequence"] = len(run.captures) + 1
                        packet = _packet(public, offers)
                        run.captures = [*run.captures, packet]
                        run.bytes_used += reserve
                        ticket["capture_id"] = run.id
                row = self.models.activity(attempt_id=public["attempt_id"], owner_key=key,
                    session_id=context.session_id, run_id=context.run_id, created_at=self.clock(), projection=public)
                db.add(row)
                db.flush()
                self._bound_history(db, key, context.session_id)
            return ticket
        except DecisionError:
            raise
        except Exception:
            if required:
                raise DecisionError("decision_capture_store_unavailable") from None
            return None

    def after_attempt(self, ticket, *, status, usage=None, result=None, failure_kind=None):
        """Best effort after settlement: failure cannot affect the provider result."""
        if ticket is None:
            return
        try:
            public = copy.deepcopy(ticket["public"])
            public.update(status=status if status in STATUSES else "unresolved", usage=valid_usage(usage),
                questions=_answers(result, ticket["mapping"], ticket["offers"]),
                duration_ms=max(0, round((self.clock() - ticket["started"]) * 1000)),
                failure_kind=failure_kind if isinstance(failure_kind, str) and failure_kind in FAILURES else "decision_failed" if failure_kind is not None else None)
            public["usage_provenance"] = "provider_reported" if public["usage"] is not None else "unknown_held"
            with self._transaction(write=True) as db:
                key = self._admit(db, ticket["owner"], ticket["session_id"])
                self._expire(db)
                if ticket["capture_id"]:
                    run = db.get(self.models.capture, ticket["capture_id"])
                    if run is not None and run.owner_key == key:
                        public["capture_state"] = "complete" if status == "settled" else "outcome_recorded"
                        packet = _packet(public, ticket["offers"])
                        if len(encode(packet)) > MAX_RECORD_BYTES:
                            public["capture_state"] = "incomplete"
                        else:
                            run.captures = [packet if item["packet"]["attempt_id"] == ticket["attempt_id"] else item for item in run.captures]
                    else:
                        public["capture_state"] = "expired_or_deleted"
                row = db.query(self.models.activity).filter_by(attempt_id=ticket["attempt_id"], owner_key=key).first()
                if row is not None:
                    row.projection = public
        except Exception:
            # A pre-dispatch capture remains explicitly reserved/incomplete.
            return

    def _bound_history(self, db, key, session_id):
        model = self.models.activity
        for criteria, maximum in [({"owner_key": key, "session_id": session_id}, MAX_HISTORY), ({"owner_key": key}, MAX_OWNER_HISTORY)]:
            old = db.query(model.sequence).filter_by(**criteria).order_by(model.sequence.desc()).offset(maximum).all()
            if old:
                db.query(model).filter(model.sequence.in_([r[0] for r in old])).delete(synchronize_session=False)

    def history(self, owner, session_id, *, before=None, limit=50, run_id=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise HTTPException(422, "History limit must be between 1 and 100")
        sequence = None
        if before is not None:
            try:
                if not isinstance(before, str) or len(before) > 32:
                    raise ValueError()
                raw = base64.b64decode(before, altchars=b"-_", validate=True)
                sequence = int(raw)
                if sequence <= 0 or len(raw) > 20:
                    raise ValueError()
            except (ValueError, TypeError, UnicodeError):
                raise HTTPException(422, "Invalid history cursor") from None
        with self._transaction() as db:
            key = self._admit(db, owner, session_id)
            query = db.query(self.models.activity).filter_by(owner_key=key, session_id=session_id)
            if sequence is not None:
                query = query.filter(self.models.activity.sequence < sequence)
            if run_id is not None:
                query = query.filter_by(run_id=run_id)
            rows = query.order_by(self.models.activity.sequence.desc()).limit(limit + 1).all()
            items = [dict(copy.deepcopy(r.projection), created_at=r.created_at) for r in rows[:limit]]
            cursor = base64.urlsafe_b64encode(str(rows[limit - 1].sequence).encode()).decode() if len(rows) > limit else None
            return {"schema": "odysseus-decision-activity/v1", "items": items, "next_before": cursor}

    def capture(self, owner, session_id, run_id):
        with self._transaction(write=True) as db:
            key = self._admit(db, owner, session_id)
            self._expire(db)
            row = db.query(self.models.capture).filter_by(owner_key=key, session_id=session_id, run_id=run_id).first()
            if row is None:
                raise HTTPException(404, "Capture not found or expired")
            return {"schema": CAPTURE_SCHEMA, "exact_original_replay": False,
                "expires_at": row.expires_at, "records": copy.deepcopy(row.captures)}

    def delete_capture(self, owner, session_id, run_id=None):
        with self._transaction(write=True) as db:
            key = self._admit(db, owner, session_id)
            query = db.query(self.models.capture).filter_by(owner_key=key, session_id=session_id)
            if run_id is not None:
                query = query.filter_by(run_id=run_id)
            else:
                db.query(self.models.consent).filter_by(owner_key=key, session_id=session_id).delete(synchronize_session=False)
            query.delete(synchronize_session=False)
        with self._required_lock:
            for scope in list(self._required_runs):
                if scope[:2] == (key, session_id) and (run_id is None or scope[2] == run_id):
                    self._required_runs.pop(scope, None)
        return {"deleted": True}

    def record_handoff(self, owner, session_id, run_id, *, attempt_ids=(), action_ids=(), check_ids=(),
                       stop_reason="review_required", parent_run_id=None, artifact_ids=(), completion_verified=False):
        """Trusted runtime consumer only. This is an immutable observation."""
        from core.database import ChatAutomationSubmission, DecisionAttempt
        if stop_reason not in {"handoff", "review_required", "needs_input", "budget", "uncertainty", "cancelled", "completed"}:
            raise ValueError("Invalid handoff reason")
        if any(len(refs) > 64 for refs in (attempt_ids, action_ids, check_ids, artifact_ids)):
            raise ValueError("Too many handoff references")
        refs = {name: [_reference(value) for value in values] for name, values in
            [("attempt_ids", attempt_ids), ("action_ids", action_ids), ("check_ids", check_ids), ("artifact_ids", artifact_ids)]}
        with self._transaction(write=True) as db:
            key = self._admit(db, owner, session_id)
            run = db.query(ChatAutomationSubmission).filter_by(submission_id=run_id, owner_key=key, session_id=session_id).first()
            attempts = db.query(DecisionAttempt).filter_by(owner_key=key, session_id=session_id, run_id=run_id).all()
            if run is None and not attempts:
                raise ValueError("An actual owned run is required")
            if not set(attempt_ids).issubset({a.attempt_id for a in attempts}):
                raise ValueError("Attempt references do not belong to this run")
            # Actual action/check/artifact IDs must be supplied by the canonical
            # receipt owner, never through a public POST or a model response.
            if parent_run_id is not None:
                parent = db.query(ChatAutomationSubmission).filter_by(submission_id=parent_run_id, owner_key=key, session_id=session_id).first()
                if parent is None:
                    raise ValueError("An actual owned parent run is required")
            receipt = {"id": uuid.uuid4().hex, "run_id": _reference(run_id), "parent_run_id": parent_run_id,
                **refs, "stop_reason": stop_reason, "review_required": stop_reason != "completed",
                "completion_verified": completion_verified is True, "executable": False, "created_at": self.clock()}
            db.add(self.models.handoff(id=receipt["id"], owner_key=key, session_id=session_id, run_id=run_id,
                created_at=self.clock(), receipt=receipt))
            db.flush()
            for criteria, cap in [({"owner_key": key, "session_id": session_id}, 128), ({"owner_key": key}, 1024)]:
                old = db.query(self.models.handoff.id).filter_by(**criteria).order_by(
                    self.models.handoff.created_at.desc(), self.models.handoff.id.desc()).offset(cap).all()
                if old:
                    db.query(self.models.handoff).filter(self.models.handoff.id.in_([r[0] for r in old])).delete(synchronize_session=False)
            return copy.deepcopy(receipt)

    def handoffs(self, owner, session_id, *, limit=50):
        with self._transaction() as db:
            key = self._admit(db, owner, session_id)
            return [copy.deepcopy(row.receipt) for row in db.query(self.models.handoff).filter_by(owner_key=key,
                session_id=session_id).order_by(self.models.handoff.created_at.desc()).limit(min(100, max(1, limit))).all()]

    def purge_expired(self):
        with self._transaction(write=True) as db:
            self._expire(db)

    def delete_owner(self, owner):
        with self._transaction(write=True) as db:
            for model in vars(self.models).values():
                db.query(model).filter_by(owner_key=_owner_key(owner)).delete(synchronize_session=False)

    def delete_session(self, owner, session_id):
        with self._transaction(write=True) as db:
            key = self._admit(db, owner, session_id)
            for model in vars(self.models).values():
                db.query(model).filter_by(owner_key=key, session_id=session_id).delete(synchronize_session=False)


def rename_observability_owner(db, old_owner, new_owner):
    """Use within canonical account rename transaction; capture consent retires."""
    old, new = _owner_key(old_owner), _owner_key(new_owner)
    models = observation_models()
    existing = set(_existing_observation_models(db))
    for model in (models.activity, models.handoff):
        if model not in existing:
            continue
        db.query(model).filter_by(owner_key=old).update({"owner_key": new}, synchronize_session=False)
    for model in (models.consent, models.capture):
        if model not in existing:
            continue
        db.query(model).filter_by(owner_key=old).delete(synchronize_session=False)


def retire_observability_owner(db, owner):
    for model in _existing_observation_models(db):
        db.query(model).filter_by(owner_key=_owner_key(owner)).delete(synchronize_session=False)


def _existing_observation_models(db):
    from sqlalchemy import inspect
    # Legacy DBs without observations remain valid. Never create tables within
    # another owner's account/session transaction.
    inspector = inspect(db.connection(), raiseerr=False)
    if inspector is None:
        return []
    return [model for model in vars(observation_models()).values() if inspector.has_table(model.__tablename__)]


def delete_observability_session(db, session_id):
    """Canonical session deletion transaction, independent of FK enforcement."""
    for model in _existing_observation_models(db):
        db.query(model).filter_by(session_id=session_id).delete(synchronize_session=False)


_default = None
_default_lock = threading.Lock()


def default_observability():
    global _default
    with _default_lock:
        if _default is None:
            _default = DecisionObservability()
    return _default


def validate_capture(data):
    """Validate a bounded transformed fixture; no backend callback is accepted."""
    if len(data) > OWNER_CAPTURE_BYTES:
        raise ValueError("Capture exceeds offline size limit")
    envelope = decode(data)
    if (set(envelope) != {"schema", "exact_original_replay", "expires_at", "records"}
            or envelope.get("schema") != CAPTURE_SCHEMA or envelope.get("exact_original_replay") is not False
            or type(envelope.get("expires_at")) not in (int, float)):
        raise ValueError("Unsupported capture version or exact-replay claim")
    records = envelope.get("records")
    if not isinstance(records, list) or len(records) > MAX_CAPTURE_RECORDS:
        raise ValueError("Invalid capture record count")
    seen = set()
    for ordinal, entry in enumerate(records, 1):
        if not isinstance(entry, dict) or set(entry) != {"packet", "sanitized_sha256"}:
            raise ValueError("Invalid capture entry")
        packet = entry["packet"]
        if not isinstance(packet, dict):
            raise ValueError("Invalid capture packet")
        if set(packet) != {"schema", "source_kind", "redaction_policy", "exact_original_replay", "decision_schema",
                "implementation_sha256", "ordinal", "expected", "label_provenance", "omitted", "attempt_id", "run_id",
                "purpose", "fixture", "observation"}:
            raise ValueError("Unknown capture packet field")
        if len(encode(entry)) > MAX_RECORD_BYTES or hashlib.sha256(encode(packet)).hexdigest() != entry["sanitized_sha256"]:
            raise ValueError("Capture digest mismatch")
        if (packet.get("schema") != CAPTURE_SCHEMA or packet.get("redaction_policy") != REDACTION_POLICY
                or packet.get("source_kind") != "captured_live_redacted" or packet.get("exact_original_replay") is not False
                or packet.get("expected") is not None or packet.get("label_provenance") != "unscored_transformed_input"
                or packet.get("ordinal") != ordinal or packet.get("decision_schema") != SCHEMA_VERSION
                or not isinstance(packet.get("implementation_sha256"), str)
                or not _HASH.fullmatch(packet["implementation_sha256"])):
            raise ValueError("Unsupported capture provenance")
        attempt = _reference(packet.get("attempt_id"))
        if attempt in seen:
            raise ValueError("Duplicate captured attempt")
        seen.add(attempt)
        observation = packet.get("observation", {})
        if (not isinstance(observation, dict) or set(observation) - {"attempt_id", "run_id", "purpose", "status", "usage",
                "usage_provenance", "provider", "questions", "failure_kind", "duration_ms", "capture_state",
                "confidence_meaning", "policy_revision", "authority_epoch", "capture_sequence"}):
            raise ValueError("Unknown observation field")
        if observation.get("attempt_id") != attempt or observation.get("run_id") != packet.get("run_id") or observation.get("status") not in STATUSES:
            raise ValueError("Capture identity or status mismatch")
        if packet.get("purpose") not in PURPOSES | {"other"} or observation.get("purpose") != packet["purpose"]:
            raise ValueError("Invalid capture purpose")
        usage = observation.get("usage")
        if usage is not None and valid_usage(usage) != usage:
            raise ValueError("Invalid captured usage")
        fixture = packet.get("fixture")
        if not isinstance(fixture, dict) or set(fixture) != {"state", "questions"} or fixture["state"] != {"content_omitted": True}:
            raise ValueError("Invalid transformed fixture")
        questions = fixture["questions"]
        if not isinstance(questions, dict) or not 1 <= len(questions) <= 8:
            raise ValueError("Invalid fixture questions")
        for qid, choices in questions.items():
            if ((qid not in QUESTION_IDS and not re.fullmatch(r"q[0-9]{3}", qid)) or not isinstance(choices, list) or not 2 <= len(choices) <= 128
                    or choices != [f"c{i + 1:03d}" for i in range(len(choices))]):
                raise ValueError("Invalid opaque fixture choices")
        for qid, answer in observation.get("questions", {}).items():
            if not isinstance(answer, dict) or set(answer) != {"choice", "confidence", "probabilities"}:
                raise ValueError("Invalid captured answer")
            choices = questions.get(qid, [])
            distribution = answer.get("probabilities", {})
            if (answer.get("choice") not in choices or set(distribution) != set(choices)
                    or not probability(answer.get("confidence")) or not all(probability(v) for v in distribution.values())
                    or abs(sum(distribution.values()) - 1) > 1e-5):
                raise ValueError("Invalid captured distribution")
    return {"valid": True, "records": len(records), "source_kind": "captured_live_redacted",
        "exact_original_replay": False, "scored": False, "provider_calls": 0, "action_calls": 0}
