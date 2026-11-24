# Plan: [PI6] Chat 前端 Longdoc 选项、配色、引用展示

Status: implemented

## Deviations

1. **fake 后端证据文件须真实落盘且用 realpath 命名**（超出 spec 预注的两行）：PathGuard
   `filter_evidence_paths` 对缺失文件 fail-closed（`_resolve_evidence_path` 要求 `exists()`），
   且 macOS `/var/folders` 经 `/private/var` 符号链接解析——evidence `source_path` 与保留集
   （realpath 字符串）做集合比较，用未解析路径会零证据。fake server 落盘
   `workdir/longdoc-notes.md` 并以 `resolve()` 后路径作 `source_path`。属测试脚手架修正，
   生产链路（pageindex_sync 发真实绝对路径）不受影响；已记入 spec 版本头 1.8。
2. rose 对比度实测回填：light `#ffe4e6`/`#9f1239` = 6.68:1；dark rose-500 20% 混合
   slate-800（`#492d42`）底 + rose-200（`#fecdd3`）字 = 8.57:1。均 ≥ AA 4.5:1。

依据 `docs/specs/PAGEINDEX_TREE_RETRIEVAL_SPEC.md` §3.2「前端（PI6）」与 §4 测试方案（前端条目）。
后端契约（PI5 已交付）：capabilities 增 `longdoc_enabled`（`tree_search.enabled`），`retrievals` 末尾可能含
`longdoc`；`Citation.to_dict()` 只增可选 `section` / `line_start` / `line_end`（None 不出键）。

## Files

1. `frontend/lib/chat/types.ts` — `RetrievalMode` 加 `longdoc`；`RETRIEVAL_MODES` 加 `"longdoc"`；
   `ChatCapabilities` 增 `longdoc_enabled: boolean`；`Citation` 增可选
   `section?: string` / `line_start?: number` / `line_end?: number`。
2. `frontend/lib/chat/normalize.ts` — `normalizeCapabilities` 解析 `longdoc_enabled`（缺省 false）；
   `default_retrieval === "longdoc"` 仅当 `longdoc_enabled` 且 `retrievals` 含 `longdoc` 时保留，否则归一
   hybrid（对齐 auto 先例）；`normalizeCitation` 透传三个可选字段（类型不符丢弃；缺失不出键）。
3. `frontend/lib/chat/modes.ts` — `LABELS` 加 `longdoc: "Longdoc"`；`buildModeOptions` 把 agentic 提前
   返回改为条件块，longdoc 判断独立于 `agentic_enabled`；顺序 Auto、Hybrid、fast、deep、Longdoc；
   `longdoc_enabled` 为真且 `retrievals` 含 `longdoc` 才出现。
4. `frontend/components/chat/ModeSelector.tsx` — `MODE_TONE` 加 longdoc rose 选中态
   （light `bg-rose-100 text-rose-800`，dark `dark:bg-rose-500/20 dark:text-rose-200`，全字面类名）。
5. `frontend/lib/chat/citations.ts` — `citationLabel()` 支持 ` · <section> · L<start>–<end>`（单行
   `L<n>`；仅 section；无新字段输出与现状逐字节一致）。
6. 测试夹具同步（语义不变）：`frontend/lib/chat/normalize.test.ts`（capabilities 精确断言补
   `longdoc_enabled: false`）、`frontend/lib/chat/modes.test.ts`（caps 工厂补字段 + 新用例）、
   `frontend/components/chat/chat.components.test.tsx`（caps 对象补字段 + rose 配色用例）。
7. 新增用例：`modes.test.ts`（agentic off + longdoc on 仍出现 Longdoc；顺序）、`normalize.test.ts`
   （default_retrieval longdoc 两种分支；citation 新字段透传 / 丢弃）、`citations.test.ts`
   （label 三种格式）、`chat.components.test.tsx`（rose 选中态）。
8. `tests/e2e_web/fake_api_server.py` — tree_search.enabled 的 settings（fake token / loopback）+
   注入 fake tree backend 的 `TreeChatRetriever`，longdoc 轮次返回带 `section` / `line_*` 的引用。
9. `frontend/e2e/chat.spec.ts` — Playwright longdoc 用例（选项出现、选中、引用标签含章节与行号）。

`SourcesPanel.tsx` / `MarkdownContent.tsx` 经 `citationLabel()` 自动生效，不改（spec 明示）。

## Order

types → normalize → modes → citations → ModeSelector → 既有夹具同步 → 新用例 → fake server →
Playwright 用例。类型先行让后续文件 typecheck 可过；夹具同步与新增用例同文件批量改。

## Risks

- **既有精确断言**：`normalize.test.ts` 的 `toEqual` capabilities 断言与 ModeSelector 配色遍历不含
  longdoc——补字段 / 补 tone 行即可，语义不变（spec §4 既有测试语义）。
- **`default_retrieval` 归一**：服务端 `validate_settings` 已保证 default=longdoc 时 enabled=true，
  前端仍防御 older server（`normalizeCapabilities` 缺字段即 false → 归一 hybrid）。
- **红线**：不 import pageindex / litellm；fake server 的 tree backend 用 `libs/tree_search` 的
  fake 后端注入（PI5 同构），零网络。
- **rose 对比度**：light 需实测 ≥ 4.5:1（与 amber/emerald/sky 同构图案），结果回填 plan 与 spec。

## Verification

- `cd frontend && npm run typecheck && npm run lint && npm test` 全绿（spec 验收标准）。
- rose 对比度脚本计算 light `#ffe4e6`/`#9f1239` ≥ 4.5:1；dark 沿用既有四档同构（半透明底 +
  rose-200 文字，与 emerald 等已收口配色一致）。
- Playwright：`(cd frontend && npm run build)` 后 `npm run e2e`（opt-in）跑 longdoc 用例；
  若构建不可行则如实报告跳过。
- 回归：`pytest tests/e2e_web -q`（frontend/out 未构建则 skip，属预期）；
  `python scripts/check_redlines.py` 0 违规。

（Deviations 见文件头 Status 之下。）
