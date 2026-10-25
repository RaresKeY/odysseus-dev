# Chat automation

Last updated: dev@f8e043e8 | 2026-10-24

## Scope

`src/chat_automation.py`, the `OwnerChatAutomation`, `ChatAutomationState`, lease,
submission and decision-attempt SQL tables in `core/database.py`,
`routes/chat_automation_routes.py`, `routes/browser_control_routes.py`,
`src/chat_policy.py`, `src/approval_review.py`, `src/decisions/`,
`src/model_auto.py`, `src/approach_routing.py`, `src/browser_pipe.py`, `src/browser_automation.py`,
`src/tool_execution.py`, `src/llm_core.py`, and `static/js/chatAutomation.js`.

## Persistence and authority

Owner settings are SQL-backed, independently of the general JSON preferences.
New sessions snapshot the latest explicit approval choice in their creation
transaction. Existing sessions without feature state start in Always Ask.
Forks copy mode and model-choice policy, without additional folder grants.
Settings writes use revisions; SQLite reserves the write transaction before
reading, while PostgreSQL locks the owner row. Preparing a turn obtains a
bounded lease; submission IDs cannot be silently reused after ambiguous calls.

| Mode | Tool review after deterministic admission | Extra folders with ceiling off | Extra folders with ceiling on |
| --- | --- | --- | --- |
| Always Ask | One exact human decision | None | Explicit per-chat grant within deployment roots |
| Auto-Approve | Independent configured decision call; deny/review/uncertainty/error stops, with no human card | None | Same explicit grant; reviewer cannot elevate |
| YOLO | Discretionary review omitted for admitted operations | None | Explicit bounded scope, with process controls retained |

The owner ceiling defaults off. `agent_elevation_enabled` and
`agent_elevation_roots` are separate absolute deployment restrictions. Grants
seal folder identities, read/write access, expiry, chat revision and owner
epoch. Mode changes clear the chat grant; ceiling revisions invalidate all old
grants, including after an off/on cycle. No mode grants unrestricted access or
disables filesystem/process containment. Execution fails if required
containment is unavailable. Read-only additional roots remain read-only for
native filesystem and contained process tools.

The canonical dispatcher seals exact operation, backend, filesystem, owned
resource, process and browser identities before review, and checks them at
claim/dispatch. New cards allow once or deny, with no broad chat/task grant.
Legacy stored permissions cannot replace a current bound submission. Unattended
and delegated entry points cannot inherit interactive elevation or automatic
review consent; missing submission bindings stop rather than reconstructing a
grant from historical transcripts. Control tools do not require action review.

## Models and acknowledgement

Each chat stores an explicit `auto_models` pool (up to 32 exact registered
endpoint/model identities) in its existing SQL feature settings. Model Auto in
the existing API picker chooses model and compatible effort together in one
bounded Jev call. Empty, disabled, hidden, unregistered, foreign or stale pools
stop visibly without widening the offer. Effort comes from the exact ChatGPT subscription runtime catalogue or the
versioned exact-ID Google compatibility table on the canonical verified HTTPS
Google endpoint. Unknown models/providers offer only the explicit default,
disclosed in the picker; capability is not inferred from names. An unsupported
model/effort pair errors without normalization. Manual selection does not classify
and selecting a concrete model returns the chat to Manual. The legacy approach
classifier remains available to experiment callers; foreground Auto no longer
routes through fixed category slots. Existing Auto chats without `auto_models`
materialize only their previous unique mapped endpoint/model identities once
under the SQL transaction, including before legacy owner mapping edits or forks; stored empty pools remain empty. Forks preserve the pool.
Pool saves validate the same picker inventory/pin allow-list inside CAS without
credential refresh under the writer lock. Independent permission/settings edits
remain available when an unchanged pool becomes unavailable. Each candidate may carry up to800 characters of verbatim user role text. Roles
are task-fit evidence in candidate-specific criteria, never instructions or
authority; unassessed fit stays unknown. Roles bind to the pool revision and
snapshot. The full offer and current credentials/effort metadata are rechecked after Jev
and the current selected pool is checked again at actual provider dispatch.

JSON/SSE chat ingress and `/v1/chat` bind committed settings, the exact original
ask and provider descriptor before execution. The real provider boundary
revalidates current owner, model, headers, effort and registration. Automatic
routes do not silently retry or change providers. Existing explicit manual
fallback consent is revalidated and its actual descriptor recorded before use.
Approval continuations keep the original submission instead of classifying the
synthetic button text. Detached streams keep the binding until producer cleanup;
reconnecting only subscribes to existing events.

Decision transport uses existing httpx and registered credentials, rejects
redirects, bounds time/input/output/calls, and validates finite complete choice
distributions. Missing or ambiguous usage remains reserved/unresolved, not zero.
No SDK, model download, provisioning or inference probe is needed. See
`docs/decision-provider.md` and `docs/chat-approval-review.md`.

The composer permission menu shows Always Ask / Auto-Approve / YOLO. Selecting
Auto-Approve shows an activation disclosure; cancel or failed save retains the
last acknowledged mode. Enabled Auto-Approve retires unsuccessful action reviews
and emits a blocked error, never an individual approval card. Explicitly changing
mode and issuing a new request remains available. Tools and options live in More
tools → Chat settings. The model menu owns Auto and searchable pool editing.

## Browser and evidence

The original agent-browser page-action denial and identity-only sidecar remain.
The new producer requires a deployment-owned hash/architecture/qualification
manifest and actual sandboxed Chromium pipe. It captures an isolated world and
immutable node handles, invalidates on navigation/human intervention, intercepts
unadmitted origin/effects, and never retries an indeterminate effect. Each
automation decision receives a compact permission-filtered shortlist and finite
budgets; selected children re-enter canonical tool dispatch. Human routes show
actual pixels and opaque observed controls, revoke automation on takeover, and
attribute results to their real actor. A model's DONE choice is not factual
completion evidence.

`scripts/decision_experiments.py` runs frozen synthetic fixtures offline. Versioned
historical aggregates retain donor provenance and small-sample caveats; they do
not establish calibration, safety, general speed or reliability.

## Current Gaps

Extended routing, task/input lifecycle, observation/capture, semantic advice and
global experiment accounting are specified in [decision-assistance.md](decision-assistance.md).

The task runner currently cannot establish the mount/PID/procfs boundary needed
for sandboxed Chromium; actual producer/UI runtime qualification is pending.
No manifest is fabricated to bypass qualification. Browser ownership accepts a
signed-in owner or the explicit auth-disabled local operator; a missing
authenticated owner never selects the local namespace. CDP origin checks do not provide DNS/socket pinning. No paid
provider or application smoke is part of this port's evidence. Runtime gaps are
distinct from passing fake-provider and fake-channel regression contracts.
