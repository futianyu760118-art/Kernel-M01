# M01 控制台设计规范（UI Design System v1.0）

> 角色：interface-design（跨会话一致性锚点）。本规范由「UI 设计自动化工作流」生成（路线：产品后台/仪表盘）。
> 任何新增页面、组件或修改 `public/` 下 UI，必须遵循本规范，保证 W01–W36 全周期演示的视觉一致性。

## 1. 设计定位

- **气质**：安全内核控制台 —— 可信、冷静、精密。信息密度高但不拥挤。
- **主题**：深色（安全运维场景惯例）；桌面优先（≥1280px 最佳），平板/移动可用（侧栏折叠）。
- **约束**：单文件零依赖（原生 HTML/CSS/JS），所有交互即时可撤销、有反馈。

## 2. Design Tokens（唯一色彩/度量来源，禁止硬编码散色）

### 色彩

| Token | 值 | 用途 |
|---|---|---|
| `--bg-0` | `#0b0e13` | 页面底色 |
| `--bg-1` | `#11151c` | 卡片/面板 |
| `--bg-2` | `#171d27` | 输入框/嵌套块/悬浮 |
| `--bg-hover` | `#1c2432` | 行/按钮悬浮态 |
| `--line` | `#232b38` | 1px 边框 |
| `--line-strong` | `#313c4e` | 强调边框 |
| `--text-1` | `#e6ecf5` | 主文本 |
| `--text-2` | `#9aa8bd` | 次级文本 |
| `--text-3` | `#5d6b80` | 弱化文本/占位 |
| `--brand` | `#4f7cf0` | 主操作/品牌 |
| `--brand-strong` | `#3f6ae0` | 主操作悬浮 |
| `--allow` | `#34c98e` | 决策 ALLOW |
| `--deny` | `#f0564f` | 决策 DENY / 错误 |
| `--warn` | `#e8a33d` | REQUIRE_APPROVAL / 高风险 |
| `--info` | `#4fb3d9` | 信息/trace |

决策三态是内核语义色，任何展示决策的地方必须使用：`ALLOW→--allow`、`DENY→--deny`、`REQUIRE_APPROVAL→--warn`。

### 度量

| Token | 值 |
|---|---|
| `--radius` | `10px`（卡片）/ `7px`（按钮、输入） |
| `--sp-1 … --sp-5` | `4 / 8 / 12 / 16 / 24px` |
| 字号 | 12（辅助）/ 13（正文）/ 15（组标题）/ 20（页面标题）/ 28（决策大字） |
| 字体 | `-apple-system, "Segoe UI", "Microsoft YaHei", sans-serif`；trace/ID/代码 `"Cascadia Code", Consolas, monospace` |
| 动效 | 快速反馈 `120ms cubic-bezier(.34,1.56,.64,1)`（弹性回弹）；出现 `240ms ease`；禁止 >500ms 的装饰动画 |

### 主题注册表（Theme Registry）
系统整体颜色可选：`<html data-theme="...">` 切换，顶栏色板圆点点选，localStorage（`m01-theme`）记忆。

| 主题 | data-theme | 品牌色 | 底色系 |
|---|---|---|---|
| 午夜蓝（默认） | `midnight` | `#4f7cf0` | 深蓝黑 |
| 紫罗兰 | `violet` | `#8b6ff2` | 深紫黑 |
| 洋红 | `magenta` | `#d65aa8` | 深酒红黑 |
| 青金 | `cyan` | `#38b6c9` | 深青黑 |
| 晨雾白（浅色） | `dawn` | `#3465e0` | 浅灰白（`color-scheme: light`） |

### 自定义主题（无限选色，种子色推导）
色板末位为取色盘（原生 `<input type="color">`，圆点呈 conic 彩虹），任选一色即生成整套主题：

- **推导公式**（`applyCustomTheme`，HSL 域）：`brand = hsl(H, S, 60)`、`brand-strong L51`、`ring/veil α .15–.18`、`ink L84`；底色族 `bg-0..2/hover/side = H, 18%, 5–13%`、`line 16%/24%`、`topbar H,20%,5%,α.88`。
- **约束自动执行**：S 夹取 45–95（保证品牌辨识）；色相落入决策语义区间（绿 150°/红 5°/琥珀 38° ±14°）时自动 +20° 偏移并 toast 告知——红线 3 由代码强制。
- 持久化：`m01-theme=custom` + `m01-theme-color=#rrggbb`；切回预设主题时清空全部行内自定义属性（`style.cssText=''`）。
- 自定义主题恒为深色（对比度由底色明度 5–13% 保证）；浅色需求用预设 `dawn`。

**主题规则**：
1. 主题只允许覆写既有 tokens（品牌/底色/侧栏/文本/微调语义色明度），禁止新增散色；组件一律 `var(--…)` 引用，禁止硬编码色值（focus 光环、气泡、chips 等已全部 tokenize：`--brand-ring / --brand-veil / --brand-veil-strong / --brand-ink / --bg-topbar / --side-*` / `--spin-track`）。
2. **决策语义三色（绿/红/琥珀）色调恒定**，浅色主题可加深明度保证 ≥4.5:1 对比；不得将品牌色混入决策语义。
3. 品牌色不得与 `--allow/--deny/--warn` 同色相区间（避免语义混淆）。
4. 新主题先在本表登记再实现。

## 3. 布局骨架

```
┌──────────────────────────────────────────────┐
│ TopBar: 品牌 · 健康灯+延迟 · DEMO ONLY 徽章     │ sticky
├───────────┬──────────────────────────────────┤
│ Sidebar   │ Main                             │
│ 260px     │  ┌──────────┬──────────┐         │
│ 配置       │  │ 决策结果   │ 主体快照  │  Row1   │
│ M07 人事   │  ├──────────┴──────────┤         │
│ POMS 判权  │  │ 状态表（人员/Agent）  │  Row2    │
│ M13 Agent │  ├─────────────────────┤         │
│ 撤权/观测  │  │ 活动日志流（审计）     │  Row3    │
└───────────┴──┴─────────────────────┴─────────┘
```

- 栅格：`display:grid; grid-template-columns: 260px 1fr; gap: 20px; max-width: 1440px`
- <960px：侧栏折为主区上方横向分组；Row1 两卡纵向堆叠。
- 卡片：`--bg-1` + `--line` 边框 + `--radius`，组标题用 `--text-2` 13px 大写间距。

## 3.5 侧边栏模式（02 深色重底 + 06 分组留白/底部收口）

> 依据 dashboard-sidebar-design skill 选型：信息密度极高的安全运维台 → 02；多分组+底部承载负责人/总控状态 → 06。层级关系：**并置**。

- 底色 `--bg-side: #0e1218`（近黑，非纯黑），边框 `rgba(255,255,255,.06)`；整栏 sticky + 自身滚动。
- 组标题：11px / 500 / letter-spacing .05em / `rgba(255,255,255,.45)`；**组间距 30px ≫ 组内 2px**（分组成立的前提）。
- 导航项（.btn）：无边框透明底，文字 `rgba(255,255,255,.65)`；hover `rgba(255,255,255,.06)`；激活/成功用半透明块（**不用左彩条**，单一表达避免过载）；高风险项琥珀文字 `▲` 前缀。
- 底部收口（.side-anchor）：`1px rgba(255,255,255,.08)` 分割线 + 上下留白，中性色承载 负责人/总控状态/租户，不用主色。
- 避坑：激活态只保留"半透明块+文字变白"一种表达；图标未启用则全案不用图标（保持统一）；导航项按 6 组收纳，单组 ≤7 项。

## 4. 组件规范

### 按钮（统一三态 + 回弹，事件委托实现）
- 默认：`--bg-2` 底 `--line-strong` 边 `--text-1` 字；主按钮 `--brand` 底白字。
- `:active`：`scale(.96)` 弹性回弹（120ms 弹性曲线）。
- `.loading`：隐藏文字、显示 spinner（18px 旋转圈），期间 `disabled`。
- `.success`：变 `--allow` 底 + 对勾 pop 动画，1.2s 后自动恢复。
- 高风险动作按钮（data.export / device.control / 撤权）：文字前置 `▲` 标记 `--warn` 色调边框。

### 输入框
- `--bg-2` 底 `--line` 边；focus：`--brand` 边 + `0 0 0 3px rgba(79,124,240,.18)` 光环。
- label 12px `--text-2`。

### 徽章 / 标签
- 徽章：等宽字体 11px，`DEMO ONLY` 用 `--warn` 描边；HTTP 状态码：2xx→`--allow`、401/403→`--deny`、5xx→`--warn`。

### 表格
- 表头 11px `--text-3` 大写；行高 36px；行悬浮 `--bg-2`；状态列用语义色圆点+文字。

### 日志流
- 最新在顶部或自动滚动到底（选定：追加到尾部 + 自动滚动）；新条目 `fadeUp 240ms`。
- 每条：时间(12px mono) + 类型标签 + 摘要；决策条目整行左侧 3px 语义色条；trace_id 可点击复制（复制成功 toast）。

### Toast
- 右下角浮出，240ms ease，2.5s 自动消失；info/success/error 三语义。

### 指令对话面板（Owner ↔ Agent Chat，自然语言）
负责人与总控 Agent（下属）的大白话对话通道，multica quick_actions 范式：
- 容器：`--bg-0` 底 + `--line` 边，max-height 300px 滚动，`aria-live="polite"`；全宽。
- 负责人消息（.msg.owner）：右对齐，主色 16% 底/35% 边，等宽字体。
- 下属回报（.msg.agent）：左对齐，`--bg-2` 底，按结果语义描边（EXECUTED 绿/DENY 红/REQUIRE_APPROVAL 琥珀）；输出 JSON 用 pre 折叠。
- **选项 chips（.qa）**：跟随提问消息渲染，pill 圆角；≤3 个、恰好一个 primary（主色描边 + "→" 后缀）；`title` 悬浮预览将发送的 prompt（先看后发）；点击即把 prompt 作为负责人消息发送；执行中全部置灰禁用。
- 输入行（.chat-input）：底部通栏文本框 + 发送三态按钮，Enter 发送；初始渲染 3 个开场 starter chips。
- 反馈节奏：打字指示灯（三点 blink）→ 回报（含 trace 可点击复制）。

## 5. 微交互清单（来自 web-advanced-interaction，P0 落地）

| 交互 | 实现 | 状态 |
|---|---|---|
| 按钮三态反馈 | `.loading/.success` + 委托统一管理 | 必须 |
| 按下回弹 | `:active scale(.96)` 弹性曲线 | 必须 |
| 输入 focus 光环 | box-shadow ring | 必须 |
| 日志条目入场 | fadeUp | 必须 |
| 健康灯呼吸 | 2s opacity 脉动（仅在线状态） | 必须 |
| trace 点击复制 | navigator.clipboard + toast | 必须 |

## 6. 新增页面规则

1. 只允许使用第 2 节 tokens（CSS 变量引用），禁止新造颜色/圆角/时长。
2. 新组件先在本规范登记，再实现。
3. 决策语义色不得挪作装饰用途。
4. 保持零依赖单文件；数据一律来自 `demo/docs/CONTRACTS.md` 定义端点。
5. 修改后必须通过：npm test 3/3 + 页面冒烟 + 本规范 checklist。

## 7. impeccable 终检 Checklist

- [ ] 无硬编码色值（除 tokens 定义处）
- [ ] 所有按钮具备三态 + 回弹
- [ ] 决策三处以上展示语义一致（徽章/日志/决策卡）
- [ ] 键盘可达（button/input 原生元素，focus 可见）
- [ ] 空态有占位文案；错误有 toast，不用 alert
- [ ] <960px 无横向滚动
- [ ] `npm test` 3/3；页面 200；健康灯在线
