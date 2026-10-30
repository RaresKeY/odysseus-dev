"""Donor routing scores are metadata; strict choice and authority guards remain."""
import json

import httpx
import pytest
from fastapi import HTTPException

from core import database as d
from src.chat_automation import prepare_chat_submission, validate_provider_binding
from src.decisions import (DecisionContext, DecisionError, DecisionProvider,
                           MemoryAttemptStore, ResolvedDecisionEndpoint)
from src.model_auto import select_model_and_effort
from tests.test_model_auto import ASK, CATEGORIES, registered, store, categories
from tests.test_model_auto_google import google_pool, session


CONFIG = {'endpoint_id': 'decision', 'model': 'shared', 'protocol': 'systemone'}
MODEL_PROBABILITIES = {'complex_tasks': .8, 'straightforward_tasks': 0., 'unavailable': .2}
EFFORT_PROBABILITIES = {'none': 0., 'minimal': .06, 'low': .01,
                        'medium': .24, 'high': .51, 'unavailable': .18}


def reply(model_confidence=.6, effort_confidence=.41):
    # Exact finite distributions reported by the current live Auto diagnostic;
    # only fixture identity/usage are synthetic. No provider body/key is copied.
    return {'model': 'shared', 'answers': {
        'execution_model': {'type': 'choice', 'choice': 'complex_tasks',
            'confidence': model_confidence, 'probabilities': dict(MODEL_PROBABILITIES)},
        'reasoning_effort': {'type': 'choice', 'choice': 'high',
            'confidence': effort_confidence, 'probabilities': dict(EFFORT_PROBABILITIES)},
        }, 'usage': {'input_tokens': 20, 'output_tokens': 10}}


def provider(raw):
    attempts, calls = MemoryAttemptStore(), []
    endpoint = ResolvedDecisionEndpoint('decision', 'shared', 'systemone',
        'https://decision.invalid/v1/systemone', {'Authorization': 'Bearer SYNTHETIC'})

    def transport(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, content=json.dumps(raw).encode())

    client = DecisionProvider(attempts, resolver=lambda *_: endpoint, observer=False,
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(transport), **kwargs))
    return client, attempts, calls


def configure(registered):
    pool = google_pool(registered, model='models/gemini-2.5-flash')
    registered.update_configuration('alice', {'decision_provider': CONFIG}, 0)
    return pool


@pytest.mark.asyncio
@pytest.mark.parametrize('model_confidence,effort_confidence', [(.6, .41), (0., 0.), (1., 1.)])
async def test_valid_donor_winner_binds_and_retains_raw_scores(registered, model_confidence, effort_confidence):
    pool = configure(registered)
    client, attempts, calls = provider(reply(model_confidence, effort_confidence))
    bound = await prepare_chat_submission(registered, 'alice', session(), ASK, decision_provider=client)
    assert len(calls) == 1 and bound.raw_ask == ASK
    assert set(calls[0]['questions']) == {'execution_model', 'reasoning_effort'}
    assert bound.descriptor['endpoint_id'] == pool[0]['endpoint_id']
    assert bound.descriptor['model'] == pool[0]['model'] == 'models/gemini-2.5-flash'
    assert bound.descriptor['reasoning_effort'] == 'high'
    assert bound.approach_selection['confidence'] == model_confidence
    assert bound.approach_selection['probabilities'] == MODEL_PROBABILITIES
    assert bound.effort_selection['confidence'] == effort_confidence
    assert bound.effort_selection['probabilities'] == EFFORT_PROBABILITIES
    assert bound.policy['approval_mode'] == 'always_ask' and not bound.policy['elevation_grant']
    assert registered.policy('alice', 'a')['auto_categories'] == categories({k:pool[0][k] for k in ('endpoint_id','model')})
    with bound.activate():
        validate_provider_binding(bound.endpoint_url, pool[0]['model'], bound.headers, 'a', 'high')
    record = next(iter(attempts.records.values()))
    assert record['purpose'] == 'model_auto' and record['status'] == 'settled'
    assert record['usage'] == {'input_tokens': 20, 'output_tokens': 10}


@pytest.mark.asyncio
@pytest.mark.parametrize('question', ['execution_model', 'reasoning_effort'])
async def test_valid_explicit_decline_still_stops_without_retry_or_human_fallback(registered, question):
    configure(registered)
    raw = reply()
    answer = raw['answers'][question]
    chosen = answer['choice']
    answer['choice'] = 'unavailable'
    probabilities = answer['probabilities']
    probabilities[chosen], probabilities['unavailable'] = probabilities['unavailable'], probabilities[chosen]
    client, _, calls = provider(raw)
    with pytest.raises(HTTPException) as stopped:
        await prepare_chat_submission(registered, 'alice', session(), ASK, decision_provider=client)
    assert stopped.value.headers['X-Decision-Failure'] == 'auto_selection_uncertain'
    assert 'declined' in stopped.value.detail and 'confidence' not in stopped.value.detail
    assert len(calls) == 1
    with registered.transaction() as db:
        assert db.query(d.ChatAutomationSubmission).one().status == 'failed'
        assert db.query(d.ChatAutomationInputCard).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('corruption', ['nonfinite', 'negative', 'incomplete', 'unnormalized',
    'nonwinner', 'confidence_nonfinite', 'confidence_negative', 'confidence_bool',
    'question', 'outside_pool', 'model_identity', 'purpose', 'usage'])
async def test_low_confidence_does_not_bypass_strict_provider_answers(registered, corruption):
    configure(registered)
    raw = reply()
    answer = raw['answers']['reasoning_effort']
    probabilities = answer['probabilities']
    if corruption == 'nonfinite': probabilities['high'] = float('nan')
    if corruption == 'negative': probabilities['none'] = -.01
    if corruption == 'incomplete': probabilities.pop('none')
    if corruption == 'unnormalized': probabilities['high'] = .5
    if corruption == 'nonwinner': answer['choice'] = 'low'
    if corruption == 'confidence_nonfinite': answer['confidence'] = float('inf')
    if corruption == 'confidence_negative': answer['confidence'] = -.01
    if corruption == 'confidence_bool': answer['confidence'] = True
    if corruption == 'question': raw['answers']['wrong_question'] = raw['answers'].pop('reasoning_effort')
    if corruption == 'outside_pool': raw['answers']['execution_model']['choice'] = 'foreign_category'
    if corruption == 'model_identity': raw['model'] = 'another-model'
    if corruption == 'purpose': raw['purpose'] = 'approval'
    if corruption == 'usage': raw['usage']['input_tokens'] = -1
    client, _, calls = provider(raw)
    with pytest.raises(HTTPException):
        await prepare_chat_submission(registered, 'alice', session(), ASK, decision_provider=client)
    assert len(calls) == 1
    with registered.transaction() as db:
        assert db.query(d.ChatAutomationSubmission).one().status == 'failed'


@pytest.mark.asyncio
async def test_owner_mismatch_stops_before_provider_request():
    client, attempts, calls = provider(reply())
    with pytest.raises(DecisionError, match='auto_models_owner_mismatch'):
        await select_model_and_effort(ASK, CATEGORIES, owner='alice', provider=client,
            config=CONFIG, context=DecisionContext('bob', 'a', 'run'))
    assert not calls and not attempts.records
