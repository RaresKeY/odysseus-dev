# Decision assistance

Last updated: dev@f8e043e8 | 2026-10-24

## Scope

`src/decision_routing.py`, `src/chat_tool_routing.py`, `src/chat_automation.py`,
`src/decision_advice.py`, `src/decision_handoff.py`, `src/decisions/`,
`routes/decision_activity.py`, the chat routes/helpers, both agent runtimes,
`static/js/chatAutomation.js`, `static/js/decision-activity.js`, and
`scripts/{jev_live_campaign,replay_decision_capture}.py`.

## Routing and semantic lifecycle

Canonical SQL chat settings independently select `tool_routing=existing|jev|all`,
`effort_mode=mapped|auto`, and follow-up, preparation and completion-advice flags.
Defaults retain existing routing, mapped effort and disabled new assistance.
Owner decision-provider/three approach mappings remain separate from execution.
Optional mapping `supported_efforts` is explicitly owner-configured metadata,
not a claim of provider-advertised capability. Automatic effort conditions on
the selected admitted endpoint/model and committed finite choices; actual
execution transport checks the final endpoint/model/effort descriptor again.
Manual model selection does not classify approach or effort.

`prepare_http_selection` filters the complete current catalog by owner/admin,
credential, request toggles and contextual restrictions before a decision. The
catalog freezes aliases, schema fingerprints and complete finite bundles.
`no_tools` is explicit, including an empty selected set. Context preprocessing,
compact/full schemas, shortcuts and canonical tool dispatch consume that same
selection. A required-operation conflict is unavailable; no omitted tool is
restored by a falsy-set fallback. Compact runtime identity remains independent
of routing. Overflow/uncertainty returns a visible manual recovery, no retry.
Selection never creates request/resource authority.

Acquisition snapshots owned prior task meaning before superseding older work.
Exact current ask, original task, previous answer/status and real pending card
are separate SQL facts. Ordinary clarification IDs are created by actual
producer events, carried in SSE and saved history, and reloaded from owned
task state. Structured replies validate the current card atomically before
supersession; a delayed stale reply cannot cancel newer work. They do not
approve actions. Only the existing sealed-action control can approve a tool.
New-topic classification does not delete history. Already returned owned
history excerpts produce bounded preparation receipts with exact coverage;
freshness/search/project diagnostics are advice, never retrieval permission.
Incognito/Nobody turns keep exact asks in memory and omit new private SQL
task/card/answer content, history preparation and capture consent.

## Observation, capture and advice

The actual `DecisionProvider` reserves and settles canonical attempts and calls
a shared observer bound to that SQL factory. Observation is separate from
accounting. Owner/chat history, bounded read-only polling, metadata export,
explicit next-turn sanitized capture, deletion and handoffs are served by
`routes/decision_activity.py`. These routes do not infer, dispatch or invoke
the scheduler event bus. Retention runs through the application's explicit
lifespan. Account/session deletion and rename cover observation and semantic
state, including SessionManager tool deletion and SQLite without FK enforcement.

Default metadata/captures omit free text and private choice names, use opaque
offers and preserve known/null usage honestly. Consent is purpose/expiry/cap
bound to one run. Redacted replay is transformed/unscored, not an exact original
inference reproduction. Offline replay has no resolver, key reader, HTTP client
or executor. Original wire, response and replay identities remain distinct.

Safety advice loads an actual current sealed card, checks owner/policy/resource
identity before and after a separate decision, and memoizes uncertain calls.
It never starts/consumes an automatic approval review or dispatches an action.
Completion advice runs only after decisive actual journal/ledger evidence; it
may veto semantic completion but cannot upgrade failed/missing evidence or
restart tools. Nested journals do not multiply this call. Durable handoffs
reference actual attempt/action/check/artifact IDs without live handles,
grants or executable continuations. Browser DONE remains unverified handoff.

## Experiments and global accounting

`CampaignLedger` owns one immutable five-million aggregate input-plus-output
authorization across processes, restarts and admitted source/suite versions.
Atomic conservative reservations precede transport and bind exact wire/provider
identity. Known malformed usage is charged, partial known usage is retained,
unknown spend remains held and halts expansion, and overruns are persisted.
There is no ambiguous retry. Stage gates and independently authored synthetic
development/held-out cohorts remain separate. Exact settled synthetic capture
replay can resume a stage without a new paid denominator or duplicate call.

Offline campaign execution uses the actual registered endpoint resolver,
SQL attempt owner and provider with mocked HTTP. Live admission requires a
reviewed restrictive host runner and Jev-only credential mount. The separate
`RootApprovedChoiceProfile` admits the root-reviewed finite native-Choice
operational profile with conservative 65,536 input plus 65,536 output planning
reservations. This is an explicit trusted-service accounting assumption, not
a published vendor output maximum or a guarantee against misreporting. HTTP
byte caps and historical campaign caps are not billing bounds. A durable one-
million known-plus-held operational stop additionally limits this initial
campaign; the immutable five-million authorization remains shared across all
versions and stages. Ordinary advisory label disagreements are scored without
retry; material protocol, identity, lifecycle, privacy or accounting failures halt. Feature-shaped synthetic decisions do
not establish real task success, broad safety, calibration, provider ranking
or general browser speed/reliability.

## Current Gaps

No live Jev qualification or refreshed deployment is asserted by source/offline
tests. A frozen source/config/profile bundle and explicit host stage review
remain required before any live dispatch. Browser
and native execution remain unavailable without their own containment
qualification; no manifest or sandbox bypass is fabricated. Desktop/narrow
render evidence requires an available qualified offscreen runtime or the
separately preserved private deployment after its reviewed refresh.

## Endpoint purpose and UI updates

Canonical `model_type=decision` rows are eligible only for structured decision
providers. Registration and manual model-ID editing send no provider probes.
General model lists, aliases, first prompts, saved chats, comparison routes and
actual execution transport reject these rows. Ordinary `llm` rows may still
serve both openai_json decisions and chat. The decision settings picker sees
both supported purposes; the three execution approach slots see only `llm`.
The service-worker revision refreshes changed control modules without deleting
accounts, cookies, IndexedDB or other user storage.
The floating composer Options menu overrides generic foldout clipping so its
buttons remain visible and reachable by both pointer and keyboard.

## Restrictive task runtime

`ODYSSEUS_TASK_AUTOMATION_ENABLED=0` blocks automatic ScheduledTask/event/webhook/scanner/chain admission before stored authority, handlers or inference. This is separate from the loop-only `ODYSSEUS_INPROCESS_TASKS=0`, approval modes and owner preferences. Seeded exact-action grants remain real persisted authority, but are not automatically consumed while deployment admission is off. Explicit manual owned runs preserve their existing checks; this guard neither grants elevation nor changes decision-provider settings. The canonical owners are `src/task_automation.py`, `src/task_scheduler.py`, `src/event_bus.py`, app startup and `routes/task/task_routes.py`.
