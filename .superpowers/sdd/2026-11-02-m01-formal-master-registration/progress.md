# SDD ledger — plan: docs/superpowers/plans/2026-11-02-m01-formal-master-registration.md

## Setup

- Execution mode: Native inline execution in branch `aeos/phase1-v1.1`.
- Spec: `docs/superpowers/specs/2026-11-02-m01-formal-master-registration-design.md`.
- Plan: `docs/superpowers/plans/2026-11-02-m01-formal-master-registration.md`.
- Worktree helper script did not return a usable path on Windows; this ledger is maintained manually in the plan-scoped ignored directory.

## Pre-flight

- Shared interface Task 1 → Task 2: Task 1's `registration` response becomes Task 2's formal `RegistrationResultV1`; preserve field names and add idempotency without breaking UI.
- Shared interface Task 2 → Task 3: `KernelStateV1` is the exact payload persisted by `PostgresStore`; repository must reject unknown/invalid state versions.
- Shared interface Task 3 → Task 4: formal server startup/readiness must initialize persistence before OIDC-protected routes accept traffic.
- Shared interface Task 3 → Task 5: durable Inbox/idempotency is required before HUB retry semantics can be considered recovered.
- Shared interface Task 4 → Task 6: formal Docker configuration must require OIDC and database variables; DEMO stays an explicit local profile.

## Baseline

- Baseline: `demo/npm test` passed, 14 tests / 0 failures.

## Task 1: complete

- RED: added structured registration, idempotency and UI contract tests; focused run failed 3 tests for the expected missing behavior.
- GREEN: explicit registration button, registration response contract, refresh-on-success and idempotent active registration implemented.
- Verification: focused tests passed 8/8; `demo/npm test` passed 16/16.
- Commit: `73952a5 fix(m01): make master registration explicit and observable`.

## Task 2: complete

- RED: formal Owner/revocation and state round-trip tests failed as expected; the state module was initially missing.
- GREEN: added `KernelStateV1` validation, export/restore, formal Owner requirements, fixed Master ID and revoked-master guard.
- Verification: focused tests passed 7/7; `demo/npm test` passed 18/18.
- Ruling: the existing DEMO behavior still permits implicit Owner creation and re-registering a revoked master so legacy local flows remain compatible; formal mode disables both behaviors as required by the spec.

## Task 3: complete

- RED: persistence server tests failed because the PostgreSQL store and readiness lifecycle did not exist.
- GREEN: added PostgreSQL migrations/store, versioned snapshot persistence, revision conflict protection, audit deduplication, startup restore, `/readyz`, and mutation rollback on persistence failure.
- Verification: `demo/npm test` passed 34/34; PostgreSQL store/server integration passed 5/5 with disposable PostgreSQL on `127.0.0.1:55432`.
- Commit: `643063f feat(m01): persist kernel state in postgres`.

## Task 4: complete

- RED: OIDC formal contract tests failed for missing public config, missing external-identity mapping, and the DEMO-token console.
- GREEN: added public OIDC configuration, external `issuer + sub` mapping with tenant enforcement, formal startup checks, Authorization Code + PKCE browser flow with in-memory access Token, and removed static DEMO/HMAC defaults from HTML.
- Verification: `demo/npm test` passed 37/37, including formal OIDC contract tests.
- Commit: `4cbeed8 feat(m01): enforce oidc for formal control-plane access`.

## Task 5: complete

- RED: the HUB bridge had stable event retry behavior but no explicit idempotency-key field or signed M01 Agent status contract.
- GREEN: M01 M13 responses now echo `idempotency_key`; `POST /integrations/m13/status` returns tenant-scoped Agent state under M13 HMAC; HUB persists and validates idempotency metadata while retaining pending/rejected fail-closed behavior.
- Verification: M01 `npm test` passed 37/37; HUB type check, M01 bridge/registration/console tests and full `npm test` passed.
- Commits: M01 `7658794 feat(m01): expose signed agent status for hub recovery`; HUB `34989dd feat(hub): persist M01 idempotency metadata`.

## Task 6: complete

- RED: Docker previously had no durable PostgreSQL service, no migration files in the image, and health checks did not wait for persistence readiness.
- GREEN: added PostgreSQL named volume/health dependency, production image dependency installation and migration copy, formal OIDC environment contract, `/readyz` smoke command, graceful shutdown, and updated operations/README/UI flow documentation.
- Verification: Docker image built; PostgreSQL and M01 containers healthy; `/health` and `/readyz` passed; 147 EBMS personnel loaded; total control and HUB Agent registered; restart preserved 148 control-plane persons, 2 Agents, active master and audit entries; smoke passed after restart.
- Commit: `d87fb5d deploy(m01): add durable formal control-plane stack`.

## Task 7: complete

- M01 final verification: `npm test` 37/37, `npm run test:postgres` 3/3, Docker smoke passed.
- HUB final verification: TypeScript check passed; M01 bridge 9/9; registration 4/4; console contract 2/2; full HUB test suite passed earlier in the same task.
- Repository verification: `git diff --check` clean in both M01 and HUB; no untracked or unstaged changes remain in either repository.
- Deployment evidence: `demo` Compose stack is running with `aeos-m01-kernel` and `aeos-m01-postgres` healthy; restart recovery confirmed.
