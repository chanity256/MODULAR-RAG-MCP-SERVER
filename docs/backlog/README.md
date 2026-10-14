# 候选功能（backlog/）

存放**待选功能**的设计文档：已有初步想法、但尚未立项排期的功能提案。

## 约定

- 每个候选功能一个文档，命名 `kebab-case.md`（如 `graph-rag.md`）。
- 文档内容建议至少回答三个问题（对齐 [FUTURE_OPTIMIZATION_ROADMAP 第 10 章](../reference/FUTURE_OPTIMIZATION_ROADMAP.md)的决策原则）：
  1. 它解决了哪个已经观察到的问题？
  2. 是否有更简单、改动更小的方案？
  3. 如何通过测试、指标或用户流程确认它确实有效？
- **晋升路径**：功能立项后，规格文档移入 [specs/](../specs/)（格式对齐 DEV_SPEC 的扩展规格先例，如 SIRCHMUNK/OBSIDIAN），并在其中引用本目录的原提案作为决策依据。
- 尚无法回答上述三问的，只在此目录以草案形式记录，不进入排期。

## 当前候选

| 文档 | 一句话说明 |
|------|-----------|
| [OBSIDIAN_RAG_SPEC.md](OBSIDIAN_RAG_SPEC.md) | Obsidian Vault RAG 扩展规格（V 系列；M1/M2 Markdown 摄取已移交 INGESTION_ROBUSTNESS_SPEC 阶段 IR7，剩余 M3/M4 待排期） |
| [dashboard-api-migration.md](dashboard-api-migration.md) | Dashboard 前后端分离迁移提案（Vue 3 + Vite + FastAPI；自 FUTURE_OPTIMIZATION_ROADMAP 原第 1–5 章拆分，含 P1–P6 实施包） |
| [sirchmunk-capability-completion.md](sirchmunk-capability-completion.md) | Sirchmunk 剩余能力补齐提案（已立项并**收口冻结**为 [frozen/SIRCHMUNK_EXTENSION_SPEC_frozen.md](../frozen/SIRCHMUNK_EXTENSION_SPEC_frozen.md)，本页保留作决策依据） |
| [sirchmunk-extension-followups.md](sirchmunk-extension-followups.md) | Sirchmunk SX 规格遗留事项台账：明确不做（evolver 等 6 项）、实验负结果存档（编译 GX 不通过、复用保持关闭）、已知缺陷与候选池（流式线程泄漏等 7 项） |
