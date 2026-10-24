"""Frozen local decisions through the actual browser producer and dispatch seam."""
import contextlib
from dataclasses import dataclass, replace
import json
import httpx
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src import browser_automation as automation
from src import browser_identity
from src import browser_pipe
from src.agent_runtime.authority import ExactOperation, OperationGrant, RequestAuthority, bind_request_authority
from tests import test_browser_pipe_producer as producer_fixtures

FRAME, SESSION = producer_fixtures.FRAME, producer_fixtures.SESSION


@dataclass
class FixtureDecisionRequest:
    purpose: str
    state: dict
    choices: dict


class FrozenProvider:
    def __init__(self, choices, hook=None):
        self.choices, self.hook, self.requests = list(choices), hook, []

    async def evaluate(self, request, context, *, config):
        self.requests.append(request)
        if self.hook:
            self.hook(request)
        choice = self.choices.pop(0)
        return SimpleNamespace(choice=choice, confidence=.99,
            probabilities={key: (1 if key == choice else 0) for key in request.choices},
            usage=None, attempt_id='frozen-attempt-' + str(len(self.requests)),
            provider_identity={'endpoint_id': 'fixture', 'model': 'synthetic', 'distribution_source': 'fixture'})


class BrowserAutomationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = producer_fixtures.PipeProducerTests.asyncSetUp
    asyncTearDown = producer_fixtures.PipeProducerTests.asyncTearDown

    async def dispatch(self, args, grant):
        # Same real operation/resource/active-authority boundary as the public
        # tool; no child capability is inferred from BrowserTaskGrant itself.
        operation = ExactOperation.normalize('private_browser', json.dumps(args))
        page_scope = replace(self.record.pages[0], scope='page', loader_id='', context_unique_id='')
        authority = RequestAuthority('request', 'alice', 'thread', '', (OperationGrant('private_browser'),),
            browser_sessions=(self.record.session,), browser_pages=(page_scope,))
        bound = browser_identity.resolve_browser_operation(authority, operation)
        with bind_request_authority(authority), browser_identity.bind_browser_operation(bound):
            return await browser_identity.execute_browser(json.dumps(args), {'owner': 'alice', 'session_id': 'thread'})

    async def run_driver(self, provider, dispatch=None, **kwargs):
        # Before provider-worker integration this isolated checkout deliberately
        # contains no inference package. Only its immutable request DTO is
        # stubbed; the actual pipe actor and canonical binding execute below.
        try:
            import src.decisions
            imported = contextlib.nullcontext()
        except ImportError:
            imported = patch.dict(sys.modules, {'src.decisions': SimpleNamespace(DecisionRequest=FixtureDecisionRequest)})
        context = SimpleNamespace(owner='alice', session_id='thread', run_id='run')
        self.record.artifact_writer = self.store
        with imported:
            return await automation.run(self.record, 'Fill Email then review', self.grant, provider, {}, context,
                dispatch or self.dispatch, **kwargs)

    async def store(self, data):
        self.assertTrue(data['data'].startswith(b'\x89PNG'))
        return {'url': '/fixture-artifact/' + data['sha256'], 'width': data['width'], 'height': data['height']}

    async def test_permissions_before_ranking_private_values_redacted_shortlist_bounded(self):
        await self.record.observe()
        self.record.current.packet['text'] += ' private@example.test'
        self.record.current.packet['title'] = 'private%40example.test'
        self.record.current.packet['url'] = 'https://example.com/private%40example.test'
        state, choices, offered = automation.decision_packet(self.record, replace(self.grant, allowed_effects=frozenset({'fill'})), 'private@example.test', [])
        serialized = json.dumps({'state': state, 'choices': choices})
        self.assertNotIn('private@example.test', serialized)
        self.assertNotIn('private%40example.test', serialized)
        self.assertNotIn('private%40example.test', json.dumps(automation.redact_prepared_evidence(
            {'nested': ['private@example.test', {'value': 'private%40example.test'}]}, self.grant)))
        self.assertNotIn('Delete', serialized)
        self.assertNotIn('captured-object', serialized)
        self.assertLessEqual(len(serialized.encode()), 12000)
        self.assertLessEqual(sum(key.startswith('TARGET_') for key in offered), 12)
        self.assertEqual({args.get('ref') for args in offered.values() if args and args.get('ref')}, {'n1'})

    async def test_done_is_handoff_with_actual_screenshot_and_ambiguous_usage(self):
        provider = FrozenProvider(['DONE'])
        output = await self.run_driver(provider)
        self.assertEqual(output['status'], 'awaiting_review')
        self.assertEqual(output['stop_reason'], 'done')
        self.assertFalse(output['completion_verified'])
        self.assertTrue(output['screenshot']['available'])
        self.assertEqual(output['decisions_completed'], 1)
        self.assertIsNone(output['decision_attempts'][0]['usage'])
        self.assertIsNone(self.record.task_grant)

    async def test_action_crosses_real_dispatch_and_repeated_packet_stops(self):
        provider = FrozenProvider(['TARGET_0'])  # Goal rank selects Email.
        output = await self.run_driver(provider)
        self.assertEqual(output['actions_executed'], 1)
        self.assertEqual(output['stop_reason'], 'repeated_packet')
        self.assertEqual(len(provider.requests), 1)
        calls = [r for r in self.channel.requests if r['method'] == 'Runtime.callFunctionOn' and r['params']['functionDeclaration'] == browser_pipe._CALL_ACTION]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['params']['arguments'][0]['value']['action'], 'fill')

    async def test_traversal_uses_one_task_page_scope_and_fresh_document_bindings(self):
        await self.record.observe()  # A human view exists before task admission.
        original = RequestAuthority('request', 'alice', 'thread', '', (OperationGrant('private_browser'),))
        sessions, pages = browser_identity.seal_browser_resources(original)
        authority = replace(original, browser_sessions=sessions, browser_pages=pages)
        self.assertEqual(authority.browser_pages[0].scope, 'page')
        captured = []
        async def admitted_child(args, grant):
            operation = ExactOperation.normalize('private_browser', json.dumps(args))
            bound = browser_identity.resolve_browser_operation(authority, operation)
            captured.append((args['action'], bound.page.loader_id, bound.page.context_unique_id))
            with bind_request_authority(authority), browser_identity.bind_browser_operation(bound):
                return await browser_identity.execute_browser(json.dumps(args), {'owner': 'alice', 'session_id': 'thread'})
        self.channel.traverse_on_click = True
        provider = FrozenProvider(['TARGET_0', 'DONE'])
        # Only click is allowed so TARGET_0 is the admitted Next link.
        self.grant = replace(self.grant, allowed_effects=frozenset({'click'}), prepared_values={})
        self.record.task_grant = self.grant
        output = await self.run_driver(provider, admitted_child)
        self.assertEqual(output['stop_reason'], 'done')
        self.assertEqual(output['actions_executed'], 1)
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(self.record.loader, 'loader-2')
        snapshots = [loader for action, loader, _ in captured if action == 'snapshot']
        self.assertEqual(snapshots, ['loader-1', ''])
        self.assertEqual(self.record.current.loader, 'loader-2')

    async def test_pending_child_preserves_only_existing_expiring_scope(self):
        provider = FrozenProvider(['TARGET_0'])
        card = {'kind': 'tool_approval', 'approval_id': 'opaque-canonical-id', 'session_id': 'thread'}
        async def pending(args, grant):
            return {'approval_required': True, 'ask_user': card, 'executed': False} if args['action'] == 'fill' else await self.dispatch(args, grant)
        output = await self.run_driver(provider, pending)
        self.assertEqual(output['stop_reason'], 'human_approval_required')
        self.assertIs(self.record.task_grant, self.grant)
        self.assertEqual(output['actions_executed'], 0)
        self.assertEqual(output['executor'], 'decision_provider')
        self.assertEqual(output['status'], 'awaiting_approval')
        self.assertIs(output['ask_user'], card)

    async def test_actual_automate_entrypoint_propagates_canonical_always_ask_card(self):
        from src import decisions, tool_approvals, tool_execution
        from src.tool_approvals import ToolApprovalStore
        from src.tool_capabilities import ToolRunSecurityContext
        from src.tool_types import ToolBlock
        policy = {'contract_version': 1, 'owner': 'alice', 'session_id': 'thread',
            'approval_mode': 'always_ask', 'policy_revision': 1, 'authority_epoch': 1,
            'elevation_ceiling': False, 'elevation_grant': None, 'effective_roots': []}
        args = {'action': 'automate', 'page': 't1', 'goal': 'Review the observed page'}
        authority = RequestAuthority('request', 'alice', 'thread', '', (OperationGrant('private_browser'),),
            browser_sessions=(self.record.session,), browser_pages=self.record.pages)
        # The transport remains fake, while the outer dispatcher, injected
        # runtime, recursive child dispatcher, proposal store and Always Ask
        # service are the actual integrated owners. Only the bound SQL policy
        # view is frozen here; no manual proposal can omit canonical fields.
        self.turn.policy = policy
        self.turn.configuration = {}
        self.turn.store = SimpleNamespace(policy=lambda *args, **kwargs: policy)
        self.turn.raw_ask = 'Original user browser task'
        self.turn.submission_id = 'submission'
        self.turn.pending = False
        store = ToolApprovalStore()
        provider = FrozenProvider([])
        with (patch.object(tool_approvals, 'tool_approval_store', store),
              patch.object(decisions, 'DecisionProvider', return_value=provider)):
            _, output = await tool_execution.execute_tool_block(
                ToolBlock('private_browser', json.dumps(args)), owner='alice', session_id='thread',
                workspace='', request_authority=authority, security_context=ToolRunSecurityContext())
        self.assertEqual(output.get('status'), 'awaiting_approval', output)
        self.assertTrue(output['approval_required'])
        self.assertIs(output['executed'], False)
        self.assertIsNone(output['exit_code'])
        self.assertEqual(output['status'], 'awaiting_approval')
        self.assertEqual(output['ask_user']['kind'], 'tool_approval')
        pending = store.peek(output['ask_user']['approval_id'])
        self.assertEqual(pending.session_id, 'thread')
        self.assertEqual(json.loads(pending.content), {'action': 'snapshot', 'page': 't1'})
        self.assertIsNotNone(pending.browser_operation)
        self.assertIsNotNone(pending.backend_operation)
        self.assertEqual(pending.policy_binding['approval_mode'], 'always_ask')
        self.assertTrue(self.turn.pending)
        self.assertEqual(provider.requests, [])
        self.assertIs(self.record.task_grant, self.grant)
        self.assertFalse(any(request['method'] == 'Page.navigate' or
            request['method'] == 'Page.captureScreenshot' or
            (request['method'] == 'Runtime.callFunctionOn' and
             request['params']['functionDeclaration'] == browser_pipe._CALL_ACTION)
            for request in self.channel.requests))

    async def test_handoff_screenshot_crosses_child_approval_and_native_visual_projection(self):
        provider, seen = FrozenProvider(['DONE']), []
        async def traced(args, grant):
            seen.append(args['action'])
            return await self.dispatch(args, grant)
        output = await self.run_driver(provider, traced)
        self.assertEqual(seen, ['snapshot', 'screenshot'])
        self.assertEqual(len(output['images']), 1)
        from src.clean_agent_preview import bounded_visual_result_blocks
        blocks = bounded_visual_result_blocks(output)
        self.assertEqual(len(blocks), 1)
        self.assertTrue(blocks[0]['image_url']['url'].startswith('data:image/png;base64,iVBOR'))
        from src.agent_loop import _append_tool_results
        messages = [{'role': 'user', 'content': 'Review the current browser image'}]
        _append_tool_results(messages, '', [], [], ['Browser handoff'], False, 1,
            tool_result_records=[{'tool_name': 'private_browser', 'content': '{}', 'result': output}])
        projected = [item for item in messages if isinstance(item.get('content'), list)]
        self.assertEqual(len(projected), 1)
        self.assertTrue(any(block.get('type') == 'image_url' and block['image_url']['url'].startswith('data:image/png;base64,iVBOR')
            for block in projected[0]['content']))
        from src import llm_core
        requests = []
        def answer(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'model': 'fixture-vision', 'choices': [{'message': {'content': 'Reviewed fixture pixels'}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
            with (patch.object(llm_core, '_get_http_client', return_value=client),
                  patch.object(llm_core, '_get_cached_response', return_value=None),
                  patch.object(llm_core, '_set_cached_response'),
                  patch.object(llm_core, 'get_context_length', return_value=32768),
                  patch.object(llm_core, '_is_host_dead', return_value=False)):
                await llm_core.llm_call_async('https://vision-fixture.invalid/v1/chat/completions', 'fixture-vision',
                    messages, max_tokens=64, max_retries=1)
        wire_blocks = [block for message in requests[0]['messages'] if isinstance(message.get('content'), list)
            for block in message['content'] if block.get('type') == 'image_url']
        self.assertEqual(len(wire_blocks), 1)
        self.assertTrue(wire_blocks[0]['image_url']['url'].startswith('data:image/png;base64,iVBOR'))

    async def test_handoff_after_human_takeover_reads_no_new_pixels_or_page(self):
        def takeover(_request):
            self.record.task_grant = None
            self.record.invalidate_document()
        output = await self.run_driver(FrozenProvider(['DONE'], takeover))
        self.assertEqual(output['stop_reason'], 'browser_task_revoked')
        self.assertFalse(output['screenshot']['available'])
        self.assertNotIn('images', output)
        self.assertEqual(output['page']['title'], 'Fixture')
        self.assertFalse(any(r['method'] == 'Page.captureScreenshot' for r in self.channel.requests))

    async def test_spent_wall_budget_does_not_begin_final_capture(self):
        clock = [0]
        provider = FrozenProvider(['DONE'], lambda _: clock.__setitem__(0, 121))
        output = await self.run_driver(provider, clock=lambda: clock[0])
        self.assertEqual(output['stop_reason'], 'time_limit')
        self.assertFalse(output['screenshot']['available'])
        self.assertEqual(len(output['decision_attempts']), 1)
        self.assertFalse(any(r['method'] == 'Page.captureScreenshot' for r in self.channel.requests))

    async def test_final_pixels_pause_as_an_exact_read_instead_of_bypassing_approval(self):
        card = {'kind': 'tool_approval', 'approval_id': 'screenshot-card'}
        async def pending(args, grant):
            if args['action'] == 'screenshot':
                return {'approval_required': True, 'ask_user': card, 'executed': False}
            return await self.dispatch(args, grant)
        output = await self.run_driver(FrozenProvider(['DONE']), pending)
        self.assertEqual(output['stop_reason'], 'human_approval_required')
        self.assertIs(output['ask_user'], card)
        self.assertFalse(output['screenshot']['available'])
        self.assertIs(self.record.task_grant, self.grant)
        self.assertFalse(any(r['method'] == 'Page.captureScreenshot' for r in self.channel.requests))

    async def test_trusted_checks_reach_actual_automation_tool_without_inference(self):
        self.grant = replace(self.grant, completion_checks=({'kind': 'text_contains', 'value': 'Fixture'},))
        self.record.task_grant = self.grant
        self.record.artifact_writer = self.store
        args = {'action': 'automate', 'page': 't1', 'goal': 'Check the supplied condition'}
        operation = ExactOperation.normalize('private_browser', json.dumps(args))
        authority = RequestAuthority('request', 'alice', 'thread', '', (OperationGrant('private_browser'),),
            browser_sessions=(self.record.session,), browser_pages=self.record.pages)
        bound = browser_identity.resolve_browser_operation(authority, operation)
        provider = FrozenProvider([])
        runtime = {'provider': provider, 'config': {}, 'decision_context': SimpleNamespace(owner='alice', session_id='thread', run_id='run'),
                   'dispatch': self.dispatch}
        with bind_request_authority(authority), browser_identity.bind_browser_operation(bound):
            output = await browser_identity.execute_browser(json.dumps(args), {'owner': 'alice', 'session_id': 'thread', '_browser_automation_runtime': runtime})
        handoff = output['browser_handoff']
        self.assertEqual(handoff['stop_reason'], 'conditions_met')
        self.assertEqual(provider.requests, [])
        self.assertEqual(len(output['images']), 1)
        self.assertNotIn('images', handoff)
        self.assertNotIn('iVBOR', output['output'])

    async def test_navigation_while_inference_pending_never_retargets(self):
        provider = FrozenProvider(['TARGET_0'], lambda request: self.record._event({'method': 'Page.navigatedWithinDocument', 'params': {'frameId': FRAME}, 'sessionId': SESSION}))
        output = await self.run_driver(provider)
        self.assertEqual(output['stop_reason'], 'stale_observation')
        self.assertEqual(output['actions_executed'], 0)
        self.assertFalse(any(r['method'] == 'Runtime.callFunctionOn' and r['params']['functionDeclaration'] == browser_pipe._CALL_ACTION for r in self.channel.requests))

    async def test_indeterminate_child_is_not_retried(self):
        provider = FrozenProvider(['TARGET_0'])
        attempts = []
        async def ambiguous(args, grant):
            if args['action'] == 'fill':
                attempts.append(args)
                return {'executed': None, 'outcome': 'indeterminate', 'exit_code': 1}
            return await self.dispatch(args, grant)
        output = await self.run_driver(provider, ambiguous)
        self.assertEqual(output['stop_reason'], 'effect_indeterminate')
        self.assertEqual(len(attempts), 1)
        self.assertEqual(len(provider.requests), 1)

    async def test_explicit_check_is_reported_as_untrusted_evidence(self):
        provider = FrozenProvider([])
        output = await self.run_driver(provider, checks=[{'kind': 'text_contains', 'value': 'Fixture'}])
        self.assertEqual(output['stop_reason'], 'conditions_met')
        self.assertEqual(output['checks'][0]['evidence_source'], 'untrusted_page_text')
        self.assertFalse(output['completion_verified'])
        self.assertEqual(provider.requests, [])

    async def test_human_control_revokes_automated_scope_and_retains_exact_target(self):
        observed = await self.record.observe()
        result = await self.record.human_control({'action': 'fill', 'page': 't1', 'observation': observed['observation'], 'ref': 'n1', 'value_id': 'Email'}, grant=self.grant)
        self.assertTrue(result['executed'])
        self.assertEqual(result['actor'], 'human')
        self.assertIsNone(self.record.task_grant)
        self.assertIsNone(self.record.current)
        self.assertIs(self.record.network_grant, self.grant)

    async def test_automate_requires_server_runtime_and_exact_run(self):
        args = {'action': 'automate', 'page': 't1', 'goal': 'Review page'}
        operation = ExactOperation.normalize('private_browser', json.dumps(args))
        authority = RequestAuthority('request', 'alice', 'thread', '', (OperationGrant('private_browser'),),
            browser_sessions=(self.record.session,), browser_pages=self.record.pages)
        bound = browser_identity.resolve_browser_operation(authority, operation)
        with self.assertRaisesRegex(browser_pipe.BrowserPipeError, 'browser_automation_runtime_unavailable'):
            await automation.execute_automation(self.record, bound, args, {})
        runtime = {'provider': object(), 'config': {}, 'decision_context': SimpleNamespace(owner='alice', session_id='thread', run_id='other'), 'dispatch': self.dispatch}
        with self.assertRaisesRegex(browser_pipe.BrowserPipeError, 'browser_decision_binding_changed'):
            await automation.execute_automation(self.record, bound, args, {'_browser_automation_runtime': runtime})


if __name__ == '__main__': unittest.main()
