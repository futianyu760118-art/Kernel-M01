# AEOS M01 Kernel DEMO（开发说明）

M01 第一阶段 MVP 可运行 DEMO：统一人员/组织/岗位/Agent 的身份与授权决策（RBAC 基线 + 简化 ABAC + Guard + 撤权 + 内存审计），内置**总控 Agent（A_MASTER_KERNEL）**作为负责人指令的唯一执行者，统管 M07 人事、M13 Agent、POMS 判权与系统观测通道。

**状态：DEMO ONLY / 证据等级 L2**（本地自动化契约测试），对应归档 `docs/archive/06_现有DEMO证据与复现说明.md`。M07/M13/POMS 真实环境联调（L3/L4）尚未完成，不得作为生产系统使用。

## 目录结构

```
demo/
  src/server.js           # Node.js HTTP API：开发 Bearer/OIDC、M07/M13 HMAC 入站、授权/审批/撤权/状态接口
  src/kernel.js           # 内存身份与岗位、Agent、Guard 决策、审批票据、撤权状态机、内存审计
  public/index.html       # 开发管理页面（浏览器直连演示）
  test/*.test.js          # 本地自动化契约测试（node:test，零依赖）
  docs/CONTRACTS.md       # 候选接口契约（L1）
  src/legacy-import.js    # EBMS 人员导入、账号冲突识别、筛选和幂等迁移
  scripts/import-ebms-personnel.mjs # 使用真实 EBMS 数据做导入/筛选/授权验证
  .env.example            # 环境变量模板
  start_windows.bat       # Windows 一键 测试+启动
  Dockerfile / docker-compose.yml
```

## 环境要求

- Node.js >= 20（零 npm 运行时第三方依赖）。

## 快速开始

```bash
cd demo
npm test        # 期望：全部通过
npm start       # http://localhost:3101（默认 0.0.0.0:3101）

# 使用工作区中的 EBMS 数据做一次真实人员导入验证
node scripts/import-ebms-personnel.mjs
# 只筛选已有唯一登录账号的人员
node scripts/import-ebms-personnel.mjs --account=linked
# 按组织筛选并限制测试样本
node scripts/import-ebms-personnel.mjs --department=研发中心 --limit=3
```

- Windows：双击/执行 `start_windows.bat`（先测试后启动）。
- 端口占用时：先停止占用 3101 的程序，或在 `.env` 中改 `PORT`。
- Docker：`cp .env.example .env`（替换全部密钥）→ `docker compose up --build`。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| PORT | 3101 | 监听端口 |
| HOST | 0.0.0.0 | 监听地址 |
| M01_ADMIN_TOKEN | local-demo-admin | 开发管理令牌（仅限本机 DEV） |
| M07_WEBHOOK_SECRET | local-demo-m07-secret | M07 Webhook HMAC 密钥 |
| M13_WEBHOOK_SECRET | local-demo-m13-secret | M13 Webhook HMAC 密钥（独立轮换） |

## 本地演示链路（对应页面按钮）

1. **总控通道 / 负责人指令台（大白话对话）**：注册总控 Agent（绑定负责人 P_OWNER）→ 主区对话输入框用通俗语言下指令（如「让 P_1001 入职」「把 P_1001 调到经理岗」「看看系统状态」）；总控 Agent（下属）即时回报；缺参数或不能执行时以**选项 chips** 追问（点选即答，≤3 项含主选项，范式移植自 multica Mika）；高风险指令先确认、无票据 fail-closed；指令历史点击回填输入框。
2. M07 入职（HMAC 签名事件）→ 人员激活并绑定岗位。
3. POMS 查询能力 → 仅返回岗位策略允许的能力。
4. authorize：普通动作 ALLOW；未授权动作 DENY；高风险（数据导出/付款/生产控制）REQUIRE_APPROVAL。审批申请、审批决定和一次性票据消费由 M01 DEMO 闭环演示。
5. M13 注册主/子 Agent：子 Agent 申请超出父 scope 的权限被收敛，无隐式提权；总控 Agent 同受治理——撤权 `A_MASTER_KERNEL` 后所有指令 DENY，重注册恢复。
6. M07 离职 / 强制撤权 → 人员及其全部 Agent 立即 DENY；重复 event_id 幂等；乱序旧事件不恢复权限。

### EBMS 人员导入规则

- 主人员源为 EBMS `org_personnel`，没有该表时回退到 `personnel`；当前工作区实际读取到 147 条人员记录。
- EBMS `users` 只用于识别登录账号映射，密码字段永不进入 M01。
- `linked_user_id` 优先；缺失时才按姓名匹配。多账号同名会标记 `AMBIGUOUS_LOGIN_ACCOUNT`，不自动绑定。
- 旧岗位默认映射到最低权限 `POS_STAFF`；明确包含经理/主管/主任/总监/部长/厂长/负责人时才映射 `POS_MANAGER`，其余进入 `POSITION_NOT_MAPPED` 复核清单。
- 导入使用稳定事件 ID，重复执行幂等；重复导入不能恢复已经撤销的人员。

## 运行检查（对应归档 07 手册）

- [x] `npm test`：11/11 通过（含审批票据闭环、归档基线和总控 Agent）。
- [x] `GET /health` 返回 `status: ok`。
- [x] 页面可访问，接口需服务端令牌。
- [x] 可完成本地 M07→POMS→M13→撤权链路。
- [x] 安全状态显式标注 `DEMO ONLY`。

- 顶栏色板可切换系统主题（午夜蓝/紫罗兰/洋红/青金/晨雾白 5 套预设 + 取色盘**任意自定义色**：单色自动推导整套主题，自动避开决策语义色区间），选择记忆在浏览器本地。

## 安全约束（红线）

- `local-demo-admin` 等默认令牌**禁止**用于公网或共享网络；生产必须 OIDC + 最小权限 + MFA。
- 内存存储，重启丢失；不可用于真实授权、资金或设备控制。
- `principal_id` 取自请求 Body 仅限 MVP；正式版必须由已验证 JWT claim 与服务端上下文推导（R07）。
- `.env`、真实令牌、个人信息不得提交仓库或证据包。
- Webhook 现无时间戳/nonce 防重放（R05）；正式版须 mTLS/短期凭据/重放窗。

## 与归档文档的关系

- 执行归档见 `../docs/archive/`（00–07）。
- 接口候选契约见 `docs/CONTRACTS.md`（L1，非现网事实）。
- 生产化差距（审批持久化、OIDC/MFA 集成、Outbox、分布式撤权等）见归档 02/05；本 DEMO 已提供内存审批闭环，但仍不得用于生产。
