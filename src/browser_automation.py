"""Bounded browser decisions above canonical per-action admission.

The driver never executes a classified action itself. The injected dispatcher
must use Odysseus request/resource authority and its independent approval gate.
Only trusted server code supplies BrowserTaskGrant and prepared values.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import time
from collections.abc import Mapping
from urllib.parse import quote, quote_plus

from src.browser_pipe import BrowserPipeError, BrowserTaskGrant, digest

MAX_REQUEST_BYTES = 12_000
SHORTLIST = 12


def redact_prepared_evidence(value, grant):
    """Project review/decision evidence without locally prepared field values.

    This never changes the host's captured target table or dispatched input.
    A page may echo a value in text, labels, or an encoded URL after a fill.
    """
    if isinstance(value, Mapping):
        return {key: redact_prepared_evidence(item, grant) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_prepared_evidence(item, grant) for item in value]
    if not isinstance(value, str):
        return value
    text = value
    for prepared in grant.prepared_values.values():
        if prepared:
            variants = {prepared, quote(prepared, safe=''), quote_plus(prepared, safe=''), quote(prepared), quote_plus(prepared)}
            variants |= {re.sub(r'%[A-Fa-f0-9]{2}', lambda match: match[0].lower(), item) for item in variants}
            for item in sorted(variants, key=len, reverse=True):
                text = text.replace(item, '[prepared value]')
    return text


def redact(value, grant):
    return redact_prepared_evidence(str(value), grant)


def candidates(record, grant):
    grant.check(record.owner, record.thread_id)
    if record.current is None:
        raise BrowserPipeError('browser_observation_required')
    found = []
    nodes = record.current.packet['elements']
    labels = {}
    for node in nodes:
        labels[node.get('label')] = labels.get(node.get('label'), 0) + 1
    destinations = set()
    for node in nodes:
        if node.get('disabled'):
            continue
        action = 'fill' if node.get('editable') else 'click'
        if action == 'fill' and (node.get('label') not in grant.prepared_values or labels[node.get('label')] != 1):
            continue
        try:
            record.permitted_node(action, node.get('ref'), grant)
        except BrowserPipeError:
            continue
        destination = node.get('destination')
        if destination:
            if destination in destinations:
                continue
            destinations.add(destination)
        args = {'action': action, 'page': 't1', 'observation': record.current.nonce, 'ref': node['ref']}
        if action == 'fill':
            args['value_id'] = node['label']
        found.append((node, args))
    # Permissions precede any goal rank; a description never grants a target.
    return found


def decision_packet(record, grant, goal, history, offset=0):
    available = candidates(record, grant)
    words = set(str(goal).casefold().split())
    available.sort(key=lambda pair: (sum(word in str(pair[0].get('label', '')).casefold() for word in words), bool(pair[0].get('in_viewport'))), reverse=True)
    shortlist = available[offset:offset + SHORTLIST]
    offered = {'HANDOFF': None, 'DONE': None, 'BLOCKED': None,
               'SCROLL': {'action': 'scroll', 'page': 't1', 'observation': record.current.nonce, 'amount': 600}}
    descriptions = {'HANDOFF': 'Return control for human/main-agent instructions or visual review.',
                    'DONE': 'Request independent completion review; this is not proof of success.',
                    'BLOCKED': 'The observed page cannot support the task.', 'SCROLL': 'Scroll the captured main document down.'}
    if offset + SHORTLIST < len(available):
        offered['EXPAND'] = None
        descriptions['EXPAND'] = 'Inspect the next bounded set of already permitted targets.'
    for index, (node, args) in enumerate(shortlist):
        key = 'TARGET_' + str(index)
        offered[key] = args
        descriptions[key] = args['action'] + ': ' + redact(node.get('label', ''), grant)[:120]
    observation = record.public_observation()
    state = {'trusted_task': {'goal': redact(goal, grant)[:2000], 'allowed_origins': sorted(grant.allowed_origins),
                              'allowed_effects': sorted(grant.allowed_effects)},
             'untrusted_page': {k: redact(observation[k], grant) for k in ('url', 'title', 'text', 'ready')},
             'recent_outcomes': history[-3:], 'omitted_targets': max(0, len(available) - len(shortlist))}
    state, descriptions = redact_prepared_evidence(state, grant), redact_prepared_evidence(descriptions, grant)
    # No prepared text, form data, cookies, raw CDP identities or node handles.
    from src.automation_catalog import CatalogError, compact_catalog
    try:
        return compact_catalog(state, descriptions, offered,
            max_request_bytes=MAX_REQUEST_BYTES, max_candidates=SHORTLIST + 5)
    except CatalogError as error:
        raise BrowserPipeError(error.code) from None


def completion_checks(record, checks):
    if record.current is None:
        return []
    packet = record.current.packet
    results = []
    for check in checks:
        if not isinstance(check, Mapping) or set(check) != {'kind', 'value'} or not isinstance(check['value'], str) or len(check['value']) > 1000:
            raise ValueError('Invalid browser completion check')
        kind, value = check['kind'], check['value']
        if kind == 'text_contains':
            passed, source = value in packet['text'], 'untrusted_page_text'
        elif kind == 'url_equals':
            passed, source = value == packet['url'], 'observed_browser_url'
        elif kind == 'element_label':
            passed, source = any(node.get('label') == value for node in packet['elements']), 'untrusted_page_label'
        else:
            raise ValueError('Unsupported browser completion check')
        results.append({'kind': kind, 'passed': passed, 'evidence_source': source})
    return results


async def run(record, goal, grant, provider, provider_config, decision_context, dispatch, *, initial_url=None, checks=(), max_steps=20, wall_seconds=120, clock=time.monotonic):
    """One bounded task; each chosen operation crosses the supplied dispatcher.

    ``dispatch(args, grant)`` returns the real canonical tool result. It may
    return approval_required; that is a handoff, never an implicit child grant.
    """
    if (not isinstance(grant, BrowserTaskGrant) or not isinstance(goal, str) or not goal.strip()
            or len(goal.encode()) > 24_000 or type(max_steps) is not int or not 1 <= max_steps <= 100
            or type(wall_seconds) is not int or not 1 <= wall_seconds <= 120):
        raise ValueError('Invalid bounded browser task')
    if record.task_grant is not None and record.task_grant.run_id != grant.run_id:
        raise BrowserPipeError('browser_task_already_active')
    grant.check(record.owner, record.thread_id)
    record.task_grant = grant
    started, offset = clock(), 0
    decisions, actions = 0, 0
    history, seen = [], set()
    stop = 'step_limit'
    latest_checks = []
    screenshot = {'available': False, 'reason': 'not_captured'}
    attempts = []
    pending_card = None
    last_page, last_document, images = None, None, []
    def document_key():
        current = record.current
        return (current.target, current.loader, current.context_unique_id, current.epoch, current.nonce) if current is not None else None
    async def bounded(awaitable):
        remaining = wall_seconds - (clock() - started)
        if remaining <= 0:
            # Do not begin a new child after the budget is spent. Closing a
            # not-yet-started coroutine avoids an unawaited-coroutine leak.
            if hasattr(awaitable, 'close'):
                awaitable.close()
            raise TimeoutError()
        async with asyncio.timeout(remaining):
            return await awaitable
    try:
        from src.decisions import DecisionRequest
        if initial_url is not None:
            if initial_url not in grant.navigation_urls:
                raise BrowserPipeError('browser_initial_url_not_granted')
            initial = await bounded(dispatch({'action': 'navigate', 'page': 't1', 'url': initial_url}, grant))
            if initial.get('approval_required'):
                pending_card = initial.get('ask_user')
                raise BrowserPipeError('human_approval_required')
            if initial.get('outcome') == 'indeterminate' or initial.get('executed') is None:
                raise BrowserPipeError('browser_action_indeterminate', indeterminate=True)
            if initial.get('executed') is not True:
                raise BrowserPipeError(initial.get('failure_kind', 'navigation_failed'))
            actions += 1
        for step in range(max_steps):
            if clock() - started >= wall_seconds:
                stop = 'time_limit'; break
            grant.check(record.owner, record.thread_id)
            if record.task_grant is not grant:
                raise BrowserPipeError('browser_task_revoked')
            observation = await bounded(dispatch({'action': 'snapshot', 'page': 't1'}, grant))
            if observation.get('approval_required'):
                pending_card = observation.get('ask_user')
                stop = 'human_approval_required'; break
            if observation.get('exit_code') not in (None, 0) or observation.get('executed') is not True:
                stop = observation.get('failure_kind', 'observation_failed'); break
            last_document = document_key()
            if last_document is None or record.task_grant is not grant:
                raise BrowserPipeError('browser_observation_stale')
            last_page = copy.deepcopy(record.public_observation())
            latest_checks = completion_checks(record, checks)
            if latest_checks and all(item['passed'] for item in latest_checks):
                stop = 'conditions_met'; break
            if record.current.packet.get('ready') == 'loading':
                await asyncio.sleep(min(.25, max(0, wall_seconds - (clock() - started))))
                continue
            state, descriptions, offered = decision_packet(record, grant, goal, history, offset)
            identity = digest({'state': {k: v for k, v in state.items() if k != 'recent_outcomes'}, 'choices': descriptions})
            if identity in seen:
                stop = 'repeated_packet'; break
            seen.add(identity)
            request = DecisionRequest('browser', state, descriptions)
            result = await bounded(provider.evaluate(request, decision_context, config=provider_config))
            decisions += 1
            attempts.append({'attempt_id': result.attempt_id, 'usage': result.usage,
                             'provider_identity': result.provider_identity})
            if clock() - started >= wall_seconds:
                stop = 'time_limit'; break
            grant.check(record.owner, record.thread_id)
            if record.task_grant is not grant:
                raise BrowserPipeError('browser_task_revoked')
            if document_key() != last_document:
                raise BrowserPipeError('stale_observation')
            if result.choice not in offered:
                stop = 'decision_invalid'; break
            if result.choice in {'HANDOFF', 'DONE', 'BLOCKED'}:
                stop = result.choice.lower(); break
            if result.confidence < .8 or result.probabilities[result.choice] < .8:
                stop = 'uncertain'; break
            if result.choice == 'EXPAND':
                offset += SHORTLIST
                continue
            if record.task_grant is not grant:
                raise BrowserPipeError('browser_task_revoked')
            # dispatch must check current epoch, exact action authority and
            # independent review here, after inference and before producer IO.
            output = await bounded(dispatch(offered[result.choice], grant))
            if output.get('approval_required'):
                pending_card = output.get('ask_user')
                stop = 'human_approval_required'; break
            if output.get('outcome') == 'indeterminate' or output.get('executed') is None:
                stop = 'effect_indeterminate'; break
            if output.get('exit_code') not in (None, 0) or output.get('executed') is not True:
                stop = output.get('failure_kind', 'action_failed'); break
            actions += 1
            history.append({'action': offered[result.choice]['action'], 'executed': True, 'actor': 'decision_provider'})
            offset = 0
    except asyncio.CancelledError:
        stop = 'cancelled'
        record.invalidate_document()
        if record.task_grant is grant:
            record.task_grant = None
        raise
    except BrowserPipeError as error:
        stop = 'effect_indeterminate' if error.indeterminate else error.code
    except TimeoutError:
        stop = 'time_limit'
    except Exception:
        stop = 'decision_unresolved'
    # The final read has the same exact action approval, run deadline and
    # observed-document binding as every other child. Never inspect a newer
    # human document or take unapproved pixels after an initial pending card.
    try:
        if (record.live and record.task_grant is grant and last_document is not None
                and document_key() == last_document
                and stop in {'done', 'handoff', 'blocked', 'conditions_met', 'uncertain', 'repeated_packet', 'step_limit'}
                and clock() - started < wall_seconds):
            captured = await bounded(dispatch({'action': 'screenshot', 'page': 't1',
                'observation': last_document[-1]}, grant))
            if captured.get('approval_required'):
                pending_card, stop = captured.get('ask_user'), 'human_approval_required'
                screenshot = {'available': False, 'reason': 'human_approval_required'}
            elif captured.get('executed') is True and captured.get('exit_code') == 0 and record.task_grant is grant and document_key() == last_document:
                screenshot = {'available': True, **captured.get('screenshot', {})}
                images = captured.get('images', [])
            else:
                screenshot = {'available': False, 'reason': captured.get('failure_kind', 'capture_unavailable')}
        else:
            screenshot = {'available': False, 'reason': stop if stop == 'human_approval_required' else 'no_current_admitted_observation'}
    except (Exception, asyncio.CancelledError) as error:
        screenshot = {'available': False, 'reason': getattr(error, 'code', 'capture_unavailable')}
        if isinstance(error, asyncio.CancelledError):
            if record.task_grant is grant:
                record.task_grant = None
            raise
    finally:
        # An old run cannot remove a newer run's task capability.
        if record.task_grant is grant and stop != 'human_approval_required':
            record.task_grant = None
    result = {'status': 'awaiting_approval' if stop == 'human_approval_required' else 'awaiting_review', 'stop_reason': stop, 'executor': 'decision_provider', 'run_id': grant.run_id,
            'decisions_completed': decisions, 'actions_executed': actions, 'recent_outcomes': history[-3:],
            'decision_attempts': attempts, 'usage_scope': 'browser_choices_only; canonical_ledger_accounts_for_action_reviews',
            'checks': latest_checks, 'screenshot': screenshot, 'handoff_required': True,
            'producer_namespace': 'native:odysseus-cdp-pipe', 'producer_version': '1',
            'completion_verified': False, 'page': last_page}
    if images:
        result['images'] = images
    if stop == 'human_approval_required':
        # Preserve the canonical public card at the tool boundary. A reason
        # string alone cannot drive the chat approval UI or exact continuation.
        result.update(approval_required=True, ask_user=pending_card)
    return result


async def execute_automation(record, bound, args, ctx):
    """Canonical tool seam; runtime dependencies are injected by dispatch.

    Models can supply a goal and finite budget, never a provider, credentials,
    executable, task capability or callback. Every selected child operation is
    dispatched again through the canonical owner admission/approval mechanism.
    """
    if set(args) - {'action', 'page', 'goal', 'url', 'max_steps'} or args.get('page') != 't1':
        raise BrowserPipeError('invalid_arguments')
    bound.validate()
    runtime = ctx.get('_browser_automation_runtime')
    if not isinstance(runtime, dict) or set(runtime) != {'provider', 'config', 'decision_context', 'dispatch'} or not callable(runtime['dispatch']):
        raise BrowserPipeError('browser_automation_runtime_unavailable')
    grant = record.task_grant
    if not isinstance(grant, BrowserTaskGrant):
        raise BrowserPipeError('browser_task_authority_required')
    grant.check(bound.owner, bound.thread_id)
    context = runtime['decision_context']
    if (context.owner, context.session_id, context.run_id) != (bound.owner, bound.thread_id, grant.run_id):
        raise BrowserPipeError('browser_decision_binding_changed')
    initial_url = args.get('url')
    if initial_url is None and not grant.permits_navigation(record.document_url) and len(grant.navigation_urls) == 1:
        initial_url = next(iter(grant.navigation_urls))
    result = await run(record, args.get('goal'), grant, runtime['provider'], runtime['config'], context,
                       runtime['dispatch'], initial_url=initial_url, checks=grant.completion_checks,
                       max_steps=args.get('max_steps', 20))
    images = result.pop('images', [])
    output = {'output': json.dumps(result, allow_nan=False), 'exit_code': 0,
              'executed': True, 'actor': 'decision_provider', 'browser_handoff': result}
    if images:
        output['images'] = images
    if result.get('approval_required'):
        output.update(approval_required=True, ask_user=result['ask_user'], exit_code=None,
                      executed=False, status='awaiting_approval')
    return output
