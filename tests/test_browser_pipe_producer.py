"""Offline contracts: real pipe reader/producer with a hostile fake transport.

These tests do not claim Chromium/isolated-world qualification. They exercise
the Python protocol, resource binding and dispatch boundary without inference.
"""
import asyncio
import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import struct
import sys
import time
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src import browser_identity
from src import browser_pipe as pipe
from src.agent_runtime.authority import ExactOperation, OperationGrant, RequestAuthority, bind_request_authority
from src.agent_runtime.resources import BrowserPageResource, BrowserSessionResource
from src.process_lifecycle import ProcessIdentity

TARGET, SESSION, FRAME = 'A' * 32, 'B' * 32, 'C' * 32


def packet(nonce):
    return {'nonce': nonce, 'url': 'https://example.com/form', 'title': 'Fixture', 'text': 'Fixture content',
            'elements': [dict(ref='n0', label='Next', role='a', editable=False, disabled=False, destination='https://example.com/next', form_action='', form_method='', submits_form=False, in_viewport=True),
                         dict(ref='n1', label='Email', role='input', editable=True, disabled=False, destination='', form_action='', form_method='', submits_form=False, in_viewport=True),
                         dict(ref='n2', label='Delete', role='button', editable=False, disabled=False, destination='', form_action='', form_method='', submits_form=False, in_viewport=True)],
            'total_elements': 3, 'truncated': False, 'ready': 'complete'}


class FakeChannel:
    """Implements actual NUL transport and records every wire request."""
    def __init__(self):
        self.identity = ProcessIdentity(43210, 'fixture-start', 43210)
        self.stdout = asyncio.StreamReader()
        self.stdin = self
        self.requests = []
        self.closed = False
        self.drop = None
        self.before_response = None
        self.next_action = {'executed': True}
        self.last_packet = None
        self.wrong_session = False
        self.traverse_on_click = False
        self.traversed = False
        self.context_counter = 6

    def feed(self, message):
        self.stdout.feed_data(json.dumps(message).encode() + b'\0')

    def event(self, method, params=None, session=SESSION):
        self.feed({'method': method, 'params': params or {}, 'sessionId': session})

    def write(self, raw):
        request = json.loads(raw[:-1])
        self.requests.append(request)
        method, args = request['method'], request['params']
        if method == self.drop:
            return
        result = {}
        if method == 'Browser.getVersion': result = {'product': 'Chrome/140.0.7339.16'}
        elif method == 'Target.createTarget':
            self.event('Target.attachedToTarget', {'targetInfo': {'targetId': TARGET, 'type': 'page'}, 'sessionId': SESSION, 'waitingForDebugger': True}, session=None)
            result = {'targetId': TARGET}
        elif method == 'Page.getFrameTree': result = {'frameTree': {'frame': {'id': FRAME, 'loaderId': 'loader-1', 'url': 'https://example.com/form'}}}
        elif method == 'Page.createIsolatedWorld':
            self.context_counter += 1
            self.event('Runtime.executionContextCreated', {'context': {'id': 7, 'uniqueId': 'fixture-context-' + str(self.context_counter), 'name': 'odysseus-observed-v1', 'auxData': {'frameId': FRAME}}})
            result = {'executionContextId': 7}
        elif method == 'Runtime.evaluate': result = {'result': {'objectId': 'captured-object-' + str(self.context_counter)}}
        elif method == 'Runtime.callFunctionOn':
            if request['params']['functionDeclaration'] == pipe._CALL_SNAPSHOT:
                self.last_packet = packet(args['arguments'][0]['value'])
                if self.traversed:
                    self.last_packet.update(url='https://example.com/next', text='Reached second document', title='Next fixture')
                result = {'result': {'value': self.last_packet}}
            else:
                result = {'result': {'value': self.next_action}}
                if self.traverse_on_click and args['arguments'][0]['value']['action'] == 'click':
                    self.traversed = True
                    self.event('Page.frameNavigated', {'frame': {'id': FRAME, 'loaderId': 'loader-2', 'url': 'https://example.com/next'}})
        elif method == 'Page.captureScreenshot':
            result = {'data': base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'\0' * 8 + struct.pack('>II', 640, 480)).decode()}
        if self.before_response:
            self.before_response(request)
        message = {'id': request['id'], 'result': result}
        if 'sessionId' in request:
            message['sessionId'] = ('D' * 32) if self.wrong_session else request['sessionId']
        self.feed(message)

    async def drain(self):
        await asyncio.sleep(0)

    async def close(self):
        self.closed = True
        self.stdout.feed_eof()
        return SimpleNamespace(dead=True)


class PipeProducerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.turn = SimpleNamespace(owner='alice', session_id='thread', run_id='run', current=lambda: True)
        try:
            import src.chat_automation as automation_module
            self.turn_patch = patch.object(automation_module, 'current_turn', return_value=self.turn)
        except ImportError:
            self.turn_patch = patch.dict(sys.modules, {'src.chat_automation': SimpleNamespace(current_turn=lambda: self.turn)})
        self.turn_patch.start()
        self.owned = patch.object(ProcessIdentity, 'owned', return_value=True)
        self.owned.start()
        self.channel = FakeChannel()
        self.manifest = pipe.ProducerManifest('/usr/lib/chromium/chromium', 'a' * 64,
            hashlib.sha256(pipe.OBSERVER.read_bytes()).hexdigest(), 'x86_64', 'b' * 64)
        self.validation = patch.object(pipe.ProducerManifest, 'validate', return_value=None)
        self.validation.start()
        self.record = pipe.PipeBrowserRecord('alice', 'thread', self.manifest, self.channel, object())
        await self.record.start()
        pipe.REGISTRY[('alice', 'thread')] = self.record
        self.grant = pipe.BrowserTaskGrant('alice', 'thread', 'run', frozenset({'https://example.com'}),
            frozenset({'click', 'fill'}), {'Email': 'private@example.test'}, time.time() + 60,
            frozenset({'https://example.com/form'}))
        self.record.task_grant = self.grant

    async def asyncTearDown(self):
        pipe.REGISTRY.clear()
        await self.record.close()
        self.validation.stop()
        self.owned.stop()
        self.turn_patch.stop()

    async def bind(self, args):
        if self.record.current is None:
            await self.record.observe()
        operation = ExactOperation.normalize('private_browser', json.dumps(args))
        authority = RequestAuthority('request', 'alice', 'thread', '', (OperationGrant('private_browser'),),
            browser_sessions=(self.record.session,), browser_pages=self.record.pages)
        return authority, browser_identity.resolve_browser_operation(authority, operation)

    async def test_enrolled_namespace_resources_and_unique_context_roundtrip(self):
        observed = await self.record.observe()
        resource = self.record.pages[0]
        self.assertEqual(resource.context_unique_id, 'fixture-context-7')
        self.assertEqual(BrowserPageResource.from_dict(resource.to_dict()), resource)
        self.assertEqual(BrowserSessionResource.from_dict(resource.session.to_dict()), resource.session)
        serialized = json.dumps(resource.session.to_dict())
        self.assertIn(pipe.NAMESPACE, serialized)
        self.assertNotIn('browser_instance_digest', serialized)
        self.assertEqual(observed['page'], 't1')
        self.assertNotIn('captured-object-7', json.dumps(observed))
        evaluate = next(item for item in self.channel.requests if item['method'] == 'Runtime.evaluate')
        self.assertEqual(evaluate['params']['uniqueContextId'], 'fixture-context-7')
        self.assertNotIn('contextId', evaluate['params'])

    async def test_page_scope_requires_explicit_current_run_grant(self):
        await self.record.observe()
        authority = RequestAuthority('request', 'alice', 'thread', '', (OperationGrant('private_browser'),))
        sessions, pages = browser_identity.seal_browser_resources(authority)
        self.assertEqual(pages[0].scope, 'page')
        self.record.task_grant = None
        sessions, pages = browser_identity.seal_browser_resources(authority)
        self.assertEqual(pages[0].scope, 'document')
        self.record.task_grant = replace(self.grant, run_id='different')
        sessions, pages = browser_identity.seal_browser_resources(authority)
        self.assertEqual(pages[0].scope, 'document')

    async def test_action_uses_retained_object_and_prepared_field_only(self):
        observation = await self.record.observe()
        args = dict(action='fill', page='t1', observation=observation['observation'], ref='n1', value_id='Email')
        authority, bound = await self.bind(args)
        with bind_request_authority(authority), browser_identity.bind_browser_operation(bound):
            output = await browser_identity.execute_browser(json.dumps(args), {'owner': 'alice', 'session_id': 'thread'})
        self.assertIs(output['executed'], True)
        action = self.channel.requests[-1]
        self.assertEqual(action['method'], 'Runtime.callFunctionOn')
        self.assertEqual(action['params']['objectId'], 'captured-object-7')
        self.assertEqual(action['params']['uniqueContextId'], 'fixture-context-7')
        self.assertNotIn('executionContextId', action['params'])
        self.assertEqual(action['params']['arguments'][0]['value']['text'], 'private@example.test')
        self.assertIsNone(self.record.current)
        self.assertNotIn('private@example.test', json.dumps(output))

    async def test_no_ambient_or_other_owner_dispatch(self):
        observation = await self.record.observe()
        args = dict(action='click', page='t1', observation=observation['observation'], ref='n0')
        authority, bound = await self.bind(args)
        before = len(self.channel.requests)
        output = await browser_identity.execute_browser(json.dumps(args), {'owner': 'alice', 'session_id': 'thread'})
        self.assertFalse(output['executed'])
        with bind_request_authority(authority), browser_identity.bind_browser_operation(bound):
            output = await browser_identity.execute_browser(json.dumps(args), {'owner': 'mallory', 'session_id': 'thread'})
        self.assertFalse(output['executed'])
        self.assertEqual(len(self.channel.requests), before)

    async def test_navigation_event_during_capture_discards_observation(self):
        self.channel.before_response = lambda request: self.channel.event('Page.navigatedWithinDocument', {'frameId': FRAME, 'url': 'https://example.com/form'}) if request['method'] == 'Runtime.callFunctionOn' else None
        with self.assertRaisesRegex(pipe.BrowserPipeError, 'browser_observation_stale'):
            await self.record.observe()
        self.assertIsNone(self.record.current)

    async def test_same_url_or_context_replacement_rejects_bound_action_without_io(self):
        for event, params in [('Page.navigatedWithinDocument', {'frameId': FRAME, 'url': 'https://example.com/form'}),
                              ('Runtime.executionContextDestroyed', {'executionContextId': 7}),
                              ('Page.lifecycleEvent', {'frameId': FRAME, 'name': 'backForwardCacheRestore'})]:
            observed = await self.record.observe()
            args = dict(action='click', page='t1', observation=observed['observation'], ref='n0')
            _authority, bound = await self.bind(args)
            self.record._event({'method': event, 'params': params, 'sessionId': SESSION})
            before = len(self.channel.requests)
            with self.assertRaises(pipe.BrowserPipeError):
                await self.record.execute_bound(bound, args)
            self.assertEqual(len(self.channel.requests), before)

    async def test_request_origin_method_and_unknown_target_filter(self):
        await self.record.observe()
        with self.assertRaisesRegex(pipe.BrowserPipeError, 'browser_effect_denied'):
            self.record.permitted_node('click', 'n2', replace(self.grant, allowed_effects=frozenset()))
        self.channel.event('Fetch.requestPaused', {'requestId': 'unsafe', 'request': {'url': 'https://evil.example/post', 'method': 'POST'}})
        self.channel.event('Fetch.requestPaused', {'requestId': 'safe', 'request': {'url': 'https://example.com/picture', 'method': 'GET'}})
        await asyncio.sleep(.01)
        controls = [(r['method'], r['params']['requestId']) for r in self.channel.requests if r['method'].startswith('Fetch.') and r['method'] != 'Fetch.enable']
        self.assertEqual(controls, [('Fetch.failRequest', 'unsafe'), ('Fetch.continueRequest', 'safe')])
        self.assertEqual(next(r for r in self.channel.requests if r['method'] == 'Fetch.enable')['params']['patterns'], [{'urlPattern': '*', 'requestStage': 'Request'}])

    async def test_admitted_link_never_implies_click_handler_permission(self):
        observed = await self.record.observe()
        readonly = replace(self.grant, allowed_effects=frozenset())
        with self.assertRaisesRegex(pipe.BrowserPipeError, 'browser_effect_denied'):
            self.record.permitted_node('click', 'n0', readonly)
        _authority, bound = await self.bind({'action': 'click', 'page': 't1', 'observation': observed['observation'], 'ref': 'n0'})
        before = len(self.channel.requests)
        with self.assertRaisesRegex(pipe.BrowserPipeError, 'browser_effect_denied'):
            await self.record.execute_bound(bound, json.loads(bound.operation.input), readonly)
        self.assertEqual(len(self.channel.requests), before)

    async def test_queued_network_continue_rechecks_scope_inside_wire_lock(self):
        self.record.task_grant=self.grant
        self.record.network_policy_validator=lambda:True
        await self.record.cdp.write_lock.acquire()
        self.channel.event('Fetch.requestPaused', {'requestId':'queued', 'request':{'url':'https://example.com/image','method':'GET'}})
        await asyncio.sleep(.01)
        self.record.network_policy_validator=lambda:False
        self.record.cdp.write_lock.release()
        await asyncio.sleep(.01)
        controls=[r['method'] for r in self.channel.requests if r['params'].get('requestId')=='queued']
        self.assertEqual(controls,['Fetch.failRequest'])

    async def test_public_observation_preserves_nondefault_origin_port(self):
        await self.record.observe()
        self.record.current.packet['url']='https://example.com:8443/path?private=query'
        self.assertEqual(self.record.public_observation()['url'],'https://example.com:8443/path')

    async def test_constructor_failure_closes_the_already_owned_channel(self):
        from src import containment
        channel=FakeChannel()
        async def factory(grant,command):return channel
        with tempfile.TemporaryDirectory() as directory, patch.object(containment,'acquire',return_value=SimpleNamespace(id='owned')):
            with patch.object(pipe,'PipeBrowserRecord',side_effect=pipe.BrowserPipeError('browser_producer_changed')):
                with self.assertRaises(pipe.BrowserPipeError):
                    await pipe.enrol('constructor-owner','constructor-thread',self.manifest,directory,channel_factory=factory)
        self.assertTrue(channel.closed)

    async def test_popups_and_workers_never_resume_an_unobserved_target(self):
        before = len(self.channel.requests)
        self.channel.event('Target.attachedToTarget', {'targetInfo': {'targetId': 'D' * 32, 'type': 'page'}, 'sessionId': 'E' * 32, 'waitingForDebugger': True}, session=None)
        await asyncio.sleep(.01)
        sent = self.channel.requests[before:]
        self.assertEqual([(r['method'], r['params']) for r in sent], [('Target.closeTarget', {'targetId': 'D' * 32})])
        self.assertFalse(any(r['method'] == 'Runtime.runIfWaitingForDebugger' for r in sent))
        self.assertEqual(self.record.target, TARGET)

    async def test_protocol_duplicate_keys_close_owned_channel(self):
        self.channel.stdout.feed_data(b'{"method":"Page.frameNavigated","method":"Runtime.executionContextsCleared"}\0')
        await asyncio.sleep(.01)
        self.assertFalse(self.record.live)
        self.assertTrue(self.channel.closed)

    async def test_effect_timeout_revokes_no_retry_or_retarget(self):
        self.channel.drop = 'Page.navigate'
        with self.assertRaises(pipe.BrowserPipeError) as failure:
            await self.record.cdp.call('Page.navigate', {'url': 'https://example.com/form'}, session=SESSION, effect=True, timeout=.01)
        self.assertTrue(failure.exception.indeterminate)
        self.assertIsNone(failure.exception.result()['executed'])
        self.assertFalse(self.record.live)
        self.assertEqual(sum(r['method'] == 'Page.navigate' for r in self.channel.requests), 1)
        with self.assertRaises(pipe.BrowserPipeError):
            await self.record.observe()
        self.assertEqual(sum(r['method'] == 'Target.createTarget' for r in self.channel.requests), 1)

    async def test_mode_revision_while_waiting_for_wire_lock_sends_no_effect(self):
        observation = await self.record.observe()
        args = dict(action='click', page='t1', observation=observation['observation'], ref='n0')
        _authority, bound = await self.bind(args)
        await self.record.cdp.write_lock.acquire()
        pending = asyncio.create_task(self.record.execute_bound(bound, args))
        await asyncio.sleep(0)
        before = len(self.channel.requests)
        self.turn.current = lambda: False
        self.record.cdp.write_lock.release()
        with self.assertRaisesRegex(pipe.BrowserPipeError, 'browser_policy_stale'):
            await pending
        self.assertEqual(len(self.channel.requests), before)

    async def test_wrong_session_response_revokes_channel(self):
        self.channel.wrong_session = True
        with self.assertRaises(pipe.BrowserPipeError):
            await self.record.observe()
        self.assertFalse(self.record.live)

    async def test_screenshot_is_actual_payload_with_truthful_consistency(self):
        output = await self.record.screenshot()
        self.assertEqual(output['screenshot']['width'], 640)
        self.assertEqual(output['screenshot']['height'], 480)
        self.assertTrue(output['screenshot']['target_consistency_checked'])
        self.assertFalse(output['screenshot']['atomic_document_capture'])
        self.channel.before_response = lambda request: self.channel.event('Page.navigatedWithinDocument', {'frameId': FRAME}) if request['method'] == 'Page.captureScreenshot' else None
        with self.assertRaisesRegex(pipe.BrowserPipeError, 'browser_screenshot_stale'):
            await self.record.screenshot()

    async def test_new_origin_grant_cannot_read_an_existing_human_page(self):
        self.record.task_grant = replace(self.grant, allowed_origins=frozenset({'https://other.example'}), navigation_urls=frozenset())
        args = {'action': 'snapshot', 'page': 't1'}
        _authority, bound = await self.bind(args)
        before = len(self.channel.requests)
        with self.assertRaisesRegex(pipe.BrowserPipeError, 'browser_origin_denied'):
            await self.record.execute_bound(bound, args)
        self.assertEqual(len(self.channel.requests), before)

    async def test_concurrent_enrolment_publishes_one_owned_channel_and_close_waits(self):
        from src import containment
        entered, release = asyncio.Event(), asyncio.Event()
        channels = []
        async def factory(grant, command):
            channel = FakeChannel()
            channels.append(channel)
            entered.set()
            await release.wait()
            return channel
        with tempfile.TemporaryDirectory() as directory, patch.object(containment, 'acquire', return_value=object()):
            first = asyncio.create_task(pipe.enrol('new-owner', 'new-thread', self.manifest, directory, channel_factory=factory))
            await entered.wait()
            second = asyncio.create_task(pipe.enrol('new-owner', 'new-thread', self.manifest, directory, channel_factory=factory))
            closing = asyncio.create_task(pipe.close('new-owner', 'new-thread'))
            await asyncio.sleep(0)
            self.assertEqual(len(channels), 1)
            self.assertFalse(closing.done())
            release.set()
            admitted = await first
            with self.assertRaisesRegex(pipe.BrowserPipeError, 'browser_enrolment_denied'):
                await second
            outcome = await closing
            self.assertTrue(outcome.dead)
            self.assertTrue(channels[0].closed)
            self.assertIsNone(pipe.get('new-owner', 'new-thread'))
            self.assertEqual(len(channels), 1)

    async def test_human_input_revokes_old_document_capability(self):
        observed = await self.record.observe()
        old = self.record.pages[0]
        await self.record.human_action(lambda record: asyncio.sleep(0))
        with self.assertRaises(pipe.BrowserPipeError):
            old.validate()
        with self.assertRaisesRegex(pipe.BrowserPipeError, 'stale_observation'):
            self.record._check_observation(observed['observation'])


class GrantTests(unittest.TestCase):
    def test_completion_checks_are_bounded_immutable_and_origin_scoped(self):
        checks = [{'kind': 'text_contains', 'value': 'Saved'}]
        grant = pipe.BrowserTaskGrant('alice', 'thread', 'run', frozenset({'https://example.com'}),
            expires_at=time.time() + 60, completion_checks=checks)
        checks[0]['value'] = 'changed'
        self.assertEqual(grant.completion_checks[0]['value'], 'Saved')
        with self.assertRaises(TypeError):
            grant.completion_checks[0]['value'] = 'changed'
        with self.assertRaises(ValueError):
            replace(grant, completion_checks=({'kind': 'url_equals', 'value': 'https://evil.example'},))

    def test_prepared_values_are_immutable_and_owner_expiry_are_enforced(self):
        values = {'Email': 'secret'}
        grant = pipe.BrowserTaskGrant('alice', 'thread', 'run', frozenset({'https://example.com'}),
            prepared_values=values, expires_at=time.time() + 60)
        values['Email'] = 'changed'
        self.assertEqual(grant.prepared_values['Email'], 'secret')
        with self.assertRaises(TypeError): grant.prepared_values['Email'] = 'changed'
        with self.assertRaises(pipe.BrowserPipeError): grant.check('mallory', 'thread')
        with self.assertRaises(pipe.BrowserPipeError): replace(grant, expires_at=1).check('alice', 'thread')

    def test_no_credential_or_private_navigation(self):
        for url in ['http://example.com', 'https://user:password@example.com', 'https://127.0.0.1', 'https://[::1]', 'https://localhost', 'file:///etc/passwd']:
            with self.subTest(url=url), self.assertRaises(ValueError): pipe.origin(url)
        self.assertEqual(pipe.origin('https://example.com/path?query=private'), 'https://example.com')
        self.assertEqual(pipe.origin('https://example.com/path#section'), 'https://example.com')

    def test_grant_expiry_must_be_finite(self):
        for expiry in (float('nan'), float('inf'), True, 'later'):
            with self.subTest(expiry=expiry), self.assertRaises(ValueError):
                pipe.BrowserTaskGrant('alice', 'thread', 'run', frozenset({'https://example.com'}), expires_at=expiry)


if __name__ == '__main__': unittest.main()
