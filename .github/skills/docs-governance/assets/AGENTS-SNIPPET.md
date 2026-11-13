<!-- 合并进项目 AGENTS.md（或等效的 agent 指令文件）的文档治理纪律片段。按项目实际改写规格名与路径。 -->

## 开发文档参考（开始编码任务前必读）

**编写代码之前，先阅读 `docs/frozen/` 下的已冻结规格**：<主规格名> 是主规格（架构设计、模块契约、测试方案的权威来源）。改动某模块前先查对应 spec 中该模块的契约、验收标准与既有测试语义——既有测试的语义属于契约的一部分，不得因重构而悄然改变。规格中标注的链接路径均相对仓库根。

其余文档**按需选读**，不必预先通读：

- `docs/specs/` — 立项后新规格的落点。
- `docs/analysis/` — 架构分析与演进方向（判断文档，非可执行排期）。
- `docs/reference/` — 使用速查与路线图。
- `docs/backlog/` — 候选功能与遗留事项台账（新提案约定见其 README）。
- 想了解某主题的来龙去脉（起源分析、提案、规格、遗留台账），先查 `docs/MOC.md` 的「文档谱系」表。

**`docs/MOC.md` 是 docs/ 目录的索引文档（MOC, Map of Content）**：在 docs/ 下增删、移动文档后，必须同步更新 MOC.md 的对应条目与文档谱系表；根目录 README 的「开发文档」导航表同步维护。docs/ 文档增删或重命名后，运行文档链接/索引 drift guard 测试（如 `tests/unit/test_docs_links.py`）确认零坏链、索引无遗漏。可选：按 docs-governance skill 的 `assets/docs-guard/` 模板安装 Droid hook，会话内 docs/ 改动在每轮结束时自动触发该检查（结构漂移必须修，语义同步提醒）。

### 规格与排期纪律

- 已冻结规格（frozen/）的任务表已收口，不在其中新增任务。
- 新功能走 `docs/backlog/` 提案（须回答：解决什么已观察到的问题 / 有无更简单方案 / 如何验证有效）。
- 立项时默认把提案 `git mv` 到 `docs/specs/` 改写为独立规格（单文件晋升，命名 `<NAME>_SPEC.md`，不带编号；多提案合并时原提案存为 `frozen/*_backlog_frozen.md`），任务编号用新前缀（取规格名首字母缩写，如 WEBUI_CHAT → WC）避免与已冻结规格冲突；任务全部收口后按编号规则（编号 = 冻结顺序，取 frozen/ 最大编号 +1）命名移入 `docs/frozen/`，并更新 MOC（分区索引 + 谱系表）与 README 导航；遗留事项新建 `backlog/<topic>-followups_backlog.md` 台账，不写回冻结规格。
