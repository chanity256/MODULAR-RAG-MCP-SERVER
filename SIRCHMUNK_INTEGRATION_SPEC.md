<!-- 扩展规格：Sirchmunk 无索引 Agentic 检索整合。格式与架构分层对齐 DEV_SPEC.md；基线为「阶段 A–I 已完成」状态（OBSIDIAN_RAG_SPEC V 系列为独立增量，互不阻塞）。 -->
# Sirchmunk Agentic 检索整合规格 (SIRCHMUNK_INTEGRATION_SPEC)

> 版本：0.2 — 由《整合可行性分析》升级为可执行规格（调研结论 + 整合注意事项 + 项目排期）
>
> 调研对象：[modelscope/sirchmunk](https://github.com/modelscope/sirchmunk)（`main` 分支，版本 `0.2.1+main`，Apache-2.0，Beta；**锁定 commit `b314e11`**）
>
> 基线：DEV_SPEC.md 阶段 A–I 全部完成（PDF Ingestion / Hybrid Search / MCP Server / Dashboard / Evaluation / E2E 均已落地）
>
> 调研日期：2026-10-04（源码审计：2026-10-05，所有 file:line 引用基于 `b314e11`）

## 目录

- 项目概述
- 核心目标与范围
- 技术选型与模块设计
- 整合注意事项（源码实证）
- 测试方案
- 配置变更汇总
- 项目排期
- 优点亮点与风险应对
- 参考

---

## 1. 项目概述

### 1.1 结论速览

| 问题 | 结论 |
|------|------|
| 能否整合？ | **可以**。协议兼容（本项目 MIT，sirchmunk Apache-2.0，依赖无版本冲突），本项目的可插拔架构天然留有接入点。 |
| 应该怎么定位？ | **作为「无索引 Agentic 检索」独立 MCP Tool 与（P2 门控后）条件第三路**，与现有 Dense + BM25 + Rerank 互补，**不替换** Chroma/BM25 主链路。 |
| 运行方式（已决策） | **HTTP Sidecar（进程外隔离）**：sirchmunk 装在独立 venv，以 `sirchmunk serve`（FastAPI，绑定 127.0.0.1 + Bearer Token）常驻；本项目通过同步 httpx 调用 `POST /api/v1/search`。**主 venv 永不 import sirchmunk。** |
| 实施范围（已决策） | **P0–P1 为承诺任务**（S0/S1）；**P2/P3 为门控任务**（S2/S3）：仅当 P0 对比与 P1 工具验收证明有明确增益后才启动。 |
| 主要代价 | 延迟（FAST 约 2–5s，DEEP 数十秒）、每次查询 2～N 次 LLM 调用、结果非确定性、sidecar 依赖重（torch / kreuzberg / duckdb / igraph / rg / rga）、0.x API 无 changelog 演进快。 |

### 1.2 Sirchmunk 是什么

**一句话**：一个「不建向量索引、直接在活文件上做检索」的 Agentic Search 引擎——用 `ripgrep` / `ripgrep-all` 做词法召回，用 LLM 做查询分析、多路证据融合与 ReAct 式迭代探索，最后输出带出处的答案。

| 层 | 机制 | 关键代码（`src/sirchmunk/`，基于 `b314e11`） |
|----|------|---------|
| 词法召回 | `rg` 处理纯文本/代码/Markdown；`rga` 处理 PDF/DOCX/EPUB；LOG/PPT(X)/XLS(X) 走原生抽取兜底 | `retrieve/text_retriever.py`（`GrepRetriever`） |
| FAST 模式 | 1 次 LLM 查询分析 → grep 定位最佳文件 → 采样上下文窗口 → 1 次 LLM 合成答案（约 2 次 LLM 调用） | `search.py` |
| DEEP 模式（默认） | LENS 多路召回（关键词片段/精确实体/目录树/知识簇/主题图）+ 加权 RRF + 置信度融合 + 零命中救援 + ReAct 迭代 | `search.py`、`agentic/react_agent.py` |
| 知识簇缓存 | 每次成功检索沉淀为 `KnowledgeCluster`（DuckDB 内存 + Parquet 落盘），相似查询 ≥0.85 直接复用 | `storage/knowledge_storage.py` |

> ⚠️ 「Embedding-free」并不绝对：文档检索不依赖向量库，但 `reuse_knowledge=True`（默认）会立即 `import torch` 并后台下载 embedding 模型。本项目配置 `reuse_knowledge=false` 绕开（见第 4 章）。

### 1.3 与当前项目的互补关系

| 维度 | 当前项目（Indexed RAG） | Sirchmunk（Indexless Agentic） | 互补点 |
|------|----------------------|------------------------------|--------|
| 数据准备 | 必须先 `ingest` | 无需摄取，指定路径即可搜 | 新文件/未摄取文件可立即检索 |
| 格式支持 | 仅 `.pdf` | MD/代码/JSON/YAML/CSV/HTML/PDF/DOCX/XLSX/PPTX/EPUB | 补齐多格式检索诉求（结构化解析仍归 MarkdownLoader，见 OBSIDIAN_RAG_SPEC） |
| 召回方式 | Dense + BM25 + RRF + Cross-Encoder | grep 精确匹配 + LLM 关键词扩展 + 多跳迭代 | 精确标识符/代码符号/跨文件多跳问题 |
| 延迟 | 毫秒级到 1 秒级 | FAST 2–5s，DEEP 数十秒 | 不进默认热路径，条件触发 |
| 成本 | 查询期仅 Embedding | 每次查询 2～N 次 LLM 调用 | 预算/开关/超时三重护栏 |
| 确定性 | 高（单测零网络） | 低（LLM 驱动） | 隔离在独立路由，测试 fake 注入 |
| 进程边界 | 单进程 | **独立进程（sidecar）** | 依赖隔离、stdout 隔离、超时可控 |

---

## 2. 核心目标与范围

### 2.1 In Scope

| 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|--------|-----------|
| P0 | Sidecar 环境与旁路对比 | 承诺（S0） | 独立 venv 跑通 `sirchmunk serve`；5–10 条 golden 问题旁路对比，产出增益数据 |
| P1 | HTTP 适配器 + `agentic_search` MCP Tool | 承诺（S1） | `libs/agentic_search`（Base/Factory/HTTP 后端/PathGuard）+ 新 MCP tool + trace + 离线测试 |
| P2 | HybridSearch 条件第三路 | 门控（S2，G1 通过后） | N 路 RRF 融合 + 触发策略 + 去重 + `query.py --agentic` |
| P3 | 评估 A/B 与 Dashboard 展示 | 门控（S3，P2 零回归后） | source 级指标 A/B + Query Traces 页 agentic 阶段展示 |

### 2.2 Out of Scope（明确不做）

- **不做进程内嵌入（in-process import）**：源码审计确认构造期副作用（自动联网装 `rga`、DEEP 路径无条件 `print` 到 stdout、asyncio 原语绑定首个事件循环）与本项目 stdio MCP / 离线测试纪律冲突（详见 4.1/4.2），全部需求由 sidecar 方式满足。
- **不暴露 FILENAME_ONLY 模式**：其返回形态（list 或字符串）与引用契约不兼容，且文件名定位价值有限；工具只开放 FAST/DEEP。
- **不替换主检索链路**：Chroma/BM25/RRF/Rerank 既有契约与测试语义零改动（S2 之前）；S2 也只做「可选第三路」，`enabled=false` 时行为与基线完全一致。
- **不引入 sirchmunk 的 HTTP 网页端与 SSE 流**：仅使用同步 `POST /api/v1/search`。
- **不做「发现 → 摄取」回流自动化**（原分析 P3 可选项）：留作未来展望。
- **不改 sirchmunk 源码**：上游 AGENTS.md 对公共 API/核心管线有 owner 审批门禁，本项目只做调用方适配。

---

## 3. 技术选型与模块设计

### 3.1 运行架构（HTTP Sidecar）

```
┌──────────────────── MCP Client（Claude / Copilot）────────────────────┐
└───────┬─────────────────────────┬─────────────────────────────────────┘
        │ stdio JSON-RPC          │ stdio JSON-RPC
        ▼                         ▼
┌───────────────────────────────────────────────────────────────────────┐
│ Modular RAG MCP Server（本仓库，主 venv，同步 stdio 主循环）             │
│                                                                       │
│  query_knowledge_hub          agentic_search（新，enabled 时注册）      │
│  HybridSearch + Rerank        └ AgenticSearchTool                     │
│                                   ├ PathGuard（调用前校验/调用后过滤） │
│                                   └ SirchmunkHttpBackend（同步 httpx） │
└───────────────────────────────────┬───────────────────────────────────┘
                                    │ HTTP POST /api/v1/search
                                    │ Bearer Token，127.0.0.1
                                    ▼
┌───────────────────────────────────────────────────────────────────────┐
│ sirchmunk serve（独立 venv，独立进程，FastAPI + 内部信号量串行）         │
│   ├ AgenticSearch（FAST/DEEP）── rg / rga 子进程                       │
│   ├ OpenAI 兼容 LLM（LLM_BASE_URL / LLM_API_KEY / LLM_MODEL_NAME）     │
│   └ work_path/.cache/（Parquet 知识簇 + spec + grep 缓存）             │
└───────────────────────────────────────────────────────────────────────┘
```

选型理由（相对进程内方案）：

1. **stdout 隔离**：DEEP 路径存在无条件 `print`（4.2），sidecar 的 stdout 污染不再影响本项目 stdio 通道。
2. **事件循环隔离**：`AsyncOpenAI` 客户端与模块级信号量绑定首个事件循环（4.3），进程内反复 `asyncio.run` 不可靠；sidecar 内部由 FastAPI 管理唯一事件循环。
3. **依赖隔离**：torch / kreuzberg / duckdb / igraph 等重依赖不进主 venv；`pyproject.toml` 主依赖零新增（httpx 已随 chromadb 间接可用，仍显式登记到可选组）。
4. **超时可靠**：httpx 客户端超时与重试完全由本项目控制，不受上游 600s 默认值影响。
5. **故障域分离**：sidecar 崩溃/未启动 → 工具返回友好 `isError`，主 server 与其余 tool 不受影响。

### 3.2 对接契约（POST /api/v1/search）

- 请求（本项目发送）：`{"query": str, "paths": [abs_path, ...], "mode": "FAST"|"DEEP", "response_format": "context", "max_loops": int, "max_token_budget": int, "top_k_files": int, "include"/"exclude": [...]}`。
- 响应（`response_format="context"` 经 FastAPI `_format_result` 序列化）：`{"success": true, "data": {"type": "context", **context.to_dict()}}`，其中 `data.answer`、`data.cluster.evidences[]`（`file_or_url`/`summary`/`snippets`）、`data.total_llm_tokens`、`data.llm_usages`、`data.telemetry` 为本项目消费的字段。
- 失败：HTTP 500（`Search failed`）或连接失败 → 映射为 `AgenticSearchBackendError`，由 tool 层转为友好 `isError` 响应。
- 鉴权：`SIRCHMUNK_API_TOKEN` 设置后上游对 `/api/` 路由强制 Bearer；本项目恒带 `Authorization: Bearer ${SIRCHMUNK_API_TOKEN}`。

### 3.3 模块改动清单（P1 落地形态）

| 模块 | 文件 | 改动 |
|------|------|------|
| 配置 | [config/settings.yaml](config/settings.yaml) | 新增 `agentic_search` 节（见第 6 章，默认 `enabled: false`） |
| 配置解析 | [src/core/settings.py](src/core/settings.py) | 新增 `AgenticSearchConfig` dataclass + `_build_agentic_search()`；可选节纪律（缺失走默认值），非法值 fail-fast |
| 适配层（新增） | `src/libs/agentic_search/`：`base_agentic_search.py` / `http_backend.py` / `path_guard.py` / `agentic_search_factory.py` | 沿用 `libs/*` 的 Base + Factory 模式；`AgenticSearchResponse` 契约类型；PathGuard 白名单；HTTP 后端同步 httpx + 错误映射 + 防御式解析 |
| MCP Tool（新增） | `src/mcp_server/tools/agentic_search.py` | 参照 `query_knowledge_hub.py`：懒初始化、可注入、参数校验（`query` 必填、`mode∈{FAST,DEEP}`、`paths` 必须在白名单内、`top_k` 正整数）、`persist_trace` |
| Tool 注册 | [src/mcp_server/server.py](src/mcp_server/server.py) `build_default_protocol_handler()` | 仅当 `agentic_search.enabled` 时 `register_tool(create_agentic_search_definition())` |
| 依赖 | [pyproject.toml](pyproject.toml) | optional dev 组登记 `httpx`（MockTransport 测试用）；**不新增**主依赖、不引入 sirchmunk |
| 测试 | `tests/unit/test_agentic_*.py`、`tests/integration/test_agentic_http.py`、`tests/e2e/test_agentic_mcp.py` | 见第 5 章 |

P2 追加（门控后）：`src/core/query_engine/fusion.py`（`fuse_routes` N 路）、`src/core/query_engine/hybrid_search.py`（可选第三路 + 触发策略）、`src/core/query_engine/agentic_trigger.py`（新增）、`scripts/query.py`（`--agentic`）。

无需改动：Ingestion 全链路、Chroma/BM25/Image 存储、既有 3 个 MCP Tool 契约、CitationGenerator（已读 `metadata["source_path"]`，直接工作）。

---

## 4. 整合注意事项（源码实证）

> 全部结论经 2026-10-05 对 `b314e11` 源码逐一核实；file:line 相对 `sirchmunk/src/sirchmunk/`。

### 4.1 构造期副作用：绝不许进程内构造

- **缺 `rg`/`rga` 时自动联网安装**：`AgenticSearch.__init__` 调 `install_rga()`（`search.py:537-539`），查 GitHub Releases API 并下载二进制到 `~/.local/bin`（`utils/install_rga.py:82-115`）；离线时构造直接抛 `RuntimeError`。**本机当前有 `rg`、缺 `rga`**。
- **`reuse_knowledge=True`（默认）构造即 `import torch`**（`utils/embedding_util.py:51-56`），随后台线程 modelscope 下载模型（`:73-137`）。
- **隐藏下载**：存在编译产物 `summary_index` 时 `TokenizerUtil()` 经 modelscope 加载 `Qwen/Qwen3-8B` tokenizer（`search.py:4806-4808`）。
- **每次构造注册 atexit + 守护线程**（`knowledge_storage.py:94-96`）。
- **环境变量在 import 期读取**（`utils/constants.py:5-73`：`GREP_*`/`LLM_*`/`SIRCHMUNK_WORK_PATH`；`search.py:163-245`：约 15 个 `SIRCHMUNK_*`/`LENS_*` 开关）——必须先设 env 再 import。

**应对**：sidecar 独立 venv + 手动预装 `rg`/`rga`（`brew install ripgrep-all`）+ 启动前 preflight（`rg --version`、`rga --version`、`/api/v1/search/status` 健康检查）；`SIRCHMUNK_ENABLE_CLUSTER_REUSE=false` 关闭 embedding 下载；`.env` 模板集中管理环境变量。

### 4.2 stdout 污染：stdio 集成的硬伤

- DEEP 路径存在**无条件** `print(..., flush=True)`：`learnings/tree_indexer.py:246,298,346,376,430,1439`、`learnings/compiler.py:784,857`。
- 官方自己的 MCP server（`sirchmunk_mcp/service.py:31-80`）也只在 import/构造期把 stdout 换成 devnull，**search 期间同样会漏**。
- 另有默认 demo 回调（`utils/log_utils.py:149,156,187`）与 tqdm（`scan/file_scanner.py:157`）写 stdout。

**应对**：sidecar 隔离（本规格决策）；本项目侧零容忍——`MCPServer.serve` 持有独立 stdout 引用且 e2e 断言「tools/call agentic_search 后 stdout 无多余行」。

### 4.3 异步模型：进程内 `asyncio.run` 不可行（修正原分析）

- `OpenAIChat` 构造即建 `AsyncOpenAI`（`llm/openai_chat.py:223`），httpx 连接池绑定首个事件循环；模块级 `asyncio.Semaphore`（`retrieve/text_retriever.py:36-37`）与实例 `_spec_lock`（`search.py:559`）同样绑定。**同一实例第二次 `asyncio.run` 典型报 "Event loop is closed" / "bound to a different event loop"**。原分析 §7「适配器内 `asyncio.run(...)` 桥接」不成立，本规格以 sidecar 取代。
- `search()` 无内部总超时，须调用方 `asyncio.wait_for`；取消时 rg/rga 子进程会被 killpg 回收（`text_retriever.py:517-577,660-682`），但 executor 线程与 kreuzberg 抽取子进程（600s 超时）不可取消。
- OpenAI 客户端默认 600s 超时 + 2 次内部重试 + 上游 3 次重试（`openai_chat.py:33,605-621`）——sidecar 内部已隔离，本项目 httpx 超时（默认 30s）独立可控。

### 4.4 路径安全：`paths` 不是安全边界

- ReAct 工具 `file_read` 可读 LLM 请求的**任意绝对路径**，无范围校验（`agentic/tools.py:574-628`）。
- `SIRCHMUNK_ALLOWED_PATHS` 白名单（realpath + 符号链接拒绝，`api/security.py:69-112`）**只保护 FastAPI 文件浏览端点**；经核实 `POST /api/v1/search` **不执行**白名单校验（`api/search.py:309-336` 无 is_path_allowed 调用）。
- 正面因素：`rg`/`rga` 默认遵循 .gitignore、跳过隐藏文件、不跟随符号链接（`text_retriever.py:1257-1288`）；`DirectoryScanner` 跳过 dot-目录（`scan/dir_scanner.py:383-387`）。

**应对（本项目 PathGuard 双层防线）**：
1. **调用前**：校验请求 `paths` 与允许根（realpath 归一、拒绝符号链接逃逸、拒绝白名单外路径、强制排除 `.env`/`.git`/`data/db`/密钥目录），越权直接 `ToolInvalidParams`，**不发请求**（fail-closed）。
2. **调用后**：过滤 `evidences[].file_or_url` 与 `cluster.search_results` 中落在白名单外的条目（防 `file_read` 经注入读出的路径泄露）。
3. **信任模型**：语料内容不可信（prompt injection 可操纵 Agent 读文件），agentic 答案按不可信输入处理，仅作为引用指向白名单内文件；DEEP 重度使用建议在受限用户/容器内运行 sidecar。

### 4.5 LLM 配置：不能复用 `settings.llm`（修正原分析 §5.1）

- `OpenAIChat` **仅 OpenAI 兼容**（原生 `OpenAI`/`AsyncOpenAI`，`openai_chat.py:222-223`），无 `timeout` 参数；Azure 只有 URL 识别 profile，从不使用 `AzureOpenAI`。
- 本项目 `settings.llm` 当前为 `provider: azure` 且未配置 endpoint/api_key，不具可用性。
- **决策**：sidecar 拥有独立 `LLM_*` 环境变量（推荐 ZAI OpenAI 兼容端点，与本项目 embedding/vision 同源），不与 `settings.llm` 耦合；两套配置在文档中明确标注职责差异。

### 4.6 返回结构与口径（比原分析更细）

- `snippets` 两形态并存：dict `{snippet, start, end, score, reasoning}`（`learnings/evidence_processor.py:535-542`）与纯字符串（FAST/fallback/compile 路径，`search.py:1097,1137`；`compiler.py:1199-1210`）；适配器两者都收。
- `start/end` 是**抽取文本上的字符偏移**，不是原文页码/行号；**全链路无行号**；`page_range` 在检索路径上几乎恒为 `None`（仅从存储回读时出现，`knowledge_storage.py:517`）→ citations 的 `page` 恒为 `None`，在工具文档字符串中说明。
- `SearchContext` 的 `to_dict()` 合并动态 `telemetry`（`schema/search_context.py:163-189`），含 `llm_call_count`/`budget_remaining` 等（修正原分析「无耗时字段」——耗时仍以本项目 trace `elapsed_ms` 为准）。
- **打招呼短路**：形如 "hi"/"test" 的查询命中正则（`search.py:76-99`）会跳过检索直接闲聊回复（`:2427-2434`）——golden 问题集必须避开此类措辞。
- **相对路径解析**：`paths` 缺省时按 请求参数 → 构造参数 → `SIRCHMUNK_SEARCH_PATHS` → **sidecar 进程 CWD**（`search.py:631-663`）——本项目恒发送绝对路径，不依赖 CWD。
- 无遥测外传（唯一外呼：GitHub rga 安装、modelscope 下载、LLM 端点）；Token 记账靠 `total_llm_tokens`/`llm_usages`，无价格字段。

### 4.7 持久化与并发

- 即使 `reuse_knowledge=false`，仍写 `<work_path>/.cache/`（Parquet 知识簇、`spec/*.json`、grep 缓存），有 60s 同步守护线程 + atexit。
- **无文件锁**：两进程共享 `work_path` 会互相覆盖 Parquet（last-writer-wins，`search.py:262-263` 仅按 mtime 判 staleness）。
- **应对**：`work_path` 每进程独占（本项目配置 `data/db/sirchmunk/`）；该目录列入 PathGuard 排除与摄取排除；测试内用 `tmp_path`。
- 上游信号量使 sidecar 搜索串行；HTTP 断连**不会取消**服务端执行 → 客户端超时后 LLM 成本仍在消耗，只能靠 `max_token_budget` + `max_loops` + `LENS_HARD_TOKEN_BUDGET=true` 兜底。

### 4.8 依赖与版本

- 核心依赖含 torch（经 sentence-transformers）、`kreuzberg>=4.0.0rc1`（预发布）、duckdb、igraph/leidenalg、fastapi/uvicorn（即使不用 web）、旧 `parquet` 包（拉 thriftpy2）。
- `0.2.1+main` 无 CHANGELOG，`search.py` 约 1.3 万行且默认开启多个 env 开关，行为易漂移。
- **应对**：独立 venv 用 Python 3.11/3.12；锁定 commit `b314e11`（升级=显式重跑验收）；抓一份真实 `/api/v1/search` 响应作为契约 fixture，单测据此锁响应 shape。

### 4.9 对原《可行性分析》的勘误汇总

| 原结论 | 勘误 |
|--------|------|
| §5.1 适配器内 `asyncio.run` 桥接 | 不成立（4.3），改 sidecar |
| §5.1 由 `settings.llm` 构造 `OpenAIChat` | 不可行（4.5），sidecar 独立 LLM 配置 |
| §2.2 HTTP API「支持允许路径白名单」 | 白名单不覆盖 `/api/v1/search`（4.4） |
| §2.3 「SearchContext 无耗时字段，需自行计时」 | `to_dict()` 含 telemetry/llm_call_count；耗时仍以本项目 trace 为准 |
| §7 「MCP 工具以 server.py 实际注册为准（1 个）」 | 保留，原分析已正确 |
| `get_document_summary` 与 agentic ID | 该 tool 的 `doc_id` 语义是 `source_path` 而非 chunk_id；agentic 命中的未摄取路径自然返回 "Document not found"，无需改代码，文档说明即可 |

---

## 5. 测试方案

延续 DEV_SPEC 第 4 章分层纪律与离线确定性先例（`RAG_LLM_INTEGRATION` / `RAG_E2E_INTEGRATION` 门控）。

| 层 | 对象 | 策略 |
|----|------|------|
| Unit | `AgenticSearchConfig`、PathGuard、HTTP 后端、证据转换、fake 后端、MCP tool 校验 | 全离线：`httpx.MockTransport` 模拟 sidecar（成功/超时/连接拒绝/500/畸形 JSON/snippets 两形态/cluster=None）；PathGuard 参数化（越权/符号链接/排除目录）；fake `BaseAgenticSearch` 注入 tool；契约 fixture 锁 `to_dict()` shape |
| Integration | Tool 注册与路由、`ProtocolHandler` 交互 | 进程内起本地 HTTP server（线程 + `http.server`）模拟 sidecar；断言 `tools/list` 条件注册（enabled=true/false 两态）、`tools/call` 参数校验 `-32602`、sidecar 不可用时 `isError` 友好响应而非 `-32603` |
| E2E | 子进程 MCP server 全链路 | initialize → tools/list（含 agentic_search）→ tools/call agentic_search → shutdown；**断言 stdout 每行均为合法 JSON-RPC**（对齐 4.2 的零容忍）；真实 sidecar 冒烟走 `RAG_AGENTIC_INTEGRATION=1` 门控默认跳过 |
| 门控验收 | S0 对比 / G1 复测 | golden 问题集（5–10 条，覆盖精确标识符/非 PDF 格式/多跳三类），记录 命中(source 级)/延迟/Token，结果表回填本文档第 7 章 |

---

## 6. 配置变更汇总

### 6.1 `config/settings.yaml` 新增节（全部可选，缺省关闭）

```yaml
# Agentic 检索（Sirchmunk sidecar，可选；enabled=false 时零加载、tools/list 不出现该工具）
agentic_search:
  enabled: false
  backend: sirchmunk_http        # 唯一后端（Factory 预留扩展）
  base_url: http://127.0.0.1:8584
  api_token: ${SIRCHMUNK_API_TOKEN}   # 环境变量解析，不落盘
  mode: FAST                     # FAST | DEEP（DEEP 另受 allow_deep 门控）
  allow_deep: false              # DEEP 单次可达数十秒，默认禁止工具选择
  timeout_s: 30                  # httpx 总超时（须低于 MCP 客户端工具超时）
  top_k_files: 5
  max_loops: 6
  max_token_budget: 32000
  allowed_roots:                 # PathGuard 白名单（同时是证据过滤边界）
    - ./data/documents
  denied_substrings:             # 在白名单内仍强制排除（fail-closed）
    - .env
    - .git
    - data/db
```

### 6.2 Sidecar `.env` 模板（独立 venv 侧，不入主仓库密钥）

```bash
# sirchmunk sidecar — 独立 venv，绑定本机回环 + Bearer
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
LLM_API_KEY=${ZAI_API_KEY}
LLM_MODEL_NAME=glm-5.3-flash
SIRCHMUNK_WORK_PATH=./data/db/sirchmunk
SIRCHMUNK_ENABLE_CLUSTER_REUSE=false   # 关闭 embedding 模型下载（4.1）
SIRCHMUNK_API_TOKEN=<生成一个随机 token，与主项目 agentic_search.api_token 一致>
LENS_HARD_TOKEN_BUDGET=true            # 超预算硬失败，防客户端断连后成本泄漏（4.7）
# GREP_* 按需覆盖（默认已满足 9.6 成本不变量，不改）
```

不新增存储路径（`data/db/sirchmunk/` 归 sidecar work_path）、不新增数据库表；主项目存储三路（Chroma/BM25/图片 SQLite）零改动。

---

## 7. 项目排期

> **排期原则**（延续 DEV_SPEC 第 6 章）：1 小时一个可验收增量；每步在文件系统产生可见变化；TDD 优先；外部依赖可 Mock。
>
> **任务前缀 S（Sirchmunk）**，避免与既有 A–I / V 编号冲突。**S0–S1 为承诺任务；S2–S3 为门控任务，仅在闸门通过后启动。**

### 阶段总览

1. **阶段 S0（P0，承诺）**：Sidecar 环境就绪 + 旁路对比，产出增益数据 → **闸门 G0**。
2. **阶段 S1（P1，承诺）**：适配层 + `agentic_search` MCP Tool + trace + 离线测试全绿 → **闸门 G1**。
3. **阶段 S2（P2，门控）**：HybridSearch 条件第三路（N 路 RRF + 触发策略 + 去重）。
4. **阶段 S3（P3，门控）**：评估 A/B（source 级指标）+ Dashboard 展示 + 文档收口。

> 依赖关系：S1 依赖 S0（G0 通过）；S2 依赖 S1（G1 通过）；S3 依赖 S2 零回归。

### 📊 进度跟踪表 (Progress Tracking)

> **状态说明**：`[ ]` 未开始 | `[~]` 进行中 | `[x]` 已完成

#### 阶段 S0：Sidecar 环境与旁路对比（P0，承诺）

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 |
|---------|---------|------|---------|---------|
| S0.1 | Sidecar 环境搭建 runbook + 启动脚本 | [~] | | 独立 venv（Python 3.11/3.12）+ 锁定 commit `b314e11` 安装成功；手动预装 `rg`/`rga`（preflight 检查脚本三连：`rg --version`/`rga --version`/`GET /api/v1/search/status` 200）；`.env` 模板落位（6.2）；`sirchmunk serve --host 127.0.0.1` 启动且 Bearer 鉴权生效（无 token 401）；脚本入 `scripts/sirchmunk/`，步骤写入 README 增补节 |
| S0.2 | Golden 问题旁路对比（5–10 条） | [~] | | 问题集覆盖三类：精确标识符（错误码/函数名）、非 PDF 格式（md/docx/xlsx）、多跳跨文档；每类 ≥2 条；curl 直调 `/api/v1/search`（response_format=context）记录 命中文件(source 级)/耗时/llm_call_count/total_llm_tokens；避开打招呼型措辞（4.6）；结果表回填本节下方；**闸门 G0：至少一类问题存在明确增益（命中率或证据质量可辨）才进入 S1**，否则止步于旁路方案并在文档记录结论 |

#### 阶段 S1：适配层与 MCP Tool（P1，承诺）

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 |
|---------|---------|------|---------|---------|
| S1.1 | `AgenticSearchConfig` 配置解析 | [x] | 2026-10-05 | `core/settings.py` 新增 dataclass + `_build_agentic_search()`；可选节缺失时默认 `enabled=false` 全默认值（向后兼容）；`mode∈{FAST,DEEP}`、正整数、URL 格式、`allowed_roots` 非空（enabled 时）校验 fail-fast；`config/settings.yaml` 补注释样例；单测含可选节缺省/非法值参数化 |
| S1.2 | `libs/agentic_search` Base + Factory + Fake | [x] | 2026-10-05 | `BaseAgenticSearch.search(request) -> AgenticSearchResponse` 抽象；`AgenticSearchResponse` dataclass（answer/evidences[{source_path,snippet,score,char_start,char_end}]/mode/llm_call_count/total_llm_tokens/elapsed_ms + to_dict）；`AgenticSearchFactory` 按 `backend` 路由；`FakeAgenticSearch` 确定性实现供下游测试；未注册 backend 报错风格对齐既有工厂 |
| S1.3 | PathGuard 白名单守卫 | [x] | 2026-10-05 | 调用前：realpath 归一、符号链接逃逸拒绝、`allowed_roots` 外拒绝、`denied_substrings` 强制排除（fail-closed，宁可误拒）；调用后：过滤 evidences 与 search_results 越权条目；参数化单测（越权/软链/`~` 展开/相对路径/空 roots） |
| S1.4 | `SirchmunkHttpBackend`（HTTP 后端） | [x] | 2026-10-05 | 同步 httpx（显式依赖登记 optional dev 组）；POST /api/v1/search + Bearer + `response_format=context` + 绝对路径；超时/连接拒绝/HTTP 5xx/非 JSON → 分型 `AgenticSearch*Error`；防御式解析：snippets dict|str 两形态、cluster=None、telemetry 缺失容忍；`httpx.MockTransport` 离线单测；契约 fixture（S0 真实响应脱敏落 `tests/fixtures/agentic/`）锁 shape |
| S1.5 | 证据 → RetrievalResult 转换 | [x] | 2026-10-05 | 确定性 ID `agentic:{sha1(source_path|start|end)[:12]}`（不与 C4 chunk_id 空间冲突）；metadata 契约：`retrieval_route="agentic"`/`source_path`/`char_start`/`char_end`/`agentic_confidence`（cluster.confidence 缺省 None）/`mode`；snippet 越短则保留原文不伪扩（扩上下文归 S2.4）；`page` 恒 None 的口径写入工具描述（4.6）；纯函数单测 |
| S1.6 | MCP Tool `agentic_search` | [x] | 2026-10-05 | schema：`query` 必填、`mode`（FAST/DEEP，DEEP 受 `allow_deep` 门控拒绝）、`paths`（可选，缺省用 `allowed_roots`；传入值全过 PathGuard，越权抛 ToolInvalidParams → -32602）、`top_k`；懒初始化（首调才建 backend）；TraceContext 记录 `agentic_search` 阶段（method/backend/mode/llm_call_count/total_llm_tokens/elapsed_ms + persist_trace）；输出复用 `ResponseBuilder`（Markdown 引用 + structuredContent.citations + answer 摘要）；sidecar 不可用 → `isError:true` 友好内容（资源缺失不误报 -32603，对齐 E5 语义）；`build_default_protocol_handler` 仅 `enabled` 时注册；单测覆盖校验矩阵/门控/降级/trace |
| S1.7 | 集成 + E2E + 文档收口 | [x] | 2026-10-05 | 集成：本地线程 HTTP server 模拟 sidecar，tools/list 两态注册断言、tools/call 全链路、-32602/isError 路由；E2E：子进程 MCP server（对齐 I1 手法）+ **stdout 纯净断言**（每行合法 JSON-RPC）；真实 sidecar 冒烟走 `RAG_AGENTIC_INTEGRATION=1` 门控默认跳过；README 增补「Agentic 检索（sidecar）」小节（启动/配置/工具说明/安全边界）；`pytest -q` 全套件离线全绿（新增跳过仅本门控）；**闸门 G1：用 S0 问题集经 MCP tool 复测并复记指标，据此决定是否启动 S2** |

> **进度备注（2026-10-05）**：S1 离线交付已完成，全套件离线全绿（1128 passed / 3 skipped，新增跳过仅 `RAG_AGENTIC_INTEGRATION` 门控）。S0.1 脚本与 S0.2 问题集/对比脚本已入库，但真实 sidecar 尚未启动（本机缺 `rga`），**闸门 G0 与 G1 均待真实 sidecar 运行 `scripts/sirchmunk/compare.py` 后回填**；S1 按用户指示先于 G0 实施，S2 仍须等 G1 结论。

#### 阶段 S2：HybridSearch 条件第三路（P2，门控：G1 通过后启动）

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 |
|---------|---------|------|---------|---------|
| S2.1 | `RRFFusion.fuse_routes`（N 路融合） | [ ] | | 新增 `fuse_routes(routes: dict[str, Sequence[RetrievalResult]])`；纯函数 `rrf_fuse_rankings` 数学零改动；payload 取首个出现路由（dense>sparse>agentic 优先级）；trace details 含各路计数；既有 `fuse(dense, sparse)` 行为与测试零改动；单测手工基准值 |
| S2.2 | `AgenticTrigger` 触发策略 | [ ] | | `tool_only / fallback / always` 三策略；fallback 判定信号与阈值**在独立校准集上标定**（cross-encoder 分数是未校准 logits，禁用 golden set 调参——上游 AGENTS.md 9.5 纪律同理适用）；rerank 后分数触发需二次融合+重排的成本写入配置注释；单测策略矩阵 |
| S2.3 | HybridSearch 可选第三路 | [ ] | | 注入可选 `agentic_retriever`；触发→第三路召回→`fuse_routes`→统一 rerank；失败降级泛化为 N 路（全失败才抛 HybridSearchError）；`last_agentic_results`/`routes.agentic` trace；`enabled=false` 或未触发时与基线行为逐字节一致（回归断言）；既有 18 集成用例零回归 |
| S2.4 | 去重与上下文扩展 | [ ] | | 后置去重：`source_path` + 文本重叠判定（Dense 命中同一文件时保留 chunk 级结果，agentic 证据仅补位）；agentic snippet 按 char 偏移向宿主文件扩展至 ≈`chunk_size`（Cross-Encoder 评分公平）；扩展失败（文件不可读/越权）保留原 snippet；单测覆盖重叠/不可读/越权 |
| S2.5 | `query.py --agentic` | [ ] | | `--agentic`（启用第三路）+ `--verbose` 打印 agentic 路中间结果（证据文件/Token/耗时）；退出码语义沿用 D7 矩阵；注入 fake 后端离线单测 |

#### 阶段 S3：评估 A/B 与 Dashboard（P3，门控：S2 零回归后启动）

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 |
|---------|---------|------|---------|---------|
| S3.1 | 评估 A/B（source 级指标） | [ ] | | `scripts/evaluate.py --retrieval-mode hybrid\|agentic\|hybrid+agentic`；**source 级** hit/MRR（基于 `expected_sources`）——agentic 确定性 ID 与 `expected_chunk_ids` 永不匹配（4.6），不得混入 chunk 级指标；报告含延迟与 Token 对比列；fake runner 离线单测 |
| S3.2 | Dashboard agentic 展示 | [ ] | | Query Traces 页渲染 `agentic_search` 阶段（模式/Token/调用次数/证据文件列表）；Overview 配置卡显示 agentic 开关与 backend；基于 trace `method` 动态渲染（未启用时自然隐藏）；AppTest 冒烟 |
| S3.3 | 文档收口与决策记录 | [ ] | | README/FAQ 更新；本文档进度表勾完 + A/B 数据结论写回第 1.1 节；FUTURE_OPTIMIZATION_ROADMAP 对应展望项（6.3 Agentic tools）标记落地状态；全套件离线全绿 |

---

## 8. 优点亮点与风险应对

### 8.1 功能与工程亮点

1. **零摄取即时检索**：新文档放入目录即可被搜到，弥补 SHA256 增量摄取的空窗期。
2. **格式覆盖立刻扩展**：MD/Word/Excel/PPT/代码/配置文件可检索（结构化解析仍归 MarkdownLoader）。
3. **精确匹配与多跳**：错误码/函数名/配置键 grep 天然强项；ReAct 跨文件迭代弥补单轮 Top-K 上限。
4. **双引擎检索架构**：Indexed RAG（低延迟确定性）+ Indexless Agentic（新鲜度深度推理），按查询特征路由。
5. **进程边界设计**：sidecar 隔离依赖/stdout/事件循环/超时，体现生产级故障域思维。
6. **成本感知路由**：默认毫秒级 Hybrid，门控升级 Agentic；Token 预算/超时/循环上限/硬预算失败四重护栏。
7. **评估驱动决策**：P0/G0/G1 三处数据闸门，杜绝「凭感觉引入重依赖」。
8. **安全边界完整**：PathGuard 双层防线 + 语料不可信假设 + 最小暴露面（单工具、双模式、无 FILENAME_ONLY）。

### 8.2 风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| 延迟高（DEEP 数十秒） | MCP 客户端超时、体验差 | 默认 FAST；`allow_deep=false` 门控；httpx `timeout_s=30` 硬超时 |
| 客户端超时后 sidecar 仍执行 | 成本泄漏 | `max_token_budget` + `max_loops` + `LENS_HARD_TOKEN_BUDGET=true`（4.7） |
| 单线程 stdio 阻塞 | agentic 调用期间其余 tool 排队 | 文档明示；FAST 优先；超时 < 客户端工具超时 |
| 非确定性输出 | 与离线确定性测试纪律冲突 | 全部 fake 注入；真实调用 `RAG_AGENTIC_INTEGRATION` 门控；契约 fixture 锁 shape |
| 任意文件读取 / 注入 | 语料 prompt injection 操纵 `file_read` | PathGuard 双层（4.4）；DEEP 建议容器内运行 |
| 上游 0.x 漂移 | 升级破坏契约 | 锁 commit `b314e11`；调用收敛在 `http_backend.py` 单文件；契约 fixture + 升级需重跑 S0 验收 |
| sidecar 依赖装不上（torch/kreuzberg 预发布） | 环境搭建受阻 | 独立 venv + Python 3.11/3.12；runbook 固化步骤；装不上时止步旁路方案 A（MCP 客户端直连 `sirchmunk mcp serve`），主项目零影响 |
| `work_path` 并发覆盖 | 知识簇缓存损坏 | 每进程独占；列入摄取/检索排除清单 |
| 证据无页码/行号 | 引用精度弱于主链路 | citations `page=None` 口径公开；字符偏移入 metadata；S2.4 扩上下文缓解 |

---

## 9. 参考

- 上游仓库：<https://github.com/modelscope/sirchmunk>（锁定 commit `b314e11`，`main` 分支）
- `AgenticSearch.search` 签名：`src/sirchmunk/search.py:2296`；构造副作用：`search.py:415-559`
- 返回结构：`src/sirchmunk/schema/search_context.py`、`src/sirchmunk/schema/knowledge.py`
- HTTP API：`src/sirchmunk/api/search.py`（`/api/v1/search`，Bearer 见 `api/main.py:97`）
- MCP 实际注册（仅 1 工具）：`src/sirchmunk_mcp/server.py:51-65`
- 本项目适配对接点：[src/mcp_server/server.py](src/mcp_server/server.py)、[src/mcp_server/tools/query_knowledge_hub.py](src/mcp_server/tools/query_knowledge_hub.py)、[src/core/query_engine/hybrid_search.py](src/core/query_engine/hybrid_search.py)、[src/core/query_engine/fusion.py](src/core/query_engine/fusion.py)
- 关联规格：[OBSIDIAN_RAG_SPEC.md](OBSIDIAN_RAG_SPEC.md)（多格式结构化解析归其 M1/M2）、[FUTURE_OPTIMIZATION_ROADMAP.md](FUTURE_OPTIMIZATION_ROADMAP.md)（6.3 Agentic RAG 展望）
