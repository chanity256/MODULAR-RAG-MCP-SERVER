# 候选功能（backlog/）

存放**待选功能**的提案与**遗留事项台账**：已有初步想法、但尚未立项排期。

## 约定

- 每个候选功能一个文档，命名 `<kebab-case>_backlog.md`（如 `graph-rag_backlog.md`）。
- 文档内容建议至少回答三个问题（对齐 [FUTURE_OPTIMIZATION_ROADMAP 第 10 章](../reference/FUTURE_OPTIMIZATION_ROADMAP.md)的决策原则）：
  1. 它解决了哪个已经观察到的问题？
  2. 是否有更简单、改动更小的方案？
  3. 如何通过测试、指标或用户流程确认它确实有效？
- 尚无法回答上述三问的，只在此目录以草案形式记录，不进入排期。
- 文件头写「起源：<链接>」，说明想法来自哪份分析、路线图条目或冻结规格。
- **晋升路径**（默认单文件晋升）：立项时把提案 `git mv` 到 [specs/](../specs/) 并改写为规格（先纯移动提交，再改写提交；格式对齐 frozen/ 中 SIRCHMUNK / WEBUI_CHAT / LOGGING 先例），三问答案并入规格第 1 章「决策依据」。例外：
  - 多个提案合并进一个规格，或规格大幅推翻提案时，原提案立项时即移入 [frozen/](../frozen/) 存为 `*_backlog_frozen.md`（不参与编号），规格引用它作决策依据。
  - 提案只有一部分被某规格承接时（如 OBSIDIAN M1/M2 → IR7），本文档留在此目录，删去已移交内容的细节，并在文件头记录移交去向。
- 冻结规格的遗留事项新建 `<topic>-followups_backlog.md` 台账，不写回冻结文档。
- 文档离开本目录（立项、存档）后，从下方「当前候选」表移除；历史谱系见 [MOC 文档谱系](../MOC.md#文档谱系)。

## 当前候选

只列本目录下实际存在的文档。

| 文档 | 一句话说明 |
|------|-----------|
| [OBSIDIAN_RAG_SPEC_backlog.md](OBSIDIAN_RAG_SPEC_backlog.md) | Obsidian Vault RAG 扩展规格（V 系列；M1/M2 Markdown 摄取已移交 INGESTION_ROBUSTNESS_SPEC 阶段 IR7，剩余 M3/M4 待排期） |
| [webui-parity-followups_backlog.md](webui-parity-followups_backlog.md) | WebUI 双轨核验遗留差异（WC10.1）：#5 翻页/#2 趋势图/#3 预检已移交并随 [frozen/08_WEBUI_PARITY_SPEC_frozen.md](../frozen/08_WEBUI_PARITY_SPEC_frozen.md) 收口（WP 系列）；剩 Deep 进度日志（依赖上游线程泄漏修复）、sidecar 托管编排二选一、展示细节接受项 |
| [sirchmunk-extension-followups_backlog.md](sirchmunk-extension-followups_backlog.md) | Sirchmunk SX 规格遗留事项台账：明确不做（evolver 等 6 项）、实验负结果存档（编译 GX 不通过、复用保持关闭）、已知缺陷与候选池（流式线程泄漏等 7 项） |
