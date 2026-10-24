# Exact chat action review

`src/chat_policy.py` interprets the versioned, server-loaded SQL policy. The
approval mode chooses a reviewer; it does not enable tools, supply missing
resource authority, disable confinement, or grant elevation. Always Ask uses
an exact human decision. Auto-Approve uses an independent configured decision
provider invocation. YOLO skips discretionary review only after deterministic
admission. Saved interactive preferences do not grant unattended consent.

`src/tool_execution.py` applies this policy after request/tool restrictions and
resource binding. Missing semantic authority may produce a human card when
the request is valid, non-inherited and not restricted. It cannot be approved
automatically. A disabled tool, unavailable producer, changed resource, denied
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
missing usage, timeout and malformed responses leave a human card without an
automatic retry. A deny executes nothing and visibly reports that recommendation.

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
