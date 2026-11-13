# Modular RAG MCP Server

> 一个可插拔、可观测的模块化 RAG（检索增强生成）服务框架，通过 MCP（Model Context Protocol）协议对外暴露工具接口，支持 Copilot / Claude 等 AI 助手直接调用。同时也是一份专为**大模型相关岗位学习与面试求职**设计的实战项目与配套教学资源。

---

## 📖 目录

- [项目概述](#-项目概述)
- [分支说明](#-分支说明)
- [快速开始](#-快速开始)
- [运行手册（10 分钟跑通全链路）](#️-运行手册10-分钟跑通全链路)
- [谁适合用这个项目 & 怎么用](#-谁适合用这个项目--怎么用)
- [简历参考](#-简历参考)
- [常见问题](#-常见问题)
- [后续安排](#-后续安排)

### 📚 开发文档（docs/）

| 目录 | 内容 |
|------|------|
| [docs/frozen/](docs/frozen/) | 已冻结文档：[01_DEV_SPEC_frozen.md](docs/frozen/01_DEV_SPEC_frozen.md)（主规格，架构参考）、[03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md](docs/frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md)（S 系列）、[05_SIRCHMUNK_EXTENSION_SPEC_frozen.md](docs/frozen/05_SIRCHMUNK_EXTENSION_SPEC_frozen.md)（SX 系列，Sirchmunk 能力补齐，已收口）、[04_INGESTION_ROBUSTNESS_SPEC_frozen.md](docs/frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md)（IR 系列，IR0–IR7 已交付收口：摄取健壮性 + Markdown 摄取）、[06_WEBUI_CHAT_SPEC_frozen.md](docs/frozen/06_WEBUI_CHAT_SPEC_frozen.md)（WC 系列，WebUI + Chat，已收口）、[07_LOGGING_SPEC_frozen.md](docs/frozen/07_LOGGING_SPEC_frozen.md)（LG 系列，日志分层与按日切分，已收口）、[08_WEBUI_PARITY_SPEC_frozen.md](docs/frozen/08_WEBUI_PARITY_SPEC_frozen.md)（WP 系列，WebUI 双轨遗留差异收口，已收口）、[09_WEBUI_SIDECAR_SPEC_frozen.md](docs/frozen/09_WEBUI_SIDECAR_SPEC_frozen.md)（WS 系列，sidecar 托管编排 + Playground Deep 进度日志，已收口）、[10_STREAMLIT_RETIREMENT_SPEC_frozen.md](docs/frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md)（SR 系列，Streamlit 退役，已收口）、[02_ACCEPTANCE_GAPS_frozen.md](docs/frozen/02_ACCEPTANCE_GAPS_frozen.md) |
| [docs/specs/](docs/specs/) | 开发中规格：[AI_NATIVE_SDLC_SPEC.md](docs/specs/AI_NATIVE_SDLC_SPEC.md)（SD 系列，AI-Native SDLC 流程升级：CI 门、AI review、evals、度量、skill 单一来源；承接 ci-pipeline 提案，进行中：SD1 CI 门与分支保护、SD2 review 规则、SD3a QA 对照补测、SD4 定时闭环（两个本机周度 automation）、SD5 过程度量、SD6 skill 单一来源、SD7 规格结构校验已交付；SD3.4 evals 单次成本已测、预算已确认；SD3.5 evals 判据与用例已交付） |
| [docs/analysis/](docs/analysis/) | 分析与规划：[MICROSERVICES_ANALYSIS.md](docs/analysis/MICROSERVICES_ANALYSIS.md)、[SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md](docs/analysis/SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md)、[SIRCHMUNK_WEBUI_CHAT_REUSE_ANALYSIS.md](docs/analysis/SIRCHMUNK_WEBUI_CHAT_REUSE_ANALYSIS.md)（WebUI/Chat 复用分析）、[WEBUI_PARITY_AUDIT.md](docs/analysis/WEBUI_PARITY_AUDIT.md)（Streamlit ↔ Web UI 七页双轨等价核验）、[STORAGE_SCALING_ANALYSIS.md](docs/analysis/STORAGE_SCALING_ANALYSIS.md)、[process-metrics.md](docs/analysis/process-metrics.md)（过程度量基线） |
| [docs/backlog/](docs/backlog/) | 候选功能与待开发规格（文件命名固定带 `_backlog` 后缀）：[OBSIDIAN_RAG_SPEC_backlog.md](docs/backlog/OBSIDIAN_RAG_SPEC_backlog.md)、[sirchmunk-extension-followups_backlog.md](docs/backlog/sirchmunk-extension-followups_backlog.md)（SX 规格遗留事项台账）等（webui-parity-followups 台账已晋升为 [10_STREAMLIT_RETIREMENT_SPEC_frozen.md](docs/frozen/10_STREAMLIT_RETIREMENT_SPEC_frozen.md)；ci-pipeline 提案已并入 [AI_NATIVE_SDLC_SPEC.md](docs/specs/AI_NATIVE_SDLC_SPEC.md) 并存档） |
| [docs/reference/](docs/reference/) | 使用参考：[USAGE_GUIDE.md](docs/reference/USAGE_GUIDE.md)（速查手册）、[FUTURE_OPTIMIZATION_ROADMAP.md](docs/reference/FUTURE_OPTIMIZATION_ROADMAP.md)（优化路线图） |

---

## 🏗️ 项目概述

### 这个项目是什么

本项目将 RAG 面试中最常见的核心环节——**检索（Hybrid Search + Rerank）**、**多模态视觉处理（Image Captioning）**、**RAG 评估（Ragas + Custom）**、**生成（LLM Response）**——以及当下热门的应用协议 **MCP（Model Context Protocol）** 串联为一个完整的、可运行的工程项目。

**项目的一大亮点是极易适配到你自己的业务中**。得益于全链路可插拔架构，你可以快速将它结合到自己已有的项目里，无论你的背景和需求如何，都能找到适合自己的使用方式。具体的使用策略会在后文 [谁适合用这个项目 & 怎么用](#-谁适合用这个项目--怎么用) 中详细展开。

### 不只是项目，更是一整套思路

**比这个项目本身更有价值的，是它背后蕴含的一整套工程化思路**：

- 如何编写 **DEV_SPEC**（开发规格文档）来驱动开发
- 如何用 **Skill** 基于 Spec 自动完成代码编写
- 如何用 **Skill** 进行自动化测试、打包、环境配置
- 如何基于可插拔架构进行扩展（比如扩展到 Agent）

**学会了思路，你可以自己做全新的项目和扩展**。以上每一步的具体做法、设计思路，在笔记中都有对应的视频讲解，建议配合观看。

### 核心能力一览

| 模块 | 能力 | 说明 |
|------|------|------|
| **Ingestion Pipeline** | PDF → Markdown → Chunk → Transform → Embedding → Upsert | 全链路数据摄取，支持多模态图片描述（Image Captioning） |
| **Hybrid Search** | Dense (向量) + Sparse (BM25) + RRF Fusion + Rerank | 粗排召回 + 精排重排的两段式检索架构 |
| **MCP Server** | 标准 MCP 协议暴露 Tools | `query_knowledge_hub`、`list_collections`、`get_document_summary` |
| **Web UI** | FastAPI + Next 静态导出，单端口托管（主界面） | Chat（多轮对话 + 有据回答）+ 七个管理页：Overview / Data Browser / Ingestion Manager / Ingestion Traces / Query Traces / Evaluation / Query Playground |
| **Evaluation** | Ragas + Custom 评估体系 | 支持 golden test set 回归测试，拒绝"凭感觉"调优 |
| **Observability** | 全链路白盒化追踪 | Ingestion 与 Query 两条链路的每一个中间状态透明可见 |
| **Skill 驱动全流程** | 从编写到测试、打包、配置一键完成 | auto-coder / qa-tester / package / setup 等 Skill 覆盖完整开发生命周期（笔记中每个 Skill 的使用和设计思路均有讲解，请参考配套视频） |

### 技术亮点

**🔌 全链路可插拔架构**：LLM / Embedding / Reranker / Splitter / VectorStore / Evaluator 每一个核心环节均定义了抽象接口，支持"乐高积木式"替换，通过配置文件一键切换后端，零代码修改。

**🔍 混合检索 + 重排**：BM25 稀疏检索解决专有名词精确匹配 + Dense Embedding 解决同义词语义匹配，RRF 融合后可选 Cross-Encoder / LLM Rerank 精排，平衡查全率与查准率。

**🖼️ 多模态图像处理**：采用 Image-to-Text 策略，利用 Vision LLM 自动生成图片描述并缝合进 Chunk，复用纯文本 RAG 链路即可实现"搜文字出图"。

**📡 MCP 生态集成**：遵循 Model Context Protocol 标准，可直接对接 GitHub Copilot、Claude Desktop 等 MCP Client，零前端开发，一次开发处处可用。

**📊 可视化管理 + 自动化评估**：Web UI（FastAPI + Next）提供完整的数据管理、链路追踪与 Chat 能力，集成 Ragas 等评估框架，建立基于数据的迭代反馈回路。

**🧪 三层测试体系**：Unit / Integration / E2E 分层测试，覆盖独立模块逻辑、模块间交互、完整链路（MCP Client / Web UI）。

**🤖 Skill 驱动全流程**：内置 auto-coder（自动编码）、qa-tester（自动测试）、package（清理打包）、setup（一键配置）等 Agent Skill，覆盖从代码编写到测试、打包、部署的完整开发生命周期。每个 Skill 的使用方法和设计思路在笔记的项目部分均有讲解视频，可参考学习。

> 📖 详细架构设计、模块说明和任务排期请参阅 [docs/frozen/01_DEV_SPEC_frozen.md](docs/frozen/01_DEV_SPEC_frozen.md)（全部开发文档索引见 [docs/MOC.md](docs/MOC.md)）

---

## 📂 分支说明

本项目提供三个分支，面向不同使用场景，请根据自身需求选择：

### `main` — 最干净的完整代码

- 始终只有 **1 个 commit**，包含项目的最新完整代码
- **适合人群**：
  - 想要快速体验项目完整功能的同学
  - 时间紧迫，想要快速拿到一个项目去面试、跳过中间开发过程的同学
  - 想要直接在该项目基础上做二次扩展的同学
- **使用方式**：克隆后直接运行 Setup Skill 即可体验

### `dev` — 保留完整开发记录

- 代码与 `main` 完全一致，但保留了完整的 commit 历史
- 记录了从零开始逐步构建的每一步过程，包含大量中间节点
- **适合人群**：想了解项目是如何一步步从零搭建起来的同学，可以通过 commit 历史回溯开发思路

### `clean-start` — 干净起点，从零开始

- 仅包含工程骨架（Agent Skills + DEV_SPEC），所有任务进度清零
- 保留了完整的 Skill 配置，可以使用 Agent 辅助开发
- **适合人群**：
  - 时间充分、想要从头开发的同学（**强烈建议**）
  - 想要体验完整工作流的同学：写 Spec → 拆任务 → 写代码 → 写测试 → 迭代优化
  - 甚至可以基于自己的理解重新设计架构，用自己的思路实现，深度理解每一个模块
  - 使用我们讲的所有对应思路（Spec 驱动开发、测试先行、可插拔架构等）来完成整个项目
- **核心理念**：整个项目的代码编写是 **让 AI 基于 DEV_SPEC 来自动完成的**，你自己不需要手写代码。AI 通过 Skill 读取 Spec 中的任务定义、架构设计和接口规范，自动生成符合规格的代码。这个思路请参考笔记对应视频讲解：**5.1 项目 Skills 使用：如何让 AI 使用 Skill 遵循 DEV_SPEC 完成代码**。

---

## 🚀 快速开始

### 1. 克隆项目

```bash
git clone <repo-url>
cd Modular-RAG-MCP-Server
```

### 2. 一键配置（Setup Skill）

本项目提供了 **Setup Skill** 一键完成所有环境配置，包括：Provider 选择 → API Key 配置 → 依赖安装 → 配置文件生成 → Dashboard 启动。

在 VS Code 中打开项目，通过 Copilot / Claude 对话框输入：

```
setup
```

Agent 会自动引导你完成全部配置流程。

> 💡 如果不熟悉 Skill 的使用方式，请观看配套笔记中的 **Setup Skill 使用讲解视频**。

---

## 🛠️ 运行手册（10 分钟跑通全链路）

> 本节面向想手动跑通 **ingest → query → dashboard → evaluate** 全流程的用户。以下所有命令均在仓库根目录执行，需先激活虚拟环境：
>
> ```bash
> # macOS / Linux
> source .venv/bin/activate
> # Windows (PowerShell)
> .\.venv\Scripts\Activate.ps1
> ```

### 1. 安装依赖

```bash
pip install -r requirements.txt
# 或
pip install -e .
```

### 2. 配置 API Key

编辑 `config/settings.yaml`（或使用环境变量），至少配置：

- `embedding`: provider（openai / azure / ollama）+ `api_key` + `model`
- `llm`: provider + `api_key`（仅在使用 LLM 增强 / LLM Rerank / Ragas 时必需）

```yaml
embedding:
  provider: openai
  model: text-embedding-3-small
  api_key: ${OPENAI_API_KEY}   # 支持环境变量占位符
```

> 想完全离线运行？把 `embedding.provider` 换成 `ollama` 并本地部署 `nomic-embed-text` 即可。

### 3. 首次摄取

把文档放入 `data/documents/`（或任意目录），然后：

```bash
python scripts/ingest.py --path data/documents/ --collection default
# 强制重摄取（跳过增量判定）
python scripts/ingest.py --path data/documents/ --collection default --force
# 查看逐阶段进度
python scripts/ingest.py --path data/documents/ -v
```

**支持格式**：`.pdf` 开箱即用；`.md` / `.markdown` 走 MarkdownLoader（Obsidian 语法感知：frontmatter / tags / wikilinks / 笔记嵌入，图片引用落盘图片索引）；`.docx` / `.pptx` / `.xlsx` / `.html` 走 markitdown，需额外安装可选依赖 `pip install '.[ingest-formats]'`（未安装时这些文件在 load 阶段失败并给出安装提示）。摄取 Obsidian 库时，`.obsidian/`、`.trash/`、`templates/` 目录默认被排除（`ingestion.source_filters` 可定制，见 [04_INGESTION_ROBUSTNESS_SPEC_frozen.md](docs/frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md) 3.8，承接自 [OBSIDIAN_RAG_SPEC_backlog](docs/backlog/OBSIDIAN_RAG_SPEC_backlog.md) M1/M2）。

**增量与版本替换**：按 `(source_path, collection)` 判定，同一文件可分别摄取到不同 collection；文件内容变更后重摄取会先清理旧版本的 chunk / BM25 / 图片，不留残留。

**同步与重试模式**（`--sync` / `--prune` / `--retry-failed` 三者互斥）：

```bash
# 目录与存储对比：摄取新增/修改文件，清理磁盘上已删除的文档
python scripts/ingest.py --path data/documents/ --collection default --sync
# 只清理已删除的文档，不摄取
python scripts/ingest.py --path data/documents/ --collection default --prune
# 只打印计划（new / modified / unchanged / deleted / stale），不写任何存储；必须配合 --sync 或 --prune
python scripts/ingest.py --path data/documents/ --collection default --sync --dry-run
# 重跑该集合历史中失败的文件（--path 可省略，给出时限定范围）
python scripts/ingest.py --collection default --retry-failed
```

`--force` 不能与 `--sync` / `--prune` 同用。退出码：`0` 全部成功或跳过；`1` 有文件失败；`2` 用法或配置错误。

摄取产物：Chroma 向量库（`data/db/chroma/`）、BM25 索引（`data/db/bm25/`）、图片索引（`data/db/image_index.db`）、摄取历史（`data/db/ingestion_history.db`，表 `ingestion_history_v2`；首次打开旧库时自动迁移，旧表改名为 `ingestion_history_legacy_backup` 保留）。设计细节见 [04_INGESTION_ROBUSTNESS_SPEC_frozen.md](docs/frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md)。

### 4. 查询验证

```bash
python scripts/query.py --query "如何配置 Azure？" --verbose
python scripts/query.py --query "bm25 scoring" --collection default --top-k 5
python scripts/query.py --query "部署密钥" --no-rerank   # 跳过精排
```

### 5. 启动 MCP Server（stdio）

```bash
PYTHONPATH=src python -m mcp_server.server
```

Server 通过 stdin/stdout 收发 JSON-RPC（MCP 2025-06-18），暴露三个 tools：`query_knowledge_hub`、`list_collections`、`get_document_summary`。开启 `agentic_search.enabled` 后会多出第四个工具 `agentic_search`（见下一节）。

### 5.1 Agentic 检索（sidecar，可选）

`agentic_search` 工具把查询转发给独立进程中的 [sirchmunk](https://github.com/modelscope/sirchmunk)（无索引 Agentic 检索：rg/rga 词法召回 + LLM 迭代探索），与 Dense + BM25 主链路互补，不替换它。sirchmunk 装在独立 venv 里，主项目 venv 永远不 import 它，只通过 HTTP 调用 `POST /api/v1/search`。完整设计与注意事项见 [`docs/frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md`](docs/frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md)。

**启动 sidecar**

```bash
brew install ripgrep ripgrep-all                     # 必须预装 rg / rga（否则 sirchmunk 会自行从 GitHub 下载）
bash scripts/sirchmunk/setup_venv.sh                 # 独立 venv + 锁定上游 commit b314e11
cp scripts/sirchmunk/env.example .env-sirchmunk      # 填入 LLM_API_KEY / SIRCHMUNK_API_TOKEN 等
bash scripts/sirchmunk/start_sidecar.sh              # 绑定 127.0.0.1:8584 + Bearer 鉴权
bash scripts/sirchmunk/preflight.sh                  # 健康检查：rg / rga / /api/v1/search/status
```

sirchmunk 只支持 OpenAI 兼容的 LLM 端点（无原生 Azure）。

**主项目配置**（`config/settings.yaml`）

```yaml
agentic_search:
  enabled: true
  base_url: http://127.0.0.1:8584
  api_token: ${SIRCHMUNK_API_TOKEN}   # 与 sidecar 使用同一 token
  mode: FAST                          # DEEP 单次可达数十秒，需同时设置 allow_deep: true
  allowed_roots: [./data/documents]   # enabled 时必须非空
  denied_substrings: [.env, .git, data/db]
```

**工具参数**：`query`（必填）、`mode`（FAST / DEEP）、`paths`（可选，缺省为 `allowed_roots`）、`top_k`。返回带出处的答案与证据片段；证据不含页码（`page` 恒为空）。

**安全边界**：sirchmunk 的 `paths` 参数本身不是安全边界（其 ReAct `file_read` 可读任意绝对路径）。本项目用 PathGuard 做两层防护：调用前校验 `paths`，越出白名单或命中 `denied_substrings` 直接返回 `-32602`；调用后丢弃白名单外的证据。sidecar 只应监听 127.0.0.1，并始终开启 Bearer 鉴权。sidecar 不可达时工具返回 `isError: true` 的友好提示，不影响其他工具。

**旁路对比**：`python scripts/sirchmunk/compare.py --base-url http://127.0.0.1:8584 --token "$SIRCHMUNK_API_TOKEN" --repo-root .` 用 `tests/fixtures/agentic/golden_questions.json` 逐条调用 sidecar，输出命中文件、耗时与 Token 用量表（真实 LLM 调用，产生费用）。

**Agentic 第三路（HybridSearch 可选路由，S2，默认关闭）**：除独立 MCP 工具外，agentic 检索还可作为 HybridSearch 的可选第三路（Dense + BM25 + RRF + Rerank 主链路之外按条件触发）。`agentic_search.enabled` 默认为 `false`，关闭或未触发时与基线行为完全一致。关键配置（`config/settings.yaml` → `agentic_search` 节）：

- `trigger`：第三路触发策略——`tool_only`（默认，永不自动触发，只由 MCP 工具显式调用）/ `fallback`（主路信号不足时触发）/ `always`（仅实验/评估）；
- `trigger_score_threshold` / `trigger_min_results`：fallback 判定阈值（0.5 为未校准占位值，须在独立校准集上标定，禁止用评估 golden set 调参）；
- `mode` / `allow_deep` / `timeout_s` / `allowed_roots` / `denied_substrings`：与 MCP 工具共用，PathGuard fail-closed 同样适用（越权路径保留原 snippet 不抛错）。

CLI 用法：

```bash
# 查询时启用第三路并打印中间结果（触发状态/证据文件/Token/耗时）；
# enabled=false 时退出码 2 并提示改 config/settings.yaml
python scripts/query.py --query "如何配置 Azure？" --agentic --verbose

# 评估 A/B：hybrid（基线）/ agentic（纯第三路）/ hybrid+agentic（第三路注入 HybridSearch）
# source 级 hit/MRR（基于 expected_sources）与 chunk 级指标并列不混算，报告含延迟/Token 列
python scripts/evaluate.py --retrieval-mode hybrid+agentic --agentic-trigger always
```

**Dashboard 展示**：Query 追踪页基于 trace 的 `agentic_search` 阶段动态渲染（模式/Token/调用次数/证据文件列表），未启用时自然隐藏；系统总览页配置卡显示 agentic 开关与 backend。

**在线检索页（S4）**：Dashboard 导航「检索」分组 → 「在线检索」提供三模式在线查询，不写代码、不敲 CLI 也能检索。Normal / Fast / Deep 三个模式按钮默认选中 Normal；门控严格遵守 settings——Fast 需 `agentic_search.enabled: true`（否则置灰并悬停提示开启方法与 sidecar 启动），Deep 另需 `allow_deep: true`；collection 输入仅 Normal 模式显示，Deep 检索时提示「可能需要数十秒」。结果区共有 Markdown 答案与引用表（来源/页码/分数），Fast/Deep 额外展示 Agentic 答案、模式、Token、LLM 调用次数、耗时与证据文件；sidecar 不可用或参数非法以页面错误提示友好展示。每次检索自动写入 trace，可到「Query 追踪」页查看各阶段耗时。

**安全边界与默认关闭**：第三路复用 PathGuard fail-closed 防护，且 `enabled: false` 为默认口径——G0 终裁结论是本仓库自指语料上裸 rg 6/6 严格优于 sidecar，故 agentic 路默认不参与检索；是否启用由真实语料场景决定。真实 sidecar 的相关测试永不自动运行，须显式 `RAG_AGENTIC_INTEGRATION=1` 门控（见第 8 节）。

### 6. 启动 Web UI（主界面）

```bash
(cd frontend && npm ci && npm run build)   # 首次或前端有改动时（Node 22）
python scripts/start_api.py --serve-ui     # http://127.0.0.1:8600
```

- `api.enabled` 默认 `false`：在 `config/settings.yaml` 设 `api.enabled: true`，或命令行追加 `--force`。
- 首页是 **Chat**（多轮对话 + RAG 有据回答，检索模式 hybrid / agentic_fast / agentic_deep），另有七个管理页：

| 页面 | 功能 |
|------|------|
| Query Playground | 三模式在线查询：Normal / Fast / Deep（agentic，默认关闭）、引用与 agentic 指标（Deep 无实时进度日志） |
| Overview | 组件配置卡片 + 数据资产统计 + 最近 trace 健康状态 |
| Data Browser | 文档/Chunk/图片浏览（集合筛选、关键词搜索、分页、带确认的删除） |
| Ingestion Manager | 上传文件 → 后台摄取任务（实时进度，刷新可恢复）、删除文档 |
| Ingestion Traces | 摄取历史、阶段耗时瀑布图 |
| Query Traces | 查询历史、来源筛选（hybrid / chat / agentic）、Dense/Sparse 对比、Rerank 前后变化、Chat 阶段 |
| Evaluation | 选择后端运行评估（后台任务）、指标卡片、持久化历史对比 |

- 非 loopback 访问须设环境变量 `RAG_API_TOKEN`（独立于 `SIRCHMUNK_API_TOKEN`）；前端开发模式、`api:`/`chat:` 配置、WS 帧协议、REST 路由索引、Playwright e2e 命令见 [docs/reference/USAGE_GUIDE.md 第 10 章](docs/reference/USAGE_GUIDE.md#10-web-ui-与-chat)。
- `start_api.py` 默认不托管 sidecar：用 agentic 检索时先手动 `bash scripts/sirchmunk/start_sidecar.sh`，或加 `--manage-sidecar` 由启动器自动托管（拉起 / 等健康 / 退出回收）。

### 7. 运行评估

```bash
python scripts/evaluate.py                                   # 默认 golden set + composite 后端
python scripts/evaluate.py --evaluator custom --top-k 5      # 纯离线指标
python scripts/evaluate.py --test-set tests/fixtures/golden_test_set.json --collection default
python scripts/evaluate.py --retrieval-mode hybrid+agentic   # A/B：agentic 第三路（见 5.1，默认关闭）
```

### 8. 运行测试

```bash
pytest -q                          # 全量（默认全绿、离线、零网络）
pytest tests/unit -q               # 单元测试
pytest tests/integration -q        # 集成测试
pytest tests/e2e -q                # E2E（MCP client 模拟 / Dashboard 冒烟 / Recall 回归）
pytest tests/e2e_web -q            # Web UI 假后端启动冒烟（需先构建 frontend/out，否则 skip）
(cd frontend && npm run typecheck && npm run lint && npm test)   # 前端静态检查与单测
(cd frontend && npx playwright install chromium && npm run build && npm run e2e)   # 浏览器 e2e（opt-in，离线假后端）
RAG_LLM_INTEGRATION=1 pytest tests/integration/test_chunk_refiner_llm.py -v   # 真实 LLM（可选，产生费用）
RAG_AGENTIC_INTEGRATION=1 pytest tests/e2e/test_agentic_mcp.py -v               # 真实 sirchmunk sidecar 冒烟（需先启动 sidecar）
```

### 9. MCP Client 配置示例

**GitHub Copilot（VS Code）** — `.vscode/mcp.json`：

```json
{
  "servers": {
    "modular-rag": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/Modular-RAG-MCP-Server",
      "env": { "PYTHONPATH": "/absolute/path/to/Modular-RAG-MCP-Server/src" }
    }
  }
}
```

**Claude Desktop** — `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "modular-rag": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/Modular-RAG-MCP-Server",
      "env": { "PYTHONPATH": "/absolute/path/to/Modular-RAG-MCP-Server/src" }
    }
  }
}
```

配置完成后，在 Copilot / Claude 中即可直接调用 `query_knowledge_hub` 检索私有知识库。

### 配置说明（settings.yaml 关键字段）

| 字段 | 含义 |
|------|------|
| `llm.provider / model / api_key` | LLM 后端（azure / openai / deepseek / ollama） |
| `embedding.provider / model` | Embedding 后端（openai / azure / ollama） |
| `vision_llm.*` | 图片描述用的 Vision LLM（azure / dashscope） |
| `vector_store.backend / persist_path` | 向量库（chroma）与持久化目录 |
| `splitter.chunk_size / chunk_overlap` | 分块大小与重叠 |
| `ingestion.*` | Transform 开关（chunk_refiner / metadata_enricher / image_captioner 的 use_llm）与批大小 |
| `ingestion.retry.*` | 可选：失败重试（`max_attempts` 缺省 1 即不重试；>1 时仅超时/连接/5xx/429 等可重试错误按指数退避，上限 30s） |
| `ingestion.version_policy.*` | 可选：写入历史表的 parser / chunk / embedding 版本（缺省 `auto` 推导）；`on_embedding_mismatch: stale`（告警跳过）/ `rebuild`（提示全量重建，不自动执行） |
| `retrieval.top_k_dense / top_k_sparse / top_k_final / rrf_k` | 混合检索各级候选数与 RRF 平滑常数 |
| `rerank.backend / model / top_m` | 精排后端（none / cross_encoder / llm） |
| `evaluation.backends / golden_test_set` | 评估后端组合与黄金测试集路径 |
| `observability.log_file` | JSON Lines trace 日志路径（Dashboard 追踪页数据源） |
| `agentic_search.*` | 可选 sirchmunk sidecar 检索（默认关闭；`base_url` / `api_token` / `mode` / `allowed_roots`，见 5.1 节） |

### 常见问题排查

- **API Key 报错**：确认 `settings.yaml` 中 `api_key` 字段或对应环境变量（`OPENAI_API_KEY` / `AZURE_OPENAI_API_KEY`）已设置；Ollama 场景确认 `base_url`（默认 `http://localhost:11434`）可达。
- **依赖安装失败**：确认 Python ≥ 3.10；`pip install -r requirements.txt` 失败时可先升级 pip。Windows 下 `chromadb` / `pillow` 编译问题建议使用 Python 3.11+。
- **查询无结果**：先确认已摄取（`python scripts/query.py --query ...` 提示先摄取则运行 `scripts/ingest.py`）；Dashboard「Ingestion 追踪」页可查看各阶段是否成功。
- **MCP 连接问题**：确认配置中 `cwd` / `PYTHONPATH` 为绝对路径；手动运行 `PYTHONPATH=src python -m mcp_server.server` 验证 server 可启动。
- **Ragas 未安装**：`ragas` 后端是可选依赖（`pip install ragas`），未安装时自动降级，`custom` 指标始终可用。

---

## 🎯 谁适合用这个项目 & 怎么用

大家的背景不同——有的校招、有的社招；基础也不同——有的有 AI 项目经验、有的是转方向。因此对于这个项目的使用策略也应该不同，**请一定灵活使用，切忌生搬硬套**。

不过有一点是通用的：**整套项目背后的思路**——如何写 Spec 快速拉起一个项目、如何用 Skill 驱动 AI 自动编码和测试——这些工程化方法论适用于任何项目，值得所有人参考学习。

对于项目本身在不同场景下的使用策略，我会提供一些具体的例子，并以我自己的亲身经历展开——**如果是我自己，面对不同的情况，我会怎么使用这个项目**——给大家作为参考。

### 1. 纯学习 RAG —— 把项目当作 RAG 全流程的学习材料

这个项目本身就是一个完整的 RAG 系统，可以作为学习 RAG 的配套实战项目。

我最开始学习 RAG 的时候，看的是这本书：**《大模型RAG实战：RAG原理、应用与系统构建》**（汪鹏、谷清水、卞龙鹏等人工智能领域专家编著）。你完全可以结合这本书来学习 RAG，书中涉及的典型环节——检索、生成、向量数据库、分块策略、重排序等——其实不管你看哪本 RAG 相关的书，核心内容都是这些。

**这个项目就是把这些步骤串起来了**，所以它可以作为一个通用的 RAG 全流程项目来学习整个过程。你可以配这本书，我相信你也可以配其它的 RAG 书籍，因为流程是通的。面试 RAG 其实也无非是这些过程的组合、原理，以及在实际中遇到的困难和优化。

### 2. 时间紧迫 —— 缺一个项目拿去面试

如果你现在没有 AI 相关项目、急需一个项目去面试，那么可以：

1. **直接使用本项目**，克隆 `main` 分支，用 Setup Skill 跑起来
2. **结合 Resume Writer Skill** 写自己的简历（Skill 会根据你的背景定制化生成项目描述）
3. **尝试理解项目**，跑通核心流程，结合我后续总结的该项目面试问题，先去面试
4. **随着面试深入理解、扩展项目**——面试本身就是最好的学习驱动力

比如现在 3 月份，需要找暑期实习的同学，时间紧迫——先写上去，一边面试一边学习，有时间再扩展。解决你着急面试没有项目的燃眉之急。思路就是：**先写上去 → 去面试 → 根据面试反馈改进项目**。

通常暑期实习从 3 月到 7 月都有机会。找到了实习、有了一个大模型项目经验后，再以此为跳板继续学习——7~10 月秋招，甚至到明年 3 月春招，你有大量的时间持续积累。现在开始虽然看起来有点晚，但其实不晚。如果你能保持学习节奏，从现在到明年 3 月，学习整整一年，校招上岸大模型方向绝对没有问题。**关键在于你自己能不能保持这么长时间的学习力。**

### 3. 时间相对充分 —— 以本项目为起点进行扩展

你可以把这个项目作为起点，根据自己的发展方向进行针对性扩展。DEV_SPEC 中也写了扩展方向，这里列几个常见的：

- **想补充 Agent 知识**：自己实现 Agent 端，做一些上下文处理、Tool Calling、ReAct 逻辑，把本项目作为 Agent 的一个模块和能力，变成一个 **Agent + RAG** 的项目
- **想展示后端工程能力**：加上后端部署能力，写 Dockerfile，做 CI/CD 流水线，加上监控和日志收集
- **想把 RAG 做深入**：扩展到 Agentic RAG、Graph RAG 等高级形态，或者在检索策略上做更多优化实验

每个人发展方向不一样——就像项目配套的 Resume Writer Skill，它写简历的时候会先问你的背景和情况。你定位大模型应用开发工程师、RAG 工程师、全栈工程师，校招、社招的要求都是不一样的（具体大模型不同岗位介绍和技术栈可以看笔记的**大模型岗位介绍**部分），所以需要自己进行针对性扩展。

> **强烈建议**：不管你什么背景、怎么扩展，你大概率需要结合自己的业务来写简历。所以**至少试一下**——把你自己领域的文档（金融、法律、医疗，或者你的业务文档）丢进去，看一下检索效果。如果效果不好，再去调整和改进。这个过程本身就是最好的学习，也是面试时最有说服力的实战经验。

### 4. 时间特别充分 —— 从零体验完整工作流

如果你时间充足，我建议你从 `clean-start` 分支开始，甚至在 `clean-start` 的基础上**删掉 DEV_SPEC**，从文档设计开始，一点点体验：

**文档设计 → AI 写代码 → 改进迭代 → 测试 → 部署**

整个过程的方法论。其中 DEV_SPEC 怎么写、Skill 怎么设计，这些都在笔记项目部分的对应视频中有讲解。你可以重新设计文档、改进文档，甚至直接做 Agent 方向的东西，来走完整个流程。

这样做你会学习到**开发一个项目的完整思路**。这套方法最大的好处是**下限极低**——几乎你都能设计出来、完成整个项目。这样你既学会了思路，又学会了过程，而且项目可以高度定制。群里已经有很多朋友都这么做了。

### 5. 融入现有项目 —— 把 RAG 能力集成到你已有的项目中

这其实也是一种很好的策略，我自己可能也会用这种方式。以我亲身经历现身说法：

我之前找工作时，其实已经有 2 个 Agent 项目了，只是 RAG 流程跑得很粗。我简历上大概的写法是"Agent 项目做了啥，其中涉及了一些 RAG 的知识"。面试的时候，面试官多少都会问 RAG 的内容，然后我和他讲，但因为之前项目的 RAG 系统很浅——其实就是做了一个基本的 Embedding 向量匹配，没有粗排、重排等策略——所以面试官一问就比较浅。

做了这个项目之后，一种处理方式是**把本项目的 RAG 能力融入到之前的 Agent 项目中**，在简历上不作为独立项目，而是作为 Agent 项目的一部分来描述。例如：

> *"……项目中使用自研的模块化 RAG 系统进行知识检索，采用 BM25 + Dense Embedding 混合召回并通过 RRF 融合排序，结合 Cross-Encoder 重排序提升 Top-K 精准度；支持多模态文档处理（PDF 解析 + Image Captioning），通过 MCP 协议暴露标准化工具接口供 Agent 调用。集成 Ragas 评估框架，建立 Golden Test Set 回归测试机制，持续优化检索质量……"*

这样你原有的 Agent 项目就有了 RAG 深度，面试官再问你就有东西可讲了。

### 6. 产品经理 —— 对，你没看错，PM 也可以用这个项目

大模型产品经理面试越来越多地会考 RAG 相关知识，有些公司甚至要求产品经理自己写一个 POC（Proof of Concept）再交给开发。**这个项目和背后的方法论，完全可以帮你做到这一点。**

**为什么 PM 可以用：**

1. **面试需要**：大模型产品岗会考 RAG 的基本原理和流程，你通过这个项目可以直观感受 RAG 的整个过程——从文档摄取、分块、向量化、检索、重排到最终生成，建立起产品层面的理解
2. **POC 能力**：你完全可以用这套方法构建出整个项目——写文档（DEV_SPEC），或者直接用现有的文档，然后用 Skill 让 AI 帮你生成代码。面试的时候你说的是你的思路和产品设计，代码是 AI 帮你写的，这在当下完全合理
3. **不需要关心技术细节**：产品不用关心每一行代码怎么写，但通过跑通这个流程，你能从产品层面思考痛点——比如检索不准怎么定义指标、用户体验上怎么设计反馈机制、数据质量如何影响 RAG 效果等

**具体怎么做：**
- 克隆 `main` 分支，用 Setup Skill 跑起来，体验完整流程
- 把你自己业务领域的文档丢进去，看检索效果，思考产品层面的优化方向
- 面试时讲你的产品思路和设计思考，技术实现部分说明是用 AI 辅助完成的

> 💡 笔记中也提供了 **Vibe Coding** 相关教程（如 Tina Huang 老师的讲解），非常适合非技术背景的同学参考，用 AI 快速构建原型。

### 关于"项目浅"这件事

最后我想独立提一点（这一点适用于上面所有情况）：

**所有项目的深入优化不是一步到位的。**

如果你是转行，项目都是自己做的，多少会遇到面试官觉得你项目浅的问题。我之前也提到过这一点，但不用害怕：

1. **项目深度不是入行的必要条件。** 我去年拿了 6 个 offer，其中包括大厂 offer，即使这样，还是有面试官觉得我项目浅。面试还会考虑很多其他方面——理论基础、算法能力、背景匹配、知识广度等等。不要因为觉得自己转行项目浅就觉得自己转不了。

2. **项目是在不断优化和深入的。** 面试官说你项目浅，你听他的反馈，一定能听出来他为什么觉得浅——比如觉得你数据不够复杂，那你就造一些复杂数据；觉得你图片处理太简单，那你可以扩展多模态策略。我自己也是在面试过程中不断往项目里加东西：我之前做的 Agent 项目，随着面试推进，我往里加了部署、训练、反思数据、评估模块——整个过程都是随着面试同步进行的。

**给自己预留多一点面试时间，一边面试，一边改进和加深。** 所以这里就又提到了整套项目的思路——学会这些思路，你才能持续扩展，而且扩展的门槛很低，都是想好想法让 AI 写嘛，所以不用怕。

说一个真实数据：**这个项目从立项到完成，是我下班后用了大概 2 个月时间做的**，期间我还要上班、做自媒体、自媒体也有其他内容要产出。所以我希望你不要完全指望这个项目作为一个不用扩展就特别深入的项目，特别是社招的同学。但反过来想——两个月的下班时间就做出了这些，如果你学会了这套方法，自己扩展的速度会有多快？

方法都有了，所有的方案、过程、记录都有留档和视频讲解。**最终一定要靠你自己去扩展、迭代，做成最适合你自己的项目。**

---

## 📝 简历参考

> ⚠️ **强烈建议**：请使用项目内置的 **Resume Writer Skill** 来生成你的简历项目经历，而不是直接复制下面的示例。
>
> 简历项目经历**一定是针对性的**——需要结合你自己的业务背景、目标岗位、技术侧重来定制化生成。下面的示例仅用于展示 Skill 的输出效果和不同场景的写法参考，**直接照搬没有任何意义**。
>
> **如何使用 Resume Writer Skill**：在 VS Code 中通过 Copilot / Claude 对话框输入 `写简历` 或 `resume`，Skill 会引导你完成画像采集并自动生成四段式简历。具体使用方式和设计思路请参考笔记中 **项目部分的视频讲解**。

### Resume Writer Skill 工作方式

Skill 采用 **"写作原则 + 项目亮点 + 用户画像 = 定制化简历"** 的三角模型，流程如下：

1. **画像采集**：Skill 会询问你的目标岗位（RAG Engineer / Backend / Agent 等）、业务背景、技术侧重、特殊要求
2. **亮点匹配**：根据你的岗位方向，从项目 10 大技术亮点中筛选 3-5 个最匹配的写入 bullet points
3. **四段式生成**：严格按 **背景 → 目标 → 过程 → 结果** 结构输出，每条 bullet 遵循"动词开头 + 技术细节 + 量化效果"
4. **面试追问预测**：自动生成 3-5 条面试官可能的追问，帮你提前准备

### 示例一：校招 · RAG Engineer 方向

> 以下为 Skill 基于"校招、RAG 方向、通用框架模式"生成的示例输出：

**智能知识检索与问答系统** | 2024.09 - 2025.02 | 独立设计与开发

**背景**：针对企业级知识库场景中文档分散、检索精度不足、AI 应用难以接入私有知识的共性痛点，设计并实现了模块化 RAG 检索框架。

**目标**：构建基于混合检索 + MCP 协议的智能知识问答系统，实现精准语义检索与 AI Agent 直接调用私有知识库的能力，将文档问答准确率提升至 90% 以上。

**过程**：
- 设计 BM25 + Dense Embedding 混合召回架构，通过 RRF 融合排序平衡查全率与查准率，结合 Cross-Encoder 重排序将 Top-10 命中率提升约 25%
- 构建全链路 Ingestion Pipeline（PDF 解析 → Markdown → 语义分块 → Metadata 增强 → Embedding → Upsert），集成 Vision LLM 实现图片自动描述并缝合进 Chunk，复用纯文本链路即可"搜文字出图"
- 实现 LLM / Embedding / Reranker / VectorStore 全链路可插拔架构，定义统一抽象接口，通过配置文件一键切换后端 Provider，支持 4+ LLM Provider 零代码切换
- 集成 Ragas + Custom 双评估体系，建立 Golden Test Set 回归测试机制，覆盖 Faithfulness / Relevancy / Recall 等维度，拒绝"凭感觉"调优
- 基于 Skill 驱动全流程开发，通过 auto-coder / qa-tester / setup / package 等 5 大 Agent Skill 覆盖编码、测试、配置、打包完整生命周期，2 个月业余时间完成 68 个子任务的全量交付

**结果**：系统支撑 5000+ 篇文档的实时语义检索，检索准确率（Hit Rate@10）达 92%，端到端查询延迟控制在 800ms 以内，三层测试体系（Unit / Integration / E2E）覆盖 1200+ 测试用例。

**技术栈**：Python / LangChain / ChromaDB / BM25 / Cross-Encoder / MCP Protocol / Web UI（FastAPI + Next）/ Ragas / Azure OpenAI

### 示例二：社招 · 已有 Agent 项目，融入 RAG 深度

> 以下为 Skill 基于"社招、Agent 方向、Windows 平台开发业务背景"生成的示例输出（将 RAG 能力融入已有 Agent 项目）：

**Windows 平台智能知识助手** | 2024.06 - 2025.02 | 核心开发

**背景**：在 Windows 平台开发团队中，版本发布相关信息（Release Notes、变更日志、补丁公告、兼容性说明等）分散于多个 Wiki、文档仓库和内部系统，工程师排查版本差异或回答客户问题时需跨系统翻找，现有关键词搜索无法理解语义，导致检索效率低、信息遗漏频发。

**目标**：为团队构建基于 Agent + RAG 架构的智能知识助手，实现跨系统文档的语义检索与自动问答，通过 MCP 协议集成至工程师日常工具链（VS Code / Claude Desktop），将文档查找时间缩短 60% 以上。

**过程**：
- 设计 Agent + RAG 分层架构，Agent 端负责意图识别与 Tool Calling，RAG 端提供 BM25 + Dense Embedding 混合召回 + Cross-Encoder 精排的两段式检索能力，通过 MCP 协议暴露标准化工具接口供 Agent 调用
- 实现全链路 Ingestion Pipeline，支持 PDF / Markdown 多格式文档解析，集成 Vision LLM 自动生成图片描述（架构图、截图等），解决"搜文字出图"的多模态检索需求
- 构建可插拔后端架构，LLM / Embedding / Reranker / VectorStore 均定义抽象接口，支持 Azure OpenAI ↔ DeepSeek ↔ Ollama 一键切换，适配团队不同网络环境
- 搭建 Web UI（FastAPI + Next）管理平台，提供数据浏览、在线检索、Ingestion 追踪、查询追踪、评估面板七大功能页，实现全链路白盒化可观测
- 集成 Ragas 评估框架 + Golden Test Set 回归测试，在版本迭代中持续监控检索质量，Faithfulness 评分稳定在 0.85 以上
- 采用 Skill 驱动全流程开发模式，编写 DEV_SPEC 规格文档驱动 auto-coder 自动编码、qa-tester 自动测试与修复、setup 一键环境配置，5 大 Agent Skill 覆盖完整开发生命周期，2 个月业余时间完成 68 个子任务交付

**结果**：系统覆盖团队 8000+ 篇技术文档，工程师日均文档查询时间从 15 分钟缩短至 3 分钟，检索准确率 Hit Rate@10 达 90%，已通过 MCP 协议接入 3 个内部 AI 工具，累计处理查询 2 万+ 次。

**技术栈**：Python / Agent / Tool Calling / RAG / BM25 / Dense Retrieval / Cross-Encoder / MCP Protocol / ChromaDB / Web UI（FastAPI + Next）/ Ragas / Skill-Driven Development / Azure OpenAI

### 示例三：社招 · 后端工程师转 AI 方向

> 以下为 Skill 基于"社招转 AI、后端/架构方向、金融合规业务背景"生成的示例输出：

**合规智能文档检索系统** | 2024.10 - 2025.02 | 设计与主导开发

**背景**：在某金融机构合规部门，法规文件和内部政策文档持续增长至万级规模，合规团队在审查和咨询场景中需要快速定位特定条款，但现有全文搜索系统只能精确匹配关键词，无法理解"反洗钱"与"AML"等语义近义表达，条款定位效率低下。

**目标**：设计并实现模块化 RAG 检索系统，将语义检索能力引入合规文档管理流程，支持近义词、跨语言条款匹配，目标将合规条款定位准确率提升至 90% 以上。

**过程**：
- 主导系统架构设计，采用全链路可插拔架构，LLM / Embedding / Reranker / Splitter / VectorStore 均定义抽象接口与工厂模式，通过 YAML 配置一键切换后端，零代码修改即可适配不同部署环境
- 实现 BM25 稀疏检索 + Dense Embedding 语义检索的混合召回策略，通过 RRF 融合排序兼顾专有名词精确匹配与语义近义匹配，检索准确率较纯向量方案提升 22%
- 构建完整的数据摄取管线，支持 PDF 解析 → 语义分块 → Chunk Refinement → Metadata Enrichment → 向量化存储，实现 DocumentManager 幂等管理，保证文档更新时的数据一致性
- 搭建三层测试体系（Unit / Integration / E2E），覆盖 1200+ 测试用例，集成 Ragas 评估框架建立自动化回归机制，确保迭代过程中检索质量不退化
- 基于 MCP 协议暴露标准化工具接口，支持 GitHub Copilot / Claude Desktop 等 AI 助手直接调用，实现"一次开发、多端调用"的服务化部署
- 实践 Skill 驱动全流程工程化方法，基于 DEV_SPEC 规格文档驱动 AI Agent 自动完成编码（auto-coder）、测试（qa-tester）、环境配置（setup）、清理打包（package），68 个子任务全量由 Agent 交付，开发周期压缩至 2 个月业余时间

**结果**：系统上线后支撑 12000+ 篇合规文档的实时语义检索，条款定位准确率从 68% 提升至 91%，单次查询延迟控制在 700ms，合规团队文档审查效率提升约 50%。

**技术栈**：Python / 可插拔架构 / 工厂模式 / BM25 / Dense Retrieval / RRF / Cross-Encoder / ChromaDB / MCP Protocol / Web UI（FastAPI + Next）/ Ragas / Skill-Driven Development / Azure OpenAI

---

> 💡 **使用提醒与重要说明**：
>
> **1. 关于放大策略**：Resume Writer Skill 中内置了我设计的**放大策略**——AI 会在合理范围内对你的项目经历进行包装和放大（例如量化指标、业务规模等）。这是我允许的，也是简历写作的正常做法。但这意味着：**生成简历后，你必须想清楚面试官可能针对每一条追问什么、你该怎么回答**。Skill 在生成简历的同时会自动给出 3-5 条面试追问预测，请认真准备这些问题。
>
> **2. 把简历当作实践清单**：简历中写到的每一个技术点，你都应该**真正去试一下**。比如简历写了"检索准确率提升 XX%"——那你就应该在自己的数据上跑一下，看看实际效果如何，过程中遇到了什么问题，你是怎么调优解决的。这些实践经验才是面试时真正有说服力的内容，也是你真正学到东西的过程。简历里没涉及到的部分（比如你没试过多模态、没跑过评估），也可以以此为契机去做代码实验。
>
> **3. 生成的是初稿，请务必结合自身情况修改**：Skill 生成的简历是**初稿**，不是终稿。你需要根据自己的实际情况进行调整——哪些技术你确实深入用过、哪些只是了解、哪些数据需要换成你自己的。简历写作有一条铁律：**写在简历上的东西，你一定要会讲**。即使某个点是放大的，你也要想清楚面试官会怎么问、你怎么自圆其说。说不清楚的东西宁可不写，写上去就要能扛住追问。
>
> **4. 方法比模板更重要**：整个简历编写的思路是我的——包括放大策略、四段式结构（背景 → 目标 → 过程 → 结果 → 技术栈）、亮点匹配逻辑等，这些都沉淀在 Resume Writer Skill 里。如果你有自己更信任的简历模板，或者你对项目做了扩展修改，完全可以去修改 Skill 本身来适配。**学会这套"用 Skill 沉淀方法论、让 AI 按规则执行"的逻辑，比简历本身更有价值**——这个思路可以复用到你未来任何项目的简历编写中。
>
> **5. 强烈建议写上 Skill 驱动全流程**：我个人的意见是，**Skill 驱动全流程开发这个闭环，适合写在任何人的简历上**。Skill 是当下非常热门的方向，已经是面试中的必考内容，很多公司内部也在研究如何用 Skill 加速项目构建。讲清楚你是如何使用 Skill 完成整个项目从编码 → 测试 → 修复 → 配置 → 打包的完整闭环，这本身就是一个比较创新和前沿的亮点，面试官会对此印象深刻。关于 Skill 相关内容在面试中怎么讲、怎么回答追问，我后面也会提供一些例子给大家参考。

---

## ❓ 常见问题

### 1. 如何切换 Provider（比如换成 Qwen / DeepSeek / Ollama）？

**非常简单——直接问 AI 帮你完成即可。**

项目从架构设计上使用了**工厂模式（Factory Pattern）**，Provider 的扩展和切换非常方便。你只需要理解内部原理就会发现：不同 API 本质上都是类似的 HTTP 请求，甚至大多数都遵循 OpenAI 的请求格式，切换起来特别容易。

**具体操作方式有两种：**

1. **使用 Setup Skill（推荐）**：运行一键 Setup Skill，AI 会主动询问你想用哪个 Provider，引导你填入 API Key，然后自动帮你完成代码适配和配置生成。
2. **直接让 AI 帮你改**：把你想切换的 Provider 告诉 AI（如 "帮我切换到 Qwen" 或 "帮我配置 DeepSeek"），AI 能根据工厂模式的架构自动完成代码编写。

> **原理说明**：项目的 `src/libs/` 下的 LLM、Embedding、Reranker 等模块都使用工厂模式，新增一个 Provider 只需要：① 新增一个 Provider 类；② 在工厂注册；③ 更新 `settings.yaml` 配置。AI 完全可以自动完成这些步骤。

### 2. 评估体系与 Cross-Encoder Reranker

评估体系（阶段 H）已完整落地：`CustomEvaluator`（hit_rate / mrr / hit_at_k）与 `RagasEvaluator`（faithfulness / answer_relevancy / context_precision，需 `pip install ragas`）经 `CompositeEvaluator` 组合，`scripts/evaluate.py` + golden test set 支持回归测试。

| 模块 | 状态 | 说明 |
|------|------|-----------|
| **自定义评估（Custom Evaluator）** | 已完成 | 离线可用，`python scripts/evaluate.py --evaluator custom` |
| **Ragas 评估** | 已完成 | 可选依赖 `pip install ragas`，未安装自动降级 |
| **Cross-Encoder Reranker** | 已实现，默认未启用 | 需下载本地重排模型（如 `cross-encoder/ms-marco-MiniLM-L-6-v2`），`settings.yaml` 中 `rerank.backend: cross_encoder` 启用 |

### 3. 项目报错 / Bug 怎么办？

**这不是一个经过广泛测试的生产级项目，而是一个面试导向的实战项目。** 遇到报错是正常的。

- **对面试的影响**：项目的 Bug 对面试几乎没有影响——面试官不会去实际运行你的项目，他们关注的是你对架构、原理和设计决策的理解。
- **如何修复**：最简单的方式是**把错误信息直接丢给 AI**，绝大多数问题 AI 都能帮你修复。
- **参考资源**：笔记中推荐的 Tina Huang 的视频里也介绍了这种用 AI 快速修复错误的方法。

### 4. 想摄取 PDF 以外的文档格式（Word / Markdown / HTML 等）怎么办？

Word / PowerPoint / Excel / HTML（`.docx` / `.pptx` / `.xlsx` / `.html`）已内置支持，安装可选依赖 `pip install '.[ingest-formats]'` 即可（见运行手册第 3 节）。

其他格式（如 Markdown）可以**直接问 AI 帮你扩展**：Loader 层采用可插拔的抽象设计（`BaseLoader`），新增一个对应的 Loader 实现，并在 `src/ingestion/pipeline.py` 与 `scripts/ingest.py` 两处登记扩展名即可（drift guard 单测会检查两处一致）。比如告诉 AI："帮我新增一个 Markdown 的 Loader，参考现有的 MarkitdownLoader 实现"。

### 5. 如何集成到 AI 工具中（Copilot / Cursor / Claude Code 等）？

本项目是一个 **MCP Server**，可以集成到任何支持 MCP 协议的 AI 工具和 Agent 中。我的演示中已经集成到了 **GitHub Copilot** 和 **Cursor** 中，你同样可以集成到 **Claude Code** 或其他支持 MCP 框架的工具。

**如何集成？非常简单——问 AI。**

本质上就是给不同的工具写一个 MCP 的配置文件：
- **Copilot（VS Code）**：让 AI 帮你生成 MCP 配置文件即可
- **Cursor**：直接导入项目，Cursor 会自动识别
- **Claude Code / 其他框架**：问 AI 怎么配置，每个工具的配置方式略有不同，但原理都一样

当然，也推荐你去理解 MCP 协议的原理——了解 Server 和 Client 之间是如何通信的、Tool 是怎么注册和调用的。这些在面试中也是加分项。

### 6. 通用建议：善用 AI

上述大多数问题（Provider 切换、模块扩展、Bug 修复、架构理解）**AI 都能解决**：

- 🔧 **代码层面**：让 AI 帮你切换 Provider、实现评估方法、修复 Bug
- 📖 **知识层面**：项目架构问题、设计模式问题，都可以问 AI 获取解释
- 🚀 **扩展层面**：想加新功能或适配新场景，描述清楚需求让 AI 帮你实现

> 多问 AI，让它指导你。这也是这个项目想要传达的核心理念之一——**学会与 AI 协作开发**。

---

## 📌 后续安排

### ✅ 会做的
- 项目相关问题的汇总与 FAQ 整理
- 面试高频问题整理与参考答案
- 技术要点讲解（RAG 核心知识、架构设计等）
- 简历包装建议与示范
- **亲身面试实践**：我会带着这个项目去面试，把遇到的问题、怎么回答，都总结到文档中
- **欢迎投稿共建**：如果你用这个项目去面试了，可以把面试录音发给我，我来帮你分析项目相关的问题并写入文档，同时也可以听一下面试整体有哪些改进建议。这样大家能共同进步，一起总结和完善这个项目的面试问答

### ❌ 不会做的
- 不会继续扩展新功能
- 不会处理 Bug Fix、设计优化等
  - 遇到 Bug 和设计上的改进点，请在自己的项目中修复和优化
  - 后续的扩展和修复一定是要靠自己的，而且**有了 AI，这些都很容易做到**
  - 这本身就是一个很好的学习和面试加分项
  - 在理解项目的基础上独立扩展，才是真正的能力体现

### 📝 个人规划说明

我后续会去学习**大模型算法、训练**方向，会把一些笔记和思路总结在笔记中。因此对于这个项目，**不会无限扩展功能或修复 Bug**，但会非常乐意持续做的事情是：

- 总结这个项目在面试中遇到的问题
- 整理如何回答、如何迭代优化的思路
- 把面试问答沉淀到文档中，供大家参考

---

## 📚 配套资源

本项目配有完整的配套学习资源，包括：

- 🎬 **视频讲解**：项目架构设计、Skill 使用、DEV_SPEC 编写、开发全流程演示
- 📝 **面试笔记**：大模型方向面试准备、RAG 核心知识点整理
- ❓ **面试问题参考**：该项目在面试中遇到的真实问题与参考回答
- 📖 **八股整理**：大模型 / RAG / NLP 相关高频面试题

> 👉 **请关注小红书：[不转到大模型不改名](https://www.xiaohongshu.com) 获取以上所有资源。**
