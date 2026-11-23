# 摄取异常内容前置防护提案（ingestion-empty-content-guard）

> 起源：[pageindex-followups_backlog](pageindex-followups_backlog.md) 第 3 节「已知缺陷候选池」（#2 Chroma `images` 空 list 校验、#1 摄取链路空文本防护）+ `logs/app/` 最近一周运行日志异常（脱敏摘要见下，来源：SD4.2 每周 followups 扫描 2026-11-23）。
>
> **状态：草案（机器起草 2026-11-23，待人工核实）**。由 SD4.2 周度 automation 按台账候选与日志异常互证规则起草，三问为初答，立项与否由用户裁决；推进按 [README 约定](README.md)。
>
> 关联：[04_INGESTION_ROBUSTNESS_SPEC_frozen](../frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md)（摄取链路与 IR 冻结契约）、[PAGEINDEX_TREE_RETRIEVAL_SPEC](../specs/PAGEINDEX_TREE_RETRIEVAL_SPEC.md) 10.3 ① / 10.8 #6（缺陷出处）。

---

## 1. 已观察到的现象（脱敏）

本周（2026-11-17 ~ 2026-11-23）`logs/app/` 摄取链路异常，按「异常类名 + 来源 + 次数」汇总：

| 异常（message 前缀） | 阶段 | 代码来源 | 次数 | 日志 |
|----------------------|------|----------|------|------|
| `Expected metadata list value for key 'images' to be non-empty in upsert` | store | chromadb upsert 校验（入口 [chroma_store.py](../../src/libs/vector_store/chroma_store.py) `upsert`/`_chroma_safe_value`） | 3（同一文档三轮重试均失败） | ingest-2026-11-22 |
| `Expected metadata list value for key 'heading_outline' to be non-empty in upsert` | store | 同上 | 1 | api-2026-11-21 |
| `FileNotFoundError: ... bm25/index.json.tmp -> index.json`（store 失败后的回滚路径连锁失败） | store 回滚 | 摄取管线回滚链路 | 2 | api-2026-11-21 |

台账侧印证：[pageindex-followups_backlog](pageindex-followups_backlog.md) §3 #2 已在 PI0.6 复测期间两轮复现同一 `images` 空 list 失败；本周日志再增 3 次（同一文档三轮重试全部失败），**互证成立**。`heading_outline` 空 list 为同族新实例：无 ATX 标题的合法 Markdown 文档（[markdown_loader.py](../../src/libs/loader/markdown_loader.py) 中 `heading_outline` 取自标题状态、无标题时为空 list）同样在 store 阶段被 Chroma 校验拒绝。

失败形态的连锁影响：

- store 阶段失败导致**整篇文档摄取失败并回滚**，摄取历史不记 success，每次重跑都重试同一失败（本周同一文档 12:29 / 21:52 / 22:28 三轮复现）。
- 回滚路径自身还有连锁缺陷：store 失败早于 BM25 写出 `.tmp` 时，回滚对 `index.json.tmp -> index.json` 的 rename 报 `FileNotFoundError`（噪声 ERROR，掩盖真实失败原因）。

## 2. 决策三问（初答，机器起草，待人工核实）

1. **解决了哪个已经观察到的问题？**——是：无图片 / 无标题的合法文档在 store 阶段必然失败且重试永不收敛（本周 3+1 次）；同族还有候选池 #1（空文本 chunk 流到 embed 阶段才报 `texts must be non-empty string`，台账出处 PI 规格 10.3 ①，本周日志未复现，但与上两条同属「异常内容流到管线后段才崩」）。
2. **是否有更简单、改动更小的方案？**——最小修复点在 `src/libs/vector_store/chroma_store.py` 的 `_chroma_safe_value`：同构原始类型列表原样透传（空 list 的 `all()` 恒真因此透传），把**空列表视为不可存直接丢弃**（或序列化为占位标量）即可同时消掉 `images` / `heading_outline` 两类失败；数据侧（loader 产出空 list 时不写该键）是备选但改动面更大。空文本防护属摄取链路契约修改，需按 IR 冻结契约逐条核实（IR 对 pypdf 空文本「行为保持不变」、markitdown 空文本按 load 失败记 `failed_stage` 的既有口径），可能需独立提案。
3. **如何通过测试、指标或用户流程确认它确实有效？**——单测：空 `images` / 空 `heading_outline` metadata 的记录 upsert 成功（键被丢弃或降级）；重放夹具按本周失败形态构造（无标题无图片 MD 文档摄取全链路成功、摄取历史记 success）；回归：此前失败的文档重跑收敛。指标：摄取失败率中 store 阶段「空值校验」类归零。

## 3. 边界与不做

- 不动 Chroma 上游校验行为本身（本侧 sanitize 即可规避）。
- 上游 issue 不在本提案范围（sirchmunk / pageindex 上游 issue 草稿见 [pageindex-followups_backlog](pageindex-followups_backlog.md) 第 2 节，提交与否由用户决定）。
- 空文本防护（候选池 #1）是否并入本提案或另立，待人工核实 IR 契约后裁决；本草案只登记关联。

## 4. 流转记录

- 2026-11-23：机器起草（SD4.2 每周 followups 扫描；证据 = PI 台账候选池 + logs/app 2026-11-21/22 异常摘要）。
