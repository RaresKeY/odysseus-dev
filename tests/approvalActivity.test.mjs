import assert from 'node:assert/strict';
import test from 'node:test';
import {decisionSummary} from '../static/js/decision-activity.js';

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
