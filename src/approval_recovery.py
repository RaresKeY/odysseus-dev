"""Interactive evidence renewal; never an execution grant or transcript summary."""
from __future__ import annotations

import json
import math
import os
import uuid
from types import SimpleNamespace

from fastapi import HTTPException
from src.decisions.contracts import DecisionError

_PROCESS = (os.getpid(), uuid.uuid4().hex)


def process_id():
    global _PROCESS
    if _PROCESS[0] != os.getpid():
        _PROCESS = (os.getpid(), uuid.uuid4().hex)
    return _PROCESS[1]
CHECKPOINT_KEY = '_approval_checkpoint'
PROCESS_KEY = '_approval_private_process'


def private_runtime():
    """Private evidence has one process owner, not shared-memory durability."""
    import multiprocessing
    multiple = False
    for name in ('WEB_CONCURRENCY', 'UVICORN_WORKERS'):
        value = os.getenv(name, '1')
        try:
            multiple |= int(value) > 1
        except ValueError:
            multiple = True
    # Uvicorn's --workers spawn path need not set WEB_CONCURRENCY.
    multiple |= multiprocessing.parent_process() is not None
    multiple |= 'gunicorn' in os.getenv('SERVER_SOFTWARE', '').casefold()
    return {'supported': not multiple, 'process_id': process_id(),
            'code': 'private_approval_multiple_workers' if multiple else None,
            'reason': 'Private Auto-Approve needs one application process. Use Always Ask or a single-worker deployment.' if multiple else
                      'Private review evidence stays in this application process; idle expiry, eviction or restart requires renewal.'}


def check_private_admission(state, private):
    """Before supersession, grants or inference; a different worker cannot adopt."""
    features = dict(state.feature_settings or {})
    if state.approval_mode == 'auto_approve' and (private or features.get(PROCESS_KEY)):
        runtime = private_runtime()
        if not runtime['supported']:
            raise HTTPException(409, runtime['reason'], headers={'X-Decision-Failure': runtime['code']})
        if features.get(PROCESS_KEY) not in (None, process_id()):
            raise HTTPException(409, 'This private chat belongs to another application process. Renew review context in Chat settings, or use Always Ask.',
                                headers={'X-Decision-Failure': 'approval_private_process_changed'})


def private_admission(state, private):
    check_private_admission(state, private)
    features = dict(state.feature_settings or {})
    if private and PROCESS_KEY not in features:
        state.feature_settings = {**features, PROCESS_KEY: process_id()}


def context_status(store, owner, session_id):
    from src.chat_automation import _models, owner_key
    from src.approval_context import user_snapshot
    d = _models()
    with store.transaction() as db:
        _, state = store.state(db, owner, session_id)
        session = db.get(d.Session, str(session_id))
        row = db.query(d.ChatAutomationSubmission).filter_by(owner_key=owner_key(owner), session_id=str(session_id)).order_by(
            d.ChatAutomationSubmission.created_at.desc(), d.ChatAutomationSubmission.submission_id.desc()).first()
        # Legacy chats may have later ordinary turns after a private one and
        # no process marker. Metadata alone must keep renewal private by default.
        private_rows = db.query(d.ChatAutomationSubmission.submission_id).filter_by(
            owner_key=owner_key(owner), session_id=str(session_id)).filter(d.ChatAutomationSubmission.current_ask.is_(None))
        checkpoint = (state.feature_settings or {}).get(CHECKPOINT_KEY)
        if (isinstance(checkpoint, dict) and type(checkpoint.get('created_at')) in (int, float)
                and math.isfinite(checkpoint['created_at'])):
            private_rows = private_rows.filter(d.ChatAutomationSubmission.created_at >= checkpoint['created_at'])
        private = (str(session.name or '').strip().casefold() in {'incognito', 'nobody'}
                   or bool((state.feature_settings or {}).get(PROCESS_KEY)) or private_rows.first() is not None)
        private_owner = (state.feature_settings or {}).get(PROCESS_KEY)
        result = {'policy_revision': state.policy_revision, 'previous_submission_id': row.submission_id if row else None,
                  'private': private, 'process_id': process_id(), 'available': True, 'code': None,
                  'checkpoint_present': bool((state.feature_settings or {}).get(CHECKPOINT_KEY))}
    runtime = private_runtime()
    if private and not runtime['supported']:
        result.update(available=False, code=runtime['code'])
    elif private and private_owner not in (None, process_id()):
        result.update(available=False, code='approval_private_process_changed')
    elif row:
        # No raw private ask is read from SQL or returned by this metadata API.
        bound = SimpleNamespace(store=store, owner=owner, session_id=str(session_id), submission_id=result['previous_submission_id'],
                                ephemeral_semantics=False, raw_ask='', approval_evidence=[])
        try:
            snapshot = json.loads(user_snapshot(bound))
            if snapshot.get('private_evidence'):
                result['private'] = True
        except DecisionError as error:
            result.update(available=False, code=error.code)
            # Unknown/corrupt context cannot justify a saved-content default.
            if error.code in {'approval_context_unavailable', 'approval_private_context_unavailable', 'approval_private_process_changed'}:
                result['private'] = True
    if result['private'] and not runtime['supported']:
        result.update(available=False, code=runtime['code'])
    result['runtime'] = runtime
    result['reason'] = {
        'approval_sensitive_input': 'Review context contains credential-like material. Renew it without secret values, keeping the task and restrictions.',
        'approval_context_checkpoint_required': 'The full review context is too large. Restate the current task, restrictions and revoked permissions below.',
        'approval_private_context_unavailable': 'Earlier private instructions are no longer in this process. Restate them below; they cannot be recovered automatically.',
        'approval_private_process_changed': 'This private chat belongs to another process. Renew it here or use Always Ask.',
        'private_approval_multiple_workers': runtime['reason'],
    }.get(result['code'], 'The review context is incomplete. Explicitly restate the current task, restrictions and revoked permissions.' if not result['available'] else
          'Earlier user instructions remain applicable until you explicitly replace them below.')
    return result


def replace_context(store, owner, session_id, body):
    """Only the interactive-owner route may call this mutation."""
    from src.chat_automation import _models, owner_key, digest
    from src.approval_context import sensitive, retain_private_submission, forget_private_evidence
    expected = {'policy_revision', 'previous_submission_id', 'process_id', 'statement', 'replace_prior_context', 'private'}
    if (not isinstance(body, dict) or set(body) != expected or body.get('replace_prior_context') is not True
            or type(body.get('private')) is not bool or type(body.get('policy_revision')) is not int
            or body.get('process_id') != process_id()):
        raise HTTPException(409, 'Reload review context and explicitly confirm replacement.')
    statement = body['statement']
    try:
        valid_statement = isinstance(statement, str) and bool(statement.strip()) and len(statement.encode('utf-8')) <= 8000
    except UnicodeError:
        valid_statement = False
    if not valid_statement:
        raise HTTPException(422, 'Enter the task, restrictions and revocations in at most 8000 UTF-8 bytes.')
    if sensitive(statement):
        raise HTTPException(422, 'Leave secret values out of the replacement; preserve the task, effects and restrictions.')
    d = _models()
    with store.transaction() as db:
        prefs, state = store.state(db, owner, session_id)
        session = db.get(d.Session, str(session_id))
        previous = db.query(d.ChatAutomationSubmission).filter_by(owner_key=owner_key(owner), session_id=str(session_id)).order_by(
            d.ChatAutomationSubmission.created_at.desc(), d.ChatAutomationSubmission.submission_id.desc()).first()
        if (state.policy_revision != body['policy_revision'] or body['previous_submission_id'] != (previous.submission_id if previous else None)):
            raise HTTPException(409, 'The chat changed. Reload before replacing review context.')
        private = body['private'] or str(session.name or '').strip().casefold() in {'incognito', 'nobody'}
        if private and state.approval_mode == 'auto_approve' and not private_runtime()['supported']:
            raise HTTPException(409, private_runtime()['reason'])
        now = store.clock()
        if previous:
            now = max(now, math.nextafter(previous.created_at, math.inf))
        if not math.isfinite(now):
            raise HTTPException(409, 'Submission ordering is unavailable')
        identifier = uuid.uuid4().hex
        db.query(d.ChatAutomationSubmission).filter(d.ChatAutomationSubmission.owner_key == owner_key(owner),
            d.ChatAutomationSubmission.session_id == str(session_id), d.ChatAutomationSubmission.status.in_(
                ['preparing', 'bound', 'running', 'awaiting_input', 'awaiting_approval'])).update({'status': 'cancelled'}, synchronize_session=False)
        db.query(d.ChatAutomationLease).filter_by(owner_key=owner_key(owner), session_id=str(session_id)).delete(synchronize_session=False)
        db.query(d.ChatAutomationInputCard).filter_by(owner_key=owner_key(owner), session_id=str(session_id), status='pending').update(
            {'status': 'superseded'}, synchronize_session=False)
        state.policy_revision += 1
        state.elevation_grant = None
        marker = {'submission_id': identifier, 'intent_digest': digest(statement), 'private': private,
                  'created_at': now, 'source': 'interactive_user_checkpoint'}
        features = {key: value for key, value in (state.feature_settings or {}).items() if key not in {CHECKPOINT_KEY, PROCESS_KEY}}
        state.feature_settings = {**features, CHECKPOINT_KEY: marker, **({PROCESS_KEY: process_id()} if private else {})}
        db.add(d.ChatAutomationSubmission(submission_id=identifier, owner_key=owner_key(owner), session_id=str(session_id),
            policy_revision=state.policy_revision, authority_epoch=prefs.authority_epoch,
            intent_digest=digest(statement), descriptor={}, status='completed', created_at=now,
            current_ask=None if private else statement, previous_submission_id=previous.submission_id if previous else None,
            task_lineage_id=identifier, original_submission_id=identifier))
        forget_private_evidence(owner, session_id)
        if private:
            retain_private_submission(SimpleNamespace(store=store, owner=owner, session_id=str(session_id), submission_id=identifier,
                                                     raw_ask=statement))
    from src.tool_approvals import tool_approval_store
    tool_approval_store.retire_for_session(owner=owner, session_id=session_id)
    return context_status(store, owner, session_id)
