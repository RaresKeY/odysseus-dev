"""Actual interactive admission and read-only polling/export route consumers."""
from types import SimpleNamespace
import asyncio

from fastapi import FastAPI
import httpx
import pytest

from routes.decision_activity import setup_decision_activity_routes
from tests.test_extended_observability import observations, attempt


@pytest.fixture
def api(observations):
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True)
    @app.middleware('http')
    async def identity(request, call_next):
        request.state.current_user = request.headers.get('test-owner')
        if request.headers.get('test-token'):
            request.state.api_token = True
            request.state.api_token_owner = 'alice'
        return await call_next(request)
    app.include_router(setup_decision_activity_routes(service=observations))
    return app


@pytest.mark.asyncio
async def test_read_export_poll_does_not_create_attempt_or_inference(api, observations, monkeypatch):
    ledger, _, _ = attempt(observations)
    before = ledger.records_for_run('alice', 'a', 'run')
    monkeypatch.setattr(observations, 'before_dispatch', lambda *_: pytest.fail('Read dispatched inference'))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test', headers={'test-owner': 'alice'}) as client:
        for suffix in ['decisions', 'decisions/live', 'decisions/run/export', 'decision-handoffs']:
            response = await client.get('/api/session/a/' + suffix)
            assert response.status_code == 200
        assert (await client.get('/api/session/c/decisions')).status_code == 404
        assert (await client.get('/api/session/a/decisions?limit=101')).status_code == 422
    assert ledger.records_for_run('alice', 'a', 'run') == before


@pytest.mark.asyncio
async def test_capture_routes_consent_download_delete_and_foreign_rejection(api, observations):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test', headers={'test-owner': 'alice'}) as client:
        assert (await client.put('/api/session/a/decision-capture', json={'purposes': ['browser']})).status_code == 200
        observations.bind_turn('alice', 'a', 'run')
        attempt(observations)
        capture = await client.get('/api/session/a/decisions/run/capture')
        assert capture.status_code == 200 and 'attachment' in capture.headers['content-disposition']
        assert capture.headers['cache-control'] == 'no-store'
        assert (await client.get('/api/session/a/decisions/run/capture', headers={'test-owner': 'bob'})).status_code == 404
        assert (await client.delete('/api/session/a/decisions/run/capture')).status_code == 200
        assert (await client.get('/api/session/a/decisions/run/capture')).status_code == 404


@pytest.mark.asyncio
async def test_unauth_delegated_and_cross_origin_rejected(api):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test') as client:
        assert (await client.get('/api/session/a/decisions')).status_code == 401
        assert (await client.get('/api/session/a/decisions', headers={'test-owner': 'api', 'test-token': 'yes'})).status_code == 403
        assert (await client.put('/api/session/a/decision-capture', headers={'test-owner': 'alice', 'Origin': 'https://foreign.invalid'}, json={'purposes': ['browser']})).status_code == 403


@pytest.mark.asyncio
async def test_router_lifespan_deletes_expired_capture_without_reads(api, observations):
    observations.consent('alice', 'a', {'purposes': ['browser'], 'retention_seconds': 1})
    observations.bind_turn('alice', 'a', 'run')
    attempt(observations)
    observations.test_clock[0] += 2
    async with api.router.lifespan_context(api):
        for _ in range(100):
            await asyncio.sleep(.005)
            with observations.session_factory() as db:
                if db.query(observations.models.capture).count() == 0:
                    break
        else:
            pytest.fail('Retention did not delete expired capture')
