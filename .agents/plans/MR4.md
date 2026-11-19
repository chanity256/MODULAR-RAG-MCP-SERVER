# Plan: [MR4] API schema、capabilities 与 Chat 前端

Status: implemented

Spec: `docs/specs/MODE_ROUTER_SPEC.md` §3.2（API schema / 前端段）、§3.4、§4（装配组 / 前端 vitest 组）。

## Files

后端：

1. `src/api/schemas/chat.py` — 新增 `RequestedRetrieval = RetrievalMode | Literal["auto"]`；`UserMessageFrame.retrieval` 与 `ChatCapabilities.default_retrieval` 改用之；`ChatCapabilities` 新增 `auto_routing: bool`。
2. `src/api/chat_wiring.py` — `compute_capabilities` 增加 `auto_routing`（路由器启用且候选非空；候选 = available ∩ 门控，与 `_auto_candidates` 同口径）。
3. `tests/unit/test_chat_auto_mode.py` — 装配组补 capabilities 断言（auto_routing 随启用状态变化、retrievals 不含 auto）。

前端：

4. `frontend/lib/chat/types.ts` — `RequestedRetrieval`；`UserMessagePayload.retrieval: RequestedRetrieval`；`ChatCapabilities.auto_routing` + `default_retrieval: RequestedRetrieval`；result 帧与 `ChatMessage` 增 `retrieval`（实际模式）/ `routing`；`ChatStage` 增 `"routing"`。
5. `frontend/lib/chat/normalize.ts` — STAGES 增 `routing`；capabilities 解析 `auto_routing` 与 `default_retrieval = "auto"`（仅 `auto_routing` 为 true，否则归一 hybrid）；result 帧解析 `retrieval` / `routing`；`messageFromRecord` 透传 `routing`（trace metadata 不含 routing，回放只显示实际模式）。
6. `frontend/lib/chat/modes.ts` — `ModeOption.mode: RequestedRetrieval`；`buildModeOptions` 在 `auto_routing` 时提供 Auto 选项；`resolveMode` 接受 RequestedRetrieval。
7. `frontend/lib/chat/status.ts` — routing 阶段标签。
8. `frontend/lib/chat/reducer.ts` — result 帧回填 `retrieval`（实际模式）与 `routing`。
9. `frontend/components/chat/ModeSelector.tsx` — Auto 第四种选中态配色（violet，light 浅底深字 / dark 半透明底亮字，对齐 CMC 规则；三档不动）。
10. `frontend/components/chat/MessageBubble.tsx` — auto 轮次展示「Auto → 实际模式」与回退原因。
11. 前端测试：`lib/chat/modes.test.ts`、`lib/chat/normalize.test.ts`、`lib/chat/reducer.test.ts`、`components/chat/chat.components.test.tsx` — Auto 选项出现/不出现、normalize 两种分支、resolveMode、Auto 选中态 class、result 帧 routing 回填。

## Order

后端 schema → wiring → 后端测试 → 前端 types → normalize/modes/status/reducer → 组件 → 测试。类型先行，避免前端连锁改错。

## Risks

- `RetrievalMode` 语义被污染（retrievals 混入 auto）：`retrievals: list[RetrievalMode]` 不动，`auto` 只进 `RequestedRetrieval`。
- `default_retrieval` 类型放宽后旧前端把 auto 静默归一 hybrid：normalize 按 `auto_routing` 分支，正是规格要求的行为。
- CMC 既有配色/aria 用例不得破坏：只增 violet tone，不动既有 class 与 aria。
- 非-auto 轮次帧不变：result 帧只读新增字段，reducer 不改既有分支语义。

## Verification

- `NO_PROXY='*' pytest tests -q --disable-socket --allow-unix-socket --allow-hosts=127.0.0.1,localhost,::1`（当前基线 2935 passed）
- `python scripts/check_redlines.py` 0 违规
- `(cd frontend && npm run typecheck && npm run lint && npm test)` 全绿
- Auto 选中态对比度：violet-100/violet-800 与 violet-500/20/violet-200 与既有三档同构造（Tailwind 调色板 AA 组合），实测记入规格 MR4 行
