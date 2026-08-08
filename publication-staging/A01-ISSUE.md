> **Fork-only staging mirror.** Canonical publication target: `odysseus-dev/odysseus`.

**Upstream title:** `perf(chat): live thinking rendering progressively lags during long streamed output`  
**Upstream labels:** `bug`, `javascript`, `needs-validation`

---

### Prerequisites

- [x] I searched open Issues, Discussions, open PRs, closed Issues/PRs, comments/reviews, and repository-move predecessors for this root cause; no focused open Issue owns the live-thinking rendering loop itself.
- [x] This is **not** a security vulnerability.
- [x] The defect is still present on the latest `dev` at `e4fa4ae5dd1d709ce4168397bd1d200fec1b2494`.

### Install Method

Other (browser-side behavior reproduced from the current `dev` code path; the prepared fix was validated in a disposable Podman deployment)

### Operating System

Linux

### Steps to Reproduce

1. Start current `dev` and open a chat in a browser.
2. Use a deterministic/local backend that streams one long reasoning block containing tens of thousands of characters over roughly 30–60 seconds.
3. Include line breaks/Markdown and a unique trailing sentinel.
4. Watch the live reasoning block as cumulative text grows.
5. Inspect DOM mutation/render activity and the elapsed `Thinking…` timer.
6. Repeat terminal cases such as natural `</think>`, `[DONE]` without a close tag, tool/agent-step transitions, abort/error, and session/background transitions.

### Expected Behaviour

Live reasoning should remain responsive as cumulative text grows. Visible thinking-body updates should be coalesced, timer updates should be bounded, line breaks should remain readable while streaming, final Markdown should render once on completion, and terminal/session transitions should not leave delayed work mutating a later view.

### Actual Behaviour

On current `dev`, every reasoning delta normalizes and Markdown-parses the entire cumulative thinking string and replaces the block's full `innerHTML`. A separate self-perpetuating `requestAnimationFrame` loop rewrites the elapsed header at display refresh cadence. The amount of parse, DOM replacement, layout, and paint work therefore grows with the stream and can progressively degrade long interactions.

### Logs / Screenshots

The prepared regression/validation package confirms the old source contract and exercises the replacement scheduler deterministically.

Prepared fix evidence includes:

```text
node --test tests/live_thinking_scheduler.test.mjs
3 passed

focused stream/thinking group
37 passed

JavaScript-area group
202 passed, 4 skipped
```

Running-app validation of the prepared branch also completed with the long deterministic reasoning sentinel appearing once, no page errors, bounded body mutations, and a manual huge-conversation check that remained responsive without the previous lag.

### Model / Backend (if relevant)

Any backend that emits streamed `<think>`/reasoning content. A deterministic local SSE fixture is sufficient; no external provider is required.

### Are you willing to submit a fix?

Yes — a focused PR is prepared.

### Additional Information

This is the narrow live-output rendering slice of odysseus-dev/odysseus#4644 and overlaps only the thinking-timer half of odysseus-dev/odysseus#5588. It does **not** close either broader Issue and does not implement finished-history virtualization, document-editor streaming changes, or the send-button accessible-name half of odysseus-dev/odysseus#5588.

Historical PRs odysseus-dev/odysseus#4661 and odysseus-dev/odysseus#5589 explored broader/adjacent versions of this work and closed unmerged. This implementation was rebuilt narrowly on current `dev` instead of reusing those stale branches.
