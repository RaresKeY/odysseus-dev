import assert from 'node:assert/strict';
import test from 'node:test';
import {decisionSummary, compactDecisionSummary} from '../static/js/decision-activity.js';

test('approval activity shows explicit assessments and fixed provenance', () => {
    const text = decisionSummary({purpose: 'approval', status: 'settled',
        usage: {input_tokens: 25, output_tokens: 8}, questions: {},
        approval: {decision_source: 'jev', risk_level: 'high', user_authorization: 'medium',
            disposition: 'approved', policy: 'odysseus-approval-review/v2',
            reason: 'High risk, with sufficient authorization and a narrow scope.'}});
    assert.match(text, /risk high/);
    assert.match(text, /authorization medium/);
    assert.match(text, /jev/);
    assert.match(text, /25 in \/ 8 out/);
    assert.match(text, /narrow scope/);
});

test('bypass never implies an assessed low risk or model usage', () => {
    const text = decisionSummary({purpose: 'approval', status: 'settled',
        usage: null, usage_provenance: 'not_applicable', questions: {},
        approval: {decision_source: 'yolo', assessed: false, disposition: 'bypass',
            policy: 'odysseus-approval-review/v2'}});
    assert.match(text, /yolo/);
    assert.match(text, /risk unassessed/);
    assert.match(text, /authorization unassessed/);
    assert.match(text, /no model call/);
    assert.doesNotMatch(text, /risk low|unknown \(held\)/);
});

test('old activity remains readable without invented classifications', () => {
    const text = decisionSummary({purpose: 'approval', status: 'settled', usage: null});
    assert.match(text, /assessment unavailable \(legacy\)/);
    assert.match(text, /usage unknown \(held\)/);
    assert.doesNotMatch(text, /risk low|authorization high/);
});

test('conversation fallback identifies its model without fabricated confidence', () => {
    const text = decisionSummary({purpose: 'approval', status: 'settled', questions: {},
        usage: {input_tokens: 30, output_tokens: 10},
        provider: {name: 'anthropic', model: 'selected-model', distribution_source: 'not_reported'},
        approval: {decision_source: 'conversation_model', risk_level: 'low', user_authorization: 'high',
            policy: 'odysseus-approval-review/v2', fallback_reason: 'decision_http_error'}});
    assert.match(text, /anthropic.*selected-model/);
    assert.match(text, /conversation_model/);
    assert.match(text, /fallback: decision_http_error/);
    assert.doesNotMatch(text, /confidence|0%/);
});


test('ordinary approval rows stay concise while details retain route and policy', () => {
    const item = {purpose: 'approval', status: 'settled', questions: {},
        usage: {input_tokens: 30, output_tokens: 10},
        provider: {name: 'synthetic', model: 'selected-model', distribution_source: 'not_reported'},
        approval: {decision_source: 'conversation_model', risk_level: 'low', user_authorization: 'high',
            disposition: 'approved', policy: 'odysseus-approval-review/v2', fallback_reason: 'decision_http_error'}};
    const brief = compactDecisionSummary(item), details = decisionSummary(item);
    assert.match(brief, /Conversation review.*approved.*risk low.*authorization high.*30 in \/ 10 out/);
    assert.doesNotMatch(brief, /odysseus-approval-review|decision_http_error|selected-model|not_reported/);
    assert.match(details, /selected-model.*decision_http_error.*odysseus-approval-review/);
    assert.match(compactDecisionSummary({...item, usage: null}), /usage unknown \(held\)/);
});


test('review details distinguish selected and actually reported accepted model IDs', () => {
    const text = decisionSummary({purpose:'approval',status:'settled',usage:{input_tokens:1,output_tokens:1},
        provider:{protocol:'approval_openai',model:'gpt-4o',reviewed_model:'gpt-4o-2024-08-06',distribution_source:'not_reported'}});
    assert.match(text,/gpt-4o \/ reported gpt-4o-2024-08-06/);
});
