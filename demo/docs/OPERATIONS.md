# M01 Docker 现场操作流程与验证记录

## 1. 访问地址

- M01 控制台：`http://192.168.0.71:3101/`
- 健康检查：`http://192.168.0.71:3101/health`
- 旧 EBMS：`http://192.168.0.71:3010/index.html`

M01 当前是 `DEMO ONLY` 内网部署。人员数据在容器内存中，容器重启时从只读挂载的 EBMS 数据目录重新导入。

## 2. 启动和检查 Docker

在 M01 仓库目录执行：

```powershell
cd work\Kernel-M01
docker compose -p aeos-m01 -f demo/docker-compose.yml ps
docker compose -p aeos-m01 -f demo/docker-compose.yml logs -f
```

正常结果：

- 容器名：`aeos-m01-kernel`
- 端口：`3101:3101`
- 状态：`healthy`
- 启动日志包含 `EBMS personnel imported`

## 3. 人员导入和筛选

当前导入源为 EBMS `org_personnel`，旧账号表 `users` 只用于账号匹配，不导入密码。

手工运行导入验证：

```powershell
cd work\Kernel-M01\demo
node scripts/import-ebms-personnel.mjs
```

筛选示例：

```powershell
# 只查看可唯一关联旧账号的人员
node scripts/import-ebms-personnel.mjs --account=linked

# 按部门筛选测试样本
node scripts/import-ebms-personnel.mjs --department=研发中心 --limit=3
```

当前数据验证结果：

| 项目 | 结果 |
|---|---:|
| EBMS 用户账号 | 14 |
| EBMS 组织人员 | 147 |
| 成功导入 M01 | 147 |
| 唯一关联旧账号 | 9 |
| 同名账号冲突 | 1 |
| 没有账号关联 | 137 |
| 重复导入幂等 | 147 |

账号冲突和岗位未映射只进入复核清单，不自动授予更高权限。

## 4. 控制台操作流程

1. 打开 M01 控制台。
2. 填写管理员 Token；Token 不从 EBMS 旧密码继承。
3. 确认顶部健康状态显示 `online`。
4. 点击“刷新状态”，确认人员数量为 147。
5. 在“人员 ID”输入框填写实际人员，例如 `P_EBMS_276`。
6. 点击“查能力”，确认导入人员具备普通 `task:execute` 能力。
7. 点击“判权：任务”，预期返回 `ALLOW`。
8. 点击“判权：导出”，普通员工不得越权，预期返回 `DENY`。
9. 点击“刷新审计”，确认导入、判权和撤权动作带有 `trace_id`。

## 5. 审批和撤权流程

高风险动作必须经过审批票据：

```text
申请审批
  → 审批人决定 APPROVE / REJECT
  → 票据绑定租户、动作、对象和参数摘要
  → 总控命令携带 approval_ticket
  → 一次性消费
  → 执行结果和审计记录
```

建议用临时人员 `P_FLOW_TEST` 做演练，不直接撤销真实人员：

1. 注册总控 Agent。
2. 通过总控命令让 `P_FLOW_TEST` 入职。
3. 创建 `person.offboard` 或 `principal.revoke` 审批。
4. 使用不同的审批人执行 `APPROVE`。
5. 携带审批票据执行高风险命令。
6. 再次使用同一票据，必须返回 `DENY`。
7. 查询授权，临时人员应为 `DENY`。

## 6. Docker 验证命令

以下检查不显示管理员 Token：

```powershell
$envList = (docker inspect aeos-m01-kernel | ConvertFrom-Json)[0].Config.Env
$token = ($envList | Where-Object { $_ -like 'M01_ADMIN_TOKEN=*' }) -replace '^M01_ADMIN_TOKEN='
$headers = @{ Authorization = "Bearer $token"; 'Content-Type' = 'application/json' }

Invoke-WebRequest http://127.0.0.1:3101/health -UseBasicParsing
Invoke-RestMethod http://127.0.0.1:3101/kernel/v1/status -Headers $headers

$body = @{ tenant_id='TENANT_A'; principal_id='P_EBMS_276'; resource='task'; action='execute' } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:3101/kernel/v1/authorize -Method Post -Headers $headers -Body $body
```

## 7. 本次 Docker 流程验证结果

本次使用正在运行的 `aeos-m01-kernel` 容器完成：

| 检查项 | 结果 |
|---|---|
| `/health` | `ok` |
| Docker 健康状态 | `healthy` |
| M01 导入人员数 | 147 |
| 实际人员 `P_EBMS_276` 普通任务 | `ALLOW` |
| 实际人员跨租户访问 | `DENY` |
| 临时人员入职 | `EXECUTED` |
| 审批票据 | `APPROVED` |
| 携票据执行高风险操作 | `EXECUTED` |
| 重放同一票据 | `DENY`，已消费 |
| 撤权后再次授权 | `DENY` |
| 审计事件 | `approval.requested`、`approval.decided`、`approval.consumed` |

## 8. 回滚和重启

只重启 M01：

```powershell
docker compose -p aeos-m01 -f demo/docker-compose.yml restart m01-kernel
```

停止 M01：

```powershell
docker compose -p aeos-m01 -f demo/docker-compose.yml down
```

重新部署：

```powershell
docker compose -p aeos-m01 -f demo/docker-compose.yml up -d --build
```

EBMS 数据目录以只读方式挂载，M01 的导入过程不会修改 EBMS 原始数据。
