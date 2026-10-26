import {decisionSummary, distributionSummary, decisionExportPath} from '../static/js/decision-activity.js';

function assert(condition, message) { if (!condition) throw new Error(message); }

Deno.test('decision display preserves native known usage, low scores and failure status', () => {
  const text = decisionSummary({purpose:'model_auto',status:'failed',usage:{input_tokens:17,output_tokens:0},
    failure_kind:'unsupported_effort',questions:{reasoning_effort:{choice:'high',confidence:.41}},
    provider:{protocol:'systemone',distribution_source:'provider_reported'}});
  assert(text.includes('17 in / 0 out'), 'Known zero is distinct from missing usage');
  assert(text.includes('high: 41% confidence'), 'Low routing confidence remains inspectable');
  assert(text.includes('failed') && text.includes('unsupported_effort'), 'Failure must remain visible');
  assert(text.includes('systemone / provider_reported'), 'Native provenance remains visible');
});
Deno.test('missing and partial usage never display fabricated zero or success', () => {
  const missing = decisionSummary({purpose:'approval_review',status:'reserved',usage:null});
  assert(missing.includes('usage unknown (held)'), 'Unknown spend remains held');
  const partial = decisionSummary({purpose:'approval_review',status:'failed',usage:{input_tokens:23,output_tokens:null}});
  assert(partial.includes('23 in / unknown out') && !partial.includes('0 out'), 'Partial native usage stays partial');
});
Deno.test('reported distribution keeps every question and offered choice', () => {
  const text = distributionSummary({execution_model:{probabilities:{complex_tasks:.8,straightforward_tasks:.2,unavailable:0}},
    reasoning_effort:{probabilities:{low:.49,high:.51,unavailable:0}}});
  for(const part of ['execution_model:','complex_tasks 80%','straightforward_tasks 20%','reasoning_effort:','low 49%','high 51%','unavailable 0%']) assert(text.includes(part), part);
});
Deno.test('metadata and redacted capture exports stay in the exact owned read-only routes', () => {
  const session='chat/other?x#y',run='run/other?z#k';
  assert(decisionExportPath(session,run)==='/api/session/chat%2Fother%3Fx%23y/decisions/run%2Fother%3Fz%23k/export','Exact encoded metadata route');
  assert(decisionExportPath(session,run,true).endsWith('/run%2Fother%3Fz%23k/capture'),'Capture is a separate explicit download');
});
