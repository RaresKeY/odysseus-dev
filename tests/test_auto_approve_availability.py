"""Reviewer configuration gates activation without inference or secret exposure."""
import json
from types import SimpleNamespace

import httpx
import pytest

from core import database as d
from src.decisions.config import decision_provider_availability, resolve_decision_endpoint
from src.decisions.contracts import DecisionError
from tests.test_chat_automation_api import api
from tests.test_chat_automation_state import store


CONFIG = {'endpoint_id': 'reviewer', 'model': 'decision-model', 'protocol': 'systemone'}


@pytest.fixture
def registration(store, monkeypatch):
    from src import model_admission
    monkeypatch.setattr(d, 'SessionLocal', store.session_factory)
    monkeypatch.setattr(model_admission, '_AUTH_MANAGER', SimpleNamespace(
        users={'alice': {}, 'bob': {}}, get_privileges=lambda _owner: {}))
    with store.transaction() as db:
        db.add(d.ModelEndpoint(id='reviewer', name='Review endpoint', owner='alice',
            base_url='https://api.typesafe.ai/v1', cached_models='["decision-model"]'))
    return store


@pytest.mark.parametrize('key', [None, '', ' \t\n'])
@pytest.mark.parametrize('protocol', ['systemone', 'openai_json'])
def test_hosted_jev_without_nonblank_key_is_unavailable(registration, key, protocol):
    with registration.transaction() as db:
        db.get(d.ModelEndpoint, 'reviewer').api_key = key
    config = {**CONFIG, 'protocol': protocol}
    availability = decision_provider_availability(config, 'alice')
    assert availability['available'] is False
    assert availability['reason_code'] == 'decision_credentials_missing'
    assert 'Jev API key' in availability['reason']
    with pytest.raises(DecisionError, match='decision_credentials_missing'):
        resolve_decision_endpoint(config, 'alice')


@pytest.mark.parametrize('url,key,protocol', [
    ('https://api.typesafe.ai/v1', 'synthetic-private-key', 'systemone'),
    ('http://127.0.0.1:8000/v1', None, 'openai_json'),
    ('http://127.0.0.1:8000/v1', None, 'systemone'),
    ('https://models.local/v1', None, 'openai_json'),
])
def test_configured_jev_and_keyless_local_reviewers_need_no_network_probe(
        registration, monkeypatch, url, key, protocol):
    import socket
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *_args, **_kwargs: pytest.fail('DNS probe'))
    monkeypatch.setattr(httpx.AsyncClient, 'send', lambda *_args, **_kwargs: pytest.fail('HTTP probe'))
    monkeypatch.setattr(httpx.Client, 'send', lambda *_args, **_kwargs: pytest.fail('HTTP probe'))
    with registration.transaction() as db:
        row = db.get(d.ModelEndpoint, 'reviewer')
        row.base_url, row.api_key = url, key
    availability = decision_provider_availability({**CONFIG, 'protocol': protocol}, 'alice')
    assert availability == {'available': True, 'reason_code': None, 'reason': None}
    assert url not in json.dumps(availability)
    assert 'synthetic-private-key' not in json.dumps(availability)


@pytest.mark.parametrize('changes,code', [
    ({'is_enabled': False}, 'decision_endpoint_unavailable'),
    ({'owner': 'bob'}, 'decision_endpoint_unavailable'),
    ({'hidden_models': '["decision-model"]'}, 'decision_model_unavailable'),
    ({'cached_models': '["another-model"]'}, 'decision_model_unavailable'),
    ({'model_type': 'image'}, 'decision_endpoint_protocol_unavailable'),
    ({'provider_auth_id': 'oauth'}, 'decision_endpoint_protocol_unavailable'),
    ({'base_url': 'https://user:private-secret@example.invalid'}, 'decision_endpoint_invalid'),
])
def test_unusable_registration_never_advertises_auto_approve(registration, changes, code, monkeypatch):
    import src.chatgpt_subscription as subscription
    monkeypatch.setattr(subscription, 'resolve_runtime_credentials',
                        lambda *_args, **_kwargs: pytest.fail('Credential refresh'))
    with registration.transaction() as db:
        row = db.get(d.ModelEndpoint, 'reviewer')
        for name, value in changes.items():
            setattr(row, name, value)
    availability = decision_provider_availability(CONFIG, 'alice')
    assert availability['available'] is False and availability['reason_code'] == code
    assert 'private-secret' not in json.dumps(availability)


def test_account_restriction_prevents_reviewer_availability(registration, monkeypatch):
    from src import model_admission
    monkeypatch.setattr(model_admission, '_AUTH_MANAGER', SimpleNamespace(
        users={'alice': {}}, get_privileges=lambda _owner: {'block_all_models': True}))
    availability = decision_provider_availability(CONFIG, 'alice')
    assert availability['available'] is False
    assert availability['reason_code'] == 'decision_model_not_allowed'


@pytest.mark.asyncio
async def test_unconfigured_mode_cannot_be_enabled_through_api_and_does_not_change_policy(api, store):
    with store.transaction() as db:
        db.get(d.Session, 'a').model = ''  # Both reviewer paths unavailable.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test',
                                headers={'test-owner': 'alice'}) as client:
        config = (await client.get('/api/chat-settings')).json()
        before = (await client.get('/api/session/a/automation')).json()
        availability = before['auto_approve_availability']
        assert availability['reason_code'] == config['auto_approve_availability']['reason_code']
        assert availability['available'] is False
        assert availability['reason_code'] == 'decision_provider_not_configured'
        result = await client.put('/api/session/a/automation', json={
            'policy_revision': before['policy_revision'], 'changes': {'approval_mode': 'auto_approve'}})
        assert result.status_code == 422
        assert result.headers['X-Decision-Failure'] == availability['reason_code']
        assert (await client.get('/api/session/a/automation')).json() == before
        assert (await client.get('/api/chat-settings')).json() == config


@pytest.mark.asyncio
async def test_key_revocation_updates_readiness_but_preserves_saved_mode_and_manual_recovery(api, registration):
    with registration.transaction() as db:
        db.get(d.ModelEndpoint, 'reviewer').api_key = 'synthetic-private-key'
        db.get(d.Session, 'a').model = ''  # This case has no selected-model fallback.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test',
                                headers={'test-owner': 'alice'}) as client:
        saved_config = await client.put('/api/chat-settings', json={
            'revision': 0, 'changes': {'decision_provider': CONFIG}})
        assert saved_config.status_code == 200
        assert saved_config.json()['auto_approve_availability']['available'] is True
        saved = await client.put('/api/session/a/automation', json={
            'policy_revision': 0, 'changes': {'approval_mode': 'auto_approve'}})
        assert saved.status_code == 200 and saved.json()['auto_approve_availability']['available'] is True
        with registration.transaction() as db:
            db.get(d.ModelEndpoint, 'reviewer').api_key = None
        unavailable = (await client.get('/api/session/a/automation')).json()
        assert unavailable['approval_mode'] == 'auto_approve'
        assert unavailable['policy_revision'] == saved.json()['policy_revision']
        assert unavailable['auto_approve_availability']['reason_code'] == 'decision_credentials_missing'
        assert (await client.get('/api/chat-settings')).json()['auto_approve_availability']['reason_code'] == unavailable['auto_approve_availability']['reason_code']
        # A stale enabled UI cannot reactivate Auto after key removal.
        denied = await client.put('/api/session/a/automation', json={
            'policy_revision': unavailable['policy_revision'], 'changes': {'approval_mode': 'auto_approve'}})
        assert denied.status_code == 422
        # Unrelated settings and an explicit switch to Always Ask remain usable.
        edited = await client.put('/api/session/a/automation', json={
            'policy_revision': unavailable['policy_revision'], 'changes': {'followup_enabled': True}})
        assert edited.status_code == 200 and edited.json()['approval_mode'] == 'auto_approve'
        recovered = await client.put('/api/session/a/automation', json={
            'policy_revision': edited.json()['policy_revision'], 'changes': {'approval_mode': 'always_ask'}})
        assert recovered.status_code == 200 and recovered.json()['approval_mode'] == 'always_ask'
        assert recovered.json()['auto_approve_availability']['available'] is False


@pytest.mark.asyncio
async def test_local_keyless_reviewer_enables_auto_only_after_explicit_selection(api, registration):
    with registration.transaction() as db:
        db.get(d.ModelEndpoint, 'reviewer').base_url = 'http://127.0.0.1:8000/v1'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test',
                                headers={'test-owner': 'alice'}) as client:
        assert (await client.get('/api/chat-settings')).json()['auto_approve_availability']['available'] is False
        configured = await client.put('/api/chat-settings', json={
            'revision': 0, 'changes': {'decision_provider': {**CONFIG, 'protocol': 'openai_json'}}})
        assert configured.status_code == 200 and configured.json()['auto_approve_availability']['available'] is True
        enabled = await client.put('/api/session/a/automation', json={
            'policy_revision': 0, 'changes': {'approval_mode': 'auto_approve'}})
        assert enabled.status_code == 200 and enabled.json()['approval_mode'] == 'auto_approve'
