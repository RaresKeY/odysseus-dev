"""Auto offers share exact account evidence with request shaping."""
import pytest
from src import model_auto, model_capability_cache as cache
from src.model_request_controls import request_controls_for, DIALECT_CODEX_RESPONSES

BASE = 'https://chatgpt.com/backend-api/codex'

@pytest.fixture(autouse=True)
def catalog():
    cache.clear()
    yield
    cache.clear()


def remember(headers, values, base=BASE):
    return cache.remember_catalog({'models': [
        {'slug': 'same', 'visibility': 'list', 'supported_reasoning_levels': values}
    ]}, base_url=base, headers=headers, vendor='chatgpt_subscription')


def test_two_accounts_same_model_keep_independent_offers_and_transport_constraints():
    a, b = {'Authorization': 'Bearer synthetic-A'}, {'Authorization': 'Bearer synthetic-B'}
    remember(a, ['low', 'high'])
    remember(b, ['minimal', 'ultra'])
    for headers, expected in [(a, ['low', 'high']), (b, ['minimal', 'ultra'])]:
        offer = model_auto.execution_capabilities(BASE + '/responses', 'same', headers)
        assert offer['supported_efforts'] == expected
        controls = request_controls_for(BASE + '/responses', 'same', headers, dialect=DIALECT_CODEX_RESPONSES)
        assert all(controls.permits('reasoning_effort', value) for value in expected)
        assert not controls.permits('reasoning_effort', 'medium')
    assert model_auto.execution_capabilities(BASE, 'same')['supported_efforts'] == ['default']
    assert model_auto.execution_capabilities(BASE + '/other', 'same', a)['supported_efforts'] == ['default']


def test_evidence_expiry_replacement_and_conflicts_invalidate_offer(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(cache.time, 'monotonic', lambda: clock[0])
    headers = {'Authorization': 'Bearer synthetic'}
    remember(headers, ['high'])
    before = model_auto.execution_capabilities(BASE, 'same', headers)
    remember(headers, ['low'])
    after = model_auto.execution_capabilities(BASE, 'same', headers)
    assert before['capability_revision'] != after['capability_revision']
    assert after['supported_efforts'] == ['low']
    clock[0] += 301
    assert model_auto.execution_capabilities(BASE, 'same', headers)['supported_efforts'] == ['default']
    cache.remember_catalog({'models': [
        {'slug': 'same', 'priority': 1, 'supported_reasoning_levels': ['low']},
        {'slug': 'same', 'priority': 2, 'supported_reasoning_levels': ['high']},
    ]}, base_url=BASE, headers=headers, vendor='chatgpt_subscription')
    assert model_auto.execution_capabilities(BASE, 'same', headers)['supported_efforts'] == ['default']


@pytest.mark.parametrize('values', [None, 'high', [True, {}, 'invented'], []])
def test_malformed_or_unrecognized_evidence_stays_default(values):
    headers = {'Authorization': 'Bearer synthetic'}
    remember(headers, values)
    assert model_auto.execution_capabilities(BASE, 'same', headers)['supported_efforts'] == ['default']
