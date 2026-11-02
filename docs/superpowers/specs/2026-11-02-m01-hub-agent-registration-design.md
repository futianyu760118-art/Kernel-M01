# M01 ↔ AEOS Hub Agent 注册设计

## 意图

将 HUB 的中枢 Agent `agent-aeos-hub` 纳入 M01 统一身份和授权治理，并让以后在 HUB 新注册的 Agent 自动同步到 M01。HUB 负责运行时、模型、会话和展示；M01 负责 Agent 身份、租户、Owner、scope 收敛、授权判定和撤权。

## 范围

目标包括：

- 为 HUB 提供一个受保护的 M01 Bridge，调用现有 M01 `/integrations/m13` 签名入口；
- 首次将 `agent-aeos-hub` 注册为 M01 Agent，并记录同步结果；
- HUB 新建 Agent 时自动注册到 M01，保存 M01 Agent ID、状态、authz version 和最近同步时间；
- M01 拒绝或收敛 scopes 时，HUB 不得把 Agent 标记为可用；
- 注册请求具备确定性 event_id、HMAC 验签、超时和可重试能力；
- 在 HUB 与 M01 控制台显示注册状态和失败原因；
- 对 Agent 撤销保留 M01 的级联撤权能力，并在 HUB 显示已撤销状态。

非目标：本期不把所有既有 HUB Agent 一次性导入；不让 HUB 签发 M01 权限；不引入 EDP 事件总线；不改变 M01 的业务数据边界；不把 Agent 的运行时状态直接当成授权状态。

## 方案选择

1. HUB 主动调用 M01：复用现有 M13 HMAC 事件契约，HUB 注册后同步 M01。改动集中、容易重试，M01 仍是授权事实源。**本期采用。**
2. M01 回调 HUB：需要新增 HUB 入站回调、验签、重试和补偿，双方耦合更强，暂不采用。
3. 经 EDP/事件总线异步同步：适合生产规模，但需要额外的 Outbox、Registry 和 DLQ，本期先不引入。

## 交互设计

```text
HUB 创建 Agent
  → 生成确定性 registration event_id
  → M01 Bridge 用 M13_SECRET 签名 POST /integrations/m13
  → M01 校验租户、Owner、父 Agent 和 scopes
  → M01 返回 active 或 clamped_scopes / error
  → HUB 保存 m01Registration 状态
  → 只有 active 的 Agent 才能作为可用执行 Agent
```

M01 侧继续使用已有字段：`event_id`、`tenant_id`、`type`、`agent_id`、`owner_person_id`、`parent_agent_id`、`scopes`。HUB 侧新增的同步元数据只描述对接状态，不复制 M01 的授权事实。

建议配置项：

- `M01_AGENT_REGISTRATION_URL`：默认指向 M01 `/integrations/m13`；
- `M01_AGENT_REGISTRATION_SECRET`：与 M01 `M13_WEBHOOK_SECRET` 配对；
- `M01_AGENT_TENANT_ID`：HUB 默认租户；
- `M01_AGENT_OWNER_PERSON_ID`：HUB 中枢 Agent 的 M01 Owner。

## 安全与失败处理

- 原始 JSON 请求体使用 HMAC-SHA256，发送 `X-M01-Signature: sha256=...`；
- 不把 secret 写入 Harbor 文件、浏览器或 Evidence payload；
- event_id 与 Agent ID、事件类型、租户绑定，重复发送依赖 M01 Inbox 幂等；
- 超时、5xx 和网络错误进入 `pending/retryable`，不会把 Agent 标记为 active；
- 4xx 业务拒绝进入 `rejected`，保留脱敏原因；
- M01 返回的 `clamped_scopes` 作为最终授权 scope，HUB 不得恢复被剔除的 scope；
- 删除或停用 HUB Agent 不直接绕过 M01，必须发送 `agent.revoked` 或由管理员在 M01 撤权；
- 运行时在线不代表 Agent 已获 M01 授权，两个状态分别展示。

## 验收标准

- HUB 现有 `agent-aeos-hub` 可通过一次受保护操作注册到 M01；
- 重复注册不会生成重复 Agent 或重复权限；
- 新注册 Agent 在 M01 拒绝时，HUB 显示失败且不能派发任务；
- 父 Agent scope 超出范围时，HUB 展示 M01 返回的收敛结果；
- M01 撤销 Agent 后，M01 判权为 `DENY`，HUB 下次刷新能看到撤销状态；
- secret、完整请求体和个人信息不出现在日志或 Evidence；
- M01 和 HUB 各自的现有测试通过，并增加注册成功、拒绝、重试/幂等和 scope 收敛测试。

