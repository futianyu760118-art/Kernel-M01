# M01 Formal Master Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 M01 总控注册从内存 DEMO 改为可见、可审计、可幂等、可持久化恢复，并在正式模式使用 OIDC/Keycloak。

**Architecture:** 保留现有 M01 Kernel 的授权和事件契约，在其外围加入 PostgreSQL 持久化仓储与启动恢复；总控注册增加显式 API/UI 和稳定幂等键。正式 API 通过 OIDC 验证主体和 Scope，M07/M13 继续使用服务间 HMAC；第一阶段只部署一个 M01 写实例。

**Tech Stack:** Node.js 20+, 原生 HTTP 服务，PostgreSQL，`pg`，Node test runner，Keycloak OIDC Authorization Code + PKCE。

**Spec:** `docs/superpowers/specs/2026-11-02-m01-formal-master-registration-design.md`

## Global Constraints

- 生产模式必须设置 `M01_MODE=oidc`；`demo` 只允许本地测试显式启用。
- M07/M13 继续使用 HMAC；浏览器 Token 不得替代服务间签名。
- 正式模式不自动创建 Owner，不恢复已撤销的 Master Agent。
- Master 在第一期按租户唯一，Agent ID 固定为 `A_MASTER_KERNEL`。
- EBMS 缺失记录不等于离职；本地撤权不能被普通导入自动恢复。
- 不把密码、Token、OIDC 私钥或数据库密码写入仓库、页面或审计正文。
- 第一阶段为单写 M01 + PostgreSQL；不实现多实例写入协调。

## Review Focus

- UI 快捷注册成功但状态不刷新：Task 1 增加回归测试，验证响应结构和刷新行为。
- 重复注册产生重复审计或覆盖 Owner：Task 2/3 验证幂等键和租户唯一约束。
- M01 重启丢失总控、事件、撤权或审批：Task 3/6 做启动恢复和 Docker 重启测试。
- OIDC Token 主体与 M01 Person/Agent 不一致：Task 4 验证身份映射、401/403 和租户边界。
- M07/M13 重放与数据库不可用：Task 3/5 验证 HMAC、Inbox 幂等和 fail-closed。

---

### Task 1: 修复总控注册 UI 与可观察响应

**Files:**
- Modify: `demo/src/kernel.js`
- Modify: `demo/public/index.html`
- Test: `demo/test/master-say.test.js`, `demo/test/master-agent.test.js`

**Interfaces:**
- `master.say` 注册回复包含 `decision`, `command`, `registration`, `trace_id`。
- 页面提供 `data-action="masterRegister"`，调用 `/kernel/v1/master/register`。

- [ ] **Step 1: 写失败测试**：断言自然语言注册返回 `EXECUTED/master.register`；HTML 含显式按钮并刷新状态；重复注册返回 `idempotent: true`。
- [ ] **Step 2: 运行失败测试**：`cd work\Kernel-M01\demo; node --test test/master-say.test.js test/master-agent.test.js`，确认因响应字段/按钮缺失失败。
- [ ] **Step 3: 实现最小修复**：增加显式按钮；让直接注册和自然语言注册返回统一结构；注册成功后刷新状态，失败显示 HTTP、原因和 trace_id。
- [ ] **Step 4: 验证**：重新运行上述聚焦测试和 `npm test`。
- [ ] **Step 5: 提交**：`git commit -m "fix(m01): make master registration explicit and observable"`。

### Task 2: 定义持久化 Kernel 状态和正式注册规则

**Files:**
- Modify: `demo/src/kernel.js`
- Create: `demo/src/state-schema.js`
- Test: `demo/test/master-agent.test.js`, `demo/test/state-schema.test.js`

**Interfaces:**
- `M01Kernel({ allowImplicitOwner?: boolean })`
- `kernel.exportState(): KernelStateV1`
- `kernel.restoreState(state: KernelStateV1): void`
- `kernel.registerMaster({ tenant_id, owner_person_id, master_agent_id, idempotency_key }): RegistrationResultV1`

- [ ] **Step 1: 写失败测试**：正式模式不自动创建 Owner；非固定 Agent ID 被拒绝；重复注册不重复创建主 Agent/注册事件；导出/恢复保留人员、Agent、事件、审批、撤权、总控和审计；已撤销总控不可恢复。
- [ ] **Step 2: 运行 `node --test test/master-agent.test.js test/state-schema.test.js`，确认按预期失败。**
- [ ] **Step 3: 实现版本化状态 Schema、Map 恢复、正式/DEMO 行为开关和总控状态机。**
- [ ] **Step 4: 运行聚焦测试与 `npm test`，确认全套通过。**
- [ ] **Step 5: 提交**：`git commit -m "feat(m01): formalize durable kernel state and master rules"`。

### Task 3: 增加 PostgreSQL、迁移和事务写入

**Files:**
- Modify: `demo/package.json`, `demo/package-lock.json`, `demo/src/server.js`
- Create: `demo/src/postgres-store.js`, `demo/migrations/001_m01_control_plane.sql`, `demo/docker-compose.test.yml`
- Test: `demo/test/postgres-store.test.js`

**Interfaces:**
- `PostgresStore({ connectionString, required }): initialize(), loadState(), saveState(state, auditEntries), close()`
- `createApp(kernel, { persistence, mode })` 在监听前完成迁移、加载和恢复。

- [ ] **Step 1: 写失败测试**：空库迁移；保存/新进程加载完整状态；乐观版本冲突拒绝覆盖；数据库不可用时正式 readiness 失败且写请求不报成功。
- [ ] **Step 2: 用 disposable PostgreSQL 运行 `node --test test/postgres-store.test.js`，确认因仓储/迁移缺失失败。**
- [ ] **Step 3: 实现 `pg` 仓储和迁移**：用 JSONB 状态快照 + append-only 审计、revision、更新时间；数据库约束租户内唯一总控；Inbox/审批消费与状态写入同事务。
- [ ] **Step 4: 接入服务启动和状态变更路由**：正式模式强制 `M01_DATABASE_URL`；增加 `/readyz`；master、M07、M13、审批、撤权和总控命令成功后先持久化再返回。
- [ ] **Step 5: 运行 `node --test test/postgres-store.test.js` 和 `npm test`。**
- [ ] **Step 6: 提交**：`git commit -m "feat(m01): persist kernel state in postgres"`。

### Task 4: 强制正式 OIDC 并提供浏览器 PKCE 配置

**Files:**
- Modify: `demo/src/oidc.js`, `demo/src/server.js`, `demo/public/index.html`, `demo/.env.example`
- Create: `demo/test/oidc-formal.test.js`

**Interfaces:**
- `GET /auth/oidc/config` 只返回 issuer、authorization endpoint、client ID、redirect URI 等公开配置。
- 验证后的 `sub` 通过 external-identity 映射到 M01 Person/Agent；不信任请求体身份字段。
- M07/M13 保持 HMAC 入站契约。

- [ ] **Step 1: 写失败测试**：OIDC 模式拒绝 DEMO Token；缺 scope 返回 403；`sub` 映射主体且不能被请求体覆盖；M07/M13 HMAC 仍可用；公开配置无 secret。
- [ ] **Step 2: 运行 `node --test test/oidc-formal.test.js`，确认按预期失败。**
- [ ] **Step 3: 实现正式 OIDC 边界**：正式配置缺失即启动失败；增加身份映射；页面采用 Authorization Code + PKCE，Token 只在内存，不写 localStorage；移除生产 HTML 中的管理/HMAC 默认值。
- [ ] **Step 4: 运行 OIDC 测试、既有 OIDC 测试和 `npm test`。**
- [ ] **Step 5: 提交**：`git commit -m "feat(m01): enforce oidc for formal control-plane access"`。

### Task 5: 让 HUB 注册可恢复并显示真实状态

**Files:**
- Modify: `work/AEOS-hub/src/m01-agent-bridge.ts`, `work/AEOS-hub/src/server.ts`, `work/AEOS-hub/public/app.js`
- Test: `work/AEOS-hub/test/m01-agent-registration.test.ts`

- [ ] **Step 1: 写失败测试**：重试使用稳定幂等键；已有 active Agent 不重复创建；M01 不可用记录为可重试 pending 而非 active。
- [ ] **Step 2: 运行 `cd work\AEOS-hub; npm run test:m01-registration`，确认失败。**
- [ ] **Step 3: 保留 M13 HMAC，增加幂等元数据和 M01 状态查询；安全重试不清除成功状态。**
- [ ] **Step 4: 运行 HUB 聚焦测试、类型检查和 `npm test`。**
- [ ] **Step 5: 提交**：`git commit -m "feat(hub): recover M01 registration after restart"`。

### Task 6: 正式 Docker 部署、恢复演练和运维文档

**Files:**
- Modify: `demo/docker-compose.yml`, `demo/Dockerfile`, `demo/.env.example`, `demo/docs/OPERATIONS.md`
- Modify: `work/AEOS-hub/docs/m01-agent-registration-deployment.md`

- [ ] **Step 1: 添加部署 smoke 检查**：PostgreSQL health、`/health`、`/readyz`、迁移 revision、147 人导入、总控 active、HUB Agent active、重启恢复。
- [ ] **Step 2: 运行当前 compose smoke，确认缺数据库/ready 行为的失败基线。**
- [ ] **Step 3: 增加 PostgreSQL 健康检查、命名数据卷、迁移启动依赖、OIDC formal profile 和本地 DEMO profile；EBMS 保持只读挂载。**
- [ ] **Step 4: 重建并执行完整流程**：Keycloak 配置、总控注册、刷新、重启 M01、再读状态、HUB Agent 注册与重复注册。
- [ ] **Step 5: 更新文档**：配置、迁移、Owner bootstrap、注册/恢复、OIDC、备份恢复、回滚、故障排查和 DEMO 边界。
- [ ] **Step 6: 提交**：`git commit -m "deploy(m01): add durable formal control-plane stack"`。

### Task 7: 最终验证和交付

- [ ] **Step 1: M01 验证**：`npm test`、持久化测试、OIDC 测试。
- [ ] **Step 2: HUB 验证**：`npm run check:types`、`npm run test:m01-bridge`、`npm run test:m01-registration`、`npm test`。
- [ ] **Step 3: Docker 验证**：`docker compose ... ps`、`/health`、`/readyz`、重启后总控/人员/Agent/审计仍存在。
- [ ] **Step 4: `git diff --check` 和 secret scan；确认没有真实密码、Token、OIDC 私钥。**
- [ ] **Step 5: 提交验证证据**：`git commit -m "docs(m01): record formal master registration verification"`。
