# Agent Note: 路线图主线拆分为 backlog 提案（章节编号保持 6–10）

Status: implemented

## Problem

`docs/reference/FUTURE_OPTIMIZATION_ROADMAP.md` 混装了两条性质不同的线：第 1–5 章是 Dashboard 前后端分离的完整可执行方案（现状限制、三层目标架构、页面/接口映射、P1–P6 实施包，含任务拆解与回归命令），第 6–10 章才是"候选方向池 + 决策纪律"的路线图本体。按 AGENTS.md 的文档治理，可执行的候选功能提案应落 `docs/backlog/`（提案 → 立项 → `docs/specs/`），`reference/` 只放速查与路线图；且 backlog/README 的提案约定（决策三问）需要独立提案文档承载，塞在路线图章节里无法走"立项时引用原提案"的晋升路径。

## Decision

（2026-10-07，用户拍板："一、二输出为前后端优化的文档到 docs/backlog/ 中，原文件的章节就删除，可以作为第 6 章的子章节简要提及"。）

- 原 1–5 章逐字迁移为 [dashboard-api-migration.md](../../../../../docs/backlog/dashboard-api-migration.md)（kebab-case 对齐 backlog 约定），头部补起源/状态说明与决策三问节。
- 路线图重写：新增简短「文档目的 + 拆分说明」，删除 1–5 章；第 6 章下新增 6.10 子章节一段话指向提案；第 7 章近期建议加一行提案指针。
- **章节编号保持 6–10 不重排**：冻结规格（INGESTION_ROBUSTNESS 的 6.2、SIRCHMUNK_INTEGRATION 的 6.3）与 backlog README 均已引用 6.2/6.3/6.8/第 10 章；frozen 文档不为重排而改，保留编号零成本消灭断链。
- 索引同步：MOC、根 README、backlog/README 三处。

## Alternatives considered

- **整篇路线图搬进 backlog**：被否——6–10 章是长期候选池与决策纪律（含"暂不建议实施"反清单），性质是 reference 不是单条提案。
- **1–5 章原位保留、开头加链接**：被否——出现两个权威源，P1–P6 任务拆解在两处会漂移。
- **删除后全文重编号（6–10 → 2–6）**：被否——需要修改 frozen 规格内的既有章节引用，违反冻结纪律，收益仅是编号连续。

## Consequences

- **收益**：backlog 获得完整度最高的提案（实施包级任务拆解 + 验收命令，随时可立项）；路线图瘦身回"候选池 + 纪律"本位；frozen 引用零破坏（git diff 验证第 6–10 章正文零改动，8 insertions / 376 deletions）。
- **代价**：路线图章节号从 6 起跳，首次阅读会困惑（靠拆分说明注解缓解）；后续增删章节须维持"不重排"纪律，编辑者需要知道这个约定。
- 编号不重排的代价在 SX 系列收口时（2026-10-14）再次兑现并被验证为正确选择——期间 frozen 规格新增了 3 处对"第 10 章"的引用。

## Verification

- `git diff` 确认路线图仅 8 行新增（拆分说明/6.10/第 7 章指针），第 6–10 章正文未动。
- `rg "specs/SIRCHMUNK_EXTENSION_SPEC\.md|FUTURE_OPTIMIZATION_ROADMAP 第" docs/ README.md AGENTS.md` 无断链；MOC/README/backlog README 三处索引已同步。
- 关联决策：[SX 规格冻结与遗留台账](2026-10-14-sx-spec-freeze-followups-ledger.md)（同一治理纪律在规格收口期的应用）。
