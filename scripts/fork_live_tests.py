#!/usr/bin/env python3
"""Bounded acceptance checks against an explicitly selected local Odysseus app.

Only the application receives provider credentials. Reports contain synthetic
identifiers/counts, never response bodies, chat text, cookies or login secrets.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import http.client
from http.cookies import SimpleCookie
import ipaddress
import json
from pathlib import Path
import socket
import stat
import time
import urllib.parse
import uuid

MAX_BYTES = 2_000_000
MAX_EVENTS = 4096


class CheckFailure(Exception):
    """A fixed diagnostic code, with no provider/application body attached."""

    def __init__(self, code, evidence=None):
        super().__init__(code)
        self.evidence = evidence or {}


def require(condition, code):
    if not condition:
        raise CheckFailure(code)


def local_origin(value):
    parsed = urllib.parse.urlsplit(value)
    require(parsed.scheme == 'http' and parsed.hostname is not None
            and not parsed.username and not parsed.password
            and parsed.path in ('', '/') and not parsed.query and not parsed.fragment,
            'invalid_local_origin')
    try:
        local = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        local = parsed.hostname == 'localhost'
    require(local, 'origin_must_be_loopback')
    # Validate the port now, before loading any credential.
    port = parsed.port or 80
    return parsed.hostname, port, value.rstrip('/')


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, host, port, socket_path, timeout):
        super().__init__(host, port, timeout=timeout)
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


class Client:
    def __init__(self, origin, socket_path=None, timeout=120):
        self.host, self.port, self.origin = local_origin(origin)
        self.socket_path, self.timeout = socket_path, timeout
        self.cookies = SimpleCookie()

    @contextmanager
    def response(self, path, value=None, *, method=None, form=False, expected=200):
        require(path.startswith('/api/') and '\r' not in path and '\n' not in path,
                'invalid_api_path')
        headers = {'Origin': self.origin, 'Sec-Fetch-Site': 'same-origin'}
        if self.cookies:
            headers['Cookie'] = '; '.join(m.OutputString() for m in self.cookies.values())
        body = None
        if value is not None:
            body = (urllib.parse.urlencode(value) if form else json.dumps(value)).encode()
            headers['Content-Type'] = ('application/x-www-form-urlencoded' if form
                                       else 'application/json')
        conn = (UnixConnection(self.host, self.port, self.socket_path, self.timeout)
                if self.socket_path else http.client.HTTPConnection(
                    self.host, self.port, timeout=self.timeout))
        try:
            # http.client does not follow redirects or use ambient proxies.
            conn.request(method or ('POST' if body is not None else 'GET'), path, body, headers)
            response = conn.getresponse()
            require(response.status == expected, 'unexpected_http_' + str(response.status))
            for name, value in response.getheaders():
                if name.lower() == 'set-cookie':
                    incoming = SimpleCookie(value)
                    for key, morsel in incoming.items():
                        if morsel.value:
                            self.cookies[key] = morsel.value
                        elif key in self.cookies:
                            del self.cookies[key]
            yield response
        finally:
            conn.close()

    def request(self, path, value=None, **options):
        with self.response(path, value, **options) as response:
            raw = response.read(MAX_BYTES + 1)
            require(len(raw) <= MAX_BYTES, 'response_too_large')
            return json.loads(raw) if raw else None


def load_login(path):
    p = Path(path)
    require(not p.is_symlink() and stat.S_ISREG(p.stat().st_mode)
            and p.stat().st_mode & 0o077 == 0, 'login_file_must_be_private_regular_file')
    require(p.stat().st_size <= 16384, 'login_file_too_large')
    data = json.loads(p.read_text())
    require(all(isinstance(data.get(k), str) and data[k] for k in ('username', 'password')),
            'invalid_login_file')
    return {k: data[k] for k in ('username', 'password')}


def read_events(response):
    events, total, done = [], 0, False
    deadline = time.monotonic() + 180
    for _ in range(MAX_EVENTS):
        require(time.monotonic() < deadline, 'stream_deadline_exceeded')
        line = response.readline(65537)
        if not line:
            break
        total += len(line)
        require(len(line) <= 65536 and total <= MAX_BYTES, 'stream_too_large')
        if not line.startswith(b'data: '):
            continue
        raw = line[6:].strip()
        if raw == b'[DONE]':
            done = True
            break
        event = json.loads(raw)
        require(isinstance(event, dict), 'invalid_stream_event')
        events.append(event)
    if not done:
        statuses = set()
        for event in events:
            value = event.get('status')
            if type(value) is int and 400 <= value <= 599:
                statuses.add(value)
            for match in __import__('re').finditer(r'HTTP[ :]+([45][0-9]{2})', str(event.get('error', ''))):
                statuses.add(int(match.group(1)))
        raise CheckFailure('stream_incomplete', {'event_count': len(events), 'http_statuses': sorted(statuses)})
    return events


def verify_turn(events, marker, model, *, file_cycle=False, endpoint_id=None):
    errors = [e for e in events if e.get('type') in {
        'error', 'intent_nudge_exhausted', 'approval_required', 'tool_approval_required'}
        or e.get('error') or e.get('blocked') or e.get('failed')]
    require(not errors, 'turn_reported_error_or_block')
    finals = [e for e in events if e.get('type') == 'final_response']
    text = ''.join(str(e.get('delta', '')) for e in events)
    text += ''.join(str(e.get('response') or e.get('content') or '') for e in finals)
    require(marker in text, 'expected_reply_missing')
    require('[Agent guard:' not in text, 'agent_guard_fired')
    metrics = [e.get('data', e) for e in events if e.get('type') == 'metrics']
    reported = [m.get('model') for m in metrics if m.get('model')]
    reported += [item for m in metrics for item in m.get('round_models', [])]
    require(reported and all(m == model for m in reported), 'actual_model_mismatch')
    if endpoint_id:
        endpoints = [m.get('endpoint_id') for m in metrics if m.get('endpoint_id')]
        endpoints += [item for m in metrics for item in m.get('round_endpoint_ids', [])]
        require(endpoints and all(item == endpoint_id for item in endpoints), 'actual_endpoint_mismatch')
    evidence = {'event_count': len(events), 'actual_model': model,
                'tool_outputs': sum(e.get('type') == 'tool_output' for e in events)}
    rounds = [len(m['round_models']) for m in metrics if isinstance(m.get('round_models'), list)]
    if rounds:
        evidence['agent_rounds'] = rounds[-1]
    if not file_cycle:
        require(not evidence['tool_outputs'] and not any(e.get('type') == 'tool_start' for e in events),
                'greeting_used_tools')
        require(rounds, 'greeting_round_count_missing')
        require(all(r == 1 for r in rounds), 'greeting_repeated_rounds')
    else:
        outputs = [e for e in events if e.get('type') == 'tool_output']
        require(len(outputs) == 1 and sum(e.get('type') == 'tool_start' for e in events) == 1,
                'unexpected_extra_file_actions')
        require(any(e.get('tool') == 'bash' and e.get('execution_attempted') is True
                    and not e.get('blocked') and e.get('exit_code') == 0
                    and e.get('output', '').strip() == marker for e in outputs),
                'missing_successful_native_file_receipt')
    return evidence


class Suite:
    def __init__(self, client, login, args):
        self.client, self.login, self.args = client, login, args
        self.marker = 'fork-regression-' + uuid.uuid4().hex[:16]
        self.results = []
        self.case_evidence = {}

    def check(self, case_id, operation):
        start = time.monotonic()
        self.case_evidence = {}
        try:
            evidence = operation() or {}
            result = {'id': case_id, 'status': 'passed', 'evidence': evidence}
        except CheckFailure as exc:
            result = {'id': case_id, 'status': 'failed', 'reason': str(exc), 'evidence': exc.evidence}
        except Exception as exc:
            # Exception strings can contain server bodies or credentials.
            result = {'id': case_id, 'status': 'failed', 'reason': type(exc).__name__}
        if self.case_evidence:
            result['evidence'] = {**self.case_evidence, **result.get('evidence', {})}
        result['seconds'] = round(time.monotonic() - start, 3)
        self.results.append(result)
        print(json.dumps(result), flush=True)
        return result['status'] == 'passed'

    def blocked(self, case_id, reason):
        result = {'id': case_id, 'status': 'blocked', 'reason': reason, 'seconds': 0}
        self.results.append(result)
        print(json.dumps(result), flush=True)

    def authenticate(self):
        self.client.request('/api/auth/login', self.login)

    def runtime(self):
        version = self.client.request('/api/version')
        require(version.get('source_commit') == self.args.expected_source, 'wrong_deployed_source')
        require(self.client.request('/api/health').get('status') == 'healthy', 'health_not_healthy')
        self.authenticate()
        self.client.request('/api/ready')
        return {'source_commit': version['source_commit']}

    def anonymous_denied(self):
        other = Client(self.client.origin, self.client.socket_path, self.client.timeout)
        other.request('/api/sessions', expected=401)
        return {'anonymous_session_list_status': 401}

    def registrations(self):
        endpoints = self.client.request('/api/model-endpoints')
        require(isinstance(endpoints, list) and endpoints, 'no_registered_endpoints')
        require(all('api_key' not in e for e in endpoints), 'credential_in_endpoint_response')
        choices = self.client.request('/api/chat-settings/models')
        require(choices.get('endpoints'), 'empty_model_picker')
        return {'endpoint_count': len(endpoints), 'picker_endpoints': len(choices['endpoints'])}

    @contextmanager
    def session(self, *, manual=False):
        workspace = self.client.request('/api/workspace/default')
        require(workspace.get('ok') and isinstance(workspace.get('path'), str)
                and workspace['path'], 'default_workspace_unavailable')
        template = getattr(self.args, 'manual_template', None) if manual else None
        source_state = None
        if template:
            source_path = '/api/session/' + urllib.parse.quote(template, safe='')
            source_state = self.client.request(source_path + '/automation')
            require(source_state['route_mode'] == 'manual', 'template_must_be_manual')
            require(self.client.request(source_path + '/context_info').get('model') == self.args.model,
                    'template_model_mismatch')
            obj = self.client.request(source_path + '/fork', {'keep_count': 0})
        else:
            obj = self.client.request('/api/session', {
                'name': self.marker, 'endpoint_id': self.args.endpoint_id,
                'model': self.args.model, 'skip_validation': 'true',
                'cwd': workspace['path']}, form=True)
        sid = obj['id']
        try:
            if template:
                self.client.request('/api/session/' + sid,
                    {'name': self.marker, 'cwd': workspace['path']}, method='PATCH', form=True)
            if manual:
                state = self.client.request('/api/session/' + sid + '/automation')
                require(state['route_mode'] == 'manual', 'auto_routing_requires_manual_template')
                require(not any(state.get(k) for k in ('followup_enabled', 'preparation_enabled',
                    'completion_advice_enabled')), 'template_has_extra_model_calls_enabled')
            yield sid
        finally:
            # Never delete a pre-existing session or an id supplied by a model.
            self.client.request('/api/session/' + sid, method='DELETE')
            if template:
                require(self.client.request(source_path + '/automation') == source_state,
                        'source_template_changed')

    def new_default(self):
        before = self.client.request('/api/chat-settings')
        with self.session() as sid:
            state = self.client.request('/api/session/' + sid + '/automation')
            require(state['approval_mode'] == 'auto_approve', 'new_chat_not_auto_approve')
            require(not state.get('elevation_grant'), 'new_chat_inherited_folder_grant')
            require(state['policy_revision'] == 0, 'new_chat_revision_not_zero')
            self.client.request('/api/auth/logout', {})
            self.authenticate()
            again = self.client.request('/api/session/' + sid + '/automation')
            require(again['approval_mode'] == 'auto_approve', 'default_not_durable')
        after = self.client.request('/api/chat-settings')
        require(before == after, 'new_chat_changed_owner_configuration')
        return {'default': 'auto_approve', 'checked_after_new_login': True}

    def mode_cas(self):
        # Choose the already-retained explicit mode so the check cannot
        # overwrite owner preferences, including another chat's live choice.
        before = self.client.request('/api/chat-settings')
        old = before['last_explicit_mode']
        with self.session() as sid:
            path = '/api/session/' + sid + '/automation'
            state = self.client.request(path)
            saved = self.client.request(path, {'policy_revision': state['policy_revision'],
                'changes': {'approval_mode': old,
                            'followup_enabled': not state.get('followup_enabled', False)}}, method='PUT')
            require(saved['approval_mode'] == old, 'explicit_mode_not_saved')
            self.client.request(path, {'policy_revision': state['policy_revision'],
                'changes': {'approval_mode': 'yolo'}}, method='PUT', expected=409)
            require(self.client.request(path)['approval_mode'] == old, 'stale_write_changed_mode')
        require(before == self.client.request('/api/chat-settings'), 'owner_configuration_changed')
        return {'stale_write_status': 409, 'owner_configuration_unchanged': True}

    def model_context(self):
        with self.session() as sid:
            context = self.client.request('/api/session/' + sid + '/context_info')
            require(context.get('model') == self.args.model, 'context_model_mismatch')
            require(isinstance(context.get('context_length'), int) and context['context_length'] > 0,
                    'context_limit_missing')
            if self.args.expected_context:
                require(context['context_length'] == self.args.expected_context, 'context_limit_mismatch')
            require(context.get('workspace', {}).get('exists_in_backend'), 'workspace_not_available')
            return {'model': context['model'], 'context_length': context['context_length'],
                    'workspace_exists': True}

    def notes(self):
        note = self.client.request('/api/notes', {'title': self.marker, 'content': self.marker})
        path = '/api/notes/' + note['id']
        try:
            require(self.client.request(path).get('content') == self.marker, 'note_read_mismatch')
            edited = self.marker + '-edited'
            self.client.request(path, {'title': self.marker, 'content': edited}, method='PUT')
            require(self.client.request(path).get('content') == edited, 'note_edit_mismatch')
        finally:
            self.client.request(path, method='DELETE')
        self.client.request(path, expected=404)
        return {'create_read_update_delete': True}

    def documents(self):
        doc = self.client.request('/api/document', {'title': self.marker, 'content': self.marker})
        path = '/api/document/' + doc['id']
        try:
            require(doc.get('current_content') == self.marker, 'document_create_mismatch')
            edited = self.client.request(path, {'content': self.marker + '-v2', 'force_version': True}, method='PUT')
            require(edited.get('version_count') == 2, 'document_version_missing')
            versions = self.client.request(path + '/versions')
            require({v['version_number']: v['content'] for v in versions}.get(1) == self.marker,
                    'document_history_mismatch')
            self.client.request(path + '/restore/1', {})
            require(self.client.request(path).get('current_content') == self.marker, 'document_restore_mismatch')
        finally:
            self.client.request(path, method='DELETE')
        return {'version_restore_and_cleanup': True}

    def catalog(self):
        path = '/api/model-endpoints/' + self.args.endpoint_id + '/models?refresh=true&refresh_timeout=10'
        with self.client.response(path) as response:
            require(response.getheader('X-Model-Refresh-Status') == 'refreshed', 'catalog_refresh_failed')
            data = response.read(MAX_BYTES + 1)
            require(len(data) <= MAX_BYTES, 'response_too_large')
            models = json.loads(data)
        require(any(m.get('id') == self.args.model and not m.get('is_hidden') for m in models),
                'selected_model_not_available')
        return {'model_count': len(models), 'selected_model_available': True}

    def turn(self, file_cycle=False):
        marker = self.marker + ('-file' if file_cycle else '-hello')
        if file_cycle:
            name = marker + '.txt'
            command = "printf '%s' '" + marker + "' > " + name + ' && cat -- ' + name + ' && rm -- ' + name + ' && test ! -e ' + name
            prompt = ('In the current workspace execute this exact bash command once, then reply with the printed token. '
                      'Do not change any other files or use any other tool: ' + command)
        else:
            prompt = 'Hello. Reply with exactly ' + marker + '. Do not call any tools.'
        owner_before = self.client.request('/api/chat-settings')
        with self.session(manual=True) as sid:
            with self.client.response('/api/chat_stream', {'session': sid, 'message': prompt,
                    'mode': 'agent', 'allow_bash': 'true', 'allow_web_search': 'false',
                    'use_web': 'false', 'use_rag': 'false', 'selected_endpoint_id': self.args.endpoint_id,
                    'selected_model': self.args.model},
                    form=True) as response:
                events = read_events(response)
            self.case_evidence = {
                'event_types': dict(Counter(e.get('type', 'delta') for e in events)),
                'round_models': [e.get('data', e).get('round_models', [])
                                 for e in events if e.get('type') == 'metrics'],
                'tools': [e.get('tool') for e in events if e.get('type') == 'tool_start']}
            evidence = verify_turn(events, marker, self.args.model, file_cycle=file_cycle,
                                   endpoint_id=self.args.endpoint_id)
            if file_cycle:
                require(any(e.get('type') == 'tool_start' and e.get('tool') == 'bash'
                            and e.get('command') == command for e in events), 'native_command_mismatch')
            history = self.client.request('/api/history/' + sid).get('history', [])
            require(any(m.get('role') == 'assistant' and marker in str(m.get('content', ''))
                        for m in history), 'assistant_reply_not_persisted')
        require(self.client.request('/api/chat-settings') == owner_before, 'owner_configuration_changed')
        return evidence

    def run(self):
        try:
            if not self.check('RT-01', self.runtime):
                return
            self.check('AU-01', self.anonymous_denied)
            self.check('MD-01', self.registrations)
            if self.args.suite in ('state', 'crud', 'provider', 'native'):
                self.check('AP-01', self.new_default)
                self.check('AP-02', self.mode_cas)
                self.check('CX-01', self.model_context)
            if self.args.suite == 'crud':
                self.check('NT-01', self.notes)
                self.check('DC-01', self.documents)
            if self.args.suite in ('provider', 'native'):
                catalogue_ok = self.check('MD-02', self.catalog)
                greeting_ok = catalogue_ok and self.check('CH-01', lambda: self.turn(False))
                if not catalogue_ok:
                    self.blocked('CH-01', 'catalogue_preflight_failed')
                if self.args.suite == 'native':
                    if greeting_ok:
                        self.check('FS-01', lambda: self.turn(True))
                    else:
                        self.blocked('FS-01', 'provider_greeting_preflight_failed')
        finally:
            if self.client.cookies:
                self.check('AU-02', self.logout)

    def logout(self):
        self.client.request('/api/auth/logout', {})
        self.client.request('/api/sessions', expected=401)
        return {'logged_out_requests_rejected': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', default='http://127.0.0.1:7824')
    parser.add_argument('--socket', help='Protected app Unix socket; no TCP listener needed')
    parser.add_argument('--login-file', required=True)
    parser.add_argument('--expected-source', required=True)
    parser.add_argument('--suite', choices=['read', 'state', 'crud', 'provider', 'native'], default='read')
    parser.add_argument('--endpoint-id')
    parser.add_argument('--model')
    parser.add_argument('--manual-template', help='Owned disposable Manual chat to fork with zero history when owner defaults use Auto')
    parser.add_argument('--expected-context', type=int)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.suite != 'read' and (not args.endpoint_id or not args.model):
        parser.error('This suite requires --endpoint-id and --model')
    client = Client(args.origin, args.socket)
    suite = Suite(client, load_login(args.login_file), args)
    suite.run()
    report = {'schema': 'odysseus-fork-live-results/v1', 'time_utc': datetime.now(timezone.utc).isoformat(),
              'suite': args.suite, 'expected_source': args.expected_source, 'results': suite.results,
              'status': 'passed' if suite.results and all(r['status'] == 'passed' for r in suite.results) else 'failed'}
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    args.output.chmod(0o600)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
