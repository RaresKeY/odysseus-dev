# Canonical capabilities at the request boundary

## Goal and scope

This branch connects the existing canonical capability layer to the common `llm_core` inference request paths. Application-wide coverage also requires the separate consumer migrations below. It fixes [#6273](https://github.com/odysseus-dev/odysseus/issues/6273): the ChatGPT Subscription Codex Responses endpoint rejects the temperature parameter regardless of the model name or value.

The target remains:

**vendor-reported metadata → vendor adapter → Odysseus canonical evidence → selected request → provider payload.**

The implementation builds on [#5796](https://github.com/odysseus-dev/odysseus/pull/5796), using dev commit `1a7c79946b559b2f460bacef4e474c44278e948d` as the integration base. It adds a runtime consumer without turning the catalog schema registry into a transport, routing or authorization registry.

## Integration update: 2026-10-29

A fresh GitHub comparison found `dev` still identical to `1a7c79946b559b2f460bacef4e474c44278e948d`. Pewds' [#6739](https://github.com/odysseus-dev/odysseus/pull/6739) editor backend, [#6738](https://github.com/odysseus-dev/odysseus/pull/6738) Ajax/runtime integration and [#6741](https://github.com/odysseus-dev/odysseus/pull/6741) editor client are already in that baseline. The #6741 merge is the baseline commit itself. Their presence changes the consumer migration and regression plan; it does not add a prerequisite to the standalone catalog foundation.

[#6498](https://github.com/odysseus-dev/odysseus/pull/6498) remains open at `d9ba51180915e38fea8d4ccddb2fb7122c80820e`. The agreed sequence is to merge that focused repair independently, then refresh #5796 against the resulting dev. The goal branch remains the reference implementation for subsequent slices.

This update changes the plan only. The existing 78/376-test results below apply to the recorded foundation and common request-path implementation. They do not validate the additional editor/Ajax migrations proposed here.

## Ownership

| Layer | Owns |
| --- | --- |
| `provider_capability_schemas` | Provider identity and recognized native catalog shapes |
| `model_capability_readers` | Normalization of explicit provider metadata into canonical records |
| `model_capabilities` | Capability and control vocabulary, evidence statuses and provenance |
| `model_capability_cache` | Bounded, temporary catalog snapshots for an exact endpoint and request-header identity |
| `model_request_controls` | Applicability of canonical evidence to one selected model, vendor and API dialect |
| `llm_core` | Translation of permitted controls into the selected transport's payload |
| Existing endpoint/authentication code | Route selection, account ownership and credential handling |

A compatible request format does not establish vendor identity. In particular, the legacy transport detector returns `openai` for unknown compatible servers. The canonical bridge must not promote that fallback into a claim that the endpoint is OpenAI.

Catalog identity also does not select authentication. The broader catalog match for ChatGPT hosts cannot replace the existing exact-host and Codex-path checks used for subscription transport.

## Evidence contract

The canonical record keeps #5796's versioned flat catalog fields. It also retains complete `deterministic_controls` and `capability_assertions` objects, including status, source, confidence, evidence and test timestamp.

The flat `controls` list is an index of control names. It does not establish support: an unsupported control can appear in that list. Runtime consumers use the typed record and inspect its evidence.

Reasoning effort and verbosity are explicit canonical control tokens. Accepted values use one representation:

```json
{
  "control": "reasoning_effort",
  "status": "claimed",
  "source": "provider_reader",
  "confidence": "provider_reported",
  "evidence": {
    "allowed_values": ["low", "high"],
    "default": "low"
  }
}
```

A default is metadata, not an instruction to insert a request value. A model ID, visibility flag or catalog position cannot establish support.

The ChatGPT native reader maps only explicit reasoning/verbosity fields. It keeps the existing native aliases where they are already consumed by the application. Conflicting duplicate records must not silently pick one set of control claims.

### Request rules

1. A known transport prohibition wins over a model's claimed or verified support.
2. Explicit unsupported model evidence suppresses the requested control on that selected route.
3. Advertised accepted-value constraints validate the requested value.
4. Missing and unknown evidence preserve existing compatibility behavior.
5. Evidence from another vendor, exact model ID, endpoint or credential context does not apply.
6. Capabilities do not grant tool authority.

The subscription temperature rule is maintained as `provider_contract` evidence for the `chatgpt_subscription` vendor and `codex_responses` dialect. It is not attached to every GPT model or inferred from a model prefix.

The payload allowlist remains an independent limit on which inference fields can reach the subscription transport. Native tool declarations remain excluded.

## Runtime snapshots

Successfully fetched catalogs populate one bounded in-memory cache. A snapshot is scoped to the normalized full endpoint URL and the original caller's header context. Credential/header material is hashed for identity; plaintext headers are not retained in snapshots. URL path case, scheme, explicit port and query remain significant. Only known terminal operation paths are normalized to their corresponding base.

Lookups require an exact model ID and stable scoped identity. There is no first matching model across endpoints, bare-host fallback, or account-independent model lookup.

A successful empty or identity-only refresh replaces earlier evidence. Failed fetches do not fabricate metadata. Entries expire after five minutes, and the cache is bounded to 128 endpoint snapshots. Credential rotation or a different header context produces a conservative cache miss.

This is a temporary runtime channel, not a new database migration or background discovery service. Existing persisted inventory and picker metadata remain compatibility surfaces. Their full migration belongs to the controls/UI integration below.

Response-cache identity includes the selected control evidence so a changed catalog constraint does not reuse a response produced under a different effective control policy.

## Implemented merge slices

| Commit | Reviewable change | Independent validation |
| --- | --- | --- |
| `b73ad900062cba9476e359f865ba8fb02627c181` | Refreshed #5796 catalog foundation with structured evidence; 19 files | 78 focused tests on the committed foundation alone |
| `6c5ea0d03b560ca10287b5003d4432e21670eaad` | Scoped common request-path consumer and #6273 fix; 11 files | 376 focused and adjacent tests on the combined implementation |

The runtime slice adds 417 production lines and 572 test lines, with 54 production lines removed. It uses one snapshot cache and one pure request-control consumer. Most of the goal branch's size is the existing reader foundation being refreshed for integration.

## Concrete merge sequence

### 0. Merge #6498 independently

Land its subscription-wide temperature omission and regression tests first. Rebase or reconstruct subsequent slices on the actual resulting dev, preserving the fix throughout. The subscription transport must omit temperature even for an unknown model or an unavailable catalog. Later work moves ownership of that rule into the provider/dialect contract without reopening the bug.

### 1. Refresh #5796 as the foundation

Rebase or reconstruct its canonical reader changes against current dev. Retain the native catalog adapters, provider/schema registry, inventory-only fallback and bounded catalog diagnostics.

Add structured evidence to the serialized projection before downstream consumers depend on it. Correct the DNS-label-boundary test to expect unknown provider identity for a look-alike host; generic inventory shape remains a separate concept.

General application/Uvicorn logging changes are separable from this work. Keep them out of the capability merge slice unless a direct dependency is demonstrated.

The refreshed foundation should import and pass its focused tests independently, including the identity-only ChatGPT reader. Preserve discovery fixes that have landed by the time of the refresh, and rerun the inherited subscription regressions. Keep request caching, policy consumers and editor/Ajax migrations in their later slices.

### 2. Land the shared request-policy integration

Add the shared runtime snapshot cache, request-control resolver, explicit native ChatGPT control mapping and payload consumers together. Include the subscription regression and actual streaming/async HTTP serialization tests in the same change.

This slice must demonstrate both vendor-level restrictions and real catalog evidence reaching a request. Avoid merging unused resolver/cache scaffolding as an isolated architectural milestone.

Absorb the already merged #6498 behavior and retain its regression cases. The new diff should describe the canonical evidence consumer and ownership change rather than present the temperature repair as new work.

[#6272](https://github.com/odysseus-dev/odysseus/pull/6272) adds a global GPT-6 prefix rule. Supersede it for this issue.

Preserve the utility effort behavior already merged in [#6572](https://github.com/odysseus-dev/odysseus/pull/6572): explicit caller effort, then the per-user utility setting, then matching chat endpoint/model effort, then provider default. Each fallback keeps its own effort and selected route. Add the dedicated utility-effort regressions, applicable synchronous calls and per-candidate fallback coverage to this slice. This does not imply adding synchronous ChatGPT Subscription transport support.

### 2b. Migrate additional request consumers separately

The common request helper is not the whole application. At the recorded baseline, [`src/editor_agent_model.py`](https://github.com/odysseus-dev/odysseus/blob/1a7c79946b559b2f460bacef4e474c44278e948d/src/editor_agent_model.py) constructs and sends its own payload in `_post_chat()`, and [`src/clean_agent_preview.py`](https://github.com/odysseus-dev/odysseus/blob/1a7c79946b559b2f460bacef4e474c44278e948d/src/clean_agent_preview.py) constructs streaming payloads directly. Neither automatically receives the new `llm_core` policy. This is a coverage gap in the migration plan, not a reproduction of #6273 in those paths.

Prepare one independently reviewable migration per consumer after the shared contract is stable:

- Editor: retain structured outputs, caption fallback and configured transport behavior. Preserve authorized endpoint IDs, owner/shared visibility, hidden/disabled choices, unambiguous bare-model selection and fail-closed invalid overrides. Replace or explicitly reconcile its base-only served-model cache with the selected request scope.
- Ajax preview: apply shared control evidence at its actual payload boundary while preserving thinking selection, sampling behavior and request authority. The Spec router requests intent/thinking behavior; it does not establish vendor support. Capability metadata cannot enable denied tools or reactivate the deliberately refused Spec driver mode.

Reuse the policy contract where appropriate without forcing every specialized transport through an incompatible helper. Require serialized-request regression tests for each migrated consumer. Maintain an inventory of remaining direct request builders before describing coverage as application-wide.

The controls/UI work in step 3 can proceed once the shared contract is stable; it need not wait for both consumer migrations unless it depends on their API changes.

### 3. Adapt #5849 to the shared contract

[#5849](https://github.com/odysseus-dev/odysseus/pull/5849) now uses canonical types, following the maintainer's requested review direction. Its old serialized-record assumptions do not match #5796.

Retain its explicit accepted-value validation, canonical UI projection and unique verbosity behavior. Adapt consumers to `model` and the structured `deterministic_controls` field, using `evidence.allowed_values` and `evidence.default`.

Current dev already has substantial reasoning selection and session plumbing. Rebase onto that implementation and keep only the unique behavior. Replace its separate endpoint lookup with the shared scoped runtime boundary; persistent/UI reads need the caller's authorized endpoint/account context rather than a scan for any endpoint listing the model.

Update the PR description to reflect its actual canonical implementation. Its existing model-name temperature check is replaced by the request-policy consumer.

Coordinate with Pewds' open draft [#6740](https://github.com/odysseus-dev/odysseus/pull/6740), head `da8ba69c74eef2dffb44bf03c205519cff421278`, which changes streaming and model-picker behavior. Preserve opening from cached inventory, non-forced background refresh and explicit refresh behavior, including the existing server timeout/backoff. Populate canonical snapshots from successful existing discovery responses; do not add a second fetch on picker opening or inference.

The editor's `modelCaps()` name matching and configured local `vision: true` values are compatibility hints or configuration assumptions. Do not publish them as provider-reported evidence. Carry canonical metadata through the server's authorized picker projection while preserving endpoint IDs and exact provider model IDs.

### 4. Adapt #6568 as a reader and consumer increment

[#6568](https://github.com/odysseus-dev/odysseus/pull/6568) provides useful native llama-swap reasoning evidence. Retain the reader and behavior tests.

Use the same control token and `allowed_values` evidence representation. Do not introduce a second model-capability cache, strip endpoint path identity, fall back to bare authority, or use the first endpoint containing a model. Apply reasoning controls through the shared request boundary across the relevant compatible call paths, including synchronous utility calls and each selected fallback from #6572.

The current branch has action-required workflows. That is an authorization gate, not evidence that its tests failed.

### 5. Merge the Ollama runtime-context lane independently

[#5799](https://github.com/odysseus-dev/odysseus/pull/5799) and [#5800](https://github.com/odysseus-dev/odysseus/pull/5800) are conceptually stacked on #5796, but their current branches independently replay the foundation. Do not merge those cumulative branches unchanged.

Extract or rebase the unique tail commits:

- #5799: `fe65e578a21dcbfdbcaa50b4ceaef8c277995284`, runtime allocation evidence, four files.
- #5800: `7a9e06cc3319ad1d949a65fe5cb47fc186714251`, the Ollama budget consumer, two files.

Land #5799 after the refreshed foundation, then #5800. Keep catalog maximum, configured context and actual loaded allocation distinct. Validate the consumer against a live supported Ollama setup before calling that lane ready to merge.

Coordinate this lane with open [#6761](https://github.com/odysseus-dev/odysseus/pull/6761), head `956c5df41d30ebec15ac2143c2e4ab16c3456c61`, which authenticates context probes and reads the served window for inferred self-hosted proxies. Preserve its served-context behavior, but obtain credentials from the selected authorized endpoint/account. Its current helper scans enabled endpoint rows and takes the first matching URL/prefix without owner/account selection; do not adopt that lookup in canonical readers or caches.

Keep the independent [#6706](https://github.com/odysseus-dev/odysseus/pull/6706) correction that treats zero context as unknown in view when reconciling budget logic. Catalog maximum, configured serving window and actual loaded allocation remain distinct observations.

This lane is not a prerequisite for fixing subscription temperature.

## Existing behavior and pending overlaps

The merged endpoint URL guards in [#6316](https://github.com/odysseus-dev/odysseus/pull/6316) and [#6327](https://github.com/odysseus-dev/odysseus/pull/6327), plus the owner-authorized endpoint changes in #6738/#6741, remain outside the capability registry. Resolve authorization and the selected route first, then apply evidence. Preserve administrator tool denials reinforced by [#6357](https://github.com/odysseus-dev/odysseus/pull/6357).

The following proposals were open when checked on 2026-10-29. They are coordination points, not additional prerequisites for the foundation or merge approvals.

| Work | Integration decision |
| --- | --- |
| [#6928](https://github.com/odysseus-dev/odysseus/pull/6928), hidden models and API inventory refresh | Preserve disabled choices and avoid one paid completion per listed API model. Listed inventory is not verified capability support. |
| [#6922](https://github.com/odysseus-dev/odysseus/pull/6922), visible pinned models; [#6949](https://github.com/odysseus-dev/odysseus/pull/6949), configured defaults after fallback | Keep selection/visibility separate from support evidence. A pinned model may remain selectable with unknown capabilities. Preserve manual choices, owner scope and subscription live-catalog validation. |
| [#6954](https://github.com/odysseus-dev/odysseus/pull/6954), malformed discovery rows | Preserve valid neighboring string IDs when one row is malformed. Keep this small correction independently reviewable. |
| [#6682](https://github.com/odysseus-dev/odysseus/pull/6682), discovery endpoint deduplication; [#6660](https://github.com/odysseus-dev/odysseus/pull/6660), picker model aliases | Do not transfer display/alias grouping into request identity. Capability scope retains the selected URL/header context and exact provider model ID. |
| [#6373](https://github.com/odysseus-dev/odysseus/pull/6373), Perplexity headers | Preserve caller overrides and hostname boundaries. Discovery and runtime lookups must use consistent original caller context rather than different decorated header sets. |
| [#6429](https://github.com/odysseus-dev/odysseus/pull/6429), Conifer | Its `openrouter` curation sentinel is not canonical vendor identity. Picker policy, endpoint vendor and wire dialect remain separate. |
| [#5901](https://github.com/odysseus-dev/odysseus/pull/5901), Ollama reasoning suppression | Preserve the intended no-thinking behavior through the applicable dialect adapter. Do not promote its model/URL heuristics to provider-reported evidence. |
| [#6519](https://github.com/odysseus-dev/odysseus/pull/6519), Valkey response cache | If adopted, L1 and L2 response caches must share effective control-policy identity. Its current L2 key has no canonical control-evidence fingerprint. Capability snapshots, completed-response caching and prompt/KV caching have different responsibilities. |

## Other proposals

[#6460](https://github.com/odysseus-dev/odysseus/pull/6460) can later adapt its Ollama vision probe to the canonical reader and scoped cache. It is separate from the subscription issue.

[#6625](https://github.com/odysseus-dev/odysseus/pull/6625) infers vision from GPT generation names. Do not normalize that inference as vendor-reported capability evidence.

[#6907](https://github.com/odysseus-dev/odysseus/pull/6907) is an independent default-selection heuristic. A future canonical selector may replace its classification input.

## Review and validation gates

The goal branch needs focused capability, catalog and transport regression tests on the actual current-base tree. The tests must cover unknown/future model IDs, zero temperature, exact endpoint/account isolation, stale-evidence replacement, contradictory evidence, unchanged ordinary provider behavior and the subscription tool boundary.

For the expanded migration, also cover utility effort precedence and fallback, editor endpoint authorization and structured outputs, Ajax request authority and thinking behavior, hidden/pinned selections, malformed catalogs and refresh semantics. Existing relevant suites include `test_utility_reasoning_effort.py`, `test_editor_agent_model_resolution.py`, `test_editor_planner_ssrf_authorization.py` and `test_task_scheduler_endpoint_authority.py`; select the applicable suites and add wire-payload cases when implementing each consumer. The recorded 376-test selection below did not include these additional consumer gates.

A branch with passing focused tests is ready for maintainer review. Merge readiness also requires current upstream integration, applicable hosted checks and the repository's risk-based human review policy. Historical green checks on an older PR head do not establish that this combined branch is ready to merge.

## Recorded validation: 2026-10-28

The combined implementation passed **376 tests** across canonical metadata, native readers, serialization, scoped caching, request policy, subscription streaming/async wire payloads, account fallback, endpoint/account binding, reasoning, temperature and existing tool boundaries. The foundation commit passed **78 tests independently**, without the runtime consumer.

Tests ran with Python 3.13, real pytest/httpx/FastAPI/SQLAlchemy dependencies and mocked upstream responses in an isolated environment with network access disabled. Existing deprecation warnings were non-failing. These are executed targeted tests, not a claim that the entire application suite or a live subscription call was run.

Reproduce the combined selection in the project's test environment:

```bash
python -m pytest -q \
  tests/test_model_capabilities.py \
  tests/test_model_capability_readers.py \
  tests/test_provider_capability_schemas.py \
  tests/test_model_capability_diagnostics.py \
  tests/test_model_capability_evidence_serialization.py \
  tests/test_model_request_controls.py \
  tests/test_chatgpt_capability_controls.py \
  tests/test_model_capability_cache.py \
  tests/test_capability_request_integration.py \
  tests/test_chatgpt_zero_native_tools.py \
  tests/test_chatgpt_reasoning_and_grouping.py \
  tests/test_llm_core_temperature_reasoning.py \
  tests/test_llm_core_fallback.py \
  tests/test_resolve_session_auth_chatgpt.py \
  tests/test_chatgpt_subscription_routes.py \
  tests/test_chatgpt_subscription_device_flow_multi_account.py \
  tests/test_chatgpt_session_binding.py \
  tests/test_llm_core_openai_reasoning_tools.py \
  tests/test_llm_core_reasoning.py
```
