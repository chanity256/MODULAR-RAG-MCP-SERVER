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

流转操作清单（立项、冻结、拆分、重开、派生台账时执行）：`git mv` 并按上述规则命名 → 全库同步引用（README、MOC、backlog/README、引用方文档；**AGENTS.md 仅在冻结新增「必读」规格条目或既有条目路径变化时更新，任务进度与状态回填不进 AGENTS.md**——它是 steering 文件，高频改动会反复触发付费 agent evals，见 docs-governance skill §3 第 5 条）→ 更新 MOC 分区索引与「文档谱系」表对应行，backlog/README「当前候选」表移除已离开 backlog/ 的文档 → 跑 `pytest tests/unit/test_docs_links.py` 确认零坏链、MOC 无遗漏 → 更新 README 导航表。冻结时另检查绑定型分析是否随之冻结。

自动守卫：项目级 hook（`.factory/hooks.json` + `.factory/hooks/docs_guard.py`）在 Droid 会话里自动执行上述纪律——每轮开始给 docs/ 拍哈希快照，本轮结束时对比；有 docs/ 改动则跑 `test_docs_links.py` 做结构检查（坏链、MOC 漏登记），并按静态映射表提醒需核对的关联文档（frozen→AGENTS/README/MOC，specs→MOC/AGENTS，backlog→backlog/README/MOC，analysis/reference→MOC）。hook 只检测与回传，修复按 docs-governance skill 执行；规则本体只在 `test_docs_links.py` 一处。

## 文档谱系

一个主题一行，记录文档间的对应关系与流转方式；各文档的当前状态见下方分区索引。历史流转按实际发生记录，不按现行规则回迁。

| 主题 / 系列 | 起源 | 提案 | 规格 | 后续 | 流转方式 |
|-------------|------|------|------|------|----------|
| 主规格（A–I） | 项目初始规格 | — | [01_DEV_SPEC_frozen.md](frozen/01_DEV_SPEC_frozen.md) | [02_ACCEPTANCE_GAPS_frozen.md](frozen/02_ACCEPTANCE_GAPS_frozen.md)（验收缺口台账 G1–G3，随主规格冻结） | 跳过提案 |
| Sirchmunk 整合（S） | 《整合可行性分析》（无独立文件，就地升级为规格 v0.2） | — | [03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md](frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md) | [SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md](analysis/SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md)（以 S0–S4 收口状态为分析起点） | 单文件晋升 |
| 摄取健壮性（IR） | [FUTURE_OPTIMIZATION_ROADMAP.md](reference/FUTURE_OPTIMIZATION_ROADMAP.md) 6.2；v1.1 起源 6.8 | — | [04_INGESTION_ROBUSTNESS_SPEC_frozen.md](frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md) | — | 跳过提案 → 重开（v1.1 承接 OBSIDIAN M1/M2 为 IR7） |
| Obsidian Vault RAG（V） | — | [OBSIDIAN_RAG_SPEC_backlog.md](backlog/OBSIDIAN_RAG_SPEC_backlog.md) | M1/M2 → [04_INGESTION_ROBUSTNESS_SPEC_frozen.md](frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md)（IR7） | M3/M4 留在提案中待排期 | 拆分移交 |
| 检索模式路由器（MR） | 会话提案（2026-11-16，用户需求：检索模式选择交由本地部署的微调决策模型；非文件来源） | mode-router_backlog.md（单文件晋升，原文见 git 历史） | [15_MODE_ROUTER_SPEC_frozen.md](frozen/15_MODE_ROUTER_SPEC_frozen.md)（已冻结 2026-11-19，MR0.1–MR5 收口；v0.3 首期接入 Cloudflare Clef） | [mode-router-followups_backlog.md](backlog/mode-router-followups_backlog.md)（对比记表延后 + MR6/MR7 台账）；[USAGE_GUIDE.md](reference/USAGE_GUIDE.md) 第 11 章 | 单文件晋升 → 派生台账 |
| Sirchmunk 能力补齐（SX） | [SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md](analysis/SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md) v0.2 | [sirchmunk-capability-completion_backlog_frozen.md](frozen/sirchmunk-capability-completion_backlog_frozen.md) | [05_SIRCHMUNK_EXTENSION_SPEC_frozen.md](frozen/05_SIRCHMUNK_EXTENSION_SPEC_frozen.md) | [sirchmunk-extension-followups_backlog.md](backlog/sirchmunk-extension-followups_backlog.md)；起源分析更新至 v0.3；台账 1.1 / 2.2 经提案单文件晋升为 [13_SIRCHMUNK_KNOWLEDGE_EVOLUTION_SPEC_frozen.md](frozen/13_SIRCHMUNK_KNOWLEDGE_EVOLUTION_SPEC_frozen.md)（KE，已收口冻结） | 提案存档 → 派生台账 → 拆分移交 |
| Sirchmunk 知识演化与复用（KE） | [SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md](analysis/SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md) v0.3 + [sirchmunk-extension-followups_backlog.md](backlog/sirchmunk-extension-followups_backlog.md) 1.1 / 2.2 | sirchmunk-evolution-reuse_backlog.md（单文件晋升，原文见 git 历史） | [13_SIRCHMUNK_KNOWLEDGE_EVOLUTION_SPEC_frozen.md](frozen/13_SIRCHMUNK_KNOWLEDGE_EVOLUTION_SPEC_frozen.md)（已收口冻结；KE2 负结果记回台账） | [sirchmunk-extension-followups_backlog.md](backlog/sirchmunk-extension-followups_backlog.md)（KE2 负结果与收口记录） | 拆分移交 → 单文件晋升 |
| WebUI + Chat（WC） | [FUTURE_OPTIMIZATION_ROADMAP.md](reference/FUTURE_OPTIMIZATION_ROADMAP.md) 原第 1–5 章（拆出为提案）；[SIRCHMUNK_WEBUI_CHAT_REUSE_ANALYSIS.md](analysis/SIRCHMUNK_WEBUI_CHAT_REUSE_ANALYSIS.md) | [dashboard-api-migration_backlog_frozen.md](frozen/dashboard-api-migration_backlog_frozen.md) | [06_WEBUI_CHAT_SPEC_frozen.md](frozen/06_WEBUI_CHAT_SPEC_frozen.md)（D1 推翻提案的 Vue 选型；§1.2 勘误起源分析） | [WEBUI_PARITY_AUDIT.md](analysis/WEBUI_PARITY_AUDIT.md)（WC10.1 交付物）→ webui-parity-followups 台账（已晋升为下方 SR 规格） | 提案存档（多来源合并） → 派生台账 |
| 日志分层（LG） | 会话内 `logs/traces.jsonl` 体量审计（无独立文件） | — | [07_LOGGING_SPEC_frozen.md](frozen/07_LOGGING_SPEC_frozen.md) | 遗留见规格 2.2（未建台账） | 跳过提案 |
| 存储扩容 | [STORAGE_SCALING_ANALYSIS.md](analysis/STORAGE_SCALING_ANALYSIS.md) | — | — | — | 止于分析（阶段 0 保持文件型存储不动） |
| 微服务演进 | [MICROSERVICES_ANALYSIS.md](analysis/MICROSERVICES_ANALYSIS.md) | — | — | — | 止于分析（保持模块化单体） |
| MCP 协议层 | [MCP_PROTOCOL_LAYER_ANALYSIS.md](analysis/MCP_PROTOCOL_LAYER_ANALYSIS.md) | — | — | — | 止于分析（暂维持自研协议层，切换触发条件见其 §4） |
| AI-Native SDLC 流程升级（SD，含持续集成 CI） | Anthropic [The AI-native SDLC playbook](https://claude.com/resources/articles/the-ai-native-sdlc-playbook)（外部参照）→ 常驻分析 `AI_NATIVE_SDLC_UPGRADE_PLAN.md` v0.4（就地升级为规格，未入 git）；CI 部分起源 [01_DEV_SPEC_frozen.md](frozen/01_DEV_SPEC_frozen.md) §4.5、[FUTURE_OPTIMIZATION_ROADMAP.md](reference/FUTURE_OPTIMIZATION_ROADMAP.md) §6.7 | [ci-pipeline_backlog_frozen.md](frozen/ci-pipeline_backlog_frozen.md)（CI-1~CI-4 并入 SD1） | [AI_NATIVE_SDLC_SPEC.md](specs/AI_NATIVE_SDLC_SPEC.md) | [AI_NATIVE_SDLC_REFERENCE.md](reference/AI_NATIVE_SDLC_REFERENCE.md)（派生参考手册：流程图 + 工具 / 文档准备清单） | 单文件晋升（分析就地升级）；提案存档（ci-pipeline） |
| WebUI 双轨差异收口（WP） | [WEBUI_PARITY_AUDIT.md](analysis/WEBUI_PARITY_AUDIT.md)（WC10.1 缺口清单 L3/L4） | webui-parity-followups 台账（已晋升为 [frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md](frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md)，原文见 git 历史） | [08_WEBUI_PARITY_SPEC_frozen.md](frozen/08_WEBUI_PARITY_SPEC_frozen.md) | 台账剩余项二次移交 WS 规格（见下行） | 拆分移交 |
| WebUI sidecar 补齐（WS） | 同上台账（#4 sidecar 托管编排、#1 Deep 进度日志） | webui-parity-followups 台账（第二次拆分移交；已晋升为 [frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md](frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md)） | [09_WEBUI_SIDECAR_SPEC_frozen.md](frozen/09_WEBUI_SIDECAR_SPEC_frozen.md) | [sirchmunk-extension-followups_backlog.md](backlog/sirchmunk-extension-followups_backlog.md) #1（SX5.3 线程泄漏，门控默认关闭的依据） | 拆分移交 |
| Streamlit 退役（SR） | [WEBUI_PARITY_AUDIT.md](analysis/WEBUI_PARITY_AUDIT.md)（退役门槛清单）；webui-parity-followups 台账（#3 接受项决议并入其 Out of Scope） | [STREAMLIT_RETIREMENT_SPEC.md](frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md)（单文件晋升，原台账原文见 git 历史） | [10_STREAMLIT_RETIREMENT_SPEC_frozen.md](frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md)（已收口冻结） | — | 单文件晋升 |
| Chat 模式选择器着色（CMC） | 会话内用户截图（三按钮同质难辨认，无独立文件） | chat-mode-selector-colors_backlog.md（单文件晋升，原文见 git 历史） | [11_CHAT_MODE_COLORS_SPEC_frozen.md](frozen/11_CHAT_MODE_COLORS_SPEC_frozen.md)（已收口冻结） | — | 单文件晋升 |
| Agent evals 读纪律（ERD） | 会话内 evals 重校准诊断（2026-11-15，三次 run 数据见规格 §1.2；非文件来源） | evals-read-discipline_backlog.md（单文件晋升，原文见 git 历史） | [12_EVALS_READ_DISCIPLINE_SPEC_frozen.md](frozen/12_EVALS_READ_DISCIPLINE_SPEC_frozen.md)（已收口冻结） | [evals/baseline.json](../evals/baseline.json)（随重校准如实更新为 1/5；ERD2 后更新为 4/5）；遗留台账已单文件晋升为下行 EJR 规格 | 单文件晋升 → 派生台账 → 单文件晋升 |
| Agent evals 判据可靠性（EJR） | [12_EVALS_READ_DISCIPLINE_SPEC_frozen.md](frozen/12_EVALS_READ_DISCIPLINE_SPEC_frozen.md) 冻结遗留（ERD2 判据⑥误报实录、fixture `.gitignore` 缺行、文字与 hook 归因未分离） | evals-read-discipline-followups_backlog.md（单文件晋升，原文见 git 历史） | [17_EVALS_JUDGE_RELIABILITY_SPEC_frozen.md](frozen/17_EVALS_JUDGE_RELIABILITY_SPEC_frozen.md)（已收口冻结：EJR0 判据⑥收紧 / EJR1 fixture 卫生已交付并冒烟验证，EJR2 归因对照门控未启动） | — | 派生台账 → 单文件晋升 |
| Agentic 检索覆盖上传目录（AR） | 会话内配置边界核查（2026-11-18，非文件来源）；依据 [03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md](frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md) S1.3 | agentic-upload-roots_backlog.md（单文件晋升，原文见 git 历史） | [14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md](frozen/14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md)（已收口冻结，AR0–AR4；AR4 真实 sidecar 冒烟通过） | [18_AGENTIC_COLLECTION_ISOLATION_SPEC_frozen.md](frozen/18_AGENTIC_COLLECTION_ISOLATION_SPEC_frozen.md)（台账候选 #1 拆分移交 RA、候选 #2 单文件晋升为 AC 规格并已收口冻结，见下行；台账原文见 git 历史） | 单文件晋升 → 派生台账 → 拆分移交 → 单文件晋升 |
| 项目根锚定（RA） | `agentic-roots-followups_backlog.md` 候选 #1（原文见 git 历史）；会话内入口复现（2026-11-20，非文件来源） | `agentic-roots-followups_backlog.md`（#1 细节移入规格；台账其后整体晋升为 AC 规格，原文见 git 历史） | [ROOT_ANCHOR_SPEC.md](frozen/16_ROOT_ANCHOR_SPEC_frozen.md)（已收口冻结，2026-11-20，RA0–RA5） | — | 拆分移交 |
| Agentic 检索按 collection 隔离（AC） | [14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md](frozen/14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md) §2.2 Out of Scope 冻结遗留（台账候选 #2，原文见 git 历史） | agentic-roots-followups_backlog.md（单文件晋升，原文见 git 历史） | [18_AGENTIC_COLLECTION_ISOLATION_SPEC_frozen.md](frozen/18_AGENTIC_COLLECTION_ISOLATION_SPEC_frozen.md)（已收口冻结，2026-11-21，AC0–AC7；AC7 真实 sidecar 冒烟通过） | — | 派生台账 → 单文件晋升 |
| 检索 Trace 挖掘 | 会话讨论（2026-11-18，源于 [15_MODE_ROUTER_SPEC_frozen.md](frozen/15_MODE_ROUTER_SPEC_frozen.md) MR0.2 标注集扩充需求；非文件来源） | [retrieval-trace-mining_backlog.md](backlog/retrieval-trace-mining_backlog.md)（草案，未立项；已核实 trace 与会话库的数据现状与六个待研究问题） | — | — | — |
| PageIndex 树检索（PI） | 会话内调研（2026-11-21，上游 PageIndex `add6b17` 源码 + 本仓库 agentic_search / sidecar 架构核对；非文件来源）；依据 [03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md](frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md) 进程隔离红线 | pageindex-tree-retrieval_backlog.md（单文件晋升，原文见 git 历史） | [PAGEINDEX_TREE_RETRIEVAL_SPEC.md](specs/PAGEINDEX_TREE_RETRIEVAL_SPEC.md)（v0.1 立项、v0.2 按规格评审修订、v0.3 PI0.3 基线落盘、v0.4 方向变更为 MD 转 PDF 建树、v0.5 按源码与语料复核修订、v0.6 PI0.4a 三种 Outline 方案零 token 实测并终裁（v1 纯检测否决；默认 v3 `pypdf` 注入、回定位 ~95% 可接受；见规格 10.5）、v0.7 PI0.5 完成（0.5 口径修正重算落盘 10.6、原型调优、D7 复核维持自建、fake 冒烟通过）、v0.8 PI0.4b 完成（merge vs full 对比：结构一致、merge 胜出、单篇 1.1M token / 17 min / 回定位 88%；落盘 10.7），开发中；PI0 立项门终裁在 PI0.7） | — | 单文件晋升 |
## specs/ — 开发规格（落点）

**立项后新规格的存放处**（从 [backlog/](backlog/) 晋升）。任务全收口后冻结入 frozen/（SX 系列先例见 [frozen/05_SIRCHMUNK_EXTENSION_SPEC_frozen.md](frozen/05_SIRCHMUNK_EXTENSION_SPEC_frozen.md)）。

格式对齐 frozen/ 中 SIRCHMUNK / WEBUI_CHAT / LOGGING 先例：含模块设计、测试方案、任务排期与进度跟踪表，任务编号用新前缀避免冲突。

| 文档 | 说明 |
|------|------|
| [AI_NATIVE_SDLC_SPEC.md](specs/AI_NATIVE_SDLC_SPEC.md) | AI-Native SDLC 流程升级规格（SD 系列，v0.5 立项；SD1 CI 门（含 fork 上真实 run 与分支保护）、SD2 review 规则、SD6 skill 单一来源、SD3a QA 计划清理与 QA ↔ pytest 对照补测已交付，SD5 过程度量基线已出（[process-metrics.md](analysis/process-metrics.md)），SD7 规格结构校验已接入测试，SD4 定时闭环已交付（两个本机周度 automation active：followups 扫描起草草案 PR + QA 离线回归写回进度，SD4.4 阈值后置），SD3.4 evals 单次成本已测、预算已确认（D5 终裁为 BYOK `custom:glm-5.3-Flash-2`，SD3.5 evals 判据 / 运行器 / 5 条用例已交付（`evals/`），SD3.6 `agent-evals.yml` 已接入（CI 基线 2/5，门槛按基线校准），SD3.7 test-file 保护 hook 已交付（`.factory/hooks/test_guard.py`），D6 baseline PR 已合入；SD2.3 自动 review 已接入（2026-11-14，验收进行中））：前置决策 D1–D6 + baseline PR、CI 确定性门（干净环境 / 双 job / 禁网 / 红线脚本 / CODEOWNERS，承接 ci-pipeline 提案）、AI PR review、QA 对照与 agent evals（门控）、定时闭环（SD4.4 阈值后置）、过程度量、skill 单一来源 + plan + PR 化、规格结构校验（可选）；不改 `src/` 业务行为 |
| [PAGEINDEX_TREE_RETRIEVAL_SPEC.md](specs/PAGEINDEX_TREE_RETRIEVAL_SPEC.md) | PageIndex 树检索规格（PI 系列，v0.1 立项 2026-11-21、同日 v0.2 按规格评审修订，2026-11-22 v0.3 PI0.3 四路对照基线落盘、v0.4 方向变更、v0.5 按源码与语料复核修订、v0.6 PI0.4a 三种 Outline 方案实测并终裁（306 页派生 PDF、全链 ~10s 零 LLM；默认 v3 `pypdf` 注入 Bookmarks、回定位 ~95% 可接受）、v0.7 PI0.5 完成并重算（口径修正表落盘 10.6、TreeSearcher 调优、fake 冒烟通过）、v0.8 PI0.4b 完成（merge vs full 结构一致、merge 胜出、单篇 1.1M token；落盘 10.7），开发中，开发中）：独立 venv 本机 HTTP sidecar（`scripts/pageindex/`）接入「树索引 + LLM 沿树推理」，Web UI Chat 新增 `longdoc` 检索模式，引用落到章节 / 行号或页码；D1 本地模式、D2 Chat 新增模式、D3 MD 转派生 PDF 后与原生 PDF 同走上游 DocStore（v0.4 废弃 `md_to_tree`；D11 pandoc → HTML → Chromium + PDF Bookmarks/Outline 注入，优先系统 Chrome；D12 派生 PDF 存 `data/db/pageindex/derived/` 且证据回映射原 MD 路径、含合成节点与漂移回退），D5 对账同步不改摄取管线、D7 自建检索循环、D10 不进 auto 候选；PI0 四路对照为立项门（PI0.3 基线已出，v0.5 调整为 PI0.4a 零 token 试转 → PI0.5 零 token 修口径与原型 → PI0.4b 单篇建树 → PI0.6 复测 → PI0.7 D9 终裁），PI1–PI7 待门控通过后开工 |


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
| [11_CHAT_MODE_COLORS_SPEC_frozen.md](frozen/11_CHAT_MODE_COLORS_SPEC_frozen.md) | Chat 模式选择器按模式着色规格（CMC 系列，CMC1–CMC2 已收口冻结，2026-11-14）：Hybrid / Agentic (fast) / Agentic (deep) 选中态黄 / 绿 / 蓝，light 浅底深字、dark 半透明底亮字，实测对比度均 ≥ 6.3:1；只改 `ModeSelector.tsx` 与单测；由 chat-mode-selector-colors 提案单文件晋升，无遗留 |
| [12_EVALS_READ_DISCIPLINE_SPEC_frozen.md](frozen/12_EVALS_READ_DISCIPLINE_SPEC_frozen.md) | Agent evals 读纪律改进规格（ERD 系列，ERD0–ERD2 已收口冻结，2026-11-15；ERD3 换模型门控未触发）：项目级 hook `.factory/hooks/read_guard.py` 在 Edit / Create / ApplyPatch 前检查必读参考是否已 Read（映射只从 AGENTS.md / skill 规则推导，Script 模式经 transcript 补读）；判据①跳过被拒 / 出错的编辑调用；3 次付费 evals 判据①失败 0/27、通过用例 1/5 → 4/5（hook 一次未拒绝，改善来自 AGENTS.md 规则文字，hook 为兜底）；由 evals-read-discipline 提案单文件晋升，遗留台账已晋升为 [17_EVALS_JUDGE_RELIABILITY_SPEC_frozen.md](frozen/17_EVALS_JUDGE_RELIABILITY_SPEC_frozen.md)（EJR 系列，2026-11-21 冻结） |
| [13_SIRCHMUNK_KNOWLEDGE_EVOLUTION_SPEC_frozen.md](frozen/13_SIRCHMUNK_KNOWLEDGE_EVOLUTION_SPEC_frozen.md) | Sirchmunk 知识演化与知识簇复用规格（KE 系列，KE0–KE3 已收口冻结，2026-11-15；KE4 上游 PR 门控未触发）：KE0 检出钉回 `b314e11` + `corpus_snapshot.sh` 不可变语料快照 + 失效探针（P1/Q3，Streamlit 退役）改写；KE1 sidecar venv 内演化启动器 + `start_sidecar.sh` 开关（preflight 前置 / fail-fast / 精确红线白名单）+ 同步与 SSE 双端点门控验证（evolver 非空、Chat 副作用入档）；KE2 门控成对实验（675,979 token）：**演化未带来改写复用收益**（reword 复用命中 6/12 vs 只开复用 6/12，提升门未达成；Phase1 合并机制确认生效但未增加覆盖），复用与演化默认均保持关闭；由 sirchmunk-evolution-reuse 提案单文件晋升，遗留与负结果记于 [backlog/sirchmunk-extension-followups_backlog.md](backlog/sirchmunk-extension-followups_backlog.md) |
| [14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md](frozen/14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md) | Agentic 检索覆盖上传目录规格（AR 系列，AR0–AR4 已收口冻结，2026-11-18）：`allowed_roots` 默认追加 `./data/uploads`（D2 默认加入 + 外发提示）；进程入口预建 `data/` 下白名单根（D1-a `ensure_local_roots`，PathGuard 契约不变）；删除文档联动清理上传源文件（`source_file_removed`）；AR4 真实 sidecar 冒烟通过（双模式命中 → 删除后不再命中）；遗留已拆分移交 RA 并单文件晋升为 [18_AGENTIC_COLLECTION_ISOLATION_SPEC_frozen.md](frozen/18_AGENTIC_COLLECTION_ISOLATION_SPEC_frozen.md)（AC 系列） |
| [18_AGENTIC_COLLECTION_ISOLATION_SPEC_frozen.md](frozen/18_AGENTIC_COLLECTION_ISOLATION_SPEC_frozen.md) | Agentic 检索按 collection 隔离规格（AC 系列，已收口冻结 2026-11-21，AC0–AC7 全部交付）：collection → 摄取历史 v2 success 记录推导定界 `paths`（AC1 组件；D3 空集零证据 / D5 与 `paths` 互斥）；MCP `agentic_search` 可选 `collection`（AC2）；Chat / Playground 透传 + 前端全模式输入（AC3）；Hybrid 第三路恒发显式白名单 `paths` + 定界（AC4）；`POST /uploads` 同名 409 `upload_name_in_use`（AC5）；USAGE_GUIDE / README 回写（AC6）；真实 sidecar 冒烟通过（AC7，定界集 1 文件/collection，FAST 约 2.9–3.3s）：由 AR 遗留台账候选 #2 单文件晋升 |
| [15_MODE_ROUTER_SPEC_frozen.md](frozen/15_MODE_ROUTER_SPEC_frozen.md) | 检索模式路由器规格（MR 系列，MR0.1–MR5 已收口冻结，2026-11-19）：Web UI Chat 新增 `auto` 模式，决策模型经 System One API（Clef / Jev）或 OpenAI 兼容端点（本地模型）在改写后定档，候选由装配层按 `allow_deep` 注入，低置信度或失败回退 `fallback_retrieval`；MR0.2 立项门通过（`clef-flash` zh-v2 留出集 93.3%）、MR1 配置节 / MR2 组件 / MR3 引擎 auto 分支 / MR4 API schema + capabilities + Chat 前端（Auto 选项 violet 选中态）/ MR5 e2e + 门控冒烟（3/3 合法定档、不可达回退 trace 记 `unreachable`）；MCP 侧不做（D1）；由 mode-router 提案单文件晋升，遗留（对比记表延后、MR6 Jev、MR7 本地模型）见 [backlog/mode-router-followups_backlog.md](backlog/mode-router-followups_backlog.md) |
| [16_ROOT_ANCHOR_SPEC_frozen.md](frozen/16_ROOT_ANCHOR_SPEC_frozen.md) | 项目根锚定规格（RA 系列，RA0–RA5 已收口冻结，2026-11-20）：`resolve_project_root()`（`RAG_PROJECT_ROOT` 优先、空串视为未设置、否则代码位置推导）+ 相对数据路径 D2-b 全量按项目根解析（上传目录 / 向量库 / BM25 / 摄取历史 / 图片库 / `tasks_db` / `session_db` / `static_dir` / 日志回退）+ 相对 `allowed_roots` 锚定与预建基准 + MCP 默认配置路径锚定（任意 CWD 读得到仓库配置）；文档撤销「须从仓库根启动」；RA5 真实 sidecar 仓库外启动冒烟通过（上传 → Agentic (fast) 命中 → 删除后不再命中，启动目录无 `data/`）；PathGuard 契约与 sidecar 不变；由 AR 台账候选 #1 拆分移交，无新遗留 |
| [17_EVALS_JUDGE_RELIABILITY_SPEC_frozen.md](frozen/17_EVALS_JUDGE_RELIABILITY_SPEC_frozen.md) | Agent evals 判据可靠性改进规格（EJR 系列，EJR0–EJR1 已收口冻结，2026-11-21；EJR2 文字 vs hook 归因对照为门控，启动条件未触发）：EJR0 按 ERD2 三次 run 的 9 条逐字实样把 qatester 判据⑥的 `K-02.*✅` 收紧为两条状态列锚定模式 + 离线实样单测（误报行不命中、各列序 ✅ 行命中）；EJR1 `prepare_fixture` 在拍基线前幂等追加 `/.factory/state/`（字节级 / CRLF 感知 `ensure_state_ignored`）；付费冒烟 run 44750104692：qatester 3/3 通过、判据⑥ 0 失败、无回归；由 ERD 遗留台账单文件晋升；判据⑥模式口径与 fixture 卫生对改 evals 用例 / 运行器者构成必读契约 |
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
| [MCP_PROTOCOL_LAYER_ANALYSIS.md](analysis/MCP_PROTOCOL_LAYER_ANALYSIS.md) | MCP 协议层与官方 Python SDK 对比分析（2026-11-16）：自研协议层实现盘点（tools 三方法 + stdio，协议版本 `2025-06-18`）、MCP 无 2.0 版本号（按日期编号）的版本线对照、自研 vs 官方 SDK 优劣对比（零依赖/可控/好测 vs 功能窄/仅 stdio/手动跟版本）、切换判断（当前不切）与三个触发条件（HTTP 远程传输 / 协议级长任务特性 / 客户端强制新版本） |
| [SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md](analysis/SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md) | Sirchmunk 能力覆盖差距分析：已接入与未接入能力对照、对 G0 结论的影响（v0.3：按 SX0–SX5 收口结果更新现状——telemetry/参数透传/SSE 已接入，编译与复用已接入但实验裁决不推荐默认开；2026-11-15 文件头勘误：演化经现有 HTTP/CLI 默认不可达，SDK 启动器已晋升为 KE 开发规格（尚未交付）；剩余缺口指向 followups 台账） |
| [WEBUI_PARITY_AUDIT.md](analysis/WEBUI_PARITY_AUDIT.md) | WebUI 双轨功能等价核验（WC10.1）：Streamlit 七页 ↔ Next 页面 ↔ API 路由逐项矩阵、缺口清单与严重度、Streamlit 退役待决事项 |
| [SIRCHMUNK_WEBUI_CHAT_REUSE_ANALYSIS.md](analysis/SIRCHMUNK_WEBUI_CHAT_REUSE_ANALYSIS.md) | Sirchmunk WebUI/Chat 复用分析：Chat 实现流程盘点、可复用资产清单（多轮三步法、消息协议、FastAPI 骨架、前端交互）、不抄清单（假流式等）、前端技术栈与传输通道两个待拍板决策 |

## backlog/ — 候选功能与遗留台账

已有初步想法、但尚未立项排期的功能提案与遗留事项台账。约定见 [backlog/README.md](backlog/README.md)。

| 文档 | 说明 |
|------|------|
| [OBSIDIAN_RAG_SPEC_backlog.md](backlog/OBSIDIAN_RAG_SPEC_backlog.md) | Obsidian Vault RAG 扩展规格（V 系列；v0.2 起 M1/M2 Markdown 摄取已移交 IR 规格 IR7，本规格保留 M3 sentence-transformers / M4 Semantic Walk 待排期） |
| [sirchmunk-extension-followups_backlog.md](backlog/sirchmunk-extension-followups_backlog.md) | Sirchmunk SX 规格遗留事项台账：明确不做（llm_fallback 等 5 项；evolver 已移交）、实验负结果存档（编译 GX 不通过；复用已移交）、已知缺陷与候选池（流式线程泄漏等 7 项） |
| [retrieval-trace-mining_backlog.md](backlog/retrieval-trace-mining_backlog.md) | 检索 Trace 挖掘管线提案（草案，未立项）：从 `logs/traces/` 与 `chat_sessions.db` 提取「问题 → 回答 → 所选检索模式」作 MODE_ROUTER 标注集扩充 / 持续校准数据源；已核实 trace 与会话库各自的数据现状，六个待研究问题（手选 vs 默认区分、弱标注口径、跨库 join、隐私、模式命名映射、时机依赖 MR0.2 门槛） |
| [mode-router-followups_backlog.md](backlog/mode-router-followups_backlog.md) | 检索模式路由器遗留事项台账（MR 规格冻结遗留）：#1 同一问题集合 auto 与人工三选一的质量 / 延迟 / 成本对比记表（用户决议延后）；#2 接入 Jev（`typesafe` 后端，原 MR6，用户决议不接入，2026-11-19，仅留档）；#3 接入本地微调模型（`openai_compatible` 后端，原 MR7，待端点） |

## reference/ — 使用参考

面向使用者的速查手册与路线图。

| 文档 | 说明 |
|------|------|
| [USAGE_GUIDE.md](reference/USAGE_GUIDE.md) | 项目使用指南：主项目用法、Sirchmunk sidecar 搭建与日常启动、常见问题排查、安全边界；第 10 章 Web UI 与 Chat（启动、`RAG_API_TOKEN`、`api:`/`chat:` 配置、WS 帧协议、REST 路由、e2e、Streamlit legacy 删除条件）；第 11 章 Auto 检索模式（mode_router 开启步骤、回退语义、AI Gateway `base_url` 覆盖、门控冒烟，承接 [15_MODE_ROUTER_SPEC_frozen.md](frozen/15_MODE_ROUTER_SPEC_frozen.md) MR5） |
| [AI_NATIVE_SDLC_REFERENCE.md](reference/AI_NATIVE_SDLC_REFERENCE.md) | AI-Native SDLC 参考手册：门禁架构 / 任务开发循环 / 规格生命周期三张 mermaid 流程图、落地前需准备的工具清单与文档清单、落地顺序与门控验收要点速查；设计细节与落地记录见 [AI_NATIVE_SDLC_SPEC.md](specs/AI_NATIVE_SDLC_SPEC.md) |
| [FUTURE_OPTIMIZATION_ROADMAP.md](reference/FUTURE_OPTIMIZATION_ROADMAP.md) | 后续优化路线图：候选优化方向与决策纪律（Dashboard 前后端分离迁移已拆分至 [frozen/dashboard-api-migration_backlog_frozen.md](frozen/dashboard-api-migration_backlog_frozen.md)，见其 6.10；含第 10 章决策原则） |
