# HLC 系统中 M01 设计相关内容（参考引用）

**来源仓库**：https://github.com/futianyu760118-art/HLC.git（master 分支）
**来源 Commit**：`c2b013d99b9961b616bae3ce389bc41fb1b002b5`（2026-10-08，由 Gitee 镜像推送）
**复制日期**：2026-10-17 | **复制方式**：GitHub API zipball 下载后按清单筛选，SHA256 逐一校验一致
**性质**：只读参考代码，供 M01 Kernel 设计对照；不参与本仓库构建，不得直接当作 M01 实现使用。

## 背景

HLC 是一个多租户 SaaS HR/学习平台（Flask + React），仓库内**没有**以 "M01/AEOS" 命名的文件，但其代码中存在与 M01（统一身份与访问控制内核：RBAC+ABAC、Policy、Guard、撤权、隔离、审计）设计高度同构的实现集群。本目录按以下映射收录 37 个文件，路径保持 HLC 仓库原样。

## 文件清单与 M01 设计映射

### 1. 身份认证（对应 M01-003 OIDC/Token 校验的现有参照）
| 文件 | 说明 |
|---|---|
| `backend-python/routes/auth.py` | 登录/JWT 会话管理 |
| `backend-python/services/security_service.py` | 安全服务（密码/锁定等） |
| `backend-python/services/current_user_helper.py` | 当前用户上下文推导 |

### 2. 授权 / 策略 / 守卫 / 决策（对应 M01-004 RBAC+ABAC、Guard/PEP/PDP）
| 文件 | 说明 |
|---|---|
| `backend-python/services/authz.py` | 全局 RBAC：角色集合 + 资源×读/写角色矩阵（`RESOURCE_ACCESS`） |
| `backend-python/services/platform_entitlements.py` | 平台权益/配额（撤权语义参照） |
| `backend-python/services/approval_workflow.py` | 审批工作流（对应 M01-011 maker-checker） |
| `backend-python/services/assistant_core/guardrails.py` | 护栏体系 |
| `backend-python/services/assistant_core/intent_router/permission_gate.py` | 权限门（执行时 PEP 参照） |
| `backend-python/services/assistant_core/intent_router/route_decision.py` | 路由决策 |

### 3. Agent 内核核心（对应 M01-006 Agent 身份与委托链的架构参照）
`backend-python/services/assistant_core/agent_kernel/` 下：`kernel.py`（内核主入口）、`policy.py`（策略）、`decision.py`（决策）、`input_guards.py`、`planner_guard.py`（守卫）、`context.py`（身份/执行上下文）、`execution.py`、`state.py`、`confirmations.py`（高风险写操作确认，对应 REQUIRE_APPROVAL/票据）、`observability.py`。

### 4. 审计（对应 M01-008 审计事件）
| 文件 | 说明 |
|---|---|
| `backend-python/services/assistant_core/audit.py` | 审计记录 |
| `backend-python/tests/test_audit_remediation.py` | 审计修复测试 |

### 5. 租户隔离（对应 M01-004 租户边界、TC09）
| 文件 | 说明 |
|---|---|
| `backend-python/services/tenancy.py` | 租户上下文与隔离 |

### 6. 组织 / 人员 / 岗位 统一身份（对应 M01-001/002）
| 文件 | 说明 |
|---|---|
| `backend-python/models.py` | User/Employee/Org/Role 等全部数据模型 |
| `backend-python/schema_migrations.py` | 建表与权限/角色表结构演进 |
| `backend-python/services/org_structure.py` | 组织结构 |
| `backend-python/services/user_employee_binding.py` | User↔Employee 幂等绑定（统一身份映射） |
| `backend-python/services/oa_employee_sync.py` | OA 员工同步（M07 人事事件源的现网参照） |
| `backend-python/routes/org.py`、`routes/headcount.py` | 组织/编制接口 |

### 7. 测试与行为规格（对应 04_验收标准 的负向/越权用例风格）
- `backend-python/tests/assistant_core/intent_router/test_permission_gate.py`
- `backend-python/tests/assistant_core/test_permission_desensitization.py` → `test_permission_desensitization_cases.py`
- `backend-python/tests/test_org_structure.py`、`test_oa_employee_sync.py`、`test_user_employee_binding.py`
- `scripts/verify-assistant-write-permission-api-gate.mjs`（写权限 API 门 E2E）
- `scripts/verify-assistant-ui-write-permission-gate.mjs`（UI 权限门 E2E）
- `scripts/verify-assistant-ui-approval-decision-e2e.mjs`（审批决策 E2E）

## 与 M01 归档文档的关系

- HLC 的 `authz.py` 属于简化 RBAC（角色硬编码集合），正是归档 `05_风险台账.md` R09（"RBAC 简化且 ABAC 不完整"）描述的形态——可作反面/基线参照。
- `user_employee_binding.py` / `oa_employee_sync.py` 可作为 M01-005（M07 入转调离事件适配）的真实系统对照，验证归档 `03_联调方案.md` 中接口字段设计的现实合理性。
- `confirmations.py` + `approval_workflow.py` 可作为 M01-011（maker-checker 审批与一次性票据）的实现参照。
- HLC 无 OIDC/mTLS/Webhook 签名防重放，M01 生产要求仍以归档文档为准。

## 完整性

本目录 37 个文件与来源 commit `c2b013d` 逐一比对 SHA256 一致；根目录 `MANIFEST_SHA256.txt` 已覆盖。HLC 仓库无 LICENSE 文件，本目录仅作同 Owner 体系内的设计参考引用。
