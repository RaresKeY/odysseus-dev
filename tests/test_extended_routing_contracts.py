"""Production preparation consumes frozen, complete permitted inventories."""
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from core import database as d
from src.chat_automation import AutomationStore, prepare_chat_turn
from src.decisions.contracts import DecisionError, DecisionResult
from src.decision_routing import prepare_tool_routing, preparation_receipt


@pytest.fixture
def store(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'routing.db'))
    d.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(d.Session(id='a', name='a', owner='alice', model='manual', endpoint_url='http://local/v1'))
        db.commit()
    yield AutomationStore(factory)
    engine.dispose()


def session():
    return SimpleNamespace(id='a', model='manual', endpoint_id=None, headers={}, endpoint_url='http://local/v1')


def schemas(count=3):
    return [{'type': 'function', 'function': {'name': 'new_registered_' + str(i),
             'description': 'A registered tool', 'parameters': {'type': 'object'}}} for i in range(count)]


class Provider:
    def __init__(self, choices):
        self.choices, self.requests = choices, []

    async def evaluate(self, request, context, config):
        self.requests.append(request)
        answers = {}
        for name, question in request.questions().items():
            choice = self.choices[name]
            answers[name] = {'type':'choice', 'choice': choice, 'confidence': .99,
                            'probabilities': {key: float(key == choice) for key in question['criteria']}}
        main = answers[request.question_id]
        return DecisionResult(main['choice'], main['confidence'], main['probabilities'],
                              {'input_tokens': 1, 'output_tokens': 1}, 'attempt', {'model':'neutral-decider'}, answers)


@pytest.mark.asyncio
async def test_actual_prepare_preserves_exact_ask_and_dynamic_catalog_over_choice_limit(store):
    store.update_chat('alice', 'a', {'tool_routing': 'jev'}, 0)
    raw = '  Exact ask\nλ  '
    provider = Provider({'tools': 'all_available'})
    inventory = schemas(180)
    bound = await prepare_chat_turn(store, 'alice', session(), raw,
                                   tool_inventory=inventory, decision_provider=provider)
    request = provider.requests[0]
    assert request.state['ask'] == raw == bound.raw_ask
    assert len(request.state['catalog']) == len(bound.tool_selection.selected_names) == 180
    assert len(request.choices) < 128
    assert set(request.state['all_available']) == {entry['id'] for entry in request.state['catalog']}
    inventory[0]['function']['parameters']['new'] = True
    with pytest.raises(DecisionError, match='tool_catalog_changed'):
        bound.tool_selection.validate(bound, inventory)


@pytest.mark.asyncio
async def test_no_tools_is_typed_empty_and_repeated_seam_spends_once(store):
    store.update_chat('alice', 'a', {'tool_routing': 'jev'}, 0)
    provider = Provider({'tools': 'no_tools'})
    bound = await prepare_chat_turn(store, 'alice', session(), 'hello')
    selection = await prepare_tool_routing(bound, schemas(), provider=provider)
    assert bound.no_tools and selection.kind == 'no_tools' and selection.selected_names == frozenset()
    assert await prepare_tool_routing(bound, schemas(), provider=provider) is selection
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_manual_defaults_and_full_catalog_make_no_classifier_call(store):
    provider = Provider({})
    bound = await prepare_chat_turn(store, 'alice', session(), 'manual', tool_inventory=schemas(), decision_provider=provider)
    assert bound.tool_selection.kind == 'existing' and not provider.requests
    store.update_chat('alice', 'a', {'tool_routing': 'all'}, 0)
    bound = await prepare_chat_turn(store, 'alice', session(), 'all', tool_inventory=schemas(), decision_provider=provider)
    assert bound.tool_selection.kind == 'all_available' and len(bound.tool_selection.selected_names) == 3
    assert not provider.requests


@pytest.mark.asyncio
async def test_receipt_returned_prefix_and_foreign_sources(store):
    bound = await prepare_chat_turn(store, 'alice', session(), 'read')
    receipt = preparation_receipt(bound, [{'source_id': 'doc', 'version': 'v2', 'excerpt': 'prefix', 'coverage': 'prefix'}])
    assert receipt.public()['sources'][0]['hash_scope'] == 'returned_excerpt'
    assert 'excerpt' not in receipt.public()['sources'][0]
    with pytest.raises(DecisionError, match='preparation_source_invalid'):
        preparation_receipt(bound, [{'owner': 'bob', 'source_id': 'doc', 'version': 'v1', 'excerpt': 'secret'}])


@pytest.mark.asyncio
async def test_complete_oversize_wire_refuses_before_http_or_reservation(store):
    from src.decisions import DecisionProvider, ResolvedDecisionEndpoint
    from src.chat_automation import SqlAttemptStore
    store.update_chat('alice', 'a', {'tool_routing': 'jev'}, 0)
    bound = await prepare_chat_turn(store, 'alice', session(), '  Preserve exact ask  ')
    calls = []
    provider = DecisionProvider(SqlAttemptStore(store), resolver=lambda *_: ResolvedDecisionEndpoint(
        'reviewer', 'neutral-decider', 'systemone', 'https://decision.invalid', {}),
        client_factory=lambda **kwargs: calls.append(kwargs))
    bound.configuration['decision_provider'] = {'endpoint_id': 'reviewer', 'model': 'neutral-decider', 'protocol': 'systemone'}
    with pytest.raises(DecisionError, match='tool_catalog_too_large'):
        await prepare_tool_routing(bound, schemas(500), provider=provider)
    assert calls == []
    with store.transaction() as db:
        assert db.query(d.DecisionAttempt).count() == 0


@pytest.mark.asyncio
async def test_actual_auto_route_and_independent_effort_bind_selected_endpoint(store, monkeypatch):
    from src import endpoint_resolver, model_auto
    candidates=[{'endpoint_id':'chosen-0','model':'model-0'}, {'endpoint_id':'chosen-1','model':'model-1'}]
    def registered(owner,item):
        identifier,model=item['endpoint_id'],item['model']
        return {'identity':item,'route':('https://'+identifier+'.invalid/v1',model,{'X-Registered':identifier}),
            'supported_efforts':['low','high'],'label':identifier}
    monkeypatch.setattr(model_auto,'registered_candidate',registered)
    monkeypatch.setattr(model_auto,'validate_registered_candidates',lambda *a,**kw:None)
    monkeypatch.setattr(endpoint_resolver,'resolve_endpoint_by_id',lambda identifier,model,**kw:registered('alice',{'endpoint_id':identifier,'model':model})['route'])
    store.update_configuration('alice', {'decision_provider': {
        'endpoint_id':'decision','model':'neutral-decider','protocol':'systemone'}},0)
    store.update_chat('alice','a',{'route_mode':'auto','auto_categories':dict(zip(model_auto.CATEGORIES,candidates))},0)
    provider=Provider({'execution_model':'straightforward_tasks','reasoning_effort':'high'})
    bound=await prepare_chat_turn(store,'alice',session(),'A short novel correctness question',decision_provider=provider)
    assert len(provider.requests)==1
    assert provider.requests[0].state['categories']['straightforward_tasks']['endpoint_id']=='chosen-1'
    assert bound.descriptor['reasoning_effort']=='high'
    assert bound.descriptor['endpoint_id']=='chosen-1'
    assert bound.endpoint_url=='https://chosen-1.invalid/v1'
    assert bound.headers=={'X-Registered':'chosen-1'}
    assert bound.approach_selection['choice']=='straightforward_tasks'
    assert bound.effort_selection['choice']=='high'
    assert bound.descriptor['supported_efforts']==['low','high']
    assert bound.descriptor['effort_support_source']=='registered_runtime'
    import httpx
    import json
    from src import llm_core
    calls = []
    def execution(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'model': 'model-1', 'choices': [{'message': {'content': 'done'}}]})
    monkeypatch.setattr(llm_core, '_get_cached_response', lambda *a: None)
    monkeypatch.setattr(llm_core, '_set_cached_response', lambda *a, **kw: None)
    monkeypatch.setattr(llm_core, 'get_context_length', lambda *a, **kw: 32768)
    monkeypatch.setattr(llm_core, '_is_host_dead', lambda *a: False)
    monkeypatch.setattr(llm_core, 'note_model_activity', lambda *a, **kw: None)
    async with httpx.AsyncClient(transport=httpx.MockTransport(execution)) as client:
        monkeypatch.setattr(llm_core, '_get_http_client', lambda: client)
        with bound.activate():
            assert await llm_core.llm_call_async(bound.endpoint_url, 'model-1',
                [{'role': 'user', 'content': bound.raw_ask}], headers=bound.headers,
                session_id=bound.session_id, reasoning_effort='high') == 'done'
    assert len(calls) == 1 and calls[0]['reasoning_effort'] == 'high'
    assert calls[0]['messages'] == [{'role': 'user', 'content': bound.raw_ask}]



@pytest.mark.asyncio
async def test_manual_auto_effort_does_not_spend_and_unknown_supported_efforts_preserve_mapping(store):
    from src.decision_routing import select_effort
    store.update_chat('alice', 'a', {'effort_mode': 'auto'}, 0)
    provider = Provider({})
    bound = await prepare_chat_turn(store, 'alice', session(), 'manual', decision_provider=provider)
    assert not provider.requests
    assert await select_effort(bound, {'reasoning_effort': 'low'}, None, provider=provider) == 'low'
    assert bound.effort_selection['status'] == 'unavailable' and not provider.requests


@pytest.mark.asyncio
async def test_preparation_diagnostics_consumed_by_actual_admission_without_tool_inventory(store):
    store.update_chat('alice', 'a', {'preparation_enabled': True}, 0)
    provider = Provider({'online_search': 'no', 'fresh_information': 'no', 'project_context': 'yes'})
    bound = await prepare_chat_turn(store, 'alice', session(), 'Explain the supplied project summary',
        preparation={'sources': [{'source_id': 'summary', 'version': '1', 'excerpt': 'Maintained summary',
                                  'coverage': 'complete', 'trust': 'owner_maintained_summary'}]},
        decision_provider=provider)
    assert len(provider.requests) == 1 and bound.tool_selection is None
    assert provider.requests[0].state['preparation']['sources'][0]['excerpt'] == 'Maintained summary'
    assert bound.diagnostics['project_context']['choice'] == 'yes'
    await prepare_tool_routing(bound, schemas(), provider=provider)
    assert len(provider.requests) == 1 and bound.tool_selection.kind == 'existing'
