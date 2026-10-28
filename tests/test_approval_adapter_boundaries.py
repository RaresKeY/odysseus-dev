"""Complete mock HTTP provider adapters: no live compatibility claim."""
import json
import httpx
import pytest
from core import database as d
from tests.test_chat_automation_state import store
from tests.test_chat_approval_sql_integration import dispatch_runtime
from tests.test_approval_fallback import install_fallback, LABELS
from src.chat_automation import prepare_chat_turn

URLS = {'openai': 'https://models.invalid/v1', 'anthropic':'https://api.anthropic.com/v1/messages',
        'ollama':'http://localhost:11434/api/chat', 'responses':'https://chatgpt.com/backend-api/codex'}


def answer(protocol, fault):
    text = json.dumps({**LABELS, 'uncertain':False})
    usage = {'input_tokens':30, 'output_tokens':10}
    if protocol == 'openai':
        raw = {'model':'manual','usage':{'prompt_tokens':30,'completion_tokens':10},
            'choices':[{'finish_reason':'stop','message':{'content':text}}]}
        if fault == 'refusal': raw['choices'][0]['message']['refusal'] = 'Refused'
        if fault == 'truncated': raw['choices'][0]['finish_reason'] = 'length'
    elif protocol == 'anthropic':
        raw = {'model':'manual','usage':usage, 'stop_reason':'end_turn','content':[{'type':'text','text':text}]}
        if fault == 'refusal': raw['content'] = [{'type':'refusal','text':'Refused'}]
        if fault == 'truncated': raw['stop_reason'] = 'max_tokens'
    elif protocol == 'ollama':
        raw = {'model':'manual','prompt_eval_count':30,'eval_count':10,
            'done':True,'done_reason':'stop','message':{'content':text}}
        if fault == 'refusal': raw['message']['refusal'] = 'Refused'
        if fault == 'truncated': raw['done_reason'] = 'length'
    else:
        raw = {'model':'manual','usage':usage,'status':'completed',
            'output':[{'type':'message','content':[{'type':'output_text','text':text}]}]}
        if fault == 'refusal': raw['output'][0]['content'] = [{'type':'refusal','refusal':'Refused'}]
        if fault == 'truncated': raw['status'] = 'incomplete'
    if fault == 'substitution': raw['model'] = 'other-model'
    if fault == 'missing_usage':
        raw.pop('usage', None); raw.pop('eval_count', None)
    if fault == 'duplicate_json':
        text = text[:-1] + ',"risk_level":"low"}'
        if protocol == 'openai': raw['choices'][0]['message']['content'] = text
        elif protocol == 'anthropic': raw['content'][0]['text'] = text
        elif protocol == 'ollama': raw['message']['content'] = text
        else: raw['output'][0]['content'][0]['text'] = text
    return raw


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', URLS)
@pytest.mark.parametrize('fault', ['refusal','truncated','substitution','missing_usage','duplicate_json'])
async def test_adapter_failures_hold_or_charge_and_never_dispatch(store, dispatch_runtime, monkeypatch, protocol, fault):
    rt = dispatch_runtime
    rt.session.endpoint_url = URLS[protocol]
    with store.transaction() as db: db.get(d.Session,'a').endpoint_url = URLS[protocol]
    store.update_configuration('alice', {'decision_provider':None},1)
    store.update_chat('alice','a',{'approval_mode':'auto_approve'},0)
    calls = []
    def transport(request):
        calls.append(request)
        raw = answer(protocol, fault)
        if protocol == 'responses':
            return httpx.Response(200, content='data: '+json.dumps({'type':'response.completed','response':raw})+'\n\n')
        return httpx.Response(200,json=raw)
    install_fallback(monkeypatch, transport)
    bound = await prepare_chat_turn(store,'alice',rt.session,'Write fixture.')
    _, result = await rt.dispatch(bound)
    assert result['blocked'] and len(calls) == 1 and not rt.effects
    [record] = rt.attempts.records_for_run('alice','a',bound.run_id)
    assert record['status'] == ('unresolved' if fault == 'missing_usage' else 'rejected')
    assert record['usage'] == (None if fault == 'missing_usage' else {'input_tokens':30,'output_tokens':10})


@pytest.mark.parametrize('protocol,url,model,reported,expected', [
    ('approval_ollama', URLS['ollama'],'manual','manual:latest',True),
    ('approval_ollama', URLS['ollama'],'manual:fixed','manual:latest',False),
    ('approval_openai','https://generativelanguage.googleapis.com/v1beta/openai/chat/completions','models/gemini-2.5-flash','gemini-2.5-flash',True),
    ('approval_openai',URLS['openai'],'models/manual','manual',False),
    ('approval_openai',URLS['openai'],'gpt-4o','gpt-4o-2024-08-06',False),
    ('approval_anthropic',URLS['anthropic'],'claude-latest','claude-20261028',False),
])
def test_only_deterministic_provider_model_spellings_are_equivalent(protocol,url,model,reported,expected):
    from src.approval_fallback import ConversationReviewProvider, ConversationEndpoint
    endpoint = ConversationEndpoint('selected',model,protocol,url,{})
    assert ConversationReviewProvider._same_response_model(endpoint, reported) is expected


def test_anthropic_cache_input_is_real_usage():
    from src.approval_fallback import ConversationReviewProvider, ConversationEndpoint
    endpoint = ConversationEndpoint('selected','manual','approval_anthropic',URLS['anthropic'],{})
    client = object.__new__(ConversationReviewProvider)
    assert client._usage({'usage':{'input_tokens':30,'output_tokens':10,'cache_creation_input_tokens':7,'cache_read_input_tokens':9}},endpoint) == {'input_tokens':46,'output_tokens':10}
