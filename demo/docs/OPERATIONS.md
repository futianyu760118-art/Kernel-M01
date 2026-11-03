# AEOS M01 正式部署与操作流程

## 1. 运行边界

正式环境使用：

```text
浏览器 → Keycloak OIDC/PKCE → M01 Kernel → PostgreSQL
HUB   → M13 HMAC → M01 Agent 注册/状态查询
M07   → M07 HMAC → M01 人员事件
```

M01 负责人员、岗位、Agent、授权、审批、撤权和审计；M03/EBMS 仍负责经营业务事实。M01 不保存或继承 EBMS 旧密码。

## 2. 配置和启动

复制 `demo/.env.example` 为 `demo/.env`，替换 PostgreSQL 密码、M07/M13 密钥、OIDC 地址和身份映射，不把真实值提交到 Git。

正式模式至少需要：

```dotenv
M01_MODE=oidc
M01_DATABASE_URL=postgres://m01:<password>@postgres:5432/m01
M01_OIDC_ISSUER=https://keycloak.example/realms/aeos
M01_OIDC_AUDIENCE=aeos-m01
M01_OIDC_JWKS_URI=https://keycloak.example/realms/aeos/protocol/openid-connect/certs
M01_OIDC_CLIENT_ID=aeos-m01-console
M01_OIDC_REDIRECT_URI=https://m01.example/
M01_EXTERNAL_IDENTITIES_JSON=[{"issuer":"https://keycloak.example/realms/aeos","subject":"<sub>","principal_id":"P_OWNER","tenant_id":"TENANT_A"}]
```

启动：

```powershell
cd work\Kernel-M01
docker compose -p aeos-m01 -f demo/docker-compose.yml up -d --build
docker compose -p aeos-m01 -f demo/docker-compose.yml ps
```

PostgreSQL 使用命名卷 `m01-postgres-data`；M01 只有在数据库健康、迁移完成和快照恢复成功后才会通过 `/readyz`。

## 3. 健康检查

```powershell
Invoke-RestMethod http://127.0.0.1:3101/health
Invoke-RestMethod http://127.0.0.1:3101/readyz
docker compose -p aeos-m01 -f demo/docker-compose.yml logs --tail=100 m01-kernel

$env:M01_SMOKE_TOKEN='<仅本地测试 Token>'
$env:M01_SMOKE_EXPECT_PERSONS='147'
npm --prefix demo run smoke:docker
```

预期：`/health` 返回服务信息，`/readyz` 返回 `status=ready` 和 `persistence=postgres`。数据库不可用时，M01 不应接受变更请求。

## 4. EBMS 人员导入和筛选

EBMS 数据目录以只读方式挂载，启动时只导入人员和账号匹配元数据，不导入密码：

```text
EBMS org_personnel → M01 Person
EBMS users         → 账号匹配结果（direct / unique_name / ambiguous / none）
```

筛选变量：

```dotenv
M01_EBMS_IMPORT_STATUS=all
M01_EBMS_IMPORT_ACCOUNT=all
M01_EBMS_IMPORT_DEPARTMENT=
```

筛选规则只影响导入候选集；冲突和无账号人员进入复核，不自动提升权限。重复导入使用稳定人员键，不能覆盖本地撤权；缺少上游行不能推断为离职。

## 5. 浏览器登录和注册总控

1. 打开 `https://m01.example/`。
2. 点击“使用企业账号登录（OIDC）”。浏览器跳转 Keycloak，使用 Authorization Code + PKCE（S256）。访问 Token 只保存在当前页面内存，不写 `localStorage`。
3. 登录成功后点击“刷新状态”，确认人员、Agent 和审计数量已恢复。
4. 确认负责人 Person 已由 M07 导入并处于 `active`，且已配置 `POS_OWNER`。
5. 点击“注册/恢复总控 Agent”。请求固定使用 `A_MASTER_KERNEL` 和幂等键：

   ```text
   tenant_id + owner_person_id + A_MASTER_KERNEL
   ```

6. 成功后页面显示“总控在线”，响应包含 `trace_id`。再次点击只返回既有 active 总控，不创建重复 Agent。
7. 正式模式不会自动创建 Owner，也不会通过重复注册恢复已撤销总控；这两类情况必须走明确的人员/管理员恢复流程。

## 6. 人员变动办理（M07 → M01）

日常流程只有一条：

```text
M07 填写人员资料
→ 提交到 M01
→ M01 管理员审核
→ M01 办理人员状态
→ 各系统同步
→ 查看结果并完成闭环
```

M07 提交时必须写清楚：人员、工号、单位、部门、工作岗位、职位名称、办理事项、生效时间和原因。离职时还要确认该人员负责的工作、账号、系统权限和智能体配备是否已经交接。

管理员也可以直接在 M01 的“人员变动”页面新建申请。直接新建和 M07 提交会进入同一个列表，不会形成两套流程。

操作步骤：

1. 在 M07 选择“人员变动”，输入姓名或工号，确认当前在职状态。
2. 填写部门、工作岗位、职位名称、办理事项、生效时间和原因，提交到 M01。
3. M01 管理员打开“人员变动”，查看资料和办理影响，选择“同意”或“拒绝”，写明说明。
4. 选择“同意”后，M01 先办理本人的状态，再把结果发送给各系统；页面显示每个系统“已完成、处理中、重试中或失败”。
5. 所有系统显示“已完成”后，才把申请标记为“已完成”；失败项必须重试或人工处理。

离职人员默认从在职名单隐藏，转到“已离职人员”；账号、系统权限、正在办理的工作和智能体配备进入交接清单。复职不会自动恢复原有高风险权限和智能体配备，必须由管理员重新确认。

接口对应关系：

| 操作 | 接口 |
|---|---|
| M07 提交 | `POST /integrations/m07`，`type=person.change.request` |
| M01 查看 | `GET /kernel/v1/person-change-requests?tenant_id=...` |
| M01 直接新建 | `POST /kernel/v1/person-change-requests` |
| M01 审核并办理 | `POST /kernel/v1/person-change-requests/:request_id/decision` |

## 7. HUB Agent 接入

在 HUB 中按以下路径操作：

```text
登录 HUB → AI 港湾 → 智能体目录 → 对 AEOS 中枢 Agent 点击“接入 M01”
```

HUB 发送 M13 HMAC 事件，固定携带：

- `event_id` / `idempotency_key`：按租户、Agent 和事件类型稳定生成；
- `owner_person_id`：M01 中已存在且 active 的 Owner；
- `scopes`：申请范围，最终以 M01 返回的 `effectiveScopes` 为准。

页面状态含义：`同步中` 表示可重试，`M01 已接入` 才允许任务执行，`M01 已拒绝/已撤销` 不可调度。M01 重启后可安全重试，重复事件不会重复创建 Agent。

## 8. 业务操作验证

使用实际导入人员或专用测试人员，按页面“系统操作流程”执行：

```text
检查健康 → 刷新人员 → 筛选/复核 → 注册总控
→ 查询能力 → 普通判权 → 高风险申请审批
→ 携审批票据执行 → 拉取审计
```

验收重点：

- 普通人员 `task:execute` 按岗位返回；
- `data:export`、`device:control` 等高风险动作必须 `REQUIRE_APPROVAL`；
- 申请人不能审批自己；审批票据只能消费一次；
- 撤权后旧 Token、WebSocket、Agent 和后台任务均不得继续执行；
- 所有关键响应都有 `trace_id`，可在审计中检索。

## 9. 重启恢复演练

```powershell
docker compose -p aeos-m01 -f demo/docker-compose.yml restart m01-kernel
Invoke-RestMethod http://127.0.0.1:3101/readyz
```

恢复后检查：总控仍为 `active`、导入人员仍存在、HUB Agent 仍为 `active`、审批和审计记录仍存在。若 M01 启动失败，先查看数据库和迁移日志，不要清理 `m01-postgres-data`。

## 10. 备份、回滚和故障处理

备份 PostgreSQL：

```powershell
docker exec aeos-m01-postgres pg_dump -U m01 -d m01 -Fc -f /tmp/m01.dump
docker cp aeos-m01-postgres:/tmp/m01.dump .\m01.dump
```

回滚应用版本时只替换镜像并保留数据库卷；恢复数据库前先停止 M01、验证备份，再执行恢复演练。禁止用 `docker compose down -v` 作为普通回滚命令，因为它会删除控制面数据卷。

## 11. DEMO 边界

本地 DEMO 只能显式设置 `M01_MODE=demo`，并使用手工 Bearer Token；它不是生产登录方式。生产必须使用 OIDC、PostgreSQL、HTTPS、密钥管理和正式备份恢复演练。

测试命令：

```powershell
cd work\Kernel-M01\demo
npm test
$env:M01_TEST_DATABASE_URL='postgres://m01_test:m01_test@127.0.0.1:55432/m01_test'
npm run test:postgres
```
