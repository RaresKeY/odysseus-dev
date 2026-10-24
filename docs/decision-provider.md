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
to its explicit local owner scope. Shared registrations retain canonical
visibility rules; they do not confer another named owner's private credential.

Each call reserves a durable attempt before transport. The supplied store must
atomically enforce the run's call limit and reject a pending or unresolved
attempt; the in-memory implementation is only for offline fixtures. Requests
are bounded at 60 KiB (12,000 bytes for browser decisions), responses at 128 KiB,
and execution at the earlier of its deadline and configured timeout. Unknown
usage retains its reservation. Ambiguous transport outcomes never retry.
Returned identity includes endpoint ID, exact model, configuration digest and
distribution provenance, with no URL or credential. The route is re-resolved
after inference to reject changed or revoked registration.

Answers must cover the exact offered questions and choices, with finite numeric
confidence and probabilities, normalized distributions, an argmax choice and
valid usage. Duplicate JSON keys, booleans as numbers, extra answer keys and
untrusted redirects fail closed. `openai_json` distributions are model-reported;
this contract does not turn them into calibrated probabilities. The caller's
deterministic admission and human fallback remain authoritative.

`src/approach_routing.py` uses the stable IDs `established_procedure`,
`bounded_domain` and `approach_discovery`. The owner maps each to an admitted
endpoint/model/effort. Classification sees the exact original ask and neutral
approach descriptions, without vendor model IDs. A separate readiness answer
and confidence threshold can require manual selection. Mapping admission occurs
before spending. Manual selection makes no classification call. The resolved
execution descriptor carries the actual URL/headers privately and safe identity
publicly; a UI setting alone is not evidence of dispatch.

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
