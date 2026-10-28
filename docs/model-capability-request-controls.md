# Canonical capabilities at the request boundary

## Goal and scope

This branch connects the existing canonical capability layer to inference request construction. It fixes [#6273](https://github.com/odysseus-dev/odysseus/issues/6273): the ChatGPT Subscription Codex Responses endpoint rejects the temperature parameter regardless of the model name or value.

The target remains:

**vendor-reported metadata → vendor adapter → Odysseus canonical evidence → selected request → provider payload.**

The implementation builds on [#5796](https://github.com/odysseus-dev/odysseus/pull/5796), using current dev commit `1a7c79946b559b2f460bacef4e474c44278e948d` as the integration base. It adds a runtime consumer without turning the catalog schema registry into a transport, routing or authorization registry.

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
| `6c5ea0d03b560ca10287b5003d4432e21670eaad` | Complete scoped runtime consumer and #6273 fix; 11 files | 376 focused and adjacent tests on the combined implementation |

The runtime slice adds 417 production lines and 572 test lines, with 54 production lines removed. It uses one snapshot cache and one pure request-control consumer. Most of the goal branch's size is the existing reader foundation being refreshed for integration.

## Concrete merge sequence

### 1. Refresh #5796 as the foundation

Rebase or reconstruct its canonical reader changes against current dev. Retain the native catalog adapters, provider/schema registry, inventory-only fallback and bounded catalog diagnostics.

Add structured evidence to the serialized projection before downstream consumers depend on it. Correct the DNS-label-boundary test to expect unknown provider identity for a look-alike host; generic inventory shape remains a separate concept.

General application/Uvicorn logging changes are separable from this work. Keep them out of the capability merge slice unless a direct dependency is demonstrated.

The refreshed foundation should import and pass its focused tests independently, including the identity-only ChatGPT reader.

### 2. Land the complete request-policy change

Add the shared runtime snapshot cache, request-control resolver, explicit native ChatGPT control mapping and payload consumers together. Include the subscription regression and actual streaming/async HTTP serialization tests in the same change.

This slice must demonstrate both vendor-level restrictions and real catalog evidence reaching a request. Avoid merging unused resolver/cache scaffolding as an isolated architectural milestone.

[#6498](https://github.com/odysseus-dev/odysseus/pull/6498) supplies the correct subscription-wide temperature outcome and useful regression cases. Preserve that behavior in this slice. If an urgent repair lands first, #6498 can serve as the small interim fix; the capability integration should absorb it without another model list.

[#6272](https://github.com/odysseus-dev/odysseus/pull/6272) adds a global GPT-6 prefix rule. Supersede it for this issue.

### 3. Adapt #5849 to the shared contract

[#5849](https://github.com/odysseus-dev/odysseus/pull/5849) now uses canonical types, following the maintainer's requested review direction. Its old serialized-record assumptions do not match #5796.

Retain its explicit accepted-value validation, canonical UI projection and unique verbosity behavior. Adapt consumers to `model` and the structured `deterministic_controls` field, using `evidence.allowed_values` and `evidence.default`.

Current dev already has substantial reasoning selection and session plumbing. Rebase onto that implementation and keep only the unique behavior. Replace its separate endpoint lookup with the shared scoped runtime boundary; persistent/UI reads need the caller's authorized endpoint/account context rather than a scan for any endpoint listing the model.

Update the PR description to reflect its actual canonical implementation. Its existing model-name temperature check is replaced by the request-policy consumer.

### 4. Adapt #6568 as a reader and consumer increment

[#6568](https://github.com/odysseus-dev/odysseus/pull/6568) provides useful native llama-swap reasoning evidence. Retain the reader and behavior tests.

Use the same control token and `allowed_values` evidence representation. Do not introduce a second model-capability cache, strip endpoint path identity, fall back to bare authority, or use the first endpoint containing a model. Apply reasoning controls through the shared request boundary across the relevant compatible call paths.

The current branch has action-required workflows. That is an authorization gate, not evidence that its tests failed.

### 5. Merge the Ollama runtime-context lane independently

[#5799](https://github.com/odysseus-dev/odysseus/pull/5799) and [#5800](https://github.com/odysseus-dev/odysseus/pull/5800) are conceptually stacked on #5796, but their current branches independently replay the foundation. Do not merge those cumulative branches unchanged.

Extract or rebase the unique tail commits:

- #5799: `fe65e578a21dcbfdbcaa50b4ceaef8c277995284`, runtime allocation evidence, four files.
- #5800: `7a9e06cc3319ad1d949a65fe5cb47fc186714251`, the Ollama budget consumer, two files.

Land #5799 after the refreshed foundation, then #5800. Keep catalog maximum, configured context and actual loaded allocation distinct. Validate the consumer against a live supported Ollama setup before calling that lane ready to merge.

This lane is not a prerequisite for fixing subscription temperature.

## Other proposals

[#6460](https://github.com/odysseus-dev/odysseus/pull/6460) can later adapt its Ollama vision probe to the canonical reader and scoped cache. It is separate from the subscription issue.

[#6625](https://github.com/odysseus-dev/odysseus/pull/6625) infers vision from GPT generation names. Do not normalize that inference as vendor-reported capability evidence.

[#6907](https://github.com/odysseus-dev/odysseus/pull/6907) is an independent default-selection heuristic. A future canonical selector may replace its classification input.

## Review and validation gates

The goal branch needs focused capability, catalog and transport regression tests on the actual current-base tree. The tests must cover unknown/future model IDs, zero temperature, exact endpoint/account isolation, stale-evidence replacement, contradictory evidence, unchanged ordinary provider behavior and the subscription tool boundary.

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
