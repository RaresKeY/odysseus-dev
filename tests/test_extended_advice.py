"""Actual sealed cards and canonical mocked HTTP; advice never consumes them."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
import json

import httpx
import pytest
from fastapi import HTTPException

from tests.test_approval_review import proposal, CONFIG
from src.decisions import DecisionProvider, MemoryAttemptStore, ResolvedDecisionEndpoint
from src.decision_advice import SafetyAdviceService, completion_advice
from src.agent_evidence import CompletionDecision, CompletionStatus, CompletionRequirements


def provider_for(choice, choices, *, mutate=None, fail=False):
    calls=[]
    def transport(request):
        calls.append(json.loads(request.content))
        if mutate: mutate()
        if fail: raise httpx.ReadTimeout('private sentinel')
        probabilities={k: .98 if k==choice else .02/(len(choices)-1) for k in choices}
        return httpx.Response(200,json={'model':'decision-model','answers':{'decision':{
            'type':'choice','choice':choice,'confidence':.98,'probabilities':probabilities}},
            'usage':{'input_tokens':21,'output_tokens':3}})
    attempts=MemoryAttemptStore()
    endpoint=ResolvedDecisionEndpoint('reviewer','decision-model','systemone','https://synthetic.invalid/v1/systemone',{})
    return DecisionProvider(attempts,resolver=lambda *_:endpoint,
        client_factory=lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(transport),**kw)),calls


def bound_turn(policy):
    return SimpleNamespace(owner='alice',session_id='chat',run_id='run',raw_ask='  exact task\n ',
        policy=policy,configuration={'decision_provider':CONFIG,'completion_advice_enabled':True},current=lambda:True)


@pytest.mark.asyncio
async def test_safety_real_http_memo_is_separate_from_independent_review(proposal):
    approvals,pending,policy,_=proposal
    pending=replace(pending,bound_turn=bound_turn(policy))
    approvals._pending[pending.approval_id]=pending
    provider,calls=provider_for('allow',['allow','deny','review'])
    service=SafetyAdviceService(approvals)
    first=await service.advise(pending.approval_id,owner='alice',session_id='chat',provider=provider)
    second=await service.advise(pending.approval_id,owner='alice',session_id='chat',provider=provider)
    assert first==second and first['advisory_only'] and len(calls)==1
    assert approvals.peek(pending.approval_id) is pending
    assert not pending._review_started.is_set()
    assert approvals.consume_reviewed(pending,owner='alice',session_id='chat',decision_attempt_id=first['attempt_id']) is None
    grant=approvals.consume(pending.approval_id,owner='alice',session_id='chat',decision='approve_once')
    assert grant is not None


@pytest.mark.asyncio
async def test_foreign_and_stale_cards_reject_before_http(proposal):
    approvals,pending,policy,cancelled=proposal
    pending=replace(pending,bound_turn=bound_turn(policy));approvals._pending[pending.approval_id]=pending
    provider,calls=provider_for('allow',['allow','deny','review'])
    service=SafetyAdviceService(approvals)
    with pytest.raises(HTTPException): await service.advise(pending.approval_id,owner='bob',session_id='chat',provider=provider)
    cancelled[0]=True
    with pytest.raises(HTTPException): await service.advise(pending.approval_id,owner='alice',session_id='chat',provider=provider)
    assert calls==[]


@pytest.mark.asyncio
async def test_changed_during_advice_and_timeout_never_auto_retry(proposal):
    approvals,pending,policy,cancelled=proposal
    pending=replace(pending,bound_turn=bound_turn(policy));approvals._pending[pending.approval_id]=pending
    service=SafetyAdviceService(approvals)
    provider,calls=provider_for('allow',['allow','deny','review'],fail=True)
    first=await service.advise(pending.approval_id,owner='alice',session_id='chat',provider=provider)
    again=await service.advise(pending.approval_id,owner='alice',session_id='chat',provider=provider)
    assert first==again and first['status']=='unavailable' and len(calls)==1
    assert approvals.peek(pending.approval_id) is pending


@pytest.mark.asyncio
async def test_completion_cannot_upgrade_failed_or_missing_evidence():
    bound=bound_turn({'policy_revision':0,'authority_epoch':0})
    provider,calls=provider_for('complete',['complete','incomplete','unknown'])
    ledger=SimpleNamespace(requirements=CompletionRequirements(),to_list=lambda:[])
    for status in [CompletionStatus.FAILED,CompletionStatus.UNVERIFIED,CompletionStatus.AWAITING_USER]:
        decision=CompletionDecision(status,False,'actual evidence missing')
        result,advice=await completion_advice(bound,decision,ledger,SimpleNamespace(actions=[]),'DONE',provider=provider)
        assert result is decision and advice['additional_provider_calls']==0
    assert calls==[]


@pytest.mark.asyncio
async def test_completion_actual_provider_can_only_veto():
    bound=bound_turn({'policy_revision':0,'authority_epoch':0})
    decision=CompletionDecision(CompletionStatus.VERIFIED,True,'verifier passed',('receipt-1',))
    ledger=SimpleNamespace(requirements=CompletionRequirements(),to_list=lambda:[{'event_id':'receipt-1','authoritative':True}])
    journal=SimpleNamespace(actions=['server-action'],to_list=lambda:[{'action_id':'server-action','dispatched':True}])
    provider,calls=provider_for('incomplete',['complete','incomplete','unknown'])
    result,advice=await completion_advice(bound,decision,ledger,journal,'Operations succeeded.',provider=provider)
    assert not result.can_complete and decision.can_complete
    assert len(calls)==1 and calls[0]['state']['original_ask']==bound.raw_ask
    assert advice['advisory_only']
