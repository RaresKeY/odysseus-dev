> **Fork-only staging mirror.** Canonical publication target: `odysseus-dev/odysseus`.

**Upstream title:** `Opening an unread email starts duplicate read and mark-read IMAP work`  
**Upstream labels:** `bug`, `javascript`, `python`, `needs-validation`

---

### Prerequisites

- [x] I searched open Issues, Discussions, open PRs, closed Issues/PRs, exact email read/mark-read paths, and repository-move predecessors; no focused Issue owns this duplicate unread-open root cause.
- [x] This is **not** a security vulnerability.
- [x] The defect is present on the latest `dev` at `e4fa4ae5dd1d709ce4168397bd1d200fec1b2494` and was reproduced against a disposable real IMAP server during validation.

### Install Method

Other (Podman/container deployment with disposable GreenMail IMAP/SMTP)

### Operating System

Linux

### Steps to Reproduce

1. Configure an IMAP account with an unread message.
2. Open Email Library and preserve browser Network plus IMAP/server logs.
3. Open the unread message once.
4. Observe the read/mark-seen API and IMAP operations.
5. Rapidly open message A and then B while A is delayed.

### Expected Behaviour

One unread open should start one authoritative server operation. On a cache miss, the server should fetch the message and set `\Seen` once on the same selected IMAP connection. Cached opens should await one mark-seen STORE. The STORE flag list must be standards-compatible (`+FLAGS (\Seen)`). A late response from message A must not repaint message B, and a failed read/STORE must restore optimistic unread state.

### Actual Behaviour

On current `dev`, the Email Library open path can start both `POST /api/email/mark-read/<uid>` and `GET /api/email/read/<uid>`, while the read path itself defaults to marking seen and can schedule another IMAP transition. This duplicates/races work and can hide failures after the response.

Validation of the first fix also exposed a strict-IMAP interoperability defect: a bare `+FLAGS \Seen` operand is rejected by GreenMail. The corrected implementation uses `+FLAGS (\Seen)`.

### Logs / Screenshots

Related Issue odysseus-dev/odysseus#5569 captured the duplicate pair:

```text
GET  /api/email/read/89772 ... elapsed=1.853s
POST /api/email/mark-read/89772 ... elapsed=1.852s
```

The corrected validation rerun against GreenMail proved:

```text
pre-open \Seen: false
Odysseus authoritative read: success
post-open \Seen: true
outbound SMTP delivery: success
```

GreenMail logs show the accepted parenthesized STORE form.

### Model / Backend (if relevant)

Not relevant; this is browser/REST/IMAP sequencing.

### Are you willing to submit a fix?

Yes — a focused PR is prepared and validated.

### Additional Information

Related to odysseus-dev/odysseus#5569, whose primary Issue is permanent deletion and must remain open independently. Closed PR odysseus-dev/odysseus#5593 addressed reader folder/action correctness rather than this duplicate-open sequencing root cause.

Non-goals include Email Library prewarming, broad cache redesign, search/deletion behavior, folder-role detection, and account persistence.
