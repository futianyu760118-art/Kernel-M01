# M01 Kernel DEMO 接口契约（候选 / L1）

**状态**：L1 静态字段与协议候选，对应 `docs/archive/03_M07_M13_POMS接口联调方案.md`。非现网事实，正式版本须 M00 冻结签批。

## 1. 公共约定

- 传输：HTTP/1.1，JSON（UTF-8）。
- 管理类接口鉴权（MVP）：`Authorization: Bearer <M01_ADMIN_TOKEN>`（开发令牌，正式版须 OIDC/JWT）。
- Webhook 鉴权：`X-M01-Signature: sha256=<hex>`，HMAC-SHA256 over 原始请求体；M07 与 M13 使用**各自独立**密钥。
- 所有响应携带 `X-M01-Environment: DEMO ONLY`。
- 审计：每个决策/事件均产生含 `trace_id` 的审计记录，可通过 `GET /kernel/v1/audit` 查询。

## 2. 端点

### GET /health
健康检查。响应 `{ "status": "ok", "service": "aeos-m01-kernel", "environment": "DEMO ONLY" }`。

### POST /integrations/m07（M07 → M01，HMAC）
人事事件入站。字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| event_id | 是 | 全局唯一，重复投递幂等忽略 |
| tenant_id | 是 | 租户 |
| type | 是 | `person.hired` / `person.transferred` / `person.offboarded` |
| person_id | 是 | 人员 ID |
| position_id | 条件 | hired/transferred 必填，须为已知岗位 |
| seq | 建议 | 人事版本号；`seq <=` 当前值视为乱序旧事件，忽略（不恢复已撤销权限） |
| reason | 否 | offboarded 撤权原因 |

响应 200：`{ ok, idempotent, stale?, applied, person }`；400 字段错误；401 验签失败；403 租户冲突。

### POST /integrations/m13（M13 → M01，独立 HMAC）
Agent 注册/撤销。字段：`event_id, tenant_id, type(agent.registered|agent.revoked), agent_id, owner_person_id, parent_agent_id(可选), scopes[]`。

- 注册时 owner 必须为同租户 active 人员。
- 子 Agent 申请的 scopes 会被收敛为父 Agent scopes 的子集（`clamped_scopes` 返回被剔除项），**不做隐式继承/提权**。
- 撤销级联子 Agent。

### POST /kernel/v1/capabilities（POMS → M01）
`{ principal_id, tenant_id? }` → 该主体当前能力清单，含 `require_approval` 标记。人员取岗位策略，Agent 取注册 scopes。

### POST /kernel/v1/authorize（各服务 → M01）
`{ tenant_id, principal_id, resource, action }` → Guard 决策：

| decision | 语义 |
|---|---|
| ALLOW | 放行 |
| DENY | 拒绝（撤销/离职/租户越界/无能力/无 scope） |
| REQUIRE_APPROVAL | 高风险动作（data.export / payment.execute / device.control），无批准票据不得执行 |

决策只依据服务端权威状态；`principal_id` 取自 Body 仅限 MVP（风险 R07，正式版由 JWT claim 推导）。

### POST /kernel/v1/revocations（管理员 → M01）
`{ principal_id, reason }` → 立即撤权：人员撤销级联其全部 Agent；Agent 撤销级联子 Agent。撤权后所有决策端 DENY。

### POST /kernel/v1/approvals
**501 Not Implemented**（台账 R08）：DEMO 未实现 maker-checker 审批票据，高风险动作保持 REQUIRE_APPROVAL / fail-closed。

### POST /kernel/v1/master/say（负责人 ⇄ 总控 Agent 自然语言对话）
`{ tenant_id, text }` → 对话式指令（交互范式移植自 multica Mika 总控智能体 quick_actions 协议）：

```json
{ "trace_id": "...", "role": "assistant", "content": "回复或提问（大白话）",
  "quick_actions": [ { "label": "普通员工", "prompt": "让 P_1001 入职，岗位 POS_STAFF", "primary": true } ],
  "decision": "EXECUTED|DENY|REQUIRE_APPROVAL?", "command": "...?", "output": ...? }
```

- 规则路由（无 LLM）：通俗语言 → 意图（入转调离/子 Agent/能力/判权/撤权/状态/审计/注册总控/问候）；ID 与 岗位/scope 从文本正则抽取（P_x/POS_x/A_x、经理→POS_MANAGER、task:execute）。
- **缺参数或歧义 → 不执行**，返回 `content` 提问 + `quick_actions` 选项：≤3 条、恰好一个 `primary`、`prompt` 为自包含完整指令——点选后作为下一轮用户消息发送，**无服务端隐藏状态**。
- **高风险 → 先确认**（Preview and confirm）：离职/撤销类先问"确认执行？"；确认后仍走 dispatchCommand，无票据 REQUIRE_APPROVAL（fail-closed）。
- 响应契约：仅 `content` 为必选；带 `decision+command` 表示已实际执行了一条白名单指令（见 /master/command）。

### POST /kernel/v1/master/register（管理员 → M01）
`{ tenant_id, owner_person_id }` → 注册总控 Agent（`A_MASTER_KERNEL`），作为负责人指令的唯一执行者：
- 自动创建/校验负责人（POS_OWNER），总控 Agent 纳入统一 Agent 身份治理（可被撤权、进状态表、进审计）。
- 指令集为白名单 `MASTER_COMMANDS`（10 条），不可越权执行白名单外动作。

### POST /kernel/v1/master/command（负责人 → 总控 Agent → 子系统）
`{ tenant_id, command, params }` → `{ trace_id, command, decision: EXECUTED|DENY|REQUIRE_APPROVAL, reason, output }`

| command | 参数 | 说明 |
|---|---|---|
| person.hire / person.transfer | person_id, position_id | 经 M07 通道执行入/调岗 |
| person.offboard ▲ | person_id | 高风险，fail-closed |
| agent.register / agent.revoke ▲ | agent_id, scopes, parent_agent_id | 经 M13 通道，子 Agent scope 收敛 |
| principal.revoke ▲ | principal_id | 高风险，fail-closed |
| capabilities.query / authorize.check | principal_id[, resource, action] | POMS 通道查询/判权 |
| system.status / audit.tail | [limit] | 系统观测 |

**红线**：高风险指令（▲）必须持批准票据，DEMO 审批为 501 → 无票据 `REQUIRE_APPROVAL` 不执行、持票据 `DENY`（总控不能绕过 maker-checker）；总控被撤权后一切指令 DENY；跨租户指令 DENY；每条指令独立 trace_id 进审计（`master.command`）。

### GET /kernel/v1/status | GET /kernel/v1/audit
运行状态摘要 / 最近 100 条审计记录（均需管理令牌）。

## 3. DEMO 种子数据
- 租户 `TENANT_A`；岗位 `POS_STAFF`（task:execute, report:read）、`POS_MANAGER`（另含 report:approve, data:export）。
- 高风险清单：`data.export`、`payment.execute`、`device.control`。

## 4. 已知边界（不得视为生产契约）
内存存储、无时间戳/nonce 防重放（R05）、单管理令牌（R01）、无 OIDC/MFA、无 M02 Outbox（R06）、审批 501（R08）、ABAC 简化（R09）。
