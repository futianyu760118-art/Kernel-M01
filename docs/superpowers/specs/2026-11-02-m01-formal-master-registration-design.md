# M01 Formal Master Registration Design

**Date:** 2026-11-02  
**Status:** Approved direction; implementation pending

## Intent

把 M01 当前的 DEMO 总控注册流程改成第一阶段可部署的正式控制面：总控注册必须可从页面明确发起，结果必须可见、可审计、可重试；M01 重启后总控、人员、Agent、撤权和审批状态不能丢失；生产请求必须由 OIDC/Keycloak 身份和权限控制，不能依赖浏览器中的静态 DEMO Token。

## Current Root Cause

当前后端 `POST /kernel/v1/master/register` 可以成功，但页面没有绑定 `data-action="masterRegister"` 的显式按钮。页面上的“注册总控 Agent”实际走 `/kernel/v1/master/say`，后端注册成功后返回普通回复，没有 `decision=EXECUTED`，前端不会触发状态刷新，因此顶部状态可能仍显示“总控未注册”。此外，`M01Kernel` 所有状态仅存于内存，容器重启后总控注册、事件、审批和审计都会消失。

## Goals

1. 页面提供明确的“注册/恢复总控 Agent”操作，并在成功、失败、重复注册和网络异常时给出结果。
2. 总控注册使用稳定的幂等键：`tenant_id + owner_person_id + master_agent_id`；重复请求不得产生重复主 Agent。
3. PostgreSQL 持久化人员、岗位、Agent、事件幂等键、撤权、审批、总控配置和审计记录。
4. M01 启动时从数据库恢复状态；EBMS 人员导入作为可重复、可审计的同步任务。
5. 生产模式强制使用 OIDC/Keycloak；管理员、服务 Agent 和业务主体权限分离。
6. HUB 注册 M01 Agent 后可以查询并展示 M01 的真实状态；M01 重启后可安全重试恢复。
7. 保留可测试的 DEMO 模式，但 DEMO 只能显式启用，不作为生产默认值。

## Non-goals

- 本次不重写 M03/EBMS 的经营业务权限；业务事实仍归 M03/EBMS。
- 本次不把 M01 变成浏览器登录中心；Keycloak 才是用户登录和 Token 签发方。
- 本次不实现多主多写集群；第一阶段部署约束为单写实例 + PostgreSQL，后续再做多实例协调。
- 本次不把密码、OIDC 私钥、数据库密码或管理 Token 写入仓库、HTML 或审计正文。

## Approaches Considered

### A. 页面补丁 + 保留内存 DEMO

补上显式按钮、刷新状态和错误提示。改动最小，但重启丢状态、审计不可恢复、生产仍依赖 DEMO Token，不能满足正式版本要求。

### B. PostgreSQL 持久化控制面 + OIDC/Keycloak（选定）

在现有 Kernel API 和 OIDC verifier 之上加入 PostgreSQL repository、迁移、启动恢复、幂等事件和审计；页面使用显式注册 API，HUB 继续通过 M13 事件接入。部署约束为单写 M01 实例，数据库负责持久化和恢复。这一方案保持现有契约，能够先解决当前故障并建立正式运行边界。

### C. 拆分独立 Master Registry 服务

把总控注册、人员、Agent 和审批全部拆成独立服务。长期扩展性最好，但会改变当前接口、部署和故障域，超出第一期修复范围，本次不采用。

## Selected Architecture

```text
Browser / HUB
    -> OIDC access token
    -> M01 API
       -> OIDC verifier + policy guard
       -> PostgreSQL transaction
          - master_registry
          - principals / agents
          - inbox_events
          - approvals
          - audit_events
       -> response with state + trace_id
```

### Master registration contract

`POST /kernel/v1/master/register`

Request:

```json
{
  "tenant_id": "TENANT_A",
  "owner_person_id": "P_OWNER",
  "master_agent_id": "A_MASTER_KERNEL",
  "idempotency_key": "TENANT_A:P_OWNER:A_MASTER_KERNEL"
}
```

Response includes `ok`, `master`, `state`, `idempotent`, `trace_id` and a safe `reason`. The same request after a successful registration returns the existing active record without duplicating the Agent or audit event. A changed owner or tenant is rejected until an explicit administrative transfer flow exists.

### Persistence

Use PostgreSQL in Docker for development and production-compatible deployment. Migrations create versioned tables for master records, persons, positions, agents, received integration events, approvals, revocations and audit events. Each state-changing API performs its state mutation, idempotency write and audit append in one transaction. Startup recovery loads the active control-plane state before accepting traffic.

### Authentication

- `M01_MODE=oidc` is required for formal deployment.
- `M01_OIDC_ISSUER`, audience, JWKS URI and required scope are mandatory.
- Admin operations require `m01:admin`; delegated policy checks require `m01:policy:delegate`.
- DEMO bearer-token mode remains test-only and requires explicit `M01_MODE=demo`.
- Web UI does not display or persist a production admin token.

### UI behavior

- Add a visible `注册/恢复总控` button that calls `/kernel/v1/master/register` directly.
- Keep the natural-language shortcut, but refresh status whenever it returns a successful registration reply.
- Disable the button while pending, show `已注册 / 已恢复 / 失败 / 需重试`, and render `trace_id` for support.
- The page must read the current status after load and after every registration attempt.

### Deployment

- Add a PostgreSQL service and durable volume to the M01 compose file.
- M01 starts only after PostgreSQL health is ready and migrations succeed.
- `M01_MODE=oidc` is the formal compose profile; `demo` is an explicitly named local profile.
- Backups must cover PostgreSQL, with restore verification before production cutover.
- First rollout is shadow/read-only, then controlled write operations, then removal of DEMO mode from the production environment.

## Acceptance Criteria

1. The UI has a visible direct registration action; clicking it sends one POST and renders the returned state.
2. A fresh database registers the master as `active`; the same request is idempotent and does not create a duplicate Agent.
3. A M01 restart preserves the master, 147 imported EBMS persons, HUB Agent registration, approvals and audit entries.
4. Missing/invalid OIDC tokens return 401; missing required scopes return 403; a DEMO token cannot be used when `M01_MODE=oidc`.
5. HUB registration returns `active` with effective scope `task:execute`; retries are safe.
6. Normal task authorization, high-risk approval requirement, cross-tenant denial and revocation remain unchanged.
7. `npm test`, migration tests, OIDC contract tests and Docker smoke tests pass.
8. No tracked file contains a real password, secret, token or private key.

## Open Implementation Calls

- PostgreSQL is the selected durable store; the application keeps an in-memory read model only after loading the durable state.
- First formal deployment uses one M01 writer. Database transactions provide durability and idempotency; horizontal write scaling is explicitly deferred.
- Keycloak is external to this repository and is configured through environment variables and the AEOS Realm/Client registry.

## Resolved Builder Questions

- M07 and M13 remain service-to-service HMAC integrations. OIDC is mandatory for operator/kernel APIs and the formal browser console; integration secrets are not replaced by browser tokens.
- The formal browser console uses a Keycloak OIDC client. The verified `sub` is resolved through an M01 external-identity mapping to a Person or service Agent; no browser-provided `principal_id` is trusted.
- “Restore master” can only restore an existing durable master record whose owner is an active, mapped `POS_OWNER` Person and whose master Agent is not revoked. Formal mode never auto-creates an Owner or revives a revoked Agent.
- Master uniqueness is tenant-scoped in phase one. `master_agent_id` is fixed to `A_MASTER_KERNEL`; the database enforces one active master per tenant and rejects caller-supplied alternative IDs.
- EBMS is authoritative for imported identity attributes and upstream employment events. A local M01 revocation is sticky until an explicit administrative restoration flow; an absent import row never implies offboarding. Only signed `person.offboarded` or an approved administrative action revokes access.
