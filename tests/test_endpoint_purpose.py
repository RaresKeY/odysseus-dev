"""Canonical registration → decisions → execution admission, with synthetic SQL/HTTP."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import core.database as database
import routes.model_routes as models
import routes.session_routes as sessions
import routes.chat_routes as chats
import routes.chat_automation_routes as settings_routes
import src.endpoint_resolver as resolver
import src.model_admission as admission
from src.approach_routing import resolve_execution_route
from src.decisions import DecisionError, resolve_decision_endpoint
from tests.helpers.database import disposable_database
from tests.test_decision_provider import CONFIG, CONTEXT, REQUEST, provider, reply


@pytest.fixture
def purpose_app(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTH_ENABLED', 'true')
    with disposable_database(tmp_path) as factory:
        for module in (database, models, sessions, chats, resolver):
            monkeypatch.setattr(module, 'SessionLocal', factory)
        monkeypatch.setattr(admission, '_AUTH_MANAGER', SimpleNamespace(
            users={'alice': {}, 'bob': {}}, get_privileges=lambda _: {'is_admin': True}))
        monkeypatch.setattr(models, 'require_admin', lambda _: None)
        monkeypatch.setattr(settings_routes, 'interactive_owner', lambda _: 'alice')
        monkeypatch.setattr(models, 'check_outbound_url', lambda *a, **kw: (True, ''))
        monkeypatch.setattr(resolver, 'resolve_url', lambda value: value)
        settings = {}
        saved = []
        monkeypatch.setattr(models, '_load_settings', lambda: settings.copy())
        monkeypatch.setattr(models, '_save_settings', lambda value: saved.append(value))
        calls = []
        def forbidden(*a, **kw):
            calls.append(True)
            raise AssertionError('Unexpected provider/catalog probe')
        monkeypatch.setattr(models, '_probe_endpoint', forbidden)
        monkeypatch.setattr(models, '_ping_endpoint', forbidden)
        monkeypatch.setattr(models, '_probe_single_model', forbidden)
        app = FastAPI()
        app.state.auth_manager = SimpleNamespace(is_admin=lambda _: True)
        @app.middleware('http')
        async def owner(request, call_next):
            request.state.current_user = 'alice'
            return await call_next(request)
        router = models.setup_model_routes(None)
        app.include_router(router)
        app.include_router(sessions.setup_session_routes(MagicMock(), {}))
        app.include_router(settings_routes.setup_chat_automation_routes(MagicMock()))
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, db=factory, calls=calls, saved=saved, router=router,
                                  monkeypatch=monkeypatch)


def register(env, **overrides):
    result = env.client.post('/api/model-endpoints', data={
        'name': 'Structured decisions', 'base_url': 'https://decisions.invalid/v1',
        'api_key': 'SYNTHETIC-KEY', 'model_type': 'decision', 'endpoint_kind': 'api',
        'shared': 'false', 'pinned_models': '["decision-model"]', **overrides})
    assert result.status_code == 200, result.text
    return result.json()['id']


@pytest.mark.asyncio
async def test_registration_decision_http_and_picker_execution_separation(purpose_app):
    env = purpose_app
    endpoint_id = register(env)
    # Defaults are not changed by registration, even with a blank global config.
    assert env.saved == [] and env.calls == []
    with env.db() as db:
        row = db.get(database.ModelEndpoint, endpoint_id)
        assert row.model_type == 'decision' and row.owner == 'alice'
        assert row.model_refresh_mode == 'manual' and row.api_key == 'SYNTHETIC-KEY'
    config = {**CONFIG, 'endpoint_id': endpoint_id}
    resolved = resolve_decision_endpoint(config, 'alice')
    assert resolved.url == 'https://decisions.invalid/v1/systemone'
    fake_requests = []
    def fake_http(request):
        fake_requests.append(request)
        assert request.url.path == '/v1/systemone'
        return httpx.Response(200, json=reply())
    client, _ = provider(fake_http, resolver=resolve_decision_endpoint)
    assert (await client.evaluate(REQUEST, CONTEXT, config=config)).choice == 'allow'
    assert len(fake_requests) == 1 and env.calls == []
    assert env.client.get('/api/models').json()['items'] == []
    choices = env.client.get('/api/chat-settings/models').json()
    assert choices['endpoints'] == []
    assert choices['decision_endpoints'] == [{
        'id': endpoint_id, 'name': 'Structured decisions', 'models': ['decision-model'], 'model_type': 'decision'}]
    assert env.client.get('/api/model-endpoints').json()[0]['model_type'] == 'decision'
    assert env.client.get('/api/default-chat').json()['endpoint_id'] == ''
    assert resolver.resolve_endpoint_by_id(endpoint_id, 'decision-model', owner='alice') is None
    assert resolver.resolve_fallback_entries([{'endpoint_id': endpoint_id, 'model': 'decision-model'}], owner='alice') == []
    with pytest.raises(DecisionError, match='execution_route_unavailable'):
        resolve_execution_route({'endpoint_id': endpoint_id, 'model': 'decision-model', 'reasoning_effort': None}, 'alice')
    assert env.calls == []


@pytest.mark.parametrize('use_id', [True, False])
def test_first_session_and_raw_alias_cannot_probe_decision_endpoint(purpose_app, use_id):
    env = purpose_app
    endpoint_id = register(env)
    response = env.client.post('/api/session', data={
        'endpoint_id': endpoint_id if use_id else '',
        'endpoint_url': 'https://decisions.invalid/v1/chat/completions',
        'model': 'decision-model', 'skip_validation': 'false'})
    assert response.status_code == 400 and 'does not support chat' in response.text
    assert env.calls == []


def test_saved_chat_and_selected_send_route_reject_decision_endpoint(purpose_app):
    env = purpose_app
    endpoint_id = register(env)
    sess = SimpleNamespace(id='old', owner='alice', endpoint_id=endpoint_id,
        endpoint_url='https://decisions.invalid/v1/chat/completions', model='decision-model', headers={'Authorization': 'stale'})
    assert chats._clear_orphaned_session_endpoint(sess, owner='alice') is True
    assert sess.headers == {}
    with pytest.raises(HTTPException, match='does not support chat'):
        chats._reconcile_selected_route_from_request(SimpleNamespace(), sess, 'old',
            {'selected_model': 'decision-model', 'selected_endpoint_id': endpoint_id}, owner='alice')
    assert env.calls == []


def test_decision_registration_is_zero_call_and_requires_saved_models(purpose_app):
    env = purpose_app
    endpoint_id = register(env, skip_probe='false', model_refresh_mode='auto')
    assert register(env) == endpoint_id  # repeat registration retains purpose
    result = env.client.post('/api/model-endpoints', data={
        'base_url': 'https://decisions.invalid/v1', 'model_type': 'decision', 'require_models': 'true'})
    assert result.status_code == 400
    assert env.router._should_refresh_endpoint(SimpleNamespace(model_type='decision'), 0, force=True) == (False, {})
    assert env.client.get(f'/api/model-endpoints/{endpoint_id}/probe').status_code == 400
    assert env.client.get(f'/api/model-endpoints/{endpoint_id}/models?refresh=true').status_code == 400
    assert env.calls == env.saved == []


@pytest.mark.parametrize('model_type', ['llm', 'decision', 'image', 'embedding', None])
def test_decision_type_and_owner_admission(purpose_app, model_type):
    env = purpose_app
    with env.db() as db:
        row = database.ModelEndpoint(id='typed', name='typed', owner='alice', model_type=model_type,
            base_url='https://typed.invalid/v1', cached_models='["decision-model"]')
        db.add(row)
        db.commit()
        # Explicit legacy null remains supported without a migration.
        if model_type is None:
            row.model_type = None
            db.commit()
    config = {**CONFIG, 'endpoint_id': 'typed', 'protocol': 'openai_json'}
    if model_type in {'llm', 'decision', None}:
        assert resolve_decision_endpoint(config, 'alice').url == 'https://typed.invalid/v1/chat/completions'
    else:
        with pytest.raises(DecisionError, match='protocol_unavailable'):
            resolve_decision_endpoint(config, 'alice')
    with pytest.raises(DecisionError, match='endpoint_unavailable'):
        resolve_decision_endpoint(config, 'bob')
    assert env.calls == []


def test_ordinary_chat_endpoint_remains_in_both_choice_lists(purpose_app):
    env = purpose_app
    with env.db() as db:
        db.add(database.ModelEndpoint(id='chat', name='Chat', owner='alice', model_type='llm',
            base_url='https://chat.invalid/v1', cached_models='["decision-model"]'))
        db.commit()
    choices = env.client.get('/api/chat-settings/models').json()
    assert choices['endpoints'] == choices['decision_endpoints']
    assert env.client.get('/api/models').json()['items'][0]['endpoint_id'] == 'chat'
    assert resolver.resolve_endpoint_by_id('chat', 'decision-model', owner='alice') is not None
    assert resolve_decision_endpoint({**CONFIG, 'endpoint_id': 'chat', 'protocol': 'openai_json'}, 'alice')
    assert env.calls == []


def test_type_api_rejects_unknown_and_dedupe_cannot_upgrade_purpose(purpose_app):
    env = purpose_app
    endpoint_id = register(env)
    assert env.client.patch(f'/api/model-endpoints/{endpoint_id}', json={'model_type': 'embedding'}).status_code == 400
    result = env.client.post('/api/model-endpoints', data={
        'base_url': 'https://invalid.invalid', 'model_type': 'embedding'})
    assert result.status_code == 400
    with env.db() as db:
        assert db.get(database.ModelEndpoint, endpoint_id).model_type == 'decision'
    assert env.calls == []


@pytest.mark.parametrize('suffix', ['', '/chat/completions', '/systemone', '/chat/completions/alias'])
def test_raw_default_fallback_and_registered_alias_refuse_decision(purpose_app, suffix):
    env = purpose_app
    register(env)
    url = 'https://decisions.invalid/v1' + suffix
    assert resolver.resolve_endpoint('utility', fallback_url=url, fallback_model='decision-model', owner='alice') == (None, None, None)
    with env.db() as db:
        with pytest.raises(ValueError, match='does not support chat'):
            resolver.reject_decision_only_endpoint_url(db, url, 'alice')
        with pytest.raises(ValueError, match='registered'):
            resolver.resolve_owner_registered_endpoint(db, 'https://decisions.invalid/v1', 'alice')
    assert env.calls == []


def test_named_model_alias_refuses_decision_before_http(purpose_app, monkeypatch):
    import src.ai_interaction as interaction
    import src.database as compatibility_database
    env = purpose_app
    register(env)
    monkeypatch.setattr(compatibility_database, 'SessionLocal', env.db)
    def forbidden(*a, **kw):
        raise AssertionError('Decision alias must not read runtime credentials or probe models')
    monkeypatch.setattr(interaction, 'resolve_endpoint_runtime', forbidden)
    monkeypatch.setattr(httpx, 'get', forbidden)
    with pytest.raises(ValueError):
        interaction._resolve_model('decision-model@Structured decisions', owner='alice')
    assert env.calls == []
