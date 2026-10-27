# Exact chat action review

`src/chat_policy.py` interprets the versioned, server-loaded SQL policy. The
approval mode chooses a reviewer; it does not enable tools, supply missing
resource authority, disable confinement, or grant elevation. Always Ask uses
an exact human decision. Auto-Approve uses an independent configured decision
provider invocation. YOLO skips discretionary review only after deterministic
admission. Saved interactive preferences do not grant unattended consent.

Auto-Approve is selectable only when the owner's selected decision endpoint and
model pass configuration admission. The settings API reports availability and a
safe explanation; activation checks the same condition in the chat-settings
transaction. Hosted Jev requires a nonblank registered API key. Compatible local
reviewers may be keyless, and an installed execution model alone does not select
a reviewer. This check makes no inference call and does not verify live endpoint
health or credential validity.

If the selected reviewer becomes unavailable, the UI keeps the saved mode but
labels Auto-Approve unavailable. Always Ask and decision settings remain
accessible. No mode change, provider fallback or action retry happens implicitly;
the runtime review checks below remain authoritative.

`src/tool_execution.py` applies this policy after request/tool restrictions and
resource binding. Missing semantic authority may produce a human card in
Always Ask or the existing manual/YOLO flow when the request is valid,
non-inherited and not restricted. Auto-Approve stops with an error instead. A disabled tool, unavailable producer, changed resource, denied
turn capability or read-only elevation root remains denied in every mode.
The browser `automate` operation is an orchestration wrapper; every actual
child operation re-enters the canonical dispatcher with its original authority.

`src/approval_review.py` receives a proposal from the existing
`ToolApprovalStore`. The trusted review context contains the exact original
ask and frozen server policy. Proposed commands and external observations are
untrusted evidence. The provider call is separate from execution and browser
selection. It must return a valid complete distribution for exactly `allow`,
`review`, `deny`, with known usage. Automatic acceptance requires `allow`,
confidence at least 0.95, and selected probability at least 0.95. These versioned
operating thresholds are not a calibration or safety claim. Errors, uncertainty,
missing usage, timeout, unavailable review and malformed responses retire the
proposal and return a clear blocked error without retry or a human approval card.
A deny or review-needed answer also stops the operation. Selecting Auto-Approve
in the composer first explains this behavior in an activation prompt; cancel or
failed persistence keeps the previous mode. Users can explicitly change mode and
make a new request; the failed proposal cannot be approved afterwards.

New cards offer `approve_once` and `deny`. Their grants bind owner, chat, run,
submission, exact arguments, effects, original-ask digest, resource/backend/
process/browser identities, policy revision, owner authority epoch, execution
scope and expiry. The pending store permits one independent review attempt and
one destructive response consumption. At actual dispatch the grant rechecks
expiry and a fresh SQL/run validator, then claims once under its lock. A card
waiting for a human is reused, rather than silently creating another attempt.
Policy/configuration changes and a new accepted ask invalidate old grants;
switching a mode or ceiling back cannot restore them. New cards never stamp
the legacy chat-wide signature, and versioned turns ignore old task/chat grants.

The review service never executes a tool and never owns another grant registry.
Human HTTP routes cannot select `decision_provider` or submit a decision attempt
as authority. `reviewer` and `decision_attempt_id` are assigned only by the
internal validated review seam. The dispatcher checks actual identities again
after the asynchronous review and before the existing effect/transport owner.

Offline regression tests use complete HTTP mock transports and the canonical
dispatcher, with an executor that records synthetic effects. They establish
lifecycle, owner, concurrency, strict-response and replay contracts. They do
not establish model judgment quality, browser speed or a working OS sandbox.
