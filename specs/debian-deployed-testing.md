# Debian Deployed Testing

Last updated: 2026-11-08

## Scope

`scripts/fork_live_tests.py`, `scripts/debian_deployed_tests.py`, `scripts/debian_smoke_tests.py`, `scripts/debian_browser_tests.py`, `scripts/debian_stop_tests.py`, and `docs/testing/fork-regression/` own reusable real-app acceptance, orchestration and the case catalogue. Tests cover reporting, source binding, privacy and provider preflight.

## Profiles and evidence

The deployed suite validates an explicit source/model plan, loads one private login, and uses the protected app socket or loopback HTTP. Core checks exercise actual auth, chat policies, context, notes and document restore. Extended checks require disposable state and cover fork defaults, saved modes, persisted categories and unavailable assignment admission. Provider/native profiles additionally require explicit paid-call opt-in and disposable state; each model/endpoint is checked independently with exact metrics/history and native command receipts. Failed greetings block native dispatch.

Every catalogue case retains its own passed/failed/blocked/skipped/not_run status. Missing procedures remain unexecuted. External browser/service receipts must match the source and known IDs. Failures take precedence across variants; unexecuted/blocked cases prevent an overall pass. Exit codes 0/1/2 mean complete pass/executed failure/incomplete coverage. Provider bodies, cookies and credentials are excluded; incomplete streams retain only event counts and numeric HTTP statuses.

Browser checks use preinstalled headless Chromium, a temporary profile and the real auth JSON API through a loopback bridge. Desktop/phone checks cover long composer control visibility, repeated permission-menu opening/icons and real wheel scrolling. Login files are private regular files, or arrive on stdin; target state must be disposable.

Release smoke boots a temporary source/state copy through the ordinary dev launcher and existing smoke suite, using prepared dependencies and synthetic model/email services. It performs no dependency installation, disables scheduled effects and vector service startup, and stops only its own instance. Its results do not imply live-provider, vector, browser or platform qualification.

## Current Gaps

The catalogue includes external integration, Windows/macOS, real browser-tool, GPU and recovery profiles requiring their declared fixtures. These are not implied to pass by core or synthetic smoke results.

## Byte writes, cancellation and aggregation

Optional per-variant `content_probe` exercises real write_file/read_file followed by an exact SHA-256 and scratch deletion command. Nested fences, Unicode and CRLF must retain their exact bytes. Optional `native_delay_seconds` (0–60) is planned initial pacing, never an automatic retry of an ambiguous action. A provider alias can declare its exact `reported_model` snapshot without changing the requested endpoint/model.

The trusted Debian Stop observer is restricted to an explicitly named disposable fixture. It binds Stop to the stream's opaque run ID, requires acceptance, observes a real Bash/sleep child, and verifies all original PID/birth identities disappear, including reparented children. Its controller never executes model commands on the host.

`--profiles receipts --previous-report FILE --receipt FILE` combines exact-source observations without logging in or replaying actions. Earlier failures remain failures; missing scenarios remain not_run. Browser results qualify composer/menu/scroll CSS behavior at the recorded viewports, not GPU antialiasing or drag latency on all platforms.

Native text writes preserve requested line endings and report UTF-8 byte counts. LF/CRLF and nested-fence/Unicode dispatcher checks compare actual disk bytes and the reported count. Platform-specific live qualification remains separate.
