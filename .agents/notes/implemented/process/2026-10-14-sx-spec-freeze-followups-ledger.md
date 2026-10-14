# Agent Note: SX 规格冻结与遗留事项台账（sirchmunk-extension-followups）

Status: implemented

## Problem

SIRCHMUNK_EXTENSION_SPEC（SX0–SX5）任务表全部收口（2026-10-14），两件事需要落位：

1. 规格纪律（AGENTS.md 与规格尾部）要求「任务全收口后本规格冻结入 `docs/frozen/`」，文档状态头仍停在「已立项」。
2. 执行过程中沉淀的三类遗留没有归属：**明确不做**（evolver 等 6 项 Out of Scope）、**实现但实验裁决保持关闭/不推荐**（编译 GX 不通过、复用默认关）、**已知缺陷与 gap**（流式线程泄漏、复用不可观测等 7 项）。「为什么不做 evolver」「为什么编译/复用不开」这些理由若只散在冻结规格的任务备注里，下一个接手的人很难检索。

## Decision

- `git mv docs/specs/SIRCHMUNK_EXTENSION_SPEC.md → docs/frozen/SIRCHMUNK_EXTENSION_SPEC_frozen.md`（保留历史），状态头改「已冻结（SX0–SX5 全部收口于 2026-10-14）」并链接遗留台账；前置规格链接改同目录引用。
- 新建 `docs/backlog/sirchmunk-extension-followups.md` 承接三类遗留。命名取 `sirchmunk-extension-` 前缀，与 `SIRCHMUNK_EXTENSION_SPEC_frozen.md`、`sirchmunk-capability-completion.md` 形成「提案 → 规格 → 遗留」谱系，一眼可对应。三类各自语义写死在文档头：第 1 类是**决议记录**（非候选），第 2 类是**负结果存档**（重开需新证据，不是重跑同一实验），第 3 类是**候选池**（其中仅「线程泄漏 → 向上游提 PR」过得了决策三问，明确不排期）。
- 全库引用同步：MOC（specs/ 区恢复「当前为空」）、根 README、AGENTS.md、backlog/README、`SIRCHMUNK_INTEGRATION_SPEC_frozen.md` 的 3 处勘误链接、gap analysis 3 处、capability-completion 2 处链接与状态行。
- 边界裁决：实验数据文件（`scripts/sirchmunk/*.json`）中记录的历史绝对路径**不改写**——那是实验原始记录；frozen 规格内的勘误注记链接更新属于「路径修复」，不违反冻结（先例：2026-10-13 的勘误注记本身）。

## Alternatives considered

- **遗留写进冻结规格新章节**（如「第 8 章 遗留事项」）：被否——冻结语义是「不再新增或改动任务」，往冻结规格加正文章节等于事实解冻，破坏先例。
- **三类各开独立 backlog 提案**：被否——第 1/2 类是决议与负结果，过不了决策三问，按 backlog 约定「尚无法回答三问的只以草案形式记录」；一篇台账承载全部，将来第 3 类某项要动时再拆独立提案。
- **遗留只追加到 gap analysis**：被否——该文档是「能力对照」判断（v0.2 已收口 SX 立项），再塞执行期缺陷会把它变成杂物抽屉，性质不符。

## Consequences

- **收益**：`docs/specs/` 恢复「当前为空」的干净语义；「不做/不开」的理由有了单一可检索权威（含源码实证行号与实验数据指针）；线程泄漏的规避方式（大量 Dashboard DEEP 后重启 sidecar）有台账可查，不再依赖读规格全文。
- **代价**：多一层间接（规格 → 台账）；sirchmunk 主题现在有 5 篇文档（gap analysis、capability-completion、extension spec、followups、integration spec），新人导航依赖 MOC 与互链，任何一篇再移动都要全局查引用。

## Verification

- `rg -n "specs/SIRCHMUNK_EXTENSION_SPEC\.md" docs/ README.md AGENTS.md` 零命中（docs 与索引用尽旧路径）。
- git status 含 RM rename、7 个修改文件、1 个新增台账。
- 关联决策：[路线图主线拆分](2026-10-07-roadmap-migration-split-to-backlog.md)（「章节编号不重排保 frozen 引用」同款纪律）。
