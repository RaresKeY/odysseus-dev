> **Fork-only staging mirror.** Canonical publication target: `odysseus-dev/odysseus`.

**Upstream title:** `perf(ui): /api/sessions loading blocks the entire application shell`  
**Upstream labels:** `bug`, `javascript`, `needs-validation`

---

### Prerequisites

- [x] I searched open Issues, Discussions, and open/closed PRs for `/api/sessions`, `loadSessions`, `app-loader`, startup loading, cold PWA load, shell blocking, initialization races, and repository-transfer predecessors; no existing Issue precisely owns this bug.
- [x] This is **not** a security vulnerability.
- [x] The source-level bug is present on the latest `dev` at `e4fa4ae5dd1d709ce4168397bd1d200fec1b2494`.

### Install Method

Other (frontend sequencing is install-method independent; running validation used a disposable local/Podman deployment)

### Operating System

Linux

### Steps to Reproduce

1. Start Odysseus and open the app in a disposable browser profile.
2. Delay only `GET /api/sessions` by about 10 seconds.
3. Hard reload.
4. While `/api/sessions` is pending, try to use the composer or another core shell control.
5. Repeat with `/api/sessions` returning an error.

### Expected Behaviour

- Core module/listener initialization completes once.
- The application shell becomes usable after initialization/paint independently of session-list latency.
- The chat sidebar owns its own `Loading chats…` / failure state.
- Composer text typed during startup is preserved.
- On success, sessions populate later and deferred/hash routes wait only for the session data they actually need.
- Authentication and normal successful startup remain unchanged.

### Actual Behaviour

On current `dev`, the full-screen `#app-loader` is tied to `sessionModule.loadSessions().finally(...)`. A slow or failed `/api/sessions` therefore keeps an application-wide overlay across an otherwise initialized UI until that request settles or a separate fallback fires.

### Logs / Screenshots

Relevant source on current `dev`:

```text
static/app.js
  sessionModule.loadSessions().finally(...) owns loader removal

static/js/sessions.js
  loadSessions() fetches /api/sessions

static/index.html
  #app-loader covers the viewport
```

The prepared fix has also been exercised in a running browser with normal startup, delayed sessions, composer draft preservation, failure handling, and mobile viewport behavior.

### Model / Backend (if relevant)

Not model-specific. Only `/api/sessions` needs to be delayed/failed.

### Are you willing to submit a fix?

Yes — a focused PR is prepared.

### Additional Information

Related to odysseus-dev/odysseus#5516 and odysseus-dev/odysseus#2140, but this Issue does not replace or close either: odysseus-dev/odysseus#5516 concerns an admin/settings module readiness failure, while odysseus-dev/odysseus#2140 concerns server availability during embedding/RAG startup.

The intended patch remains narrow: `static/app.js`, `static/index.html`, and focused tests only.
