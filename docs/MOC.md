# 开发文档 MOC（docs/ 索引）

> 本文件是 docs/ 目录的**索引文档（MOC, Map of Content）**：docs/ 下增删文档后必须同步更新本文件。项目入口见 [根目录 README](../README.md)。

## specs/ — 开发规格（落点）

**立项后新规格的存放处**（从 [backlog/](backlog/) 晋升）。任务全收口后冻结入 frozen/（SX 系列先例见 [frozen/SIRCHMUNK_EXTENSION_SPEC_frozen.md](frozen/SIRCHMUNK_EXTENSION_SPEC_frozen.md)）。

格式对齐 frozen/ 中 SIRCHMUNK / OBSIDIAN 先例：含模块设计、测试方案、任务排期与进度跟踪表，任务编号用新前缀避免冲突。

| 文档 | 说明 |
|------|------|
| [LOGGING_SPEC.md](specs/LOGGING_SPEC.md) | 日志分层与按日切分规格（LG 系列，进行中）：Trace 日志按日落盘 `logs/traces/`、应用运行日志落盘 `logs/app/`、保留期 30 天、测试隔离（`RAG_LOG_ROOT`）、历史 `traces.jsonl` 迁移 |

## frozen/ — 已冻结文档

任务全部收口、不再新增或改动任务的规格与台账，作为架构与设计参考保留。

| 文档 | 说明 |
|------|------|
| [DEV_SPEC_frozen.md](frozen/DEV_SPEC_frozen.md) | 主开发规格：架构设计、模块设计、技术选型、测试方案、阶段 A–I 排期（已冻结为架构参考） |
| [SIRCHMUNK_INTEGRATION_SPEC_frozen.md](frozen/SIRCHMUNK_INTEGRATION_SPEC_frozen.md) | Sirchmunk Agentic 检索整合规格（S 系列，S0–S4 已交付收口，G0 终裁记录） |
| [SIRCHMUNK_EXTENSION_SPEC_frozen.md](frozen/SIRCHMUNK_EXTENSION_SPEC_frozen.md) | Sirchmunk 能力补齐规格（SX 系列，SX0–SX5 已收口冻结：telemetry 消费、参数透传、compile 实验负结果 GX、复用评估保持关闭、SSE 流式；遗留事项见 [backlog/sirchmunk-extension-followups.md](backlog/sirchmunk-extension-followups.md)） |
| [INGESTION_ROBUSTNESS_SPEC_frozen.md](frozen/INGESTION_ROBUSTNESS_SPEC_frozen.md) | 摄取管线健壮性规格（IR 系列，IR0–IR7 已交付收口，实现偏差见 7.1；现状审计 A1–A7、历史表 v2 / 版本替换 / 同步模式 / 重试诊断 / 通用格式 Loader / M6 Markdown 摄取——v1.1 承接 OBSIDIAN M1/M2，MarkdownLoader + source_filters） |
| [WEBUI_CHAT_SPEC_frozen.md](frozen/WEBUI_CHAT_SPEC_frozen.md) | WebUI 前后端迁移与 Chat 规格（WC 系列，WC0–WC10 已收口冻结：FastAPI + Next 静态导出迁移 P1–P6、Chat 多轮对话 + RAG 有据回答、真流式；D1 推翻 backlog 的 Vue 选型，§1.2 含分析文档五处勘误；WC10.3 门控冒烟记录见冻结记录；Streamlit 保留为 legacy） |
| [ACCEPTANCE_GAPS_frozen.md](frozen/ACCEPTANCE_GAPS_frozen.md) | 验收缺口台账（G1–G3，已关闭） |

## analysis/ — 分析与规划

架构判断文档，非可执行排期。

| 文档 | 说明 |
|------|------|
| [STORAGE_SCALING_ANALYSIS.md](analysis/STORAGE_SCALING_ANALYSIS.md) | 存储层扩容分析（2026-10-16）：各存储现状与扩容极限、瓶颈顺序（BM25 → 向量库 → 元数据库）、对 PostgreSQL/DuckDB 的判断（均非对症）、分阶段演进路线（阶段 0 保持文件型不动，动作落地前另走 backlog 提案） |
| [MICROSERVICES_ANALYSIS.md](analysis/MICROSERVICES_ANALYSIS.md) | 微服务架构分析：当前模块化单体判断与演进方向 |
| [SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md](analysis/SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md) | Sirchmunk 能力覆盖差距分析：已接入与未接入能力对照、对 G0 结论的影响（v0.2：修正复用成本/演化不可达/telemetry 顶层合并三处判断，候选方向已立项 SX 系列） |
| [WEBUI_PARITY_AUDIT.md](analysis/WEBUI_PARITY_AUDIT.md) | WebUI 双轨功能等价核验（WC10.1）：Streamlit 七页 ↔ Next 页面 ↔ API 路由逐项矩阵、缺口清单与严重度、Streamlit 退役待决事项 |
| [SIRCHMUNK_WEBUI_CHAT_REUSE_ANALYSIS.md](analysis/SIRCHMUNK_WEBUI_CHAT_REUSE_ANALYSIS.md) | Sirchmunk WebUI/Chat 复用分析：Chat 实现流程盘点、可复用资产清单（多轮三步法、消息协议、FastAPI 骨架、前端交互）、不抄清单（假流式等）、前端技术栈与传输通道两个待拍板决策 |

## backlog/ — 候选功能与待开发规格

已有初步想法、但尚未开发排期的功能提案与待开发规格。约定见 [backlog/README.md](backlog/README.md)。

| 文档 | 说明 |
|------|------|
| [OBSIDIAN_RAG_SPEC.md](backlog/OBSIDIAN_RAG_SPEC.md) | Obsidian Vault RAG 扩展规格（V 系列；v0.2 起 M1/M2 Markdown 摄取已移交 IR 规格 IR7，本规格保留 M3 sentence-transformers / M4 Semantic Walk 待排期） |
| [dashboard-api-migration.md](backlog/dashboard-api-migration.md) | Dashboard 前后端分离迁移提案（**已立项并入** [frozen/WEBUI_CHAT_SPEC_frozen.md](frozen/WEBUI_CHAT_SPEC_frozen.md) WC 系列；原 Vue 3 选型被其 D1 推翻，保留作决策依据与 P1 拆解） |
| [sirchmunk-capability-completion.md](backlog/sirchmunk-capability-completion.md) | Sirchmunk 剩余能力补齐提案（已立项并收口冻结为 SX 系列，保留作决策依据：telemetry/compile/复用/SSE/参数透传，源码复核证据） |
| [webui-parity-followups.md](backlog/webui-parity-followups.md) | WebUI 双轨核验遗留差异（候选池，未立项）：Deep 进度日志、评估趋势图、sidecar 托管编排、追踪列表翻页等；物理删除 Streamlit 前应再评估 |
| [sirchmunk-extension-followups.md](backlog/sirchmunk-extension-followups.md) | Sirchmunk SX 规格遗留事项台账：明确不做（evolver 等 6 项）、实验负结果存档（编译 GX 不通过、复用保持关闭）、已知缺陷与候选池（流式线程泄漏等 7 项） |

## reference/ — 使用参考

面向使用者的速查手册与路线图。

| 文档 | 说明 |
|------|------|
| [USAGE_GUIDE.md](reference/USAGE_GUIDE.md) | 项目使用指南：主项目用法、Sirchmunk sidecar 搭建与日常启动、常见问题排查、安全边界；第 10 章 Web UI 与 Chat（启动、`RAG_API_TOKEN`、`api:`/`chat:` 配置、WS 帧协议、REST 路由、e2e、Streamlit legacy 删除条件） |
| [FUTURE_OPTIMIZATION_ROADMAP.md](reference/FUTURE_OPTIMIZATION_ROADMAP.md) | 后续优化路线图：候选优化方向与决策纪律（Dashboard 前后端分离迁移已拆分至 [backlog/dashboard-api-migration.md](backlog/dashboard-api-migration.md)，见其 6.10；含第 10 章决策原则） |
