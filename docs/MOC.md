# 开发文档 MOC（docs/ 索引）

> 本文件是 docs/ 目录的**索引文档（MOC, Map of Content）**：docs/ 下增删文档后必须同步更新本文件。项目入口见 [根目录 README](../README.md)。

## 命名与流转规范

docs/ 文档分两类：

- **流转文档**（backlog/ → specs/ → frozen/）：「目录即状态」，同一文件 `git mv` 前进；每个主题同一时刻最多一份活跃文档（位于 backlog/ 或 specs/）。
- **常驻文档**（analysis/、reference/）：原地维护、版本号递增，不进入 specs/；提案与规格在文件头以「起源：<链接>」引用它们。只服务单一规格的分析（该规格的输入调研或任务交付物）在该规格冻结时随之冻结；仍被活跃提案或台账依赖的除外。

| 状态 | 位置 | 命名 | 示例 |
|------|------|------|------|
| 候选提案 / 遗留台账 | backlog/ | `<kebab-case>_backlog.md` | `graph-rag_backlog.md` |
| 开发中规格 | specs/ | `<NAME>_SPEC.md`（SCREAMING，不带编号） | `INGESTION_ROBUSTNESS_SPEC.md` |
| 已冻结规格 / 台账 | frozen/ | `<NN>_<NAME>_frozen.md` | `04_INGESTION_ROBUSTNESS_SPEC_frozen.md` |
| 谱系存档（提案） | frozen/ | `<kebab-case>_backlog_frozen.md` | `dashboard-api-migration_backlog_frozen.md` |
| 谱系存档（绑定型分析） | frozen/ | `<原名>_frozen.md` | _（暂无）_ |

流转方式（下方谱系表「流转方式」列的固定词表，定义见 docs-governance skill §2.1）：

| 流转方式 | 含义 |
|----------|------|
| 单文件晋升 | 默认：提案 `git mv` 到 specs/ 改写为规格（先纯移动提交，再改写提交），三问答案并入规格第 1 章；分析就地升级为规格也属此类 |
| 提案存档 | 多提案合并进一个规格，或规格大幅推翻提案：原提案立项时存为 `*_backlog_frozen.md` 作决策依据 |
| 拆分移交 | 提案只有一部分被某规格承接：提案留在 backlog/，文件头记录移交去向 |
| 跳过提案 | 用户直接拍板，规格第 1 章写明起源与决策 |
| 重开 | 冻结规格经明确指令移回 specs/ 追加阶段，再冻结时沿用原编号 |
| 派生台账 | 冻结规格的遗留事项新建 `<topic>-followups_backlog.md`，不写回冻结文档 |
| 止于分析 | 分析结论为暂不做，不产生提案 |

编号纪律（冻结时执行，三条）：

1. **编号 = 冻结顺序**（近似实现顺序），不按立项顺序；冻结时取 frozen/ 现有最大编号 +1，永不复用、不插入；重开后再冻结沿用原编号。
2. 非系列台账（如 `02_ACCEPTANCE_GAPS_frozen.md`）同样参与编号。
3. frozen/ 中不带编号的文件都是谱系存档，不参与编号。

流转操作清单（立项、冻结、拆分、重开、派生台账时执行）：`git mv` 并按上述规则命名 → 全库同步引用（AGENTS.md、README、MOC、backlog/README、引用方文档）→ 更新 MOC 分区索引与「文档谱系」表对应行，backlog/README「当前候选」表移除已离开 backlog/ 的文档 → 跑 `pytest tests/unit/test_docs_links.py` 确认零坏链、MOC 无遗漏 → 更新 README 导航表。冻结时另检查绑定型分析是否随之冻结。

自动守卫：项目级 hook（`.factory/hooks.json` + `.factory/hooks/docs_guard.py`）在 Droid 会话里自动执行上述纪律——每轮开始给 docs/ 拍哈希快照，本轮结束时对比；有 docs/ 改动则跑 `test_docs_links.py` 做结构检查（坏链、MOC 漏登记），并按静态映射表提醒需核对的关联文档（frozen→AGENTS/README/MOC，specs→MOC/AGENTS，backlog→backlog/README/MOC，analysis/reference→MOC）。hook 只检测与回传，修复按 docs-governance skill 执行；规则本体只在 `test_docs_links.py` 一处。

## 文档谱系

一个主题一行，记录文档间的对应关系与流转方式；各文档的当前状态见下方分区索引。历史流转按实际发生记录，不按现行规则回迁。

| 主题 / 系列 | 起源 | 提案 | 规格 | 后续 | 流转方式 |
|-------------|------|------|------|------|----------|
| 主规格（A–I） | 项目初始规格 | — | [01_DEV_SPEC_frozen.md](frozen/01_DEV_SPEC_frozen.md) | [02_ACCEPTANCE_GAPS_frozen.md](frozen/02_ACCEPTANCE_GAPS_frozen.md)（验收缺口台账 G1–G3，随主规格冻结） | 跳过提案 |
| Sirchmunk 整合（S） | 《整合可行性分析》（无独立文件，就地升级为规格 v0.2） | — | [03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md](frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md) | [SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md](analysis/SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md)（以 S0–S4 收口状态为分析起点） | 单文件晋升 |
| 摄取健壮性（IR） | [FUTURE_OPTIMIZATION_ROADMAP.md](reference/FUTURE_OPTIMIZATION_ROADMAP.md) 6.2；v1.1 起源 6.8 | — | [04_INGESTION_ROBUSTNESS_SPEC_frozen.md](frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md) | — | 跳过提案 → 重开（v1.1 承接 OBSIDIAN M1/M2 为 IR7） |
| Obsidian Vault RAG（V） | — | [OBSIDIAN_RAG_SPEC_backlog.md](backlog/OBSIDIAN_RAG_SPEC_backlog.md) | M1/M2 → [04_INGESTION_ROBUSTNESS_SPEC_frozen.md](frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md)（IR7） | M3/M4 留在提案中待排期 | 拆分移交 |
| Sirchmunk 能力补齐（SX） | [SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md](analysis/SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md) v0.2 | [sirchmunk-capability-completion_backlog_frozen.md](frozen/sirchmunk-capability-completion_backlog_frozen.md) | [05_SIRCHMUNK_EXTENSION_SPEC_frozen.md](frozen/05_SIRCHMUNK_EXTENSION_SPEC_frozen.md) | [sirchmunk-extension-followups_backlog.md](backlog/sirchmunk-extension-followups_backlog.md)；起源分析更新至 v0.3 | 提案存档 → 派生台账 |
| WebUI + Chat（WC） | [FUTURE_OPTIMIZATION_ROADMAP.md](reference/FUTURE_OPTIMIZATION_ROADMAP.md) 原第 1–5 章（拆出为提案）；[SIRCHMUNK_WEBUI_CHAT_REUSE_ANALYSIS.md](analysis/SIRCHMUNK_WEBUI_CHAT_REUSE_ANALYSIS.md) | [dashboard-api-migration_backlog_frozen.md](frozen/dashboard-api-migration_backlog_frozen.md) | [06_WEBUI_CHAT_SPEC_frozen.md](frozen/06_WEBUI_CHAT_SPEC_frozen.md)（D1 推翻提案的 Vue 选型；§1.2 勘误起源分析） | [WEBUI_PARITY_AUDIT.md](analysis/WEBUI_PARITY_AUDIT.md)（WC10.1 交付物）→ webui-parity-followups 台账（已晋升为下方 SR 规格） | 提案存档（多来源合并） → 派生台账 |
| 日志分层（LG） | 会话内 `logs/traces.jsonl` 体量审计（无独立文件） | — | [07_LOGGING_SPEC_frozen.md](frozen/07_LOGGING_SPEC_frozen.md) | 遗留见规格 2.2（未建台账） | 跳过提案 |
| 存储扩容 | [STORAGE_SCALING_ANALYSIS.md](analysis/STORAGE_SCALING_ANALYSIS.md) | — | — | — | 止于分析（阶段 0 保持文件型存储不动） |
| 微服务演进 | [MICROSERVICES_ANALYSIS.md](analysis/MICROSERVICES_ANALYSIS.md) | — | — | — | 止于分析（保持模块化单体） |
| AI-Native SDLC 流程升级（SD，含持续集成 CI） | Anthropic [The AI-native SDLC playbook](https://claude.com/resources/articles/the-ai-native-sdlc-playbook)（外部参照）→ 常驻分析 `AI_NATIVE_SDLC_UPGRADE_PLAN.md` v0.4（就地升级为规格，未入 git）；CI 部分起源 [01_DEV_SPEC_frozen.md](frozen/01_DEV_SPEC_frozen.md) §4.5、[FUTURE_OPTIMIZATION_ROADMAP.md](reference/FUTURE_OPTIMIZATION_ROADMAP.md) §6.7 | [ci-pipeline_backlog_frozen.md](frozen/ci-pipeline_backlog_frozen.md)（CI-1~CI-4 并入 SD1） | [AI_NATIVE_SDLC_SPEC.md](specs/AI_NATIVE_SDLC_SPEC.md) | [AI_NATIVE_SDLC_REFERENCE.md](reference/AI_NATIVE_SDLC_REFERENCE.md)（派生参考手册：流程图 + 工具 / 文档准备清单） | 单文件晋升（分析就地升级）；提案存档（ci-pipeline） |
| WebUI 双轨差异收口（WP） | [WEBUI_PARITY_AUDIT.md](analysis/WEBUI_PARITY_AUDIT.md)（WC10.1 缺口清单 L3/L4） | webui-parity-followups 台账（已晋升为 [frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md](frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md)，原文见 git 历史） | [08_WEBUI_PARITY_SPEC_frozen.md](frozen/08_WEBUI_PARITY_SPEC_frozen.md) | 台账剩余项二次移交 WS 规格（见下行） | 拆分移交 |
| WebUI sidecar 补齐（WS） | 同上台账（#4 sidecar 托管编排、#1 Deep 进度日志） | webui-parity-followups 台账（第二次拆分移交；已晋升为 [frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md](frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md)） | [09_WEBUI_SIDECAR_SPEC_frozen.md](frozen/09_WEBUI_SIDECAR_SPEC_frozen.md) | [sirchmunk-extension-followups_backlog.md](backlog/sirchmunk-extension-followups_backlog.md) #1（SX5.3 线程泄漏，门控默认关闭的依据） | 拆分移交 |
| Streamlit 退役（SR） | [WEBUI_PARITY_AUDIT.md](analysis/WEBUI_PARITY_AUDIT.md)（退役门槛清单）；webui-parity-followups 台账（#3 接受项决议并入其 Out of Scope） | [STREAMLIT_RETIREMENT_SPEC.md](frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md)（单文件晋升，原台账原文见 git 历史） | [10_STREAMLIT_RETIREMENT_SPEC_frozen.md](frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md)（已收口冻结） | — | 单文件晋升 |
| Chat 模式选择器着色（CMC） | 会话内用户截图（三按钮同质难辨认，无独立文件） | chat-mode-selector-colors_backlog.md（单文件晋升，原文见 git 历史） | [CHAT_MODE_COLORS_SPEC.md](specs/CHAT_MODE_COLORS_SPEC.md) | — | 单文件晋升 |
## specs/ — 开发规格（落点）

**立项后新规格的存放处**（从 [backlog/](backlog/) 晋升）。任务全收口后冻结入 frozen/（SX 系列先例见 [frozen/05_SIRCHMUNK_EXTENSION_SPEC_frozen.md](frozen/05_SIRCHMUNK_EXTENSION_SPEC_frozen.md)）。

格式对齐 frozen/ 中 SIRCHMUNK / WEBUI_CHAT / LOGGING 先例：含模块设计、测试方案、任务排期与进度跟踪表，任务编号用新前缀避免冲突。

| 文档 | 说明 |
|------|------|
| [AI_NATIVE_SDLC_SPEC.md](specs/AI_NATIVE_SDLC_SPEC.md) | AI-Native SDLC 流程升级规格（SD 系列，v0.5 立项；SD1 CI 门（含 fork 上真实 run 与分支保护）、SD2 review 规则、SD6 skill 单一来源、SD3a QA 计划清理与 QA ↔ pytest 对照补测已交付，SD5 过程度量基线已出（[process-metrics.md](analysis/process-metrics.md)），SD7 规格结构校验已接入测试，SD4 定时闭环已交付（两个本机周度 automation active：followups 扫描起草草案 PR + QA 离线回归写回进度，SD4.4 阈值后置），SD3.4 evals 单次成本已测、预算已确认（D5 终裁为 BYOK `custom:glm-5.3-Flash-2`，SD3.5 evals 判据 / 运行器 / 5 条用例已交付（`evals/`），SD3.6 `agent-evals.yml` 已接入（CI 基线 2/5，门槛按基线校准），SD3.7 test-file 保护 hook 已交付（`.factory/hooks/test_guard.py`），D6 baseline PR 已合入；SD2.3 自动 review 已接入（2026-11-14，验收进行中））：前置决策 D1–D6 + baseline PR、CI 确定性门（干净环境 / 双 job / 禁网 / 红线脚本 / CODEOWNERS，承接 ci-pipeline 提案）、AI PR review、QA 对照与 agent evals（门控）、定时闭环（SD4.4 阈值后置）、过程度量、skill 单一来源 + plan + PR 化、规格结构校验（可选）；不改 `src/` 业务行为 |
| [CHAT_MODE_COLORS_SPEC.md](specs/CHAT_MODE_COLORS_SPEC.md) | Chat 模式选择器按模式着色规格（CMC 系列，v0.1 立项，由 chat-mode-selector-colors 提案单文件晋升；CMC1–CMC2 已完成）：Hybrid / Agentic (fast) / Agentic (deep) 选中态黄 / 绿 / 蓝，light 浅底深字、dark 半透明底亮字，实测对比度均 ≥ 6.3:1；只改 `ModeSelector.tsx` 与单测 |

## frozen/ — 已冻结文档

任务全部收口、不再新增或改动任务的规格与台账，作为架构与设计参考保留。

| 文档 | 说明 |
|------|------|
| [01_DEV_SPEC_frozen.md](frozen/01_DEV_SPEC_frozen.md) | 主开发规格：架构设计、模块设计、技术选型、测试方案、阶段 A–I 排期（已冻结为架构参考） |
| [02_ACCEPTANCE_GAPS_frozen.md](frozen/02_ACCEPTANCE_GAPS_frozen.md) | 验收缺口台账（G1–G3，已关闭） |
| [03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md](frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md) | Sirchmunk Agentic 检索整合规格（S 系列，S0–S4 已交付收口，G0 终裁记录） |
| [04_INGESTION_ROBUSTNESS_SPEC_frozen.md](frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md) | 摄取管线健壮性规格（IR 系列，IR0–IR7 已交付收口，实现偏差见 7.1；现状审计 A1–A7、历史表 v2 / 版本替换 / 同步模式 / 重试诊断 / 通用格式 Loader / M6 Markdown 摄取——v1.1 承接 OBSIDIAN M1/M2，MarkdownLoader + source_filters） |
| [05_SIRCHMUNK_EXTENSION_SPEC_frozen.md](frozen/05_SIRCHMUNK_EXTENSION_SPEC_frozen.md) | Sirchmunk 能力补齐规格（SX 系列，SX0–SX5 已收口冻结：telemetry 消费、参数透传、compile 实验负结果 GX、复用评估保持关闭、SSE 流式；遗留事项见 [backlog/sirchmunk-extension-followups_backlog.md](backlog/sirchmunk-extension-followups_backlog.md)） |
| [06_WEBUI_CHAT_SPEC_frozen.md](frozen/06_WEBUI_CHAT_SPEC_frozen.md) | WebUI 前后端迁移与 Chat 规格（WC 系列，WC0–WC10 已收口冻结：FastAPI + Next 静态导出迁移 P1–P6、Chat 多轮对话 + RAG 有据回答、真流式；D1 推翻 backlog 的 Vue 选型，§1.2 含分析文档五处勘误；WC10.3 门控冒烟记录见冻结记录；Streamlit 保留为 legacy） |
| [07_LOGGING_SPEC_frozen.md](frozen/07_LOGGING_SPEC_frozen.md) | 日志分层与按日切分规格（LG 系列，LG0–LG7 已收口冻结）：Trace 日志按日落盘 `logs/traces/`、应用运行日志落盘 `logs/app/`、保留期 30 天、测试隔离（`RAG_LOG_ROOT`）、历史 `traces.jsonl` 迁移（真实迁移结果与实现偏差见 7.1） |
| [08_WEBUI_PARITY_SPEC_frozen.md](frozen/08_WEBUI_PARITY_SPEC_frozen.md) | WebUI 双轨遗留差异收口规格（WP 系列，WP0–WP2 已收口冻结）：Ingestion / Query 追踪列表服务端 offset 翻页、评估历史内联 SVG 趋势图、评估提交路径 422 预检；承接自 webui-parity-followups 台账（已晋升为 [frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md](frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md)）的 #5/#2/#3 预检 |
| [09_WEBUI_SIDECAR_SPEC_frozen.md](frozen/09_WEBUI_SIDECAR_SPEC_frozen.md) | WebUI sidecar 补齐规格（WS 系列，WS0–WS4 已收口冻结）：`scripts/sidecar_manager.py` 共享托管编排 + `start_api.py --manage-sidecar`、`core/chat/progress.py` 共享日志转帧、门控 `WS /api/v1/search/ws`（`api.deep_progress_stream` 默认 false，SX5.3 规避）、Playground Deep 前端流式接入；承接自 webui-parity-followups 台账（已晋升为 [frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md](frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md)）的 #4/#1 |
| [10_STREAMLIT_RETIREMENT_SPEC_frozen.md](frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md) | Streamlit 退役规格（SR 系列，SR0–SR6 已收口冻结）：物理删除 Streamlit 页面层 / `start_dashboard.py` / streamlit 依赖 / Streamlit-only 配置与页面级测试，文档与技能镜像同步；`services/` 层及其测试保留（API 层依赖）；由 webui-parity-followups 台账单文件晋升，#3 接受项决议存档于其 Out of Scope；实测 2460 passed / 11 skipped |
| [dashboard-api-migration_backlog_frozen.md](frozen/dashboard-api-migration_backlog_frozen.md) | Dashboard 前后端分离迁移提案（**已立项并入** [frozen/06_WEBUI_CHAT_SPEC_frozen.md](frozen/06_WEBUI_CHAT_SPEC_frozen.md) WC 系列并收口冻结；原 Vue 3 选型被其 D1 推翻，保留作决策依据与 P1 拆解；backlog 提案冻结后保留 `_backlog` 后缀追加 `_frozen`） |
| [ci-pipeline_backlog_frozen.md](frozen/ci-pipeline_backlog_frozen.md) | CI 持续集成管线提案（**已立项并入** [specs/AI_NATIVE_SDLC_SPEC.md](specs/AI_NATIVE_SDLC_SPEC.md) SD1：CI-1~CI-4 → SD1.2/1.3/1.4/1.8；保留作决策依据，其 Out of Scope 继承为规格 §2.2） |
| [sirchmunk-capability-completion_backlog_frozen.md](frozen/sirchmunk-capability-completion_backlog_frozen.md) | Sirchmunk 剩余能力补齐提案（已立项并收口冻结为 SX 系列，保留作决策依据：telemetry/compile/复用/SSE/参数透传，源码复核证据） |

## analysis/ — 分析与规划

架构判断文档，非可执行排期。

| 文档 | 说明 |
|------|------|
| [process-metrics.md](analysis/process-metrics.md) | 过程度量（SD5，通用型常驻分析，1.0 基线）：立项→首任务、立项→冻结、修复轮数、QA 周转、revert/hotfix/fix 五项指标口径与基线，数据由 `scripts/measure_process.py` 离线从 git 历史算出；1.1 版起设目标值 |
| [STORAGE_SCALING_ANALYSIS.md](analysis/STORAGE_SCALING_ANALYSIS.md) | 存储层扩容分析（2026-10-16）：各存储现状与扩容极限、瓶颈顺序（BM25 → 向量库 → 元数据库）、对 PostgreSQL/DuckDB 的判断（均非对症）、分阶段演进路线（阶段 0 保持文件型不动，动作落地前另走 backlog 提案） |
| [MICROSERVICES_ANALYSIS.md](analysis/MICROSERVICES_ANALYSIS.md) | 微服务架构分析：当前模块化单体判断与演进方向 |
| [SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md](analysis/SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md) | Sirchmunk 能力覆盖差距分析：已接入与未接入能力对照、对 G0 结论的影响（v0.3：按 SX0–SX5 收口结果更新现状——telemetry/参数透传/SSE 已接入，编译与复用已接入但实验裁决不推荐默认开，演化不可达；剩余缺口指向 followups 台账） |
| [WEBUI_PARITY_AUDIT.md](analysis/WEBUI_PARITY_AUDIT.md) | WebUI 双轨功能等价核验（WC10.1）：Streamlit 七页 ↔ Next 页面 ↔ API 路由逐项矩阵、缺口清单与严重度、Streamlit 退役待决事项 |
| [SIRCHMUNK_WEBUI_CHAT_REUSE_ANALYSIS.md](analysis/SIRCHMUNK_WEBUI_CHAT_REUSE_ANALYSIS.md) | Sirchmunk WebUI/Chat 复用分析：Chat 实现流程盘点、可复用资产清单（多轮三步法、消息协议、FastAPI 骨架、前端交互）、不抄清单（假流式等）、前端技术栈与传输通道两个待拍板决策 |

## backlog/ — 候选功能与遗留台账

已有初步想法、但尚未立项排期的功能提案与遗留事项台账。约定见 [backlog/README.md](backlog/README.md)。

| 文档 | 说明 |
|------|------|
| [OBSIDIAN_RAG_SPEC_backlog.md](backlog/OBSIDIAN_RAG_SPEC_backlog.md) | Obsidian Vault RAG 扩展规格（V 系列；v0.2 起 M1/M2 Markdown 摄取已移交 IR 规格 IR7，本规格保留 M3 sentence-transformers / M4 Semantic Walk 待排期） |
| [sirchmunk-extension-followups_backlog.md](backlog/sirchmunk-extension-followups_backlog.md) | Sirchmunk SX 规格遗留事项台账：明确不做（evolver 等 6 项）、实验负结果存档（编译 GX 不通过、复用保持关闭）、已知缺陷与候选池（流式线程泄漏等 7 项） |

## reference/ — 使用参考

面向使用者的速查手册与路线图。

| 文档 | 说明 |
|------|------|
| [USAGE_GUIDE.md](reference/USAGE_GUIDE.md) | 项目使用指南：主项目用法、Sirchmunk sidecar 搭建与日常启动、常见问题排查、安全边界；第 10 章 Web UI 与 Chat（启动、`RAG_API_TOKEN`、`api:`/`chat:` 配置、WS 帧协议、REST 路由、e2e、Streamlit legacy 删除条件） |
| [AI_NATIVE_SDLC_REFERENCE.md](reference/AI_NATIVE_SDLC_REFERENCE.md) | AI-Native SDLC 参考手册：门禁架构 / 任务开发循环 / 规格生命周期三张 mermaid 流程图、落地前需准备的工具清单与文档清单、落地顺序与门控验收要点速查；设计细节与落地记录见 [AI_NATIVE_SDLC_SPEC.md](specs/AI_NATIVE_SDLC_SPEC.md) |
| [FUTURE_OPTIMIZATION_ROADMAP.md](reference/FUTURE_OPTIMIZATION_ROADMAP.md) | 后续优化路线图：候选优化方向与决策纪律（Dashboard 前后端分离迁移已拆分至 [frozen/dashboard-api-migration_backlog_frozen.md](frozen/dashboard-api-migration_backlog_frozen.md)，见其 6.10；含第 10 章决策原则） |
