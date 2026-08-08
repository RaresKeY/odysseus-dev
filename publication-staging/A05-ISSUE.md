> **Fork-only staging mirror.** Canonical publication target: `odysseus-dev/odysseus`.

**Upstream title:** `perf(email): make Email Library prewarming idle, single-flight, and bounded`  
**Upstream labels:** `bug`, `javascript`, `needs-validation`

---

### Prerequisites

- [x] I searched open/closed Issues, Discussions references, and open/closed PRs touching Email Library prewarm/list behavior; no exact-root report was found.
- [x] This is **not** a security vulnerability.
- [x] The behavior is still present on the latest `dev` at `e4fa4ae5dd1d709ce4168397bd1d200fec1b2494`.

### Install Method

Other (browser/client behavior; source reproduction on current `dev`, with disposable container/browser validation for the prepared fix)

### Operating System

Linux

### Steps to Reproduce

1. Configure more than one enabled email account.
2. Load the application but do not open Email Library.
3. Inspect `prewarmEmailLibrary()` and Network activity after its startup delay.
4. On current `dev`, the optional warm can fan out across several accounts and multiple endpoints.
5. Open Email Library or begin foreground work while the optional sequence is scheduled/running.

### Expected Behaviour

Optional warming should run only with genuine browser idle budget. It should be single-flight, select only the remembered/default enabled account, issue at most one bounded initial-page request, skip hidden/busy states, and immediately yield to foreground Email Library/chat activity.

### Actual Behaviour

Current `dev` treats a delay timer as idle work and may start multi-account, multi-endpoint sequential warming, including folders/unread/list work and sleeps. This creates avoidable browser/network/IMAP contention before the user opens Email Library, and foreground opening does not provide one shared cancellation boundary for all warm paths.

### Logs / Screenshots

Current-dev source evidence includes an ordered account fan-out of up to four accounts, folder/unread/list warming, and an inter-account sleep.

The prepared fix has deterministic regression coverage proving the scheduler/account/bounds/cancellation contract. The final publication gate is one running-browser Network trace showing the optional request fires exactly once for the default/remembered account and that an immediate foreground open prevents a later second warm.

### Model / Backend (if relevant)

Not relevant; this is browser Email Library scheduling.

### Are you willing to submit a fix?

Yes — a focused PR is prepared.

### Additional Information

Related but not duplicate: odysseus-dev/odysseus#5165 is server-side IMAP filtering; odysseus-dev/odysseus#5782 and odysseus-dev/odysseus#5789 concern background-task/poll cancellation behavior. Open Email Library PRs including odysseus-dev/odysseus#5791 and odysseus-dev/odysseus#5845 touch neighboring code for different features and must be preserved during rebase.
