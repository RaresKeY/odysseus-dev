"""Acceptance runner must reject misleading evidence and protect login material."""
import io
import json
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from scripts.fork_live_tests import (CheckFailure, Client, Suite, load_login,
                                    local_origin, read_events, verify_turn)
from scripts.fork_regression_inventory import inventory
from tests.test_chat_automation_state import store, configured_reviewer


@pytest.mark.parametrize('origin', [
    'https://example.com', 'http://127.0.0.1.example.com', 'http://127.0.0.1@other.invalid',
    'http://localhost/private', 'http://localhost?key=secret', 'http://localhost#secret',
    'http://192.168.1.2', 'file:///tmp/socket', 'http://user:secret@localhost',
])
def test_client_rejects_nonlocal_or_credential_bearing_origins(origin):
    with pytest.raises((CheckFailure, ValueError)):
        Client(origin)


@pytest.mark.parametrize('origin', ['http://127.0.0.1:7824', 'http://[::1]:7824', 'http://localhost'])
def test_client_accepts_explicit_loopback_target(origin):
    assert local_origin(origin)[2] == origin


def test_login_permissions_and_restricted_fields(tmp_path):
    p = tmp_path / 'login.json'
    p.write_text(json.dumps({'username': 'synthetic', 'password': 'SYNTHETIC', 'extra': 'not forwarded'}))
    p.chmod(0o644)
    with pytest.raises(CheckFailure, match='private_regular'):
        load_login(p)
    p.chmod(0o600)
    assert load_login(p) == {'username': 'synthetic', 'password': 'SYNTHETIC'}
    alias = tmp_path / 'alias'
    alias.symlink_to(p)
    with pytest.raises(CheckFailure, match='private_regular'):
        load_login(alias)


@pytest.fixture
def http_app():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, dict(self.headers)))
            if self.path == '/api/redirect':
                self.send_response(302)
                self.send_header('Location', '/api/leak')
            elif self.path == '/api/fail':
                self.send_response(500)
            else:
                self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"detail":"SENSITIVE_SERVER_BODY"}')
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Client('http://127.0.0.1:' + str(server.server_port)), requests
    server.shutdown()
    server.server_close()
    thread.join()


def test_client_never_follows_redirects_or_exposes_error_bodies(http_app):
    client, requests = http_app
    client.cookies['session'] = 'SYNTHETIC_COOKIE'
    for path, code in [('/api/redirect', '302'), ('/api/fail', '500')]:
        with pytest.raises(CheckFailure) as failed:
            client.request(path)
        assert str(failed.value) == 'unexpected_http_' + code
        assert 'SENSITIVE' not in str(failed.value)
    assert [r[0] for r in requests] == ['/api/redirect', '/api/fail']
    assert requests[0][1]['Origin'] == client.origin


def test_stream_requires_done_and_rejects_oversized_lines():
    with pytest.raises(CheckFailure, match='incomplete'):
        read_events(io.BytesIO(b'data: {"delta":"hello"}\n'))
    with pytest.raises(CheckFailure, match='too_large'):
        read_events(io.BytesIO(b'data: ' + b'x' * 65536 + b'\n'))
    assert read_events(io.BytesIO(b': ping\n\ndata: {"delta":"hello"}\n\ndata: [DONE]\n')) == [{'delta': 'hello'}]


def greeting():
    return [{'delta': 'synthetic-token'}, {'type': 'metrics', 'data': {'model': 'm', 'round_models': ['m']}}]


def test_greeting_oracle_uses_the_app_round_models_contract():
    assert verify_turn(greeting(), 'synthetic-token', 'm')['agent_rounds'] == 1
    mixed = greeting()
    mixed[-1]['data']['round_models'] = ['different-model']
    with pytest.raises(CheckFailure, match='model_mismatch'):
        verify_turn(mixed, 'synthetic-token', 'm')
    endpoint = greeting()
    endpoint[-1]['data'].update(endpoint_id='actual', round_endpoint_ids=['actual'])
    assert verify_turn(endpoint, 'synthetic-token', 'm', endpoint_id='actual')['agent_rounds'] == 1
    with pytest.raises(CheckFailure, match='endpoint_mismatch'):
        verify_turn(endpoint, 'synthetic-token', 'm', endpoint_id='different')


@pytest.mark.parametrize('extra,reason', [
    ({'type': 'error'}, 'error_or_block'),
    ({'type': 'intent_nudge_exhausted'}, 'error_or_block'),
    ({'type': 'tool_start', 'tool': 'write_file'}, 'used_tools'),
    ({'type': 'metrics', 'data': {'model': 'wrong', 'round_models': ['wrong']}}, 'model_mismatch'),
    ({'type': 'metrics', 'data': {'model': 'm', 'round_models': ['m', 'm', 'm']}}, 'repeated_rounds'),
])
def test_greeting_oracle_rejects_guards_tools_repetition_and_wrong_model(extra, reason):
    with pytest.raises(CheckFailure, match=reason):
        verify_turn(greeting() + [extra], 'synthetic-token', 'm')


def test_assistant_claim_does_not_prove_native_file_execution():
    with pytest.raises(CheckFailure, match='extra_file_actions|native_file_receipt'):
        verify_turn(greeting(), 'synthetic-token', 'm', file_cycle=True)


def test_native_file_oracle_requires_exact_successful_execution_receipt():
    events = greeting() + [{'type': 'tool_start', 'tool': 'bash'},
        {'type': 'tool_output', 'tool': 'bash', 'execution_attempted': True,
         'exit_code': 0, 'output': 'synthetic-token'}]
    assert verify_turn(events, 'synthetic-token', 'm', file_cycle=True)['tool_outputs'] == 1
    for field, bad in [('execution_attempted', False), ('exit_code', 1), ('output', ''), ('blocked', True)]:
        corrupt = [dict(e) for e in events]
        corrupt[-1][field] = bad
        with pytest.raises(CheckFailure):
            verify_turn(corrupt, 'synthetic-token', 'm', file_cycle=True)


def test_owned_session_cleanup_on_failure():
    calls = []
    class Fake:
        def request(self, path, value=None, **kwargs):
            calls.append((path, kwargs.get('method')))
            if path == '/api/workspace/default':
                return {'ok': True, 'path': '/synthetic-workspace'}
            if path == '/api/session':
                assert value['cwd'] == '/synthetic-workspace'
            return {'id': 'owned-synthetic-id'}
    suite = Suite(Fake(), {}, SimpleNamespace(endpoint_id='e', model='m'))
    with pytest.raises(RuntimeError):
        with suite.session():
            raise RuntimeError('deliberate failure')
    assert calls[-1] == ('/api/session/owned-synthetic-id', 'DELETE')


def test_manual_probe_forks_zero_history_without_changing_source_or_owner_defaults():
    calls = []
    source = {'route_mode': 'manual', 'approval_mode': 'always_ask'}
    class Fake:
        def request(self, path, value=None, **options):
            calls.append((path, value, options))
            if path == '/api/workspace/default': return {'ok': True, 'path': '/synthetic-workspace'}
            if path == '/api/session/template/automation': return dict(source)
            if path == '/api/session/template/context_info': return {'model': 'm'}
            if path.endswith('/fork'):
                assert value == {'keep_count': 0}
                return {'id': 'owned-child'}
            if options.get('method') == 'PATCH':
                assert set(value) == {'name', 'cwd'}
                return {}
            return {'route_mode': 'manual'}
    suite = Suite(Fake(), {}, SimpleNamespace(endpoint_id='e', model='m', manual_template='template'))
    with suite.session(manual=True) as sid:
        assert sid == 'owned-child'
    assert all(options.get('method') != 'PUT' for _, _, options in calls)
    assert any(path == '/api/session/owned-child' and options.get('method') == 'DELETE'
               for path, _, options in calls)
    assert source == {'route_mode': 'manual', 'approval_mode': 'always_ask'}


def test_failed_readiness_still_logs_out_and_wrong_source_never_authenticates():
    class Fake:
        def __init__(self, source):
            self.source, self.cookies, self.paths = source, {}, []
        def request(self, path, value=None, **options):
            self.paths.append(path)
            if path == '/api/version': return {'source_commit': self.source}
            if path == '/api/health': return {'status': 'healthy'}
            if path == '/api/auth/login': self.cookies['session'] = True
            if path == '/api/auth/logout': self.cookies.clear()
            if path == '/api/ready': raise CheckFailure('readiness_failed')
            return {}
    args = SimpleNamespace(expected_source='expected', suite='read')
    wrong = Fake('wrong')
    Suite(wrong, {}, args).run()
    assert '/api/auth/login' not in wrong.paths
    failing = Fake('expected')
    Suite(failing, {}, args).run()
    assert '/api/auth/logout' in failing.paths


def test_report_does_not_include_unexpected_exception_text(capsys):
    suite = Suite(None, {}, None)
    def failing():
        raise RuntimeError('SENSITIVE_PROVIDER_BODY')
    assert not suite.check('synthetic', failing)
    assert 'SENSITIVE' not in capsys.readouterr().out
    assert suite.results[0]['reason'] == 'RuntimeError'


def test_failed_provider_identity_preflight_blocks_native_actions():
    class Harness(Suite):
        def check(self, case_id, operation):
            self.called.append(case_id)
            return case_id != 'CH-01'
    suite = Harness(SimpleNamespace(cookies={}), {}, SimpleNamespace(suite='native'))
    suite.called = []
    suite.run()
    assert 'FS-01' not in suite.called
    assert suite.results == [{'id': 'FS-01', 'status': 'blocked',
                              'reason': 'provider_greeting_preflight_failed', 'seconds': 0}]


def test_inventory_reports_pytest_definitions_and_browser_sources_without_importing(tmp_path):
    root = tmp_path / 'tests'
    root.mkdir()
    (root / 'test_example.py').write_text('raise RuntimeError("must not import")\nclass TestCases:\n def test_async(self): pass\ndef test_simple(): pass\n')
    (root / 'widget.test.mjs').write_text('throw Error("must not execute");')
    (root / 'layout.spec.cjs').write_text('throw Error("must not execute");')
    (root / 'unnamedScenario.mjs').write_text('throw Error("must not execute");')
    (root / 'fixture.json').write_text('{}')
    (root / '__pycache__').mkdir()
    (root / '__pycache__' / 'ignored.pyc').write_bytes(b'ignored')
    result = inventory(tmp_path, nodes=True)
    assert result['counts'] == {'pytest': 1, 'javascript-source': 3, 'fixture-or-support': 1}
    assert result['python_definitions'] == 2
    row = next(f for f in result['files'] if f['kind'] == 'pytest')
    assert row['nodes'][0].endswith('::TestCases::test_async')


def test_cas_probe_changes_policy_when_retained_mode_already_matches_new_default(store, configured_reviewer):
    """Exercise the probe against real SQL CAS, including idempotent writes."""
    from fastapi import HTTPException
    from core import database as d
    store.update_chat('alice', 'a', {'approval_mode': 'auto_approve'}, 0)
    before = store.configuration('alice')
    class Adapter:
        def request(self, path, value=None, *, method=None, expected=200, **_options):
            status, result = 200, None
            try:
                if path == '/api/chat-settings': result = store.configuration('alice')
                elif path == '/api/workspace/default': result = {'ok': True, 'path': '/synthetic-workspace'}
                elif path == '/api/session':
                    with store.transaction() as db:
                        db.add(d.Session(id='probe', name='probe', owner='alice', model='manual', endpoint_url='http://local/v1'))
                        db.flush()
                        store.state(db, 'alice', 'probe', new=True)
                    result = {'id': 'probe'}
                elif method == 'DELETE':
                    with store.transaction() as db:
                        db.query(d.ChatAutomationState).filter_by(session_id='probe').delete()
                        db.query(d.Session).filter_by(id='probe').delete()
                    result = {}
                elif method == 'PUT':
                    result = store.update_chat('alice', 'probe', value['changes'], value['policy_revision'])
                else: result = store.policy('alice', 'probe')
            except HTTPException as exc:
                status = exc.status_code
            if status != expected:
                raise CheckFailure('unexpected_http_' + str(status))
            return result
    suite = Suite(Adapter(), {}, SimpleNamespace(endpoint_id='local', model='manual'))
    assert suite.mode_cas()['stale_write_status'] == 409
    assert store.configuration('alice') == before
