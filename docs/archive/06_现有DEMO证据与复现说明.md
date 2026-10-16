# M01 V1.0 DEMO 已有证据与局限

**2026-10-16 本地复验：** 在本归档来源目录 `AEOS_M01_Kernel_MVP` 运行 `npm test`，结果 TAP：3 passed、0 failed。

## 来源构成
- `src/server.js`：Node.js HTTP API、开发 Bearer Token、M07/M13 HMAC 入站、授权/能力/撤权/状态接口。
- `src/kernel.js`：内存身份与岗位、Agent、Guard 决策及内存审计。
- `public/index.html`：开发管理页面。
- `test/integration.test.js`：3 个本地 Node 自动测试。
- `docs/CONTRACTS.md`：**候选**接口格式。

## 复现
1. 安装 Node.js >=20；解压 `source_demo`。
2. `cd source_demo && npm test`。
3. `npm start`，本机浏览 `http://localhost:3101`；只用去敏化演示数据。
4. Windows 使用 `start_windows.bat`；若 3101 端口占用，先停止占用的程序。

## 证据评级
| 主张 | 当前状态 | 证据 |
|---|---|---|
| API 与 UI 代码存在 | VERIFIED | 本归档 source_demo |
| 自动化 3 项通过 | VERIFIED / L2 | 本地 Node test 输出 |
| M07 真实源同步 | NOT VERIFIED | 无真机请求响应/Trace |
| M13 真实 Agent 编排接通 | NOT VERIFIED | 无真机请求响应/Trace |
| POMS 在线 UI 和 API 接通 | NOT VERIFIED | 无真机请求响应/Trace |
| 生产安全合格 | NOT VERIFIED | 缺 MFA/OIDC/审计持久化/全面安全测试 |

此文档仅记录可复现的客观事实，未经系统 Owner 复核的结果不作为正式签批。