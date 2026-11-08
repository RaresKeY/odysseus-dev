# Debian Deployed Testing

Last updated: fork dev@418a4712 | 2026-11-08

## Scope

`scripts/fork_live_tests.py`, `scripts/debian_deployed_tests.py`, `scripts/debian_smoke_tests.py`, and `docs/testing/fork-regression/` own reusable real-app acceptance, orchestration and the case catalogue. Tests cover reporting, source binding, privacy and provider preflight.

## Profiles and evidence

The deployed suite validates an explicit source/model plan, loads one private login, and uses the protected app socket or loopback HTTP. Core checks exercise actual auth, chat policies, context, notes and document restore. Extended checks require disposable state and cover fork defaults, saved modes, persisted categories and unavailable assignment admission. Provider/native profiles additionally require explicit paid-call opt-in and disposable state; each model/endpoint is checked independently with exact metrics/history and native command receipts. Failed greetings block native dispatch.

Every catalogue case retains its own passed/failed/blocked/skipped/not_run status. Missing procedures remain unexecuted. External browser/service receipts must match the source and known IDs. Failures take precedence across variants; unexecuted/blocked cases prevent an overall pass. Exit codes 0/1/2 mean complete pass/executed failure/incomplete coverage. Provider bodies, cookies and credentials are excluded; incomplete streams retain only event counts and numeric HTTP statuses.

Release smoke boots a temporary source/state copy through the ordinary dev launcher and existing smoke suite, using prepared dependencies and synthetic model/email services. It performs no dependency installation, disables scheduled effects and vector service startup, and stops only its own instance. Its results do not imply live-provider, vector, browser or platform qualification.

## Current Gaps

The catalogue includes external integration, Windows/macOS, real browser-tool, GPU and recovery profiles requiring their declared fixtures. These are not implied to pass by core or synthetic smoke results.
