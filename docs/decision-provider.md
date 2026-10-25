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

`src/model_auto.py` selects model and compatible effort in one call from the
chat's explicit registered endpoint/model pool. It preserves the donor's scope
and effort instructions, with closed candidate IDs and no vendor-specific model
rankings. Server-owned effort metadata uses exact ChatGPT subscription catalogue entries
and the documented seven-ID Google compatibility table on the canonical HTTPS
Google OpenAI route. Unknown routes/models disclose default-only. Per-candidate
optional roles preserve bounded user wording and supply distinct task-fit
criteria; these hints are data, never classifier instructions or authority.
Capability revision and role text are part of the immutable offer snapshot. Complete
finite distributions, a maximum-consistent choice, bounded confidence, known
usage and model/effort compatibility are required. As in the donor routers,
`model_auto` has no absolute confidence or selected-probability floor: valid
low-confidence winners bind, with raw scores retained as routing metadata.
Explicit `unavailable` still stops. Routing grants no action authority; the
separate action-approval 0.95 allow thresholds are unchanged. No silent normalization or wider pool
is used. Registration and credentials are revalidated after inference; actual
provider dispatch rechecks current pool, model, headers and effort. The exact
original ask is sent only after a valid binding. Manual selection skips Jev.
Legacy `src/approach_routing.py` remains for experiments; old Auto chats retain
only their previous mapped identities as compatibility candidates.

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
