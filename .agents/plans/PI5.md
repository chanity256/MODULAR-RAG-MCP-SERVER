# Plan: [PI5] Chat 后端 `longdoc` 模式接入

Status: implemented

Spec: `docs/specs/PAGEINDEX_TREE_RETRIEVAL_SPEC.md` §3.2「Chat 接入（PI5）」/ §4「Chat（PI5）」/ §3.5。前置：PI1–PI3 已合入（`libs/tree_search/`、`tree_search` 配置节、sidecar 服务已在 fork/main）；PI4（同步脚本）未开工，但 PI5 离线交付不依赖它（真实冒烟属 PI7 门控）。

## Files

按依赖顺序修改 / 新增：

1. `src/core/chat/retriever.py` — `RETRIEVAL_LONGDOC = "longdoc"`，`RETRIEVAL_MODES` 扩为四元组（`RETRIEVAL_AUTO` 仍不在其中）。
2. `src/core/settings.py` — `CHAT_RETRIEVAL_MODES` 追加 `longdoc`；新增 `MODE_ROUTER_FALLBACK_MODES = ("hybrid", "agentic_fast", "agentic_deep")`；`mode_router.fallback_retrieval` 校验改用该常量（只接受三档、报错文案不变）。
3. `src/core/chat/engine.py` — `classify_error()` 在 agentic 分支后追加 `TreeSearch*Error` 映射（timeout/backend → UPSTREAM transient=True；response → UPSTREAM 文案「检索服务返回了无法解析的响应。」transient=False；其余 TreeSearchError → UPSTREAM transient=False）。
4. `src/core/response/citation_generator.py` — `Citation` 增可选 `section` / `line_start` / `line_end`；`None` 时 `to_dict()` 不输出该键（D8：既有 hybrid / agentic 引用逐字节不变）。
5. `src/core/chat/answer_generator.py` — `build_context()` 位置头：`[id] source[, p.N][ · <section>][ · L<start>–<end>]`（`line_start == line_end` 时 `L<n>`）；二者皆 None 时输出与现状逐字节一致。
6. `src/api/schemas/chat.py` — `RetrievalMode` 追加 `"longdoc"`；`ChatCapabilities` 声明 `longdoc_enabled: bool`。
7. `src/api/chat_wiring.py` — `_ALL_MODES` 保持三档（D10）；`compute_capabilities`：`tree_search.enabled` 且 `longdoc` 已注册时 `retrievals` 末尾追加 `longdoc`；`available=None` 冷启动推断纳入 `longdoc`；新增 `longdoc_enabled` 键。
8. `src/observability/dashboard/services/chat_retrievers.py` — 新增 `TreeChatRetriever`（collection 定界 → 空集短路 → 调后端 → PathGuard 过滤 + 定界集二次过滤 → converter → trace `record_stage("tree_search", ...)` 不 finish）；`build_chat_retrievers` 在 `tree_search.enabled` 时注册（可注入 `tree_backend` 供测试 fake）；不声明 `on_log`。
9. `scripts/start_api.py` — `ensure_local_roots(settings.tree_search)` 一行预建。
10. 既有测试同步（语义不变）：`tests/integration/test_api_chat.py` capabilities 精确字典补 `longdoc_enabled: false`；`tests/unit/test_mode_router_config.py:54` 常量断言改四元组。
11. 新增测试：`tests/unit/test_tree_chat_retriever.py`（TreeChatRetriever + classify_error + build_context + capabilities 冷启动）；`tests/integration/test_api_chat.py` 增 capabilities HTTP 断言（`longdoc_enabled` 真/假两态）。

## Order

常量与配置先行（retriever/settings），错误分类与引用次之（engine/citation/answer），再 API 层（schemas/wiring），最后检索器装配与入口预建。既有断言同步与新测试在实现后、进入 Test 步（fix_mode 标记创建）**之前**完成——test_guard 会拦截 fix 模式下对既有测试文件的修改。

## Risks

- capabilities 既有精确断言（test_api_chat、前端 normalize.test.ts）会因新键失败：按 spec §4 只补 `longdoc_enabled: false`，语义不变（前端断言属 PI6 同步面，本次前端零改动不影响 vitest）。
- `mode_router.fallback_retrieval` 报错文案若随 `CHAT_RETRIEVAL_MODES` 扩容而变化会破坏既有用例 → 用独立常量 `MODE_ROUTER_FALLBACK_MODES` 保证文案不变。
- `build_context` / `Citation.to_dict()` 对既有结果必须逐字节不变 → 新字段全部可选、None 不输出，既有单测不改。
- auto 候选（`_ALL_MODES`）不得混入 `longdoc`（D10）：`compute_capabilities` 的候选计算继续只用 `_ALL_MODES`。
- 红线：主 venv 不 import pageindex / litellm（tree_chat_retriever 只 import libs.tree_search）。

## Verification

- `pytest tests/unit/test_tree_chat_retriever.py tests/unit/test_mode_router_config.py tests/unit/test_answer_generator.py tests/unit/test_chat_engine.py tests/integration/test_api_chat.py tests/unit/test_chat_auto_mode.py -q` 全绿。
- 全量 `source .venv/bin/activate && pytest tests -q`（禁网等价命令）计数只增不减；`python scripts/check_redlines.py` 0 违规。
- 语义锁定：tree_search 关闭时 capabilities 既有键值不变、仅多 `longdoc_enabled: false`；`buildContext`/`to_dict` 旧输出不变；fallback_retrieval 报错文案不变。

## Deviations

（无）
