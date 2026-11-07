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
    return [{'delta': 'synthetic-token'}, {'type': 'metrics', 'data': {'model': 'm', 'agent_rounds': 1}}]


@pytest.mark.parametrize('extra,reason', [
    ({'type': 'error'}, 'error_or_block'),
    ({'type': 'intent_nudge_exhausted'}, 'error_or_block'),
    ({'type': 'tool_start', 'tool': 'write_file'}, 'used_tools'),
    ({'type': 'metrics', 'data': {'model': 'wrong', 'agent_rounds': 1}}, 'model_mismatch'),
    ({'type': 'metrics', 'data': {'model': 'm', 'agent_rounds': 3}}, 'repeated_rounds'),
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


def test_inventory_reports_pytest_definitions_and_browser_sources_without_importing(tmp_path):
    root = tmp_path / 'tests'
    root.mkdir()
    (root / 'test_example.py').write_text('raise RuntimeError("must not import")\nclass TestCases:\n def test_async(self): pass\ndef test_simple(): pass\n')
    (root / 'widget.test.mjs').write_text('throw Error("must not execute");')
    (root / 'layout.spec.cjs').write_text('throw Error("must not execute");')
    result = inventory(tmp_path, nodes=True)
    assert result['counts'] == {'pytest': 1, 'javascript-browser': 2}
    assert result['python_definitions'] == 2
    row = next(f for f in result['files'] if f['kind'] == 'pytest')
    assert row['nodes'][0].endswith('::TestCases::test_async')
