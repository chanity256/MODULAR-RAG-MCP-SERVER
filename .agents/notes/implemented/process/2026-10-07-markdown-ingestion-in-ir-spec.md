# Agent Note: Markdown 摄取承接进 IR 规格（解冻重开而非新规格）

Status: implemented

## Problem

路线图 6.8 的三方对比实验（主链路 RAG vs sidecar vs 裸 rg）被格式硬阻塞：golden 题集的答案全在 `.py`/`.md` 文件里，而主链路 Loader 只支持 pdf/docx/pptx/xlsx/html，结构性地无法参赛。Markdown 摄取的设计（MarkdownLoader + 路由 + 目录排除）原躺在 `docs/backlog/OBSIDIAN_RAG_SPEC.md` 的 M1/M2（V1–V4），未排期。

同时有一个治理约束：AGENTS.md 规定已收口规格的任务表不在其中新增任务，新功能走 backlog 提案 → `docs/specs/` 独立规格、任务编号用新前缀。IR 规格当时已在 `docs/frozen/` 冻结收口（IR0–IR6）。

## Decision

经用户明确拍板（「将 M1/M2 提取移动到 INGESTION_ROBUSTNESS_SPEC 排期，该规格改为 spec 执行文档」），IR 规格解除冻结：

- 文件 `docs/frozen/INGESTION_ROBUSTNESS_SPEC_frozen.md` 移入 `docs/specs/INGESTION_ROBUSTNESS_SPEC.md`（git mv 保留历史），版本升 1.1，状态头记录冻结/重开两次事件。
- OBSIDIAN 的 M1/M2 整体迁移为 IR 规格模块 M6、阶段 IR7，任务续编 IR7.1–IR7.5（不启用 V 前缀新篇）；设计全文（原 OBSIDIAN 3.1/3.2）迁至 IR 规格 3.8 节，后续以该节为唯一权威。
- OBSIDIAN_RAG_SPEC 升 0.2，仅保留 M3（sentence-transformers）/ M4（Semantic Walk），M1/M2 条目标注「已移交 → IR7.x」。
- AGENTS.md 的规格与排期条款同步改写：收口规格不再新增任务是默认，例外是经明确指令重开的执行中规格。

## Alternatives considered

- **独立新规格（治理默认路径）**：最强论据是不破「收口规格不可改」的纪律，格式对齐 SIRCHMUNK/OBSIDIAN 先例。被否：Markdown 摄取与 IR0–IR6 交付物深度耦合——`.md` 双登记要翻 IR5 留下的 drift guard 断言、单文件重摄取走 IR1/IR2 的历史表 v2 与版本替换、目录过滤要接入 IR3 的 `collect_and_diff`、CLI 汇总复用 IR3/IR4 的 `IngestSummary`。拆成两份规格会互相引用同一批文件与测试，设计权威出现两处。
- **继续留在 backlog 待排期**：最强论据是零治理成本。被否：需求已实际观察到（6.8 实验阻塞 + vault 检索诉求），不是「无法回答三问」的候选想法。

## Consequences

- **收益**：Markdown 摄取复用 IR 的幂等替换、历史表 v2、drift guard、同步模式全套基线（后由 IR7.1–IR7.5 兑现，全量 1564 passed）；MarkdownLoader 设计只有 3.8 节一个权威来源。
- **代价**：治理规则出现一个显式例外（收口规格经用户指令重开），AGENTS.md 需要为此改写条款；`docs/specs/` 的语义从「仅新规格落点」变为「含重开的收口规格」。
- **开放项**：IR7 收口后本规格再次全收口，是否按惯例移回 `docs/frozen/`（重命名 `_frozen`）由用户决定，规格内部不自行回迁。

## Verification

- `docs/specs/INGESTION_ROBUSTNESS_SPEC.md` 头部状态行与 7 章 IR7 进度表（IR7.1–IR7.5 全 `[x]`，2026-10-07）。
- 全仓库 `rg INGESTION_ROBUSTNESS_SPEC_frozen` 零残留（测试、配置注释、README、MOC、AGENTS、路线图均已改指新路径）。
- 关联设计取舍：[MarkdownLoader 的 vault_root 与缓存取舍](../feature/2026-10-07-markdown-loader-vault-root-no-cache.md)。
