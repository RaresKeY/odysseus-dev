"""Reproductions for confirmed fork baseline findings."""
import json
import stat

import pytest
from fastapi import HTTPException
from src.chat_helpers import extract_urls
from services.youtube.youtube_handler import is_youtube_url
from src import caldav_sync, secret_storage, vault_storage
from routes._validators import validate_remote_host


@pytest.mark.parametrize('url', [
    'https://notyoutube.com/watch?v=x', 'https://youtube.com.evil.invalid/watch?v=x',
    'https://example.invalid/youtube.com/watch?v=x', 'https://youtube.com@example.invalid/watch?v=x',
    'javascript://youtube.com/watch?v=x', 'https://[broken/youtube.com',
])
def test_youtube_lookalikes_do_not_divert_generic_url_processing(url):
    assert not is_youtube_url(url)


@pytest.mark.parametrize('host', ['youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be'])
def test_real_youtube_hosts_still_route(host):
    assert is_youtube_url('https://' + host + '/watch?v=x')


def test_google_caldav_rewrite_requires_domain_boundary():
    assert caldav_sync._google_caldav_events_url('https://evilgoogleusercontent.com/caldav/v2/a/user') is None
    assert caldav_sync._google_caldav_events_url('https://apidata.googleusercontent.com/caldav/v2/a/user').endswith('/events')


@pytest.mark.parametrize('value', ['server\n', 'user@server\n', 'server;id', '-oProxyCommand=id'])
def test_remote_host_rejects_trailing_newline_and_shell_syntax(value):
    with pytest.raises(HTTPException):
        validate_remote_host(value)


@pytest.mark.parametrize('tail,expected', [
    ('.' * 100_000 + 'x', '.' * 100_000 + 'x'),
    (')' * 100_000 + '.', ''), ('(balanced)).!?', '(balanced)'), ('(balanced).', '(balanced)'),
])
def test_url_cleanup_preserves_semantics_with_long_adversarial_suffixes(tail, expected):
    assert extract_urls('see https://example.invalid/' + tail) == ['https://example.invalid/' + expected]


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(secret_storage, '_KEY_PATH', tmp_path / '.app_key')
    monkeypatch.setattr(secret_storage, '_fernet', None)
    return tmp_path / 'vault.json'


def test_vault_round_trip_encrypts_disk_without_mutating_runtime_config(vault):
    cfg = {'owner': 'alice', 'session': 'synthetic-vault-session', 'server_url': 'https://vault.invalid'}
    vault_storage.save_vault_config(vault, cfg)
    assert 'synthetic-vault-session' not in vault.read_text()
    assert json.loads(vault.read_text())['session'].startswith('enc:')
    assert stat.S_IMODE(vault.stat().st_mode) == 0o600
    assert vault_storage.load_vault_config(vault) == cfg
    assert cfg['session'] == 'synthetic-vault-session'


def test_legacy_plaintext_can_be_read_and_is_encrypted_on_next_save(vault):
    vault.write_text(json.dumps({'session': 'legacy-synthetic', 'owner': 'alice'}))
    cfg = vault_storage.load_vault_config(vault)
    assert cfg['session'] == 'legacy-synthetic'
    vault_storage.save_vault_config(vault, cfg)
    assert 'legacy-synthetic' not in vault.read_text()
    assert vault_storage.load_vault_config(vault) == cfg


@pytest.mark.asyncio
async def test_tool_unlock_and_route_reader_share_encrypted_session(vault, monkeypatch):
    from src.tools import vault as tools
    from routes.vault import vault_routes as routes
    monkeypatch.setattr(tools, 'VAULT_FILE', str(vault))
    monkeypatch.setattr(routes, 'VAULT_FILE', vault)
    async def unlock(args, **kwargs):
        assert args == ['unlock', '--raw']
        assert kwargs['input_text'] == 'synthetic-master\n'
        return 'synthetic-session', '', 0
    monkeypatch.setattr(tools, '_run_bw', unlock)
    assert (await tools.do_vault_unlock(json.dumps({'master_password': 'synthetic-master'}), owner='alice'))['exit_code'] == 0
    assert 'synthetic-session' not in vault.read_text()
    assert routes._load_config()['session'] == tools._load_vault_config()['session'] == 'synthetic-session'
