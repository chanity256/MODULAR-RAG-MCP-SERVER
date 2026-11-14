<!-- 候选提案：Chat 检索模式选择器三按钮按模式着色（黄 / 绿 / 蓝）。小型前端视觉改动，未立项；立项时按单文件晋升 git mv 到 specs/ 改写为规格。 -->

# Chat 模式选择器按模式着色提案 (chat-mode-selector-colors)

> 起源：会话内用户截图（Web UI Chat 页的检索模式选择器，Hybrid / Agentic (fast) / Agentic (deep) 三按钮当前同色）。

## 三问（决策依据）

### 1. 解决哪个已经观察到的问题？

Chat 页（`frontend/components/chat/ModeSelector.tsx`）的三个检索模式按钮在视觉上完全同质：

- 未选中态统一灰字（`text-slate-600` / `dark:text-slate-300`），选中态统一白底蓝字（`bg-white text-blue-700` / `dark:bg-slate-600 dark:text-blue-200`），三个模式之间没有任何视觉区分；
- 用户截图反馈：切换 Hybrid / Agentic (fast) / Agentic (deep) 时只能靠读文字辨认当前模式，扫一眼无法定位。

### 2. 是否有更简单、改动更小的方案？

候选方案对比：

| 方案 | 改动 | 结论 |
|------|------|------|
| A. 选中态按模式着色（黄 / 绿 / 蓝） | 仅 `ModeSelector.tsx` 一个文件，按 `option.mode` 查表换选中态/悬停态的 Tailwind 类 | **推荐**：改动最小，直接满足诉求 |
| B. 三按钮常驻三色底 | 同一文件，但选中与未选中都带色 | 不推荐：未选中按钮大面积着色会加重视觉噪音，且与选中态的对比度反而下降 |
| C. 加彩色圆点 / 徽标 | 改组件结构 | 不推荐：改动大于 A，收益相同 |

方案 A 细节（文字不被颜色覆盖 + 明暗双模式）：

- 选中态用**浅色底 + 同系深色字**（light 模式）/ **低透明度同色底 + 亮色字**（dark 模式），保证 WCAG AA 对比度（≥ 4.5:1），文字颜色永远与底色成对出现，不出现「彩色底压灰字/白字」的低对比组合：

| 模式 | light 选中态 | dark 选中态 |
|------|-------------|-------------|
| hybrid（黄） | `bg-amber-100 text-amber-800` | `bg-amber-500/20 text-amber-200` |
| agentic_fast（绿） | `bg-emerald-100 text-emerald-800` | `bg-emerald-500/20 text-emerald-200` |
| agentic_deep（蓝） | `bg-sky-100 text-sky-800` | `bg-sky-500/20 text-sky-200` |

- 未选中态维持现有中性灰（`text-slate-*`），仅悬停时给对应模式色的浅色调（可选，倾向不做，保持最小改动）；
- disabled 态维持现有 `opacity-50`，不引入颜色语义冲突；
- aria 属性（`role="radiogroup"` / `role="radio"` / `aria-checked`）与文案不动，纯样式改动。

### 3. 如何确认它有效？

- `cd frontend && npm run typecheck && npm run lint && npm test`（vitest，含 `ModeSelector` 相关单测）全绿；
- `cd frontend && npm run build && npm run e2e`：`e2e/chat.spec.ts` 按 `role="radio"` + 名称定位三按钮，断言不受颜色改动影响（验证可访问性语义未破坏）；
- 人工核验：light / dark 两种模式下截图对照，确认文字在三种彩色选中态底色上清晰可读（对齐截图诉求「字体不被颜色覆盖」）。

## 任务草案（立项后编号用新前缀，如 CS）

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|---------|------|
| CS1 | `ModeSelector.tsx` 选中态按模式查表着色（上表 6 组类），补充/更新单测 | 三模式 light/dark 各 2 组快照或类名断言通过；vitest 全绿 | 未开始 |
| CS2 | build + Playwright e2e 回归 + light/dark 人工截图核验 | e2e 全绿，明暗两模式截图存档 | 未开始 |

## Out of Scope（暂不做）

- Playground 页搜索模式按钮（`frontend/components/search/PlaygroundView.tsx`）同步着色——如需要另立任务；
- 未选中态的彩色悬停样式、其他页面的配色统一。

---

*状态：候选提案，未排期。三问已答全，满足立项条件；按 [backlog/README.md](README.md) 单文件晋升流程进入 specs/。*
