"""General selection binds the real provider, context and dispatcher owners."""
import json
from types import SimpleNamespace
from dataclasses import replace

import httpx
import pytest

from tests.test_chat_automation_state import store
from tests.test_chat_automation_ingress import runtime
from src.chat_automation import prepare_chat_turn, SqlAttemptStore
from src.decisions import DecisionProvider, ResolvedDecisionEndpoint
from src.decision_routing import prepare_tool_routing
from src.agent_runtime.authority import OperationGrant, RequestAuthority
from src.tool_capabilities import ToolRunSecurityContext
from src.tool_types import ToolBlock
from src.tool_policy import ToolPolicy
from src.chat_tool_routing import selected_policy, selected_contract
from src.turn_contract import resolve_full_inventory_contract


CONFIG={'endpoint_id':'review','model':'decision','protocol':'systemone'}


@pytest.mark.asyncio
async def test_no_tools_real_sql_provider_dispatch_rejects_native_text_and_alias(store,tmp_path,monkeypatch):
    from src import tool_execution
    store.update_configuration('alice',{'decision_provider':CONFIG},0)
    store.update_chat('alice','a',{'tool_routing':'jev','approval_mode':'yolo'},0)
    session=SimpleNamespace(id='a',endpoint_id=None,endpoint_url='https://execution.invalid/v1',headers={},model='manual')
    bound=await prepare_chat_turn(store,'alice',session,'  Explain the file without doing anything.\n ')
    calls=[]
    def decision(request):
        payload=json.loads(request.content);calls.append(payload)
        options=payload['questions']['tools']['criteria']
        return httpx.Response(200,json={'model':'decision','answers':{'tools':{
            'type':'choice','choice':'no_tools','confidence':.99,
            'probabilities':{k:.99 if k=='no_tools' else .01/(len(options)-1) for k in options}}},
            'usage':{'input_tokens':32,'output_tokens':8}})
    provider=DecisionProvider(SqlAttemptStore(store),resolver=lambda *_:ResolvedDecisionEndpoint(
        'review','decision','systemone','https://synthetic.invalid/v1/systemone',{}),
        client_factory=lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(decision),**kw))
    schemas=[{'type':'function','function':{'name':name,'description':'Synthetic operation',
        'parameters':{'type':'object','properties':{}}}} for name in ['write_file','read_file','send_email','mcp__email__send_email']]
    await prepare_tool_routing(bound,schemas,provider=provider)
    bound._tool_routing_inventory=schemas
    policy=selected_policy(ToolPolicy(),bound)
    contract=selected_contract(resolve_full_inventory_contract(schemas=schemas,policy=ToolPolicy()),bound,policy)
    assert policy.block_all_tool_calls and not contract.offered
    effects=[]
    async def executor(*args,**kwargs):effects.append(True);return 'executed',{'exit_code':0}
    monkeypatch.setattr(tool_execution,'_execute_tool_block_impl',executor)
    authority=RequestAuthority('fixture','alice','a',str(tmp_path),tuple(OperationGrant(n) for n in ['write_file','read_file','send_email']))
    with bound.activate():
        for name in ['write_file','read_file','send_email','mcp__email__send_email']:
            _,result=await tool_execution.execute_tool_block(ToolBlock(name,'{}'),owner='alice',session_id='a',
                workspace=str(tmp_path),request_authority=authority,security_context=ToolRunSecurityContext())
            assert result['blocked'] and result['executed'] is False
            assert result['failure_kind']=='tool_selection_denied'
    assert len(calls)==1 and not effects
    assert calls[0]['state']['ask']==bound.raw_ask
    assert SqlAttemptStore(store).records_for_run('alice','a',bound.run_id)[0]['usage']=={'input_tokens':32,'output_tokens':8}


@pytest.mark.asyncio
async def test_selected_catalog_cannot_be_changed_after_classification(store):
    store.update_chat('alice','a',{'tool_routing':'all'},0)
    session=SimpleNamespace(id='a',endpoint_id=None,endpoint_url='https://execution.invalid/v1',headers={},model='manual')
    bound=await prepare_chat_turn(store,'alice',session,'List files')
    schemas=[{'type':'function','function':{'name':'read_file','description':'Read','parameters':{}}}]
    await prepare_tool_routing(bound,schemas)
    bound._tool_routing_inventory=schemas
    schemas[0]['function']['description']='Changed after classification'
    from src.decisions import DecisionError
    with pytest.raises(DecisionError):
        selected_contract(resolve_full_inventory_contract(schemas=schemas,policy=ToolPolicy()),bound,ToolPolicy())


@pytest.mark.asyncio
async def test_explicit_selection_preserves_compact_runtime_stamp(store):
    from src.agent_runtime.runtime_selection import COMPACT_PREVIEW_MODE, is_compact_preview_contract
    store.update_chat('alice','a',{'tool_routing':'all'},0)
    session=SimpleNamespace(id='a',endpoint_id=None,endpoint_url='https://execution.invalid/v1',headers={},model='manual')
    bound=await prepare_chat_turn(store,'alice',session,'Read the file')
    schemas=[{'type':'function','function':{'name':'read_file','description':'Read','parameters':{}}}]
    await prepare_tool_routing(bound,schemas);bound._tool_routing_inventory=schemas
    contract=replace(resolve_full_inventory_contract(schemas=schemas,policy=ToolPolicy()),selection_mode=COMPACT_PREVIEW_MODE)
    assert is_compact_preview_contract(selected_contract(contract,bound,ToolPolicy()))


@pytest.mark.asyncio
async def test_alias_required_read_conflict_is_visible_without_invalid_contract(store):
    from src.turn_contract import TurnContract,RequiredReadOperation
    store.update_chat('alice','a',{'tool_routing':'all'},0)
    session=SimpleNamespace(id='a',endpoint_id=None,endpoint_url='https://execution.invalid/v1',headers={},model='manual')
    bound=await prepare_chat_turn(store,'alice',session,'Read that email')
    names=['mcp__email__read_email','ask_user']
    schemas=[{'type':'function','function':{'name':name,'description':'Read','parameters':{}}} for name in names]
    await prepare_tool_routing(bound,schemas);bound._tool_routing_inventory=schemas
    bound.tool_selection=replace(bound.tool_selection,kind='bundle',selected_names=frozenset({'ask_user'}))
    contract=TurnContract(frozenset({'email'}),frozenset({'mcp__email__read_email'}),frozenset(names),frozenset(names),frozenset(),
        tuple(json.dumps(s) for s in schemas),required_read_operation=RequiredReadOperation('mcp__email__read_email',{'uid':'one'}))
    resolved=selected_contract(contract,bound,ToolPolicy())
    assert not resolved.offered and 'read_email' in resolved.unavailable


@pytest.mark.asyncio
async def test_actual_stream_decision_precedes_context_and_dispatch(runtime,monkeypatch,tmp_path):
    from routes import chat_routes
    from src import decisions,tool_execution
    from src.chat_automation import current_turn
    runtime.store.update_configuration('alice',{'decision_provider':CONFIG},0)
    runtime.store.update_chat('alice','a',{'tool_routing':'jev','approval_mode':'yolo'},0)
    calls=[];preprocess=[];effects=[]
    def classify(request):
        payload=json.loads(request.content);calls.append(payload)
        answers={name:{'type':'choice','choice':'no_tools','confidence':.99,
            'probabilities':{key:.99 if key=='no_tools' else .01/(len(question['criteria'])-1) for key in question['criteria']}}
            for name,question in payload['questions'].items()}
        return httpx.Response(200,json={'model':'decision','answers':answers,'usage':{'input_tokens':30,'output_tokens':5}})
    provider=DecisionProvider(SqlAttemptStore(runtime.store),resolver=lambda *_:ResolvedDecisionEndpoint(
        'review','decision','systemone','https://synthetic.invalid/v1/systemone',{}),
        client_factory=lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(classify),**kw))
    monkeypatch.setattr(decisions,'DecisionProvider',lambda *args,**kwargs:provider)
    original=chat_routes.build_chat_context
    async def context(*args,**kwargs):
        preprocess.append(kwargs.get('allow_tool_preprocessing'));assert calls
        return await original(*args,**kwargs)
    monkeypatch.setattr(chat_routes,'build_chat_context',context)
    async def executor(*args,**kwargs):effects.append(True);return 'effect',{'exit_code':0}
    monkeypatch.setattr(tool_execution,'_execute_tool_block_impl',executor)
    async def agent(*args,**kwargs):
        bound=current_turn();assert bound.tool_selection.kind=='no_tools'
        authority=RequestAuthority('fixture','alice','a','',(OperationGrant('write_file'),))
        _,result=await tool_execution.execute_tool_block(ToolBlock('write_file','{}'),owner='alice',session_id='a',
            request_authority=authority,security_context=ToolRunSecurityContext())
        assert result['failure_kind']=='tool_selection_denied'
        yield 'data: '+json.dumps({'delta':'Explanation only.'})+'\n\n'
        yield 'data: [DONE]\n\n'
    monkeypatch.setattr(chat_routes,'stream_agent_loop',agent)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app),base_url='http://test') as client:
        response=await client.post('/api/chat_stream',json={'session':'a','message':'  Explain this file.\n ','mode':'agent'})
    assert response.status_code==200,response.text
    assert len(calls)==1 and preprocess==[False] and not effects
