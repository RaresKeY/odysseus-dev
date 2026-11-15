# Proposed upstream contribution split

Planning only: this document does not authorize or open upstream PRs. Reassess each candidate against the current upstream `dev` before extraction. Prepare small branches directly from that base; carry only the named implementation/tests, never the complete fork branch or private specifications, credentials, deployment records or generated test inventory.

The integration baseline for this pass is upstream `0096533ca498ca7bf356d9fb72017f14baa05784`. Its timeout, Conifer, calendar, email MIME/move, search-migration, provider transcript and background-clock changes are already upstream work and must not be resubmitted.

## Order and boundaries

| Order | Proposed PR | Contents and acceptance | Dependency / disposition |
| --- | --- | --- | --- |
| 0 | Private security triage and focused repairs | Revalidate any ownership, auth-metadata or secret-handling audit finding on current public dev. Keep reproduction and exploit detail private; use the repository security reporting workflow where applicable. One invariant per fix, with synthetic multi-owner regressions. | Separate from feature PRs. Historical audit counts are leads, not current verified findings. |
| 1 | Fix OpenAI model-specific effort and tool continuation payloads | Extract canonical model capability resolution for supported effort values, preserve required assistant reasoning/tool metadata, and test greeting plus tool-result continuation without external calls. Do not submit an already-upstream equivalent. | First isolate the minimal fix from the larger canonical-capability refactor; no Jev/default-mode/UI dependency. |
| 2 | Preserve truthful tool/artifact results after provider failure | Retain successful command receipts after a failed final model call; distinguish incomplete final response from completed effects; recognize native stored documents and preserve exact text bytes/counts. Include failure-after-mutation, document-vs-file and Unicode/CRLF tests. | Split byte-write correctness into a separate PR if it is independent of completion evidence. Review interaction with upstream transcript normalization. |
| 3 | Account-scoped model capability and context evidence | Extract provider/model/account evidence, cache ownership and canonical payload policy in reviewable adapter stages. Follow with context-window/allocation accuracy and explicit unknown capability behavior. | Larger architecture proposal first; separate provider normalization, request consumers and context budgeting. Preserve vendor compatibility and credential scope at each step. |
| 4 | Stable composer, menus, scrolling and window movement | Small independent fixes: model picker remains reachable while typing; permission-menu first-frame stability; menu action placement; sidebar readability; scroll completion/boundaries; frame-bounded dragging. Add focused browser behavior tests. | One user-visible problem per PR. Reconcile with upstream's new background clock. Fork-specific labels/icons/defaults are not prerequisites. |
| 5 | Persist Auto assignments and report actual routed context | Save owner Auto assignments independently of Manual picks; use actual resolved model/context in history and context display. Include fresh-chat/reload and stale-response tests. | Depends on an accepted upstream Auto/routing design; otherwise retain in fork. Keep new-chat approval policy out. |
| 6 | Workspace tools across clarification turns | Separate selected-workspace execution permissions from sentence-based tool routing; preserve original authenticated user evidence across a clarification, with assistant context explicitly untrusted. Test “anything,” stale/cross-owner/internal/delegated calls and denial. | Architecture/security proposal before code. Adapt to upstream's authority framework rather than transplanting fork-only Jev plumbing. |
| 7 | Workspace Files side panel | Filename links, bounded list/read/download, explicit revision-checked text editing, unsaved drafts, accessible desktop/mobile pane and native Documents handoff. Include traversal/race/conflict/keyboard/mobile tests. | Feature/product approval first. Backend confinement and API can be one prerequisite PR; viewer/editor another; chat-link/right-panel wiring last. Optional Documents integration must not turn workspace files into synchronized stored documents. |
| 8 | Reusable deterministic smoke journeys | Extend existing release smoke with permission persistence, stale settings, workspace edit/download/conflict, anonymous/private-path rejection and actionable failure summaries. Keep synthetic providers and disposable state. | Land each regression with its owning fix where practical; portable orchestration can follow. Do not upstream operator ports, SSH paths or credentials. |
| 9 | Prompt-prefix/cache accounting and optional STT | Independently propose reusable-prefix/cache-usage correctness with provider-specific fixtures. Propose Granite STT separately only if wanted upstream, including optional dependency/service boundaries. | No dependency on workspace UI or default approval mode; existing upstream equivalence and product fit must be checked. |

## Keep in the fork unless explicitly accepted

- Auto-Approve as the new-chat default, Jev/System I routing/review policy and thresholds, and YOLO/owner-ceiling product choices. These need independent maintainer policy decisions; a bug-fix PR must not change them incidentally.
- Operator deployment/SSH/bridge machinery, private verification evidence and account configuration.
- Icon-suite assets and broad visual selection changes. Do not mix them into functional fixes.
- Optional phone QR and other historical prototypes without a current adoption request.
- The complete generated fork inventory/catalogue as a replacement for upstream's existing testing conventions. Offer portable, behavior-driven pieces instead.

## Extraction and review gates

For each approved candidate, identify the concrete current-upstream trigger, minimal touched paths, tests and unchanged behavior. Check for equivalent open/merged upstream work. Reproduce the defect on upstream, apply the smallest patch, then review and smoke-test the resulting branch independently. Feature proposals must follow current product/maintainer guidance. Avoid stacked chains unless a dependency is real; record native stack membership only if requested.

Use the upstream PR template in force when submission is requested. Include focused validation, screenshots for UI behavior, remaining platform/service limits and no claim that synthetic smoke proves live-provider behavior. Open no upstream PR and publish no security details from this plan alone.
