# Decisions and approach routing

`src/decisions` invokes an explicitly configured, owner-visible registered
endpoint once. It supports Jev/Systemone and OpenAI-compatible JSON inference;
local inference needs only an existing compatible registered endpoint. There is
no SDK, model provisioning, URL discovery, automatic retry, redirect or provider
authentication refresh in this path. Execution provider selection remains
separate.

The owner configuration stores `decision_provider` with `endpoint_id`, `model`,
`protocol` (`systemone` or `openai_json`), and optional `timeout_seconds` (0.1–30)
and `max_calls` (1–128). URLs and keys come from canonical encrypted endpoint
registration, never decision input. Normal owner visibility, enabled/hidden
models and deployment admission still apply. Auth-disabled ownership resolves
to its explicit local owner scope only when authentication is explicitly off.
An absent authenticated identity, request sentinel, or local-owner identity in
authenticated mode fails before resolver/credential access or HTTP. Shared registrations retain canonical
visibility rules; they do not confer another named owner's private credential.

Auto-Approve availability is derived from this selected registration, not the
execution-model inventory. Hosted Jev (`api.typesafe.ai`) requires a nonblank
registered API key; keyless compatible local endpoints remain supported. The
settings availability check reuses local configuration admission without probing
the endpoint, spending tokens or refreshing provider authentication. It does not
guarantee that a configured key is valid or the service is online.

Each call reserves a durable attempt before transport. The supplied store must
atomically enforce the run's call limit and reject a pending or unresolved
attempt, plus any known over-reservation result; the in-memory implementation is only for offline fixtures. Requests
are bounded at 60 KiB (12,000 bytes for browser decisions), responses at 128 KiB,
and the asynchronous admission/transport lifecycle at the earlier of its deadline
and configured timeout. Local synchronous resolver/cancellation callbacks run off
the event loop; a timed-out late result cannot dispatch HTTP. Durable SQL reserve/settle operations also run off the event loop under retained,
shielded task ownership. A caller deadline returns without waiting for a database
lock: a late committed reservation is then settled cancelled without dispatch,
and an in-flight settlement continues exactly once with its original usage.
Until completion or on store failure the durable reservation blocks replay.
SQL transactions retain their own finite lock/connection limits; process shutdown
can leave a reserved record, which also blocks replay rather than retrying HTTP. A timeout
before dispatch has no ambiguous provider spend; after dispatch it preserves the
unresolved reservation. Known over-budget usage remains known, including when
the answer is malformed, and blocks another call in the same run. Ambiguous transport outcomes never retry.
Returned identity includes endpoint ID, exact model, configuration digest and
distribution provenance, with no URL or credential. The route is re-resolved
after inference to reject changed or revoked registration.

Answers must cover the exact offered questions and choices, with finite numeric
confidence and probabilities, normalized distributions, an argmax choice and
valid usage. Duplicate JSON keys, booleans as numbers, extra answer keys and
untrusted redirects fail closed. `openai_json` distributions are model-reported;
this contract does not turn them into calibrated probabilities. The caller's
deterministic admission remains authoritative. Auto-Approve errors stop without human fallback.

The strict JSON-shaped request check adapts the earlier concern-series
`decision_contracts._json` mechanics: tuple values and coerced/non-string keys
cannot change meaning on the wire. The current probability tolerance remains
`1e-5`; older model IDs, global endpoints and experiment-only runtime policy are
not carried into chat execution. A literal SystemOne wire pair lives in
`tests/fixtures/decision_port/systemone-wire-v1.json`, independently of the
request-generation helper.

`src/model_auto.py` selects a task category and compatible effort in one call. Each chat explicitly assigns one registered endpoint/model to `complex_tasks` (Complex tasks) and `straightforward_tasks` (Straightforward tasks); the same exact model may be assigned to both. The selected category resolves deterministically to that custom model. `src/model_auto_policy.json` preserves the complete user's Codex-Jev policy scope, model, effort, ten anchors and persistence wording, changing only fixed Sol/Luna names and slugs to category labels/IDs. The donor's request learning/teaching versus conventional repeatable-work criteria are retained; its comparative model-quality assertion is omitted because categories do not establish chosen-model capabilities. No per-model roles/descriptions are sent or required.

The closed `execution_model` question offers the two categories plus `unavailable`; `reasoning_effort` offers the union of their exact supported efforts plus `unavailable`. The chosen effort must be supported by the model assigned to the selected category. Exact subscription runtime entries and the canonical HTTPS Google seven-ID table supply evidence; unknown routes/models disclose default-only. The full routing policy is in `state.routing_policy`; its hash, category assignments and capability revisions bind the immutable offer and actual dispatch. Task/brief/endpoint labels are data, never classifier instructions or action authority.

Complete finite normalized vectors, a maximum-consistent choice, bounded confidence and known nonnegative integer usage are required. Valid low-confidence winners bind with raw scores retained; no absolute confidence/selected-probability floor is added. Explicit unavailable, unsupported/stale/unowned/disabled/hidden/unpinned assignments or changed credentials error with no retry, fallback, guessing or human approval continuation. Action approval's separate 0.95 assessment thresholds are unchanged. Original ask dispatch waits for a valid binding; Manual skips Jev.

Existing single-pool `auto_models` entries/descriptions and old mapping data stay stored unchanged as compatibility history. They never infer task categories. Existing Auto without both assignments stops before Jev and requests explicit assignment; partial Manual configuration is allowed. Saving complete category assignments uses the existing revision CAS and exact local inventory/pin validator without credential refresh under a writer lock. Forks preserve categories/legacy choices with existing grant reset. No schema migration or destructive default/model rewrite is introduced.

## Offline evidence

Run `python scripts/decision_experiments.py --output report.json` in the
project's secretless review runner with networking disabled. This command has no
live mode. It replays `tests/fixtures/decision_port/synthetic-v1.json` through the
same bounded provider and an HTTP mock; it never executes a classified action.
The report identifies fixture/source hashes, policy versions, thresholds,
ordering, distributions, failures, unknown usage and safety denominators.
The portable contract is [decision-experiment-v1.schema.json](decision-experiment-v1.schema.json).
Latency and task success remain explicitly unmeasured. One deliberately unsafe
negative control verifies that reporting retains unsafe allows.

`tests/fixtures/decision_port/historical-aggregates-v1.json` preserves selected
historical Jev aggregate results and limitations. These are small reported
experiments, not current validation, safety calibration or general browser
speed/reliability evidence. Synthetic label agreement is separate from actual
executor quality, and invalid/unresolved cases stay in attempted denominators.

## Approval assessment v2

Action review uses one supported multi-question request: `risk_level`, `user_authorization`, `policy_check` and `effect_scope`. The approval-only `server_user_evidence` flag changes provenance wording for the server-assembled submission envelope; other consumers retain their existing question and transport instructions. Every distribution and usage record must validate. The host combines the four classifications using `src/approval_policy.py`; a model outcome cannot override critical risk or a policy veto. The complete prompt remains within the 4,000-character instruction cap, and the existing 60 KiB wire cap applies without truncation. See [Exact chat action review](chat-approval-review.md) for thresholds, context budgets, lifecycle and unsupported inputs.

Approval review alone permits one linked selected-conversation-model fallback after a technical primary failure or absent configuration. `ConversationReviewProvider` reuses the same durable reservation/settlement and bounded HTTP owner with provider-specific payload/response hooks; other decision consumers retain their original wire contracts. The fallback uses categorical labels plus explicit uncertainty, with no synthetic confidence distributions. Unknown primary usage remains held. A confirmed same-run technical outage can admit distinct exact-action conversation reviews without retrying the primary. A different reviewer requires a strict extension of the server-owned registered foreground route lineage, sealed to the actual current endpoint origin; request/model state cannot supply that proof. The original call ceiling, capture requirements, unrelated holds, cancellation and valid-denial finality remain. See [the fallback contract](chat-approval-review.md#conversation-model-fallback).

Approval fallback binds exact documented official-origin alias contracts in `src/approval_provider_contracts.py`, preserves the selected outgoing model and records the actually reported accepted identity in owner-scoped activity. Unknown proxy/date substitutions fail closed. Supported OpenAI/Anthropic/native Ollama schemas are explicit; subscription Responses retains its approved text contract. Bounded incompatible-binding readiness performs no inference probe or retry. Review context can be deliberately renewed by an authenticated interactive user in the same chat; private evidence remains process-local and no checkpoint can grant tool access. See [chat action review](chat-approval-review.md).
