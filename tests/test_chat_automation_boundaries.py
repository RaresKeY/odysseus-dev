"""Live preparation/elevation boundaries through the canonical dispatcher."""
import json
from types import SimpleNamespace
import pytest
from core import database as d
from src.agent_runtime.authority import RequestAuthority, OperationGrant
from src.agent_runtime.resources import FilesystemRoot
from src.chat_automation import prepare_chat_turn
from src.tool_capabilities import ToolRunSecurityContext
from src.tool_types import ToolBlock
from tests.test_chat_automation_state import store


def session(sid='a'):
    return SimpleNamespace(id=sid,model='manual',endpoint_id=None,endpoint_url='http://synthetic.invalid/v1',headers={})


def test_expired_preparation_requires_its_live_lease(store):
    now=[100.0];store.clock=lambda:now[0]
    bound=store.acquire('alice','a','original ask','interactive')
    assert bound.current()
    now[0]=191
    assert not bound.current()
    with store.transaction() as db:
        assert db.query(d.ChatAutomationSubmission).filter_by(submission_id=bound.submission_id).one().status=='preparing'


def test_inherited_process_scope_cannot_add_writable_roots(tmp_path):
    from dataclasses import replace
    from src.agent_runtime.resources import ProcessLaunchScope,NativeBackendResource,PathObservation,FileObjectIdentity
    from src.agent_runtime.process_resources import intersect_launch_scopes
    from src.containment import DEFAULT_REQUIRED
    workspace=tmp_path/'w';workspace.mkdir();extra=tmp_path/'extra';extra.mkdir()
    parent=ProcessLaunchScope(NativeBackendResource('bash'),FilesystemRoot.seal(workspace,owner='alice'),DEFAULT_REQUIRED)
    child=replace(parent,writable_roots=(PathObservation(str(extra),FileObjectIdentity.observe(extra)),))
    assert intersect_launch_scopes((parent,),(child,))==()
    assert intersect_launch_scopes((child,),(child,))==(child,)
    assert 'writable_roots' not in parent.to_dict()
    assert ProcessLaunchScope.from_dict(parent.to_dict())==parent


@pytest.mark.asyncio
async def test_same_mode_explicit_default_does_not_revoke_other_chat(store):
    a=store.policy('alice','a')
    store.update_chat('alice','b',{'approval_mode':'yolo'},0)
    other=await prepare_chat_turn(store,'alice',session('b'),'Keep this turn running')
    result=store.update_chat('alice','a',{'approval_mode':'always_ask'},a['policy_revision'])
    assert result['policy_revision']==a['policy_revision']
    assert store.configuration('alice')['last_explicit_mode']=='always_ask'
    assert other.current() and store.policy('alice','b')['approval_mode']=='yolo'


@pytest.mark.asyncio
@pytest.mark.parametrize('access,tool', [('read','read_file'),('write','write_file'),('read','write_file')])
async def test_expired_elevation_cannot_use_previously_sealed_roots(store,tmp_path,monkeypatch,access,tool):
    from src import settings,tool_execution
    workspace=tmp_path/'workspace';workspace.mkdir()
    extra=tmp_path/'extra';extra.mkdir();(extra/'note.txt').write_text('before')
    now=[100.0];store.clock=lambda:now[0]
    monkeypatch.setattr(settings,'get_setting',lambda name,default=None:{'agent_elevation_enabled':True,'agent_elevation_roots':[str(extra)]}.get(name,default))
    store.update_configuration('alice',{'elevation_ceiling':True},0)
    store.update_chat('alice','a',{'approval_mode':'yolo','elevation_roots':[{'path':str(extra),'access':access}]},0)
    bound=await prepare_chat_turn(store,'alice',session(),'Use the explicitly granted folder')
    roots=(FilesystemRoot.seal(workspace,owner='alice'),FilesystemRoot.seal(extra,owner='alice'))
    authority=RequestAuthority('request','alice','a',str(workspace),(OperationGrant(tool),),resource_roots=roots)
    effects=[]
    async def executor(*args,**kwargs):effects.append(args);return 'effect',{'exit_code':0}
    monkeypatch.setattr(tool_execution,'_execute_tool_block_impl',executor)
    content=str(extra/'note.txt') if tool=='read_file' else json.dumps({'path':str(extra/'note.txt'),'content':'after'})
    now[0]=3701
    with bound.activate():
        _,result=await tool_execution.execute_tool_block(ToolBlock(tool,content),owner='alice',session_id='a',
            workspace=str(workspace),request_authority=authority,security_context=ToolRunSecurityContext())
    assert result.get('blocked') and not effects
    assert (extra/'note.txt').read_text()=='before'


@pytest.mark.asyncio
async def test_readonly_grant_is_not_writable_before_expiry(store,tmp_path,monkeypatch):
    from src import settings,tool_execution
    extra=tmp_path/'extra';extra.mkdir();(extra/'n').write_text('before')
    monkeypatch.setattr(settings,'get_setting',lambda name,default=None:{'agent_elevation_enabled':True,'agent_elevation_roots':[str(extra)]}.get(name,default))
    store.update_configuration('alice',{'elevation_ceiling':True},0)
    store.update_chat('alice','a',{'approval_mode':'yolo','elevation_roots':[{'path':str(extra),'access':'read'}]},0)
    bound=await prepare_chat_turn(store,'alice',session(),'Read the folder')
    authority=RequestAuthority('request','alice','a',str(tmp_path),(OperationGrant('write_file'),),resource_roots=(FilesystemRoot.seal(extra,owner='alice'),))
    with bound.activate():
        _,result=await tool_execution.execute_tool_block(ToolBlock('write_file',json.dumps({'path':str(extra/'n'),'content':'after'})),
            owner='alice',session_id='a',workspace=str(tmp_path),request_authority=authority,security_context=ToolRunSecurityContext())
    assert result.get('blocked') and result['failure_kind']=='elevation_scope_denied'
    assert (extra/'n').read_text()=='before'
