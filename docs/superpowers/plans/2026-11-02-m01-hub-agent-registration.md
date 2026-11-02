# M01 ↔ AEOS Hub Agent Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register the HUB central Agent `agent-aeos-hub` with M01 and automatically register future HUB Agents through M01's existing signed M13 integration.

**Architecture:** HUB remains the owner of Agent runtime/model/session metadata. M01 remains the authority for Agent identity, tenant, owner, scopes, authorization and revocation. A small HUB-side bridge signs the existing M13 event contract, persists only synchronization metadata in the Harbor record, and keeps an Agent unavailable to dispatch when M01 rejects or has not accepted it.

**Tech Stack:** Node.js 24, TypeScript, Zod, native `fetch`, Node `crypto`, Node test runner, existing AEOS Hub file store, existing M01 `/integrations/m13` HMAC endpoint.

**Spec:** `docs/superpowers/specs/2026-11-02-m01-hub-agent-registration-design.md`

## Global Constraints

- M01 remains the authorization source of truth; HUB must not mint M01 permissions.
- Use the existing M13 event fields: `event_id`, `tenant_id`, `type`, `agent_id`, `owner_person_id`, `parent_agent_id`, `scopes`.
- Sign the raw JSON body with HMAC-SHA256 and send `X-M01-Signature: sha256=<hex>`.
- Registration must be idempotent; a duplicate event must not create duplicate M01 Agent state.
- Do not persist or emit secrets, complete signed request bodies, or personal data in Harbor/Evidence logs.
- An Agent is dispatchable only when its M01 registration state is `active`; runtime online state is separate.
- Do not add a new dependency or modify the M01 default branch; work on the existing `aeos/phase1-v1.1` integration branch.

## Review Focus

- M01 is unreachable or times out during HUB Agent creation: the Agent remains non-active and the UI exposes retry, rather than falsely enabling dispatch.
- M01 returns `clamped_scopes`: HUB displays and persists only the returned effective scopes, never the requested over-broad scopes.
- A duplicate registration is retried: the same event identity is reused and M01's idempotent response is accepted without duplicate local records.
- The M01 secret is absent or malformed: bridge calls are disabled with a safe configuration state and no secret appears in response payloads or logs.
- A legacy Harbor JSON file lacks the new registration field: the store loads it with a safe default and does not corrupt existing Agent records.

### Task 1: Build and test the HUB-to-M01 signed bridge

**Files:**
- Create: `work/AEOS-hub/src/m01-agent-bridge.ts`
- Create: `work/AEOS-hub/test/m01-agent-bridge.test.ts`
- Modify: `work/AEOS-hub/package.json` (add `test:m01-bridge` script)

**Interfaces:**
- Produces `M01AgentRegistrationState`, `M01AgentBridgeConfig`, and `registerAgentWithM01(agent, config, transport?)` for Task 2.
- `registerAgentWithM01` returns `{ state, eventId, agentId, effectiveScopes, requestedScopes, clampedScopes, reason }` and never returns the secret.

- [x] **Step 1: Write the failing bridge tests**
  - Test valid registration signs the exact raw body and sends the M13 fields for `agent-aeos-hub`.
  - Test M01 `clamped_scopes` returns `active` with only `effectiveScopes` from M01.
  - Test non-2xx response returns `rejected` without throwing the caller out of the request path.
  - Test the same Agent and event inputs produce a stable event ID.

- [x] **Step 2: Run the bridge tests and verify they fail**
  - Run: `npm run test:m01-bridge`
  - Expected: FAIL because `src/m01-agent-bridge.ts` does not exist.

- [x] **Step 3: Implement the bridge**
  - Add `M01_AGENT_REGISTRATION_URL`, `M01_AGENT_REGISTRATION_SECRET`, `M01_AGENT_TENANT_ID`, and `M01_AGENT_OWNER_PERSON_ID` configuration parsing in the bridge boundary.
  - Use `crypto.createHmac('sha256', secret)` over the exact `JSON.stringify` body; use an 8-second abort timeout; classify configuration, network and M01 business errors separately.
  - Derive the event ID from tenant, Agent ID and event type with a stable SHA-256 digest so retries are idempotent.

- [x] **Step 4: Run bridge tests and the full HUB test suite**
  - Run: `npm run test:m01-bridge && npm test`
  - Expected: all bridge tests and the pre-existing HUB suite pass.

- [x] **Step 5: Commit Task 1**
  - Run: `git add src/m01-agent-bridge.ts test/m01-agent-bridge.test.ts package.json && git commit -m "feat(hub): add signed M01 agent bridge"` from `work/AEOS-hub`.

### Task 2: Persist registration state and auto-register new Agents

**Files:**
- Modify: `work/AEOS-hub/src/harbor-store.ts`
- Modify: `work/AEOS-hub/src/server.ts`
- Create: `work/AEOS-hub/test/m01-agent-registration.test.ts`

**Interfaces:**
- Consumes Task 1 `registerAgentWithM01`.
- Produces `HarborAgent.m01Registration` with `state: not_configured | pending | active | rejected | revoked`, `m01AgentId`, `eventId`, `requestedScopes`, `effectiveScopes`, `clampedScopes`, `lastAttemptAt`, `lastSuccessAt`, and `reason`.
- Produces `POST /api/harbor/agents/:id/m01/register` for initial registration and retry.

- [x] **Step 1: Write failing store and route tests**
  - Test an old Harbor snapshot without `m01Registration` loads as `not_configured`.
  - Test successful registration persists `active` and effective scopes.
  - Test M01 rejection persists `rejected` and the Agent is not dispatchable.
  - Test retrying the same Agent reuses the stable event ID.

- [x] **Step 2: Run the tests and verify the expected failures**
  - Run: `npm run test:m01-registration`
  - Expected: FAIL because the schema, update method, and registration endpoint are not implemented.

- [x] **Step 3: Extend Harbor storage**
  - Add a defaulted strict Zod registration object so old JSON remains readable.
  - Initialize new Agents as `not_configured`; add `updateHarborAgentM01Registration(id, state)` with optimistic file-store locking and timestamp validation.

- [x] **Step 4: Add server orchestration**
  - On `POST /api/harbor/agents`, create the Harbor record then attempt M01 registration when bridge configuration is present; persist `pending`, `active` or `rejected` without exposing the secret.
  - Add the explicit `POST /api/harbor/agents/:id/m01/register` retry endpoint.
  - Return `503` only for an unavailable bridge during an explicit retry; preserve the Harbor record and its non-active state.
  - Add a small internal helper so both automatic creation and retry use exactly the same registration and persistence path.

- [x] **Step 5: Run the focused tests and full suite**
  - Run: `npm run test:m01-registration && npm test`
  - Expected: focused tests and all existing HUB tests pass.

- [x] **Step 6: Commit Task 2**
  - Run: `git add src/harbor-store.ts src/server.ts test/m01-agent-registration.test.ts && git commit -m "feat(hub): sync Harbor agents to M01"` from `work/AEOS-hub`.

### Task 3: Expose M01 registration in the HUB console and seed the central Agent

**Files:**
- Modify: `work/AEOS-hub/public/app.js`
- Modify: `work/AEOS-hub/public/index.html` and/or `work/AEOS-hub/public/style.css` only where the existing Harbor Agent row needs a status/action control.
- Create: `work/AEOS-hub/scripts/register-m01-hub-agent.ts`
- Modify: `work/AEOS-hub/README.md` and `work/AEOS-hub/docker-compose.env.example`
- Create: `work/AEOS-hub/test/m01-console-contract.test.ts`

**Interfaces:**
- Consumes Task 2 `HarborAgent.m01Registration` and retry endpoint.
- Produces a visible M01 state for `agent-aeos-hub`, a “注册到 M01/重试” action for non-active records, and a one-shot seed command for the existing central Agent.

- [x] **Step 1: Write the failing console contract tests**
  - Assert the console bundle contains M01 registration state labels, the retry endpoint, and no secret-bearing field.
  - Assert the seed command targets exactly `agent-aeos-hub` and does not create arbitrary Agents.

- [x] **Step 2: Run the console tests and verify they fail**
  - Run: `npm run test:m01-console`
  - Expected: FAIL because the UI and seed command do not yet contain the M01 registration contract.

- [x] **Step 3: Implement console and seed flow**
  - Render M01 state separately from Runtime state: 未配置、同步中、已接入、被拒绝、已撤销.
  - Add a retry button calling `POST /api/harbor/agents/:id/m01/register`; refresh Harbor after success or failure.
  - Add `scripts/register-m01-hub-agent.ts` to call the authenticated local endpoint for `agent-aeos-hub`, with a clear failure message and no secret output.
  - Document the four environment variables, Docker host URL guidance, and the exact enablement sequence.

- [x] **Step 4: Run type checks, console tests and full suite**
  - Run: `npm run test:m01-console && npm run check:types && npm test`
  - Expected: all commands pass.

- [x] **Step 5: Commit Task 3**
  - Run: `git add public/app.js public/index.html public/style.css scripts/register-m01-hub-agent.ts README.md docker-compose.env.example test/m01-console-contract.test.ts && git commit -m "feat(hub): expose M01 agent registration"` from `work/AEOS-hub`.

### Task 4: Configure, deploy and verify the live HUB ↔ M01 connection

**Files:**
- Modify: `work/AEOS-hub/docker-compose.yml` only to pass the four non-secret bridge settings and the secret variable through the existing environment mechanism.
- Modify: `work/AEOS-hub/.env` only if the deployment workspace already uses an ignored local env file; never commit it.

**Interfaces:**
- Consumes Tasks 1–3 endpoints and Docker configuration.
- Produces a live registration result for `agent-aeos-hub` and evidence that M01 authorization reflects it.

- [x] **Step 1: Add environment wiring without writing secrets to Git**
  - Configure HUB to reach M01 at the Docker-reachable URL, set tenant and owner IDs, and pass the existing M13 secret from the deployment environment.

- [x] **Step 2: Rebuild and restart only the required HUB service**
  - Run the repository’s documented Docker compose command; preserve existing data volume and environment values.

- [x] **Step 3: Execute the enablement flow**
  - Authenticate to HUB, invoke the seed command or console retry for `agent-aeos-hub`, then query both `/api/harbor` and M01 status.
  - Expected: HUB shows M01 `active`; M01 lists the Agent with the configured tenant/owner and effective scopes; repeated registration is idempotent.

- [x] **Step 4: Run final verification**
  - Run: `npm run check:types`, `npm test`, the M01 test suite, Docker health checks, and a live registration/status smoke test.
  - Expected: no test failures, HUB and M01 healthy, no secrets in logs, and the Agent is not shown as active unless M01 accepted it.

- [x] **Step 5: Record deployment evidence**
  - Update `work/AEOS-hub/docs/` or the existing operations document with the exact non-secret commands and observed result; do not include tokens or HMAC secrets.

