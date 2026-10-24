"""Canonical owner model restrictions shared by execution and decision calls."""
from __future__ import annotations
import json
from pathlib import Path

_AUTH_MANAGER = None


def configure_auth_manager(manager):
    global _AUTH_MANAGER
    _AUTH_MANAGER = manager


def owner_privileges(owner):
    from src.owner_identity import DEFAULT_LOCAL_OWNER
    if not owner or owner == DEFAULT_LOCAL_OWNER:
        return None
    if _AUTH_MANAGER is not None:
        users = getattr(_AUTH_MANAGER, 'users', None)
        if isinstance(users, dict) and str(owner).strip().lower() not in users:
            return {'block_all_models': True, '_account_available': False}
        privileges = _AUTH_MANAGER.get_privileges(str(owner).strip().lower()) or {}
    else:
        # Standalone worker roles may not own the app's in-memory manager. Read
        # its canonical store without constructing a manager or running migrations.
        from src.constants import AUTH_FILE
        path = Path(AUTH_FILE)
        try:
            if not path.exists():
                return None  # canonical unconfigured/first-run deployment
            if path.stat().st_size > 2_000_000:
                return {'block_all_models': True, '_account_available': False}
            users = json.loads(path.read_text()).get('users', {})
            user = next((v for k, v in users.items() if k.strip().lower() == str(owner).strip().lower()), None)
            if not isinstance(user, dict):
                return {'block_all_models': True, '_account_available': False}
            if user.get('is_admin'):
                return {'is_admin': True}
            privileges = user.get('privileges', {})
        except (OSError, ValueError, TypeError, AttributeError):
            return {'block_all_models': True, '_account_available': False}
    return privileges


def owner_policy_fingerprint(owner):
    import hashlib
    return hashlib.sha256(json.dumps(owner_privileges(owner), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def allowed_models_for_owner(owner):
    privileges = owner_privileges(owner)
    if privileges is None or privileges.get('is_admin'):
        return None
    if privileges.get('block_all_models'):
        return frozenset()
    allowed = privileges.get('allowed_models', [])
    if not isinstance(allowed, list):
        return frozenset()
    if privileges.get('allowed_models_restricted') or allowed:
        return frozenset(m for m in allowed if isinstance(m, str))
    return None


def require_owner_model(owner, model):
    allowed = allowed_models_for_owner(owner)
    if allowed is not None and model not in allowed:
        raise ValueError('This model is not allowed for this owner')
