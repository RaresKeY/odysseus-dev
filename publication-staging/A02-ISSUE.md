> **Fork-only staging mirror.** Canonical publication target: `odysseus-dev/odysseus`.

**Upstream title:** `Paginated history display hydrates the full transcript after restart`  
**Upstream labels:** `bug`, `context`, `python`, `needs-validation`

---

### Prerequisites

- [x] I searched open Issues, Discussions, open PRs, closed Issues/PRs, exact symbols, moved paths, and repository-transfer lineage; no focused open report owns this display-pagination/full-context hydration boundary.
- [x] This is **not** a security vulnerability.
- [x] The defect is present on the latest `dev` at `e4fa4ae5dd1d709ce4168397bd1d200fec1b2494`.

### Install Method

Other (current `dev` source/FastAPI/SQLite reproduction; the prepared fix was also exercised in a disposable Podman deployment)

### Operating System

Linux

### Steps to Reproduce

1. Persist a session with substantially more messages than one requested history page.
2. Restart/reload so the in-memory session is metadata-only, or leave a cached session with only part of the persisted transcript.
3. Request `GET /api/history/<session_id>?limit=24`.
4. Trace `chat_messages` queries and session-manager hydration.
5. Then send a new model request without first paging the whole transcript into the browser.

### Expected Behaviour

- Displaying one paginated history page performs count/page-level database work only.
- Full persisted history is hydrated only at the model-send context seam.
- An incomplete cached history hydrates once using authoritative metadata.
- A warm complete session does not reread all message rows.
- Hidden compaction summaries, raw multimodal content, and attachment metadata remain available to the model even when they are not part of the visible page.

### Actual Behaviour

On current `dev`, a display-only page can trigger complete transcript hydration/materialization after the bounded page query, defeating pagination for long sessions. The preexisting lazy-hydration check can also consult stale cached metadata before deciding whether model context is complete.

### Logs / Screenshots

Deterministic source/query evidence for the prepared fix:

```text
display page:
  count_select_count=1
  page_select_count=1
  page_select_has_limit=true
  page_select_has_offset=true
  session_manager_get_session_calls=0

first incomplete model-context lookup:
  full_history_selects_after_first_send_lookup=1

second warm lookup:
  duplicate_warm_hydration_selects=0
```

Running-app capture evidence additionally recorded model requests containing **1,200** and **1,202** messages, with all context sentinels preserved: first/middle/last history, hidden compaction summary, multimodal content, and attachment metadata.

### Model / Backend (if relevant)

Not provider-specific. The defect occurs before provider dispatch.

### Are you willing to submit a fix?

Yes — a narrow PR is prepared.

### Additional Information

Related to odysseus-dev/odysseus#4644, odysseus-dev/odysseus#5258, and odysseus-dev/odysseus#4257, but this Issue intentionally owns only the server-side boundary between paginated display history and complete model-send hydration. It does not close those broader Issues.

Historical PR odysseus-dev/odysseus#4661 attempted a much broader long-chat/browser-memory fix and closed unmerged; this implementation was rebuilt narrowly on current `dev`.
