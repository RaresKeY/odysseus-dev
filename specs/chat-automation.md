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

Each chat stores `auto_categories` in its existing SQL feature settings, with exactly `complex_tasks` (Complex tasks) and `straightforward_tasks` (Straightforward tasks), each assigned one exact registered endpoint/model. Both categories may intentionally use the same model; endpoint identity remains exact. Partial assignments can be saved in Manual, while Auto activation or assignment edits in Auto require both currently available models. Empty, missing, disabled, hidden, unregistered, foreign or stale assignments stop visibly without guessing or model fallback.

`src/model_auto_policy.json` preserves the complete supplied Codex-Jev scope/model/effort/anchors/persistence policy, replacing only Sol/Luna labels/slugs with neutral category labels/IDs. `src/model_auto.py` preserves the learning/teaching versus established repeatable-work request criteria, omitting the donor's unsupported comparative model-quality claim. Jev receives the full policy as `state.routing_policy`, chooses `execution_model=complex_tasks|straightforward_tasks|unavailable` and supported `reasoning_effort` in one `model_auto` call. The chosen category resolves deterministically to its assigned custom model. Category role is task policy, never an invented capability description for that model. The complete policy hash, category identities and exact supported capability revision bind the immutable offer and actual provider dispatch.

Effort comes from the exact ChatGPT subscription runtime catalogue or versioned exact-ID Google compatibility table on the canonical HTTPS Google endpoint; unknown providers/models offer default-only with disclosure. Unsupported category/model/effort combinations error without normalization. Complete finite normalized winner vectors, bounded confidence and known usage remain required; raw scores are observable routing metadata, without an absolute confidence/probability floor. Explicit `unavailable` still errors. Action approval's separate 0.95 allow thresholds and deterministic ceilings are unchanged.

Existing `auto_models`, descriptions, old three-approach mappings and unrelated feature data are retained as compatibility history, never classified or inferred into the two categories. Already-Auto chats without complete category assignments must explicitly assign both before sending; the existing old-mapping materialization still preserves prior unique identities before owner mapping edits/forks. No schema migration or destructive default/model rewrite occurs. Forks preserve explicit categories, legacy choices and existing grant-reset behavior. Assignment saves reuse exact picker inventory/pin admission inside CAS without credential refresh under the writer lock. Independent permission/settings/manual recovery remains possible if an unchanged assignment becomes unavailable. Manual model selection skips Jev.

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
