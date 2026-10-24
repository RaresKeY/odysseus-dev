"""Frozen local decisions through the actual browser producer and dispatch seam."""
import contextlib
from dataclasses import dataclass, replace
import json
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
        with imported:
            return await automation.run(self.record, 'Fill Email then review', self.grant, provider, {}, context,
                dispatch or self.dispatch, screenshot_store=lambda data: self.store(data), **kwargs)

    async def store(self, data):
        self.assertTrue(data['data'].startswith(b'\x89PNG'))
        return {'url': '/fixture-artifact/' + data['sha256'], 'width': data['width'], 'height': data['height']}

    async def test_permissions_before_ranking_private_values_redacted_shortlist_bounded(self):
        await self.record.observe()
        self.record.current.packet['text'] += ' private@example.test'
        state, choices, offered = automation.decision_packet(self.record, self.grant, 'private@example.test', [])
        serialized = json.dumps({'state': state, 'choices': choices})
        self.assertNotIn('private@example.test', serialized)
        self.assertNotIn('Delete', serialized)
        self.assertNotIn('captured-object', serialized)
        self.assertLessEqual(len(serialized.encode()), 12000)
        self.assertLessEqual(sum(key.startswith('TARGET_') for key in offered), 12)
        self.assertEqual({args.get('ref') for args in offered.values() if args and args.get('ref')}, {'n0', 'n1'})

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

    async def test_pending_child_preserves_only_existing_expiring_scope(self):
        provider = FrozenProvider(['TARGET_0'])
        async def pending(args, grant):
            return {'approval_required': True, 'executed': False} if args['action'] == 'fill' else await self.dispatch(args, grant)
        output = await self.run_driver(provider, pending)
        self.assertEqual(output['stop_reason'], 'human_approval_required')
        self.assertIs(self.record.task_grant, self.grant)
        self.assertEqual(output['actions_executed'], 0)
        self.assertEqual(output['executor'], 'decision_provider')

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
