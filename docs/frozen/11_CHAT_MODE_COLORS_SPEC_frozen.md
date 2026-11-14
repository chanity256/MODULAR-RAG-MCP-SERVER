<!-- 已冻结规格：Chat 检索模式选择器三按钮按模式着色（CMC 系列，CMC1–CMC2 已收口）。纯前端样式增量，只改 frontend/components/chat/ModeSelector.tsx 的选中态配色与对应单测，不动 aria 语义、文案、模式可用性逻辑与后端。基线：WC0–WC10 已收口冻结（ModeSelector 由 WC 系列交付），frozen 01–10 已收口。与其他规格的关系：独立增量，不重开 06_WEBUI_CHAT_SPEC。 -->

# Chat 模式选择器按模式着色规格 (CHAT_MODE_COLORS_SPEC)

> **📌 冻结记录**
>
> - 冻结时间：2026-11-14
> - 完成概要：CMC1–CMC2 共 2 项任务全部收口（`MODE_TONE` 选中态按模式着色 + 单测断言；构建产物 CSS 6 个配色类核验、Playwright e2e 26/26、`tests/e2e_web` 3 项、明暗双模式 × 三模式 6 张截图人工核验文字清晰可读）；验收清单 5 项全部勾选，§7 回写（AGENTS.md / README / MOC / USAGE_GUIDE 10.4）已完成。
> - 遗留：无（Out of Scope 中的 Playground 按钮着色等项按规格 §2.2 约定需要时另起提案，不构成本规格遗留）

> 版本：0.1 — 由候选提案升级为可执行规格
>
> 起源与决策依据：由 `backlog/chat-mode-selector-colors_backlog.md` 单文件晋升，原文见 git 历史（提交 `54ae922`）；提案三问并入 §1.2。提案的起源是会话内用户截图（Web UI Chat 页三个模式按钮同色难辨认，无独立文件）
>
> 基线：[06_WEBUI_CHAT_SPEC_frozen.md](06_WEBUI_CHAT_SPEC_frozen.md) WC0–WC10 已收口；前端 Tailwind CSS 3.4（`darkMode: "class"`，`lib/theme.ts` 在 `<html>` 上切换 `dark` 类）
>
> 日期：2026-11-14
>
> 任务前缀：CMC（取 CHAT_MODE_COLORS 首字母；与既有 A–I / S / IR / V / SX / WC / LG / WP / WS / SR / SD / G 系列均不冲突）

## 目录

章节顺序对齐 [SPEC-TEMPLATE](../../.factory/skills/docs-governance/assets/SPEC-TEMPLATE.md)：

- 1 项目概述（1.1 结论速览、1.2 决策依据：提案三问、1.3 背景与现状）
- 2 核心目标与范围（2.1 In Scope、2.2 Out of Scope）
- 3 技术选型与模块设计（3.1 配色表与对比度实测、3.2 实现方式、3.3 选型理由）
- 4 测试方案
- 5 任务排期与进度
- 6 验收清单
- 7 实现合入后的回写清单
- 8 隐私注记（不适用，说明见该节）

---

## 1. 项目概述

### 1.1 结论速览

| 问题 | 结论 |
|------|------|
| 要解决什么问题？ | Chat 页 Hybrid / Agentic (fast) / Agentic (deep) 三个模式按钮外观相同，只能读文字辨认当前模式 |
| 定位是什么？ | 既有组件的样式调整：只换 `ModeSelector.tsx` 选中态的 Tailwind 类，不替换组件、不改交互 |
| 实施范围（已决策） | CMC1（着色 + 单测）、CMC2（构建 + e2e 回归 + 明暗双模式截图核验）均为承诺任务，无门控任务 |
| 主要代价 | 极小：一个组件文件 + 一组单测断言；构建产物 CSS 增加约 6 个工具类 |

### 1.2 决策依据（提案三问）

**1. 解决哪个已经观察到的问题？**

`frontend/components/chat/ModeSelector.tsx` 中，三个按钮未选中时统一灰字（`text-slate-600` / `dark:text-slate-300`），选中时统一白底蓝字（`bg-white text-blue-700` / `dark:bg-slate-600 dark:text-blue-200`）。模式之间没有视觉区分。用户截图反馈：切换模式时扫一眼无法定位当前模式。

**2. 是否有更简单、改动更小的方案？**

| 方案 | 改动 | 结论 |
|------|------|------|
| A. 只给选中态按模式着色（黄 / 绿 / 蓝） | 仅 `ModeSelector.tsx`，按 `option.mode` 查表换选中态类 | **采用**：改动最小，直接满足诉求 |
| B. 三按钮常驻三色底 | 同一文件，选中与未选中都带色 | 否决：未选中按钮大面积着色加重视觉噪音，选中态反而不突出 |
| C. 按钮内加彩色圆点 / 徽标 | 改组件 DOM 结构 | 否决：改动大于 A，收益相同，且会改变按钮可访问名称的渲染结构 |

**3. 如何确认它有效？**

单测断言每个模式选中时带对应配色类；构建产物中确认这些类已生成；Playwright e2e 确认 aria 语义不变；light / dark 两种模式下截图核验文字清晰可读。细节见 §4、§5。

### 1.3 背景与现状

- 模式选项由 `lib/chat/modes.ts` 的 `buildModeOptions()` 生成：Hybrid 总是可用；sidecar 关闭时隐藏两个 agentic 模式（只剩一个选项时组件退化为文字「Hybrid retrieval」）；`allow_deep=false` 时 Deep 可见但禁用。本规格不改这部分逻辑。
- 按钮容器底色为 `bg-slate-100` / `dark:bg-slate-800`，选中按钮叠在容器上。
- 明暗模式由 `<html class="dark">` 驱动（`tailwind.config.js` 中 `darkMode: "class"`），所以 `dark:` 前缀类即可覆盖暗色模式，无需媒体查询。

## 2. 核心目标与范围

### 2.1 In Scope

| 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|--------|-----------|
| P0 | 选中态按模式着色 | 承诺（CMC1） | Hybrid 黄、Agentic (fast) 绿、Agentic (deep) 蓝；light 浅底深字、dark 半透明底亮字，文字对比度 ≥ WCAG AA 4.5:1 |
| P0 | 回归与视觉核验 | 承诺（CMC2） | 前端全量检查、构建、e2e 全绿，明暗双模式截图确认文字不被底色吞没 |

### 2.2 Out of Scope（明确不做）

- **Playground 页搜索模式按钮**（`frontend/components/search/PlaygroundView.tsx`，Normal / Fast / Deep）：是另一组模式、另一套语义，需要时另起提案。
- **未选中态的彩色悬停样式**：保持现有中性灰悬停，避免未选中按钮也出现颜色，和方案 B 的问题相同。
- **禁用态配色**：保持现有 `opacity-50`。禁用控件不在 WCAG 1.4.3 对比度要求之内；另外 `resolveMode()` 保证被禁用的 Deep 不会处于选中态。
- **主题令牌 / CSS 变量体系**、其他页面配色统一：超出本诉求。
- **aria 属性、按钮文案、模式可用性逻辑**：一律不动。

## 3. 技术选型与模块设计

```
ChatView ──(capabilities, value, onChange)──▶ ModeSelector
                                               │
                    buildModeOptions() ────────┤  (lib/chat/modes.ts，不改)
                                               ▼
                          options.map(option =>
                            selected ? `font-medium shadow-sm ${MODE_TONE[option.mode]}`
                                     : 现有中性灰类（不改）)
                                               │
                    <html class="dark"> ───────┘  dark: 前缀类自动切换
```

### 3.1 配色表与对比度实测

| 模式 | light 选中态 | dark 选中态 | light 对比度 | dark 对比度 |
|------|-------------|-------------|-------------|-------------|
| hybrid（黄） | `bg-amber-100 text-amber-800` | `dark:bg-amber-500/20 dark:text-amber-200` | 6.37:1 | 8.18:1 |
| agentic_fast（绿） | `bg-emerald-100 text-emerald-800` | `dark:bg-emerald-500/20 dark:text-emerald-200` | 6.78:1 | 8.11:1 |
| agentic_deep（蓝） | `bg-sky-100 text-sky-800` | `dark:bg-sky-500/20 dark:text-sky-200` | 6.59:1 | 7.96:1 |

- 对比度按 WCAG 2.x 相对亮度公式、Tailwind 3.4 默认色板计算；dark 列的底色是 `*-500` 以 20% 不透明度叠在容器底色 `slate-800`（`#1e293b`）上的合成色。参照：现有选中态 `blue-700` 白底为 6.70:1。
- 三种配色都满足 AA（≥ 4.5:1），并留有余量。文字色始终与底色同色系成对出现，不会出现「彩色底配灰字 / 白字」的低对比组合。

### 3.2 实现方式

- 在 `ModeSelector.tsx` 中新增 `const MODE_TONE: Record<RetrievalMode, string>`，值为上表的完整类名字符串。
- 类名必须写成完整字面量，不能拼接（如 `` `bg-${color}-100` ``）：Tailwind 3 的 JIT 只扫描源码中的完整类名，拼接出的类不会生成 CSS。
- 选中态原有的 `bg-white text-blue-700 dark:bg-slate-600 dark:text-blue-200` 换成 `MODE_TONE[option.mode]`，保留 `font-medium shadow-sm`；未选中态与禁用态类不变。
- `Record<RetrievalMode, string>` 让 TypeScript 在以后新增模式时强制补齐配色，避免漏配。

### 3.3 选型理由

1. **只着色选中态（A）而非常驻三色（B）**：选中与否的区分依旧靠「有底色 / 无底色」，颜色只负责区分模式，两种信息不互相干扰。
2. **浅底深字 / 半透明底亮字，而非实色饱和底（如 `bg-amber-400` 配白字）**：黄色饱和底配白字对比度不到 2:1，配深字又与绿、蓝两色的文字方向不一致；同色系浅底深字三色一致且都高于 6:1。
3. **配色表放在组件内而非 `lib/chat/modes.ts`**：`modes.ts` 只管可用性逻辑且有独立单测，配色属于展示层，放组件内不扩大改动面。

## 4. 测试方案

- **离线确定性测试**：只涉及前端，Python 测试套件不受影响。vitest 用 jsdom 渲染 `ModeSelector`，通过 `className` 断言配色类；不需要真实浏览器、后端或网络。
- **构建产物核验**：`npm run build` 后在 `frontend/out/` 的 CSS 中确认 `bg-amber-100`、`bg-emerald-100`、`bg-sky-100` 及三个 `dark:bg-*-500/20` 类均已生成（防止 JIT 漏扫）。
- **e2e**：`npm run e2e`（Playwright，离线假后端，opt-in），复用既有 `e2e/chat.spec.ts`，不新增 e2e 用例。
- **视觉核验**：启动 `python scripts/start_api.py --force --serve-ui`，在 light / dark 两种模式下分别选中三个模式截图（共 6 张），人工确认文字清晰可读。截图只作核验记录，不入库。
- **真实依赖冒烟**：不适用（纯样式改动，无外部依赖）。
- **既有测试语义**（不得破坏）：
  - `components/chat/chat.components.test.tsx` 的 `describe("ModeSelector")`：按 `role="radio"` + 名称定位按钮、Deep 在 `allow_deep=false` 时禁用、点击触发 `onChange`、sidecar 关闭时退化为「Hybrid retrieval」文字。
  - `e2e/chat.spec.ts`：`radiogroup` 名称为「Retrieval mode」，默认 Hybrid `aria-checked="true"`，切换到 Agentic (fast) 后 Hybrid 变为 `"false"`。
  - `lib/chat/modes.ts` 的 `buildModeOptions` / `resolveMode` 单测不受影响（本规格不改该文件）。

## 5. 任务排期与进度

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|----------|------|
| CMC1 | `ModeSelector.tsx` 新增 `MODE_TONE` 查表，选中态按模式着色（§3.1 六组类）；在 `chat.components.test.tsx` 的 `ModeSelector` 用例中补断言 | 三个模式选中时各自带 light + dark 两组配色类、未选中按钮不带任何模式配色类；既有 `ModeSelector` 用例不改语义；`npm run typecheck && npm run lint && npm test` 全绿 | ✅ 已完成（2026-11-14）：新增 `MODE_TONE` 常量与 `colors the selected button per mode` 用例；typecheck / lint 0 错误，目标文件 24/24、全量 vitest 263/263 通过 |
| CMC2 | 构建 + e2e 回归 + 明暗双模式截图核验 | `npm run build` 成功且 `frontend/out/` CSS 含 §4 列出的 6 个类；`npm run e2e` 全绿；light / dark × 三模式共 6 张截图文字清晰可读；`pytest tests/e2e_web -q` 全绿 | ✅ 已完成（2026-11-14）：`npm run build` 成功，产物 CSS（`out/_next/static/chunks/*.css`）含 3 个浅底类与 3 个 `dark:bg-*-500/20` 变体；Playwright e2e 26/26 通过；6 张截图（light/dark × 三模式）人工核验文字均清晰可读；`pytest tests/e2e_web` 3 项通过 |

> 进度备注：CMC1–CMC2 已于 2026-11-14 全部收口（提交 `b92e07a` / `4d86c42`），本规格按冻结清单移入 `docs/frozen/` 并同步 MOC.md。冻结后任务表不再新增任务。

## 6. 验收清单

- [x] Chat 页选中 Hybrid / Agentic (fast) / Agentic (deep) 时按钮分别呈黄 / 绿 / 蓝底色，未选中按钮保持中性灰
- [x] light 与 dark 两种模式下，三种选中态文字清晰可读（对比度见 §3.1，均 ≥ 4.5:1）
- [x] 禁用的 Deep 按钮外观与改动前一致（`opacity-50` + `off` 标记）
- [x] `npm run typecheck`、`npm run lint`、`npm test`、`npm run build`、`npm run e2e` 全绿
- [x] 回写完成（§7）

## 7. 实现合入后的回写清单

- `docs/MOC.md`：规格冻结时条目移入 frozen/ 分区，更新谱系表本行。
- `AGENTS.md`、根目录 `README.md` 导航表：同步规格状态。
- `docs/reference/USAGE_GUIDE.md` 第 10 章：核对是否描述了模式选择器外观，有则补一句配色说明，无则不改。
- 无架构决策、配置项或环境变量变化，`config/settings.yaml` 与 DEV_SPEC 不需回写。

## 8. 隐私注记

不适用：纯前端样式改动，不新增路由、不落库个人内容、不外发任何数据。
