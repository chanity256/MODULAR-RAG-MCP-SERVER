# 检索 Trace 挖掘管线提案（retrieval-trace-mining）

> 起源：会话讨论（2026-11-18，非文件来源）。背景是 [MODE_ROUTER_SPEC](../specs/MODE_ROUTER_SPEC.md) MR0.2 的标注集需求——当前标注集草案（60 条）取材于 `data/documents/` 学习笔记语料，缺乏真实用户问题的覆盖；本提案想法为「从既有日志中挖掘真实提问、回答与人工所选检索模式，作为标注集扩充或持续校准数据来源」。**状态：草案**，多数口径未研究清楚，仅记录想法与已核实的现状，不进入排期。
>
> 现状更新（2026-11-18）：MR0.2 已收口、D2 门槛通过（默认 clef-flash + zh-v2，留出集命中率 93.3%，见 MODE_ROUTER_SPEC D2 与 MR0.2 行）。标注集 60 条已由用户确认，另有 30 条留出集 `holdout_set.jsonl`。Q6 的前提已满足；两套集合仍都取材于本地语料，真实提问分布的缺口不变。

## 1. 想法

构建一条离线管线，从既有日志与存储中提取「问题 → 回答 → 用户所选检索模式」三元组：

- 数据源 A：`logs/traces/traces-YYYY-MM-DD.jsonl`（LG 系列按日 trace）
- 数据源 B：`data/db/chat_sessions.db`（WC6.3 会话库，`chat_sessions` / `chat_messages` 两表）
- 产出：真实问题的标注数据（问题 → 用户所选模式），可选带问答对，用于 MODE_ROUTER 的标注集扩充 / 后续持续校准，或独立的检索质量分析

## 2. 已核实的现状（2026-11-18，基于基线 `a404207` 源码与真实日志）

Chat 链路每轮写一条 `trace_type=query, routes=chat` 的 trace（`src/core/chat/engine.py:277-278`）：

| 内容 | 现状 | 位置 |
|------|------|------|
| 提问（改写前原文） | ✅ 有 | trace `metadata.query` |
| 提问（改写后） | ✅ 有 | trace `chat_rewrite` 阶段 details |
| 检索模式（实际使用） | ✅ 有 | trace `metadata.retrieval` |
| 手选 vs 默认值的区分 | ❌ 无 | `run_turn` 中 `mode = retrieval or default_retrieval`（`engine.py:240`），trace 只记解析后的值 |
| 关联会话的键 | ✅ 有 | trace `metadata.session_id` |
| 回答全文 | ❌ 不在 trace | trace `chat_generation` 阶段只有 `chars` / `grounded` / `usage` / `fallback` 等元数据 |
| 回答全文（含 sources） | ✅ 在会话库 | `chat_sessions.db` 的 `chat_messages` 表（`engine.py:578-581` `append_message`），经 `session_id` 与 trace 关联 |

其他链路：

- agentic 检索 trace：`metadata` 带 `{"query", "mode": "FAST"|"DEEP", "routes": "agentic"}`，mode 用的是 sirchmunk 命名，与 chat 三档枚举（`agentic_fast` / `agentic_deep`）不同名，映射口径未统一。
- 纯 hybrid 查询链路（`scripts/query.py` / MCP `query_knowledge_hub`）：trace 只有 `{"query", "collection"}`，无模式字段、无回答。MCP 侧数据价值有限（MCP 工具只能 hybrid 或独立 agentic 工具，且 MODE_ROUTER D1 已把 MCP 列为 Out of Scope）。

## 3. 提案三问（草案级回答）

1. **解决了哪个已观察到的问题？** MR0.2 标注集缺乏真实用户问题覆盖（现取材于本地语料笔记）；后续 MODE_ROUTER 若上线 auto，也需要持续的真实分布数据做校准与回归。trace 与会话库已有原始数据，只是没有提取管线。
2. **更简单的方案？** 不建管线，继续由 agent 起草标注集、用户逐条确认（MR0.2 现行做法）。缺点是覆盖不了真实提问分布；但 MR0.2 是一次性预研，管线收益要在「持续校准」场景才成立——是否值得建，取决于 MODE_ROUTER 决策质量门槛（D2）通过与否。
3. **如何确认有效？** 未研究。候选方向：提取出的数据经人工确认后进入标注集，统计真实问题在各档模式的分布与 MR0.2 基线分布的差异；或以「用户所选模式」作弱标注，对比决策模型输出的一致率。

## 4. 未研究清楚的问题（立项前需回答）

| # | 问题 | 说明 |
|---|------|------|
| Q1 | 手选 vs 默认值不可区分 | trace 只记解析后的 `retrieval`。若要「用户所选模式」的干净标注，需在 trace 增记 `requested_retrieval`（改 `engine.py`，属规格外增量，需评估是否走 MODE_ROUTER 补充任务或独立小改） |
| Q2 | 用户所选 ≠ 正确模式 | trace 记录的是用户的选择，不是「该问题的最优模式」。它适合作偏好/弱标注还是需人工复核为强标注，口径未定 |
| Q3 | 跨库关联与数据形态 | trace 是按日 JSON Lines，会话库是 SQLite；join 键 `session_id` 在两侧都有，但会话库含历史消息原文，提取时的脱敏 / 入库位置（`tests/fixtures/` 还是独立数据目录）未定 |
| Q4 | 隐私边界 | 问题与回答含个人笔记内容；若标注集进 git（fixtures），需逐条确认或脱敏，对齐 MODE_ROUTER MR0.2「由用户逐条确认」的先例 |
| Q5 | 模式命名映射 | agentic trace 的 `FAST`/`DEEP` 与三档枚举的映射是否在管线内统一，还是仅处理 chat 链路数据 |
| Q6 | 时机依赖 | 管线的持续校准价值以 MODE_ROUTER 立项门（MR0.2 决策质量门槛）通过为前提；门槛未过则本提案大概率止于分析 |

## 5. 与 MODE_ROUTER_SPEC 的关系

- 不阻塞 MR0.2：现行标注集（语料起草 + 用户逐条确认）继续走原计划。
- 若 MR0.2 通过，本提案可作为其后续数据来源另起提案或并入 MODE_ROUTER 后续任务（届时再定）。
- 本提案不改 MODE_ROUTER 已终裁的决策（D1–D8）；若做 Q1 的 trace 增量，需评估对「WS 帧协议既有字段只增不改」纪律（MODE_ROUTER §4）的影响。

## 6. 流转记录

- 2026-11-18：草案创建（会话讨论沉淀；现状核实基于源码 `engine.py` / `session_store.py` 与 `logs/traces/traces-2026-11-18.jsonl` 实样）。
