Last updated: dev@f8e043e | 2026-10-24

## Scope

`src/browser_pipe.py` owns the new `native:odysseus-cdp-pipe` producer, direct
NUL-delimited CDP pipe, trusted enrolment, captured document operations and human
control. `src/browser_pipe_observer.js` is fixed isolated-world source, retained
as a CDP object with private node references. `src/browser_automation.py` owns
bounded decision packets and orchestration; it receives the canonical decision
provider and child dispatcher from `src/tool_execution.py`.

`src/browser_identity.py` remains the canonical browser resource/operation
binding boundary. `src/agent_runtime/resources.py` distinguishes pipe process /
channel identity from agent-browser daemon / browser-GUID observations.
`PrivateBrowserTool` forwards through that boundary. Tool schemas and retrieval
text describe only supported opaque operations.

## Authority and lifecycle

- Enrolment is a trusted interactive-owner operation. Deployment supplies a
  root-owned immutable manifest containing exact installed Chromium and observer
  hashes, architecture and sandbox qualification receipt digest. Discovery and
  model arguments cannot select an executable, endpoint, script or manifest.
- Chromium uses private inherited pipe descriptors inside the canonical
  containment grant/startup/teardown lifecycle. Browser sandbox is enabled;
  unavailable required containment fails before registration. No cache search,
  remote debugging port, package download or alternate launch fallback exists.
- The browser owns one exact paused target and its flattened CDP session.
  Auto-attachment pauses other targets; they close without being resumed.
  Session identity includes owned process start token and independent channel
  nonce. It never pretends to be an agent-browser observation.
- A trusted, expiring `BrowserTaskGrant` provides exact owner/chat/run,
  origins, effects, prepared field text and original navigation URLs. Its
  immutable contents filter targets before ranking. It is not operation
  authority and cannot bypass deterministic admission or child approval.
- Each operation binds the actual session, target, frame, loader, isolated-world
  context unique ID and document epoch. CDP events invalidate continuously,
  including same-URL navigation, context reset and bfcache lifecycle events.
  Inside the pipe write lock, automated dispatch rechecks the bound turn's
  policy/owner/run revision, current task capability and captured observation.
- The isolated closure atomically checks document, navigation serial, nonce,
  retained node identity, current resolved href/form state and hit-test. Human
  trusted input invalidates its nonce. No default-world global, arbitrary
  selector, JavaScript, coordinate or model CDP method is accepted.
- Human control uses the same retained handles with an explicit owner scope.
  It revokes automated authority before awaiting execution and invalidates the
  document afterward. Snapshot captures a new nonce. Origin/method network
  scope may outlive the action only until the same short expiry; it grants no
  automated action authority.
- A response lost after an effect is sent yields `executed: null`,
  `outcome: indeterminate`, closes the owned channel and is never retried or
  retargeted. Stale observation/ref/owner/policy errors execute no effect.
- The existing agent-browser 0.35.0 page denial and identity-only `CDPSidecar`
  allowlist remain intact. Neither raw browser MCP nor agent-browser gains a
  generic execution bypass.

## Decisions and visible handoff

`private_browser` action `automate` binds a real enrolled page and trusted task
scope. Dispatch injects a server-only runtime with decision provider, admitted
configuration, bound `DecisionContext` and canonical child dispatcher. Every
chosen action crosses that dispatcher again. Prepared field values remain
local; matching requires the exact observed field label.

The driver filters origin/effect/field permission before ranking, offers at most
12 targets in a 12,000-byte decision packet, redacts prepared text, and has a
20-step default / 100-step hard cap plus 120-second maximum wall budget.
Uncertain/invalid decisions, repeated packets, stale documents, unavailable
review and human cards stop the loop. There is no inference retry. A pending
human card retains only its existing expiring task scope for exact continuation.

Every terminal result names actor, stop reason, counts, decision attempt usage
(including unknown usage), untrusted completion-check provenance and required
handoff. A model's DONE is not verified completion. Screenshots are actual PNG
bytes from the captured target and are stored through an owner-bound artifact
callback; the result reports a URL only after storage succeeds. Target/epoch
consistency is checked, while atomic document pixel capture is explicitly false.

## Validation and current gaps

`python -m unittest tests.test_browser_pipe_producer tests.test_browser_automation`
exercises the actual Python transport and producer with hostile deterministic
fake channels: independent namespace, exact owner/resource binding, wrong-session
responses, duplicate JSON keys, navigation races, policy changes while waiting
for the wire lock, unobserved targets, prepared fields, screenshots, ambiguous
effects and bounded decision handoff. These are source contracts, not Chromium
qualification or model safety/calibration/speed evidence.

`python scripts/qualify_browser_pipe.py` is an explicitly invoked, no-inference,
network-none qualification candidate for the installed image. It uses fixed
local intercepted HTTPS fixture bytes and the actual containment/private pipe,
isolated observer, click/fill, same-URL invalidation and screenshot paths. It
prints a candidate manifest only on success; deployment installation remains an
explicit operator action. It must run through the secretless review runner.

The current task runtime fails Chromium zygote startup under its existing
sandbox/namespace restrictions. No qualified deployment manifest has been
installed. Runtime browser acceptance remains incomplete until that precise
prerequisite passes; source contracts do not establish usability on this host.
URL admission rejects private IP literals, localhost and credentials; CDP origin
checks do not claim DNS/socket pinning. Semantic effects of arbitrary site
handlers cannot be proved from labels: unknown clicks require explicit effect
permission, and all resource requests are separately origin/method filtered.
