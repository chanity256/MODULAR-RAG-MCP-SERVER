# Plan: [MR3] ChatEngine auto 分支 + 装配注入

Status: implemented

## Files
1. `src/libs/mode_router/base_mode_router.py` / `system_one_router.py` — 补 `backend_name` 属性（trace 用，微小改动）。
2. `src/core/chat/engine.py` — 构造参数 `mode_router` / `auto_candidates` / `auto_fallback`；`run_turn` 接受 `auto`（无 router 即 ValueError）；`_run` 改写后定档（D5）：`status(routing)` 帧 → `_route`（`decide` + 候选内复检 + 失败回退 `auto_fallback`）→ trace `mode_routing` 阶段 → metadata/result 帧记实际模式；result 帧只增 `retrieval`（全部轮次）与 `routing`（仅 auto 轮次）。
3. `src/api/chat_wiring.py` — `ChatServices` 懒建共享 router（跨轮复用连接）；`_auto_candidates`（D4：已注册检索器 ∩ 门控，与 compute_capabilities 同口径）；每轮引擎注入。
4. `tests/unit/test_chat_auto_mode.py` — §4 引擎组 + 装配组（MR3 部分）用例。

## Order
backend_name → engine → wiring → 测试。

## Risks
- result 帧只增字段：既有 test_chat_engine / test_api_chat 为逐键断言，验证不改即通过。
- core → libs import：engine 已 import libs.agentic_search / libs.llm，无环（libs.mode_router 只 import core.chat.retriever / core.settings）。
- 共享 router 的 `last_input_tokens` 有并发竞态，仅诊断用途，可接受。
- 决策失败一律回退，不中断会话（spec §3.2）；回退语义与 D3 一致（检索失败按人工选中处理）。

## Verification
- `pytest tests/unit/test_chat_auto_mode.py tests/unit/test_chat_engine.py tests/integration/test_api_chat.py -q` 全绿。
- CI 等价禁网全量 + `check_redlines.py` 0 违规。

## Deviations
（无）
