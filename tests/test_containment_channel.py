"""Duplex producer launch uses the existing namespace ready/ack barrier."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
import pytest
from src import containment as c


class Writer:
    def __init__(self): self.writes=[];self.closed=False
    def write(self,data): self.writes.append(data)
    async def drain(self): pass
    def close(self): self.closed=True


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_after_ready',[False,True])
async def test_channel_keeps_stdin_and_serializes_owned_close(monkeypatch,tmp_path,fail_after_ready):
    spec=c.agent_spec(str(tmp_path),{},10)
    grant=c.ContainmentGrant('grant','bubblewrap',str(tmp_path),spec.required,(),(),'owner',c.MODE_ENFORCING,spec)
    proc=SimpleNamespace(pid=987654,stdin=Writer(),stdout=asyncio.StreamReader(),stderr=asyncio.StreamReader())
    released=[]; records=[]
    monkeypatch.setattr(c,'MECHANISMS',[SimpleNamespace(name='bubblewrap',provides=lambda s:s.required)])
    monkeypatch.setattr(c,'_load_records',lambda:{})
    monkeypatch.setattr(c,'_spawn_kwargs',lambda g:{})
    monkeypatch.setattr(c,'_update_record',lambda *a,**k:records.append(k))
    monkeypatch.setattr(c.process_ownership,'capture',lambda pid:{'start_token':'same-process'})
    monkeypatch.delattr(c.os,'pidfd_open',raising=False)
    def launch(g,command,**kwargs):
        proc.stdout.feed_data((kwargs['ready_marker']+'\n').encode())
        return command
    monkeypatch.setattr(c,'_launch_argv',launch)
    async def spawn(*args,**kwargs):return proc
    async def identity(p):
        c.os.close(p._ody_info_read)
        p._ody_namespace_pid=987655;p._ody_namespace_token='namespace';p._ody_info_read=None
    async def teardown(g,p):
        released.append(g)
        return c.ReleaseOutcome(dead=True,escalated=False)
    monkeypatch.setattr(c.asyncio,'create_subprocess_exec',spawn)
    monkeypatch.setattr(c,'_capture_namespace_identity',identity)
    monkeypatch.setattr(c,'_release_awaited',teardown)
    checks=[]
    def policy_check():
        checks.append(True)
        if fail_after_ready and len(checks)==2:raise ValueError('permissions changed')
    monkeypatch.setattr(c,'_validate_chat_dispatch',policy_check)
    if fail_after_ready:
        with pytest.raises(ValueError,match='permissions changed'):
            await c.open_channel(grant,['/trusted/producer'])
        assert len(released)==1 and proc._ody_info_read is None
        return
    channel=await c.open_channel(grant,['/trusted/producer'])
    assert not proc.stdin.closed and len(proc.stdin.writes)==1
    assert records[-1]['execution_started'] is True
    assert channel.grant.namespace_start_token=='namespace'
    one,two=await asyncio.gather(channel.close(),channel.close())
    assert one.dead and two.dead and len(released)==1


@pytest.mark.asyncio
async def test_channel_refuses_degraded_or_external_grant_before_spawn(monkeypatch,tmp_path):
    spec=c.agent_spec(str(tmp_path),{},10)
    grant=c.ContainmentGrant('grant','posix_group',str(tmp_path),frozenset(),(),tuple(spec.required),'owner',c.MODE_ENFORCING,spec)
    monkeypatch.setattr(c,'_load_records',lambda:{})
    with pytest.raises(c.ContainmentUnavailable):await c.open_channel(grant,['/trusted/producer'])
    with pytest.raises(ValueError):await c.open_channel(grant,'model shell text')
