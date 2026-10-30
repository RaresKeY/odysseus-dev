"""Both chat APIs refuse decision-only registration before execution transport."""
import httpx
import pytest

from core import database
from src import llm_core
from tests.test_chat_automation_state import store
from tests.test_chat_automation_ingress import runtime


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/api/chat', '/api/chat_stream'])
@pytest.mark.parametrize('selection', ['explicit_id', 'saved_id', 'saved_url'])
async def test_decision_only_chat_ingress_has_zero_execution_calls(runtime, monkeypatch, path, selection):
    with runtime.store.transaction() as db:
        db.add(database.ModelEndpoint(id='decision-only', name='Structured', owner='alice', is_enabled=True,
            model_type='decision', base_url='https://decision.invalid/v1', cached_models='["manual"]'))
        if selection != 'explicit_id':
            row = db.query(database.Session).filter_by(id='a').one()
            row.endpoint_id = 'decision-only' if selection == 'saved_id' else None
            row.endpoint_url = 'https://decision.invalid/v1/chat/completions'
    sess = runtime.manager.sessions['a']
    if selection != 'explicit_id':
        sess.endpoint_id = 'decision-only' if selection == 'saved_id' else None
        sess.endpoint_url = 'https://decision.invalid/v1/chat/completions'
    body = {'session': 'a', 'message': 'Write the requested reply.'}
    if selection == 'explicit_id':
        body['selected_endpoint_id'] = 'decision-only'
    calls = []
    def execution(request):
        calls.append(request)
        return httpx.Response(500, json={'error': 'Unexpected execution'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(execution)) as provider:
        monkeypatch.setattr(llm_core, '_get_http_client', lambda: provider)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url='http://test') as client:
            result = await client.post(path, json=body)
    assert result.status_code in {400, 409, 422}, result.text
    assert calls == []
