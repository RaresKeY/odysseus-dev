"""Compact provider -> canonical card -> exact continuation, entirely offline."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from tests.test_chat_automation_state import store
from src.agent_runtime.authority import OperationGrant, RequestAuthority
from src.agent_runtime.runtime_selection import COMPACT_PREVIEW_MODE
from src.chat_automation import prepare_chat_turn
from src.tool_approvals import ToolApprovalStore
from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import resolve_full_inventory_contract


def events(chunks):
    return [json.loads(chunk[6:]) for chunk in chunks
            if chunk.startswith('data: ') and '[DONE]' not in chunk]


@pytest.mark.asyncio
@pytest.mark.parametrize('artifact_handoff', [False, True])
async def test_compact_pending_card_stops_and_sealed_resume_precedes_inference(
        store, tmp_path, monkeypatch, artifact_handoff):
    from src import clean_agent_preview as preview, tool_execution, tool_approvals
    from src.agent_loop import stream_agent_loop
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    target = workspace / ('output.html' if artifact_handoff else 'note.txt')
    raw_ask = (' \tCreate the requested HTML.\n ' if artifact_handoff
               else ' \tWrite hello to note.txt.\n ')
    session = SimpleNamespace(id='a', model='test', endpoint_url='http://test',
                              headers={}, endpoint_id=None)
    bound = await prepare_chat_turn(store, 'alice', session, raw_ask)
    authority = RequestAuthority('compact-request', 'alice', 'a', str(workspace),
                                 (OperationGrant('write_file'),))
    approvals = ToolApprovalStore()
    monkeypatch.setattr(tool_approvals, 'tool_approval_store', approvals)
    effects, requests = [], []
    responses = iter([
        {'choices': [{'delta': {'reasoning_content': 'preparing body'}}]},
        {'choices': [{'delta': {'content': '<html><body>Requested</body></html>'}}]},
    ] if artifact_handoff else [
        {'choices': [{'delta': {'tool_calls': [{
            'index': 0, 'id': 'write-one', 'function': {'name': 'write_file',
                'arguments': json.dumps({'path': str(target), 'content': 'hello'})},
        }, {
            'index': 1, 'id': 'write-later', 'function': {'name': 'write_file',
                'arguments': json.dumps({'path': str(target), 'content': 'must not run'})},
        }]}}]},
    ])
    class Response:
        def __init__(self, payload): self.payload = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: ' + json.dumps(self.payload)
            yield 'data: [DONE]'
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            requests.append(kwargs['json'])
            return Response(next(responses))
    async def executor(block, **kwargs):
        effects.append(block)
        return 'synthetic write', {'output': 'written', 'exit_code': 0}
    monkeypatch.setattr(preview.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(preview, 'NATIVE_ARTIFACT_RESEARCH_LIMIT', 0)
    monkeypatch.setattr(tool_execution, '_execute_tool_block_impl', executor)
    schema = next(item for item in FUNCTION_TOOL_SCHEMAS
                  if item['function']['name'] == 'write_file')
    contract = replace(resolve_full_inventory_contract(schemas=[schema], policy=ToolPolicy()),
                       selection_mode=COMPACT_PREVIEW_MODE)
    context = {'surface': 'odysseus-native', 'terminal_agent': True, 'unattended_mode': True}
    if artifact_handoff:
        context['completion_requirements'] = {'required_artifacts': [str(target)]}
    with bound.activate():
        chunks = [chunk async for chunk in stream_agent_loop(
            endpoint_url=session.endpoint_url, model=session.model,
            messages=[{'role': 'user', 'content': raw_ask}], headers={},
            turn_contract=contract, session_id='a', owner='alice',
            disabled_tools=set(), tool_policy=ToolPolicy(), workspace=str(workspace),
            client_runtime_context=context, request_authority=authority,
            max_tokens=8192, max_rounds=3)]
    trace = events(chunks)
    cards = [item['data'] for item in trace if item.get('type') == 'ask_user']
    assert len(cards) == 1 and cards[0]['kind'] == 'tool_approval', trace
    assert len(requests) == (2 if artifact_handoff else 1) and not effects
    assert not any('Created ' in str(item.get('content', '')) for item in trace)
    metrics = next(item['data'] for item in trace if item.get('type') == 'metrics')
    pending_event = next(item for item in metrics['tool_events'] if item.get('ask_user'))
    assert pending_event['ask_user'] == cards[0]
    assert pending_event['executed'] is False and pending_event['exit_code'] is None
    assert chunks[-1] == 'data: [DONE]\n\n'
    pending = approvals.peek(cards[0]['approval_id'])
    assert pending.request_text == raw_ask and pending.bound_turn is bound
    exact = approvals.consume(pending.approval_id, owner='alice', session_id='a', decision='approve_once')
    assert exact is not None and not bound.pending
    requests_before_resume = len(requests)

    # Keep the original compact contract: this must take the normal sealed
    # continuation path, not re-enter compact inference or reclassify the ask.
    async def resume_until_tool_output():
        stream = stream_agent_loop(
            endpoint_url=session.endpoint_url, model=session.model,
            messages=[{'role': 'user', 'content': raw_ask}], headers={},
            turn_contract=contract, session_id='a', owner='alice',
            disabled_tools=set(), tool_policy=ToolPolicy(), workspace=str(workspace),
            client_runtime_context=context,
            exact_approval=exact, max_rounds=1)
        try:
            async for chunk in stream:
                parsed = events([chunk])
                if parsed and parsed[0].get('type') == 'tool_output':
                    return parsed[0]
        finally:
            await stream.aclose()
        pytest.fail('Exact continuation did not emit its sealed tool result')
    with bound.activate():
        accepted = await resume_until_tool_output()
        replayed = await resume_until_tool_output()
    assert len(effects) == 1 and len(requests) == requests_before_resume
    assert effects[0].tool_type == pending.tool_name
    assert accepted['exit_code'] == 0 and replayed['exit_code'] == 1
    assert 'did not match' in replayed['output']
