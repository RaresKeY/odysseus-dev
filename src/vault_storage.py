"""Shared encrypted-at-rest vault session storage for routes and tools."""
from pathlib import Path
import json

from core.atomic_io import atomic_write_json
from core.platform_compat import safe_chmod
from src.secret_storage import encrypt, decrypt


def load_vault_config(path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            return {}
        if 'session' in data:
            value = data['session']
            data['session'] = decrypt(value) if isinstance(value, str) else ''
        return data
    except (OSError, ValueError):
        return {}


def save_vault_config(path, config: dict) -> None:
    # Encrypt a copy before serialization so neither a temporary file nor the
    # final store contains the session, and callers keep their runtime value.
    stored = dict(config)
    if 'session' in stored:
        if not isinstance(stored['session'], str):
            raise ValueError('Invalid vault session')
        stored['session'] = encrypt(stored['session'])
    atomic_write_json(str(path), stored, indent=2)
    safe_chmod(str(path), 0o600)
