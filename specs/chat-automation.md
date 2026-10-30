# Chat automation

Last updated: dev@1a7c79946 | 2026-10-28

Feature contract updated: 2026-10-30.

## Scope

`src/chat_automation.py`, the `OwnerChatAutomation`, `ChatAutomationState`, lease,
submission and decision-attempt SQL tables in `core/database.py`,
`routes/chat_automation_routes.py`, `routes/browser_control_routes.py`,
`src/chat_policy.py`, `src/approval_review.py`, `src/decisions/`,
`src/model_auto.py`, `src/approach_routing.py`, `src/browser_pipe.py`, `src/browser_automation.py`,
`src/tool_execution.py`, `src/llm_core.py`, and `static/js/chatAutomation.js`.

## Persistence and authority

Owner settings are SQL-backed, independently of the general JSON preferences. First-use and new sessions start in Always Ask in their creation transaction, regardless of the owner's retained `last_explicit_mode`. Existing sessions without feature state also start in Always Ask. Saved per-chat approval choices survive reload/resume; stored owner choices and history are preserved rather than rewritten or used as automatic defaults. New forks also start in Always Ask while copying the source model-choice/routing policy and history, without additional folder grants; the source chat keeps its explicit mode. Settings writes use revisions; SQLite reserves the write transaction before reading, while PostgreSQL locks the owner row. Preparing a turn obtains a bounded lease; submission IDs cannot be silently reused after ambiguous calls.

| Mode | Tool review after deterministic admission | Extra folders with ceiling off | Extra folders with ceiling on |
| --- | --- | --- | --- |
| Always Ask | One exact human decision | None | Explicit per-chat grant within deployment roots |
| Auto-Approve | Independent Jev review with one selected-conversation-model technical fallback; valid denial/uncertainty, malformed/incomplete response or dual failure stops, with no human card | None | Same explicit grant; reviewer cannot elevate |
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

Effort comes from the exact ChatGPT subscription runtime catalogue or versioned exact-ID Google compatibility table on the canonical HTTPS Google endpoint; unknown providers/models offer default-only with disclosure. Unsupported category/model/effort combinations error without normalization. Complete finite normalized winner vectors, bounded confidence and known usage remain required; raw scores are observable routing metadata, without an absolute confidence/probability floor. Explicit `unavailable` still errors. Action approval's separate 0.95 assessment thresholds and deterministic ceilings are unchanged.

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

The composer permission menu shows Always Ask / Auto-Approve / YOLO. Auto-Approve readiness accepts either an admitted configured Jev reviewer or the selected conversation model; automatic routing binds fallback to the originating turn's resolved model. Checks are local registration/owner/model checks, never live inference or health probes. Clearing the configured Jev reviewer with `decision_provider: null` preserves selected-conversation-model fallback; the owner settings API resolves only non-null provider registrations and retains revision checks. Both paths unavailable disables activation without changing a saved mode; Always Ask and reviewer settings remain accessible. Auto-Approve shows an activation disclosure; cancel or failed save retains the
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

## Synchronous semantic action assessment

`src/approval_policy.py` owns `odysseus-approval-review/v2`: one independent primary review answers risk, semantic authorization, policy category and effect scope, with one bounded conversation-model fallback after HTTP/transport/timeout failure or missing Jev. Malformed/incomplete primary answers and identity/usage failures stop without another reviewer; only an explicit outage may exempt its held usage for an independent fallback. `src/approval_review.py` validates complete distributions and known usage, then applies the deterministic matrix and every veto. All four Jev classifications require confidence/probability at least 0.95 for acceptance. The ordinary-model fallback instead requires four categorical labels and explicit uncertainty, without fabricated distributions; uncertainty stops. Low/medium risk can pass with any explicit authorization label; high requires medium/high authorization and narrow scope; critical always denies. Category restrictions and missing material evidence override allowances. Readiness, three user modes, exact grants, cancellation and hard execution authority remain independent.

`src/approval_context.py` assembles the complete owner-scoped submission chain (4,096 linked submissions/24 KiB of losslessly framed user evidence), exact proposed action, policy/environment/resource facts, and bounded untrusted observations within a 32 KiB state cap. It does not infer consent from mutable chat history or compaction. `ChatAutomationSubmission.approval_evidence` retains exact human allow/deny fingerprints with provenance; private-turn contents remain in memory. Owner-serialized logical admission order survives wall-clock rollback/ties. Every owned row must belong to the validated chain; disconnected, missing, corrupt or over-budget history fails closed. `src/approval_files.py` inspects bounded directly declared native shell scripts inside admitted roots, binding their complete content and identity; complex/transitive/remote dependencies remain unverified. The authorization snapshot and inspected scripts are sealed into pending proposals and rechecked after await and at grant claim. Ordinary filesystem bindings remain object identity checks, not complete content snapshots.

Decision activity adds fixed risk, authorization, source, policy/prompt version, rule/reason and redacted evidence counts. Jev, conversation-model fallback, human, YOLO and deterministic paths remain distinguishable; old records are unassessed. The approval-only provider provenance flag leaves other decision prompts unchanged. Adaptive scoring and cross-action caches remain disabled. The maintained dispatch inventory, budgets, policy rules and limitations are in [chat-approval-review.md](../docs/chat-approval-review.md).

The fallback shares a 60-second deadline, gets at most one selected-model attempt, and uses existing durable accounting. Its narrowly linked reservation may coexist with a technically failed primary whose unknown usage remains held. After a confirmed outage, distinct actions may continue with separately reserved conversation reviews; only an owner-authorized registered route lineage permits a reviewer change. No continuation waives unrelated holds, overruns, cancellation or the original call ceiling. Exact provider/model/turn and original selection are revalidated. Valid primary denial/uncertainty is final. See the linked review documentation for protocol contracts and offline qualification limits.

## Approval retention and route continuation

Synchronous approval context v2 revalidates the complete owner/chat submission chain in one bounded SQL read. Lossless ordered exact-text framing supports up to 4,096 linked submissions within 24 KiB of user evidence; material overflow requires an explicit owner-authenticated in-chat checkpoint or Always Ask, never a relevance guess. Private asks/decisions stay only in bounded owner/chat/process memory across request-scoped stores; restart/worker changes/eviction/expiry fail explicitly. Deleted/renamed ownership clears the volatile evidence. Registered owner-authorized foreground fallback retires old cards and advances the review-route revision before a new binding; old grants/in-flight answers cannot transfer. A primary HTTP/transport/timeout outage may continue distinct exact actions with the selected conversation reviewer or an explicitly authorized registered route handoff while retaining the original unknown hold and call ceiling; unrelated holds, overrun, cancellation and valid denial remain final. See [chat action review](../docs/chat-approval-review.md).

## Owned evidence recovery and compatibility

`src/approval_recovery.py` and the interactive settings routes provide metadata-only status and explicit user-written checkpoint replacement with owner/head/revision/process binding. Known tool/delegated callers and cross-origin requests cannot renew authority evidence. Old history stays, old proposals/folder grants retire, and the mode/ceilings stay; a new action still requires canonical review. Private replacements and decisions never persist as raw content. Known multi-worker private Auto is unavailable; unknown process changes fail early before supersession/inference, with renewal or Always Ask recovery. Exact public placeholders are distinct from credential-bearing/nested input; no automatic redaction creates consent. Provider contracts bind only documented exact official identities and supported output formats; selected and actually reported reviewer identities remain distinct. A bounded incompatibility pause is not an approval cache or hidden retry.

Before a new Auto turn supersedes work or performs model/tool preparation, the prior complete review context is validated. This includes legacy private rows lacking a process marker, even if the next request turns incognito off. JSON sensitivity checks normalize exactly the recognized credential labels across case, hyphen and underscore spellings; typed null/boolean placeholders are public data, while string secrets remain blocked. Pending/new chats keep global settings accessible and cannot submit renewal until an owned chat exists.

A fully received primary response cannot become an outage if cleanup, endpoint revalidation or settlement later times out. Provider and enclosing review deadlines retain that receipt boundary; known usage stays charged, and legacy response-bearing attempts cannot establish a fallback budget exemption. Unexpected canonical resolver faults are terminal admission failures rather than endpoint absence.
