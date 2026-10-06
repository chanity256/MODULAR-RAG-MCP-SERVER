<!-- 扩展规格：Sirchmunk 无索引 Agentic 检索整合。格式与架构分层对齐 DEV_SPEC.md；基线为「阶段 A–I 已完成」状态（OBSIDIAN_RAG_SPEC V 系列为独立增量，互不阻塞）。 -->

# Sirchmunk Agentic 检索整合规格 (SIRCHMUNK_INTEGRATION_SPEC)

> 版本：0.2 — 由《整合可行性分析》升级为可执行规格（调研结论 + 整合注意事项 + 项目排期）
>
> 调研对象：[modelscope/sirchmunk](https://github.com/modelscope/sirchmunk)（`main` 分支，版本 `0.2.1+main`，Apache-2.0，Beta；**锁定 commit `b314e11`**）项目已经下载到本地 /Users/wanghaonan/Desktop/Project/Job-Search/sirchmunk 可直接使用其项目代码。
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
| 实施范围（已决策） | **P0–P1 为承诺任务**（S0/S1）；**P2/P3 为门控任务**（S2/S3）（**2026-10-06 最终裁决：G0 关闭**，见第 7 章「G0 重开复测与最终裁决」；后经显式指令重启并全部交付：**S2 已交付（S2.1–S2.5）、S3 已交付（离线框架与指标口径），真实 sidecar A/B 实测数据待门控运行回填**，见 S3 阶段进度备注）。 |
| 主要代价 | 延迟（FAST 规格预估 2–5s，S0 实测 4.6–13.6s/条（`deepseek-v4-flash`，见 7 章「S0 延迟分析与优化」）；DEEP 数十秒）、每次查询 2～N 次 LLM 调用、结果非确定性、sidecar 依赖重（torch / kreuzberg / duckdb / igraph / rg / rga）、0.x API 无 changelog 演进快。 |

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
| P4 | Dashboard 三模式在线检索 | S4 | 「在线检索」页：Normal/Fast/Deep 三模式分发，门控严格遵守 `agentic_search.enabled`/`allow_deep`，结果与引用归一化展示 |

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

- 请求（本项目发送）：`{"query": str, "paths": [abs_path, ...], "mode": "FAST"|"DEEP", "response_format": "context", "max_loops": int, "max_token_budget": int, "top_k_files": int, "include_patterns"/"exclude_patterns": [...]}`。
  - ⚠️ 过滤字段名必须是 `include_patterns` / `exclude_patterns`（上游 `api/search.py:84-91` `SearchRequest`）；FastAPI 对未知字段**静默丢弃不报错**，写成 `include`/`exclude` 会导致过滤完全不生效。上游将其转为 rg/rga 的 `-g '<glob>'` / `-g '!<glob>'`（`retrieve/text_retriever.py:834-840`），glob 相对检索根匹配。
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

S4 追加（Dashboard 在线检索，P4）：

| 模块 | 文件 | 改动 |
|------|------|------|
| 服务层（新增） | `src/observability/dashboard/services/search_service.py` | `SearchService`：`availability(settings)` 返回各模式的可用状态与原因；`run(mode, query, top_k, collection)` 分发至 `QueryKnowledgeHubTool`（normal）或 `AgenticSearchTool`（fast/deep），结果归一化为 `SearchOutcome`（mode/markdown/citations/agentic_meta/is_error/error/elapsed_ms）；工具实例懒构建并会话内缓存 |
| 页面（新增） | `src/observability/dashboard/pages/query_playground.py` | 「在线检索」页 UI：模式按钮组（默认 Normal）、置灰门控与 help 提示、collection 仅 Normal 显示、按模式渲染结果区、`st.error` 友好错误展示；`render(*, settings=None, search_service=None)` 支持注入 |
| 页面注册 | `src/observability/dashboard/app.py` | 导航新增分组「检索」，注册「在线检索」页 |
| 测试（新增/增量） | `tests/unit/test_dashboard_search_service.py`、`tests/e2e/test_dashboard_smoke.py` | 单测用 Fake 后端与 fake HybridSearch 离线覆盖；e2e 冒烟沿用 `AppTest.from_file` driver |

> **S4 接口冻结（实现前置）**：先在本节冻结 `SearchOutcome` 与 `SearchService` 接口，随后 S4.1（服务层）与 S4.2（页面 UI，基于 fake service）可并行开发，S4.3 单独收口。不改动检索核心（`core/`、`mcp_server/`、`libs/`），不新增依赖，默认配置不变（`agentic_search.enabled: false`，默认仅 Normal 可用）。

无需改动：Ingestion 全链路、Chroma/BM25/Image 存储、既有 3 个 MCP Tool 契约、CitationGenerator（已读 `metadata["source_path"]`，直接工作）。

> **2026-10-06 勘误**：「Chroma/BM25/Image 存储零改动」此后不再严格成立——主项目 eval 缺口 G1–G3 修复中，`libs/vector_store/chroma_store.py`（upsert 写入侧 metadata 净化：dict/混合列表 → JSON 字符串、None 值丢弃）与 `core/types.py`（`parse_image_refs` 读取侧兼容 JSON 字符串）确有一次修改。该修复与 agentic 整合无关（Chroma 只接受标量 metadata，`PdfLoader` 的 `images` dict 列表导致 4 个含图 PDF store 失败），故不违背「整合不要求改存储」的原判断，仅作记录。

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
- **决策**：sidecar 拥有独立 `LLM_*` 环境变量，不与 `settings.llm` 耦合；两套配置在文档中明确标注职责差异。~~推荐 ZAI OpenAI 兼容端点，与本项目 embedding/vision 同源~~（**2026-10-06 修订**：推荐 DeepSeek `deepseek-v4-flash`，理由见 4.5.1）。

#### 4.5.1 LLM 选型：思考模式决定 FAST 延迟（2026-10-06 实证）

- FAST 每次检索固定 2 次 LLM 调用（查询分析 + 回答），rg/rga 扫描仅 0.03–1.4s，**延迟几乎完全由 LLM 决定**（分段数据见 7 章「S0 延迟分析与优化」）。
- 上游 `OpenAIChat` 只在 provider profile 声明 `thinking_param` 时注入思考开关（`openai_chat.py:279-295`），默认 `enable_thinking=False`：
  - `deepseek` profile（按 base_url 含 `deepseek.com` 识别）对 `deepseek-v4-*` 发送 `extra_body={"thinking": {"type": "disabled"}}`（已在 sidecar venv 内用 `_build_request_kwargs` 验证）；
  - `zhipu` profile **无** `thinking_param`，上游不发送任何思考参数。
- `glm-5.3-flash` **强制思考**：`thinking.type=disabled` 返回错误码 1210（「该模型始终思考，不支持关闭思考」），只能用 `reasoning_effort=low/high/max` 调节，而 sidecar 无法传入该参数。同一小 prompt：默认 8.96s / 285 reasoning tokens，`reasoning_effort=low` 1.5s。
- **推荐配置**：`LLM_BASE_URL=https://api.deepseek.com`、`LLM_MODEL_NAME=deepseek-v4-flash`。直连探测（小 prompt）：`thinking: disabled` 0.85–1.02s、0 reasoning tokens；不传思考参数 1.51s、199 reasoning tokens——确认关闭思考生效。API 返回的 `model` 字段为别名 `deepseek-flash`，不影响调用。
- 不推荐：`glm-5.3-flash`（上述原因）；`glm-4.5-*` 等默认思考的 GLM 型号同样无法经 sidecar 关闭思考。

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
# LLM：推荐 DeepSeek（上游自动关闭 deepseek-v4-* 思考）；勿用强制思考的 glm-5.3-flash（4.5.1）
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=<DeepSeek API Key>
LLM_MODEL_NAME=deepseek-v4-flash
SIRCHMUNK_WORK_PATH=./data/db/sirchmunk
SIRCHMUNK_ENABLE_CLUSTER_REUSE=false   # 关闭 embedding 模型下载（4.1）
SIRCHMUNK_API_TOKEN=<生成一个随机 token，与主项目 agentic_search.api_token 一致>
LENS_HARD_TOKEN_BUDGET=true            # 超预算硬失败，防客户端断连后成本泄漏（4.7）
# GREP_* 按需覆盖（默认已满足 9.6 成本不变量，不改）
```

不新增存储路径（`data/db/sirchmunk/` 归 sidecar work_path）、不新增数据库表；主项目存储三路（Chroma/BM25/图片 SQLite）零改动（**2026-10-06 勘误**：Chroma store 因 G1–G3 修复有过一次 metadata 净化改动，见 3.3；与 agentic 整合无关）。

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
5. **阶段 S4（P4）**：Dashboard 三模式在线检索（「在线检索」页 + SearchService 分发 + 门控 + 冒烟测试）。

> 依赖关系：S1 依赖 S0（G0 通过）；S2 依赖 S1（G1 通过）；S3 依赖 S2 零回归；S4 依赖 S1（两个 Tool 均已交付）与 S3.2（Query 追踪页已支持渲染 agentic 阶段），S2/S3 已交付后启动。

### 📊 进度跟踪表 (Progress Tracking)

> **状态说明**：`[ ]` 未开始 | `[~]` 进行中 | `[x]` 已完成

#### 阶段 S0：Sidecar 环境与旁路对比（P0，承诺）

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 |
|---------|---------|------|---------|---------|
| S0.1 | Sidecar 环境搭建 runbook + 启动脚本 | [x] | 2026-10-05 | 独立 venv（Python 3.11/3.12）+ 锁定 commit `b314e11` 安装成功；手动预装 `rg`/`rga`（preflight 检查脚本三连：`rg --version`/`rga --version`/`GET /api/v1/search/status` 200）；`.env` 模板落位（6.2）；`sirchmunk serve --host 127.0.0.1` 启动且 Bearer 鉴权生效（无 token 401）；脚本入 `scripts/sirchmunk/`，步骤写入 README 增补节 |
| S0.2 | Golden 问题旁路对比（5–10 条） | [x] | 2026-10-05 | 问题集覆盖三类：精确标识符（错误码/函数名）、非 PDF 格式（md/docx/xlsx）、多跳跨文档；每类 ≥2 条；curl 直调 `/api/v1/search`（response_format=context）记录 命中文件(source 级)/耗时/llm_call_count/total_llm_tokens；避开打招呼型措辞（4.6）；结果表回填本节下方；**闸门 G0：至少一类问题存在明确增益（命中率或证据质量可辨）才进入 S1**，否则止步于旁路方案并在文档记录结论 |

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

#### S0 执行记录（2026-10-05，sidecar `b314e11`，mode=FAST，检索根=仓库根）

**环境落地（S0.1）**：`brew install ripgrep-all`（rga 0.10.10）→ `setup_venv.sh`（Python 3.12，`sirchmunk 0.2.1+main` 导入验证通过）→ `.env-sirchmunk` 生成（gitignored）→ sidecar 启动成功 → preflight 三连全绿。过程中发现的偏差：

1. 上游未声明 `python-multipart` 依赖，FastAPI 文件上传路由 import 即崩（已手动补装 sidecar venv；后续已固化进 `setup_venv.sh`，见下方「S0 遗留问题修复」）；
2. `preflight.sh` 健康检查原本不带 Bearer，`/api/` 强制鉴权下必然失败（已修复为读 `.env-sirchmunk` 携带 token）；
3. 无 token 实测返回 **500 而非 401**：上游在 `auth_middleware`（BaseHTTPMiddleware）内抛 `HTTPException` 不被转成 401 响应——鉴权仍然生效（拒绝访问），仅状态码口径与验收标准不符，不改上游源码、记录偏差；
4. `compare.py` 原以 `data/documents`（空目录）为检索根，与 golden 问题集（期望来源在 `src/` 与根文档）不匹配（已修正为仓库根并排除 `tests/fixtures/**`；但排除字段名当时写错，第 2 轮实际未排除，见下方「S0 遗留问题修复」）。

**旁路对比（S0.2，6 条 golden 问题，2 轮运行）**：

| id | 类别 | 第1轮命中（未排除fixtures） | 第2轮命中（干净口径） | 耗时 ms | LLM 调用 | Token |
|----|------|------|------|---------|----------|-------|
| Q1 | identifier | ❌ | ❌ | 175,347 | 2 | 30,760 |
| Q2 | identifier | ✅ | ❌ | 256,110 | 2 | 42,219 |
| Q3 | format | ❌ | ❌ | 100,056 | 2 | 39,634 |
| Q4 | format | ✅ | ✅ | 120,925 | 2 | 40,011 |
| Q5 | multihop | ❌ | ❌ | 158,439 | 2 | 40,292 |
| Q6 | multihop | ❌ | ❌ | 189,785 | 2 | 6,514 |

分类汇总（第 2 轮干净口径）：identifier 0/2，format 1/2，multihop 0/2。

**闸门 G0 结论：未通过。**依据：
- 干净口径命中率 1/6；两轮结果非确定性翻转（Q2 第 1 轮命中、第 2 轮未命中），无可辨稳定增益；
- FAST 实测延迟 77–256s/条，远超规格预估的 2–5s（~~全仓首扫 + 真实 LLM 延迟未计入原估计~~；**勘误（2026-10-06）**：分段日志显示 rg/rga 扫描仅 0.07–1.36s，延迟几乎全部来自两次 LLM 调用——`glm-5.3-flash` 强制思考 + 流式断连重试，与检索根大小无关，见「S0 延迟分析与优化」）；
- Token 成本 6.5k–42k/条（glm-5.3-flash）；Q6 第 2 轮仍把问题集文件列为首个证据——~~上游 exclude 语义未完全生效，语料污染无法从调用方根除~~（**勘误**：根因是 `compare.py` 字段名写错，排除规则从未送达上游，属本项目 bug，已修复，见下方「S0 遗留问题修复」；因此第 2 轮并非真正的干净口径）；
- 唯一命中的 Q4（format 类）问题主链路经 MarkdownLoader 扩展后本可覆盖，不构成引入重依赖 sidecar 的决定性理由。

**处置**：按 G0 约定**止步于旁路方案**——`agentic_search` MCP 工具（S1 已交付）保持 `enabled: false` 默认关闭，**S2/S3 不启动**；sidecar 环境与脚本保留，供后续以 DEEP 模式、更换语料或校准 exclude 后重测（重测即重开 G0）。

#### S0 遗留问题修复（2026-10-05）

**修复原则**：全部在本项目调用方/环境脚本侧修复，**不迁移、不修改 sirchmunk 源码**。理由：①问题根因均不在上游代码；②进程内嵌入会重新引入 4.1–4.3 的构造期副作用、stdout 污染与事件循环绑定问题，违背 3.1 的 sidecar 决策与 2.2 的 Out of Scope 约定；③上游依赖重且 0.x 演进快，迁移等于自维护分叉。

| # | 问题 | 根因归属 | 处置 | 验证 |
|---|------|---------|------|------|
| F1 | 排除 `tests/fixtures/**` 不生效，Q6 引用问题集文件（泄题） | **本项目 bug**：`scripts/sirchmunk/compare.py` 传 `"exclude"`，上游 `SearchRequest` 字段为 `exclude_patterns`，FastAPI 静默丢弃未知字段 | 改为 `"exclude_patterns": ["tests/fixtures/**"]` | ①直跑 `rg -l -g '!tests/fixtures/**' <仓库根>`，fixtures 文件全部消失（`rga` 同）；②真实 sidecar 单跑 Q6（FAST）：**命中**，首个证据 `src/libs/loader/pdf_loader.py`，sidecar 日志中证据文件仅 `pdf_loader.py`、`DEV_SPEC.md`，无 `tests/fixtures/*`；~~耗时 473,461 ms~~（**作废**：运行期间 Mac 进入 Idle Sleep，`pmset -g log` 22:52 起多次 Sleep/DarkWake 已确认；sidecar 日志时间戳跨度 1,940s 而 `perf_counter` 仅计 473s，两者均不代表真实处理耗时），LLM 调用 2 次，Token 38,649；命中结论仍有效 |
| F2 | sidecar 启动崩溃（缺 `python-multipart`） | 上游未声明依赖 | `setup_venv.sh` 在安装 sirchmunk 后显式 `pip install python-multipart`（不改上游 `pyproject`） | `bash -n` 语法检查通过；现有 venv 已含该包，sidecar 启动 + preflight 三连全绿；未从零重建 venv 实测 |
| F3 | 无 token 返回 500 而非 401 | 上游 `auth_middleware` 写法 | **不修复，记录偏差**：拒绝访问本身生效；本项目 `http_backend.py` 对任何非 2xx 一律映射为 `AgenticSearchBackendError`，不受状态码口径影响 | — |
| F4 | FAST 延迟 77–256 s/条（~~77–473~~，473s 因系统睡眠作废） | ~~模型速度 + 全仓扫描~~ **勘误（2026-10-06）**：①`glm-5.3-flash` 强制思考且上游 `zhipu` profile 无法关闭（4.5.1）；②流式中途断连（12 次检索中 7 次遇断连/SDK 重试），每次整轮重来；rg 扫描仅 0.07–1.36s，与检索根无关 | 换供应商 DeepSeek `deepseek-v4-flash`（上游自动 `thinking: disabled`），仅改 `.env-sirchmunk`，不改上游源码 | 6 题复测 4.6–13.6s/条、0 断连，见「S0 延迟分析与优化」 |
| F5 | 结果非确定性、样本仅 6 题 | 测试方法学 | 属重开 G0 的方法学改进（每类 ≥5 题、多轮运行、裸 rg 基准线对照） | — |

**生产链路核查**：`src/libs/agentic_search/http_backend.py:123,125` 已正确使用 `include_patterns` / `exclude_patterns`，`agentic_search` MCP 工具不受 F1 影响。

**对 G0 结论的影响**：F1 修复使第 2 轮「干净口径」失效（当时排除并未生效），G0 的命中率数据需在修复后的口径下重测才有效；但单题复测 1 次不足以重开 G0，~~且延迟（F4）进一步恶化~~（**勘误**：473s 系系统睡眠所致，不代表延迟恶化）。**G0 结论维持「未通过」**，`agentic_search` 保持 `enabled: false`，S2/S3 不启动；重开 G0 的前置条件为 F4/F5 的决策与重测预算确认。

> **进度备注（2026-10-05，S0 收口后更新）**：S0 侧车环境与旁路对比已在真实 sidecar 上完成（见上方执行记录），**闸门 G0 未通过**，S2/S3 维持不启动。S1 离线交付保持已完成状态（1128 passed / 3 skipped）；`agentic_search` 工具保留为可选能力（`enabled: false`），其真实 sidecar 冒烟门控 `RAG_AGENTIC_INTEGRATION` 继续默认跳过。G1 复测因 G0 未通过而无执行意义，一并搁置。S0 遗留问题已在调用方侧修复（F1 exclude 字段名、F2 python-multipart），G0 结论维持不变。
>
> **进度备注（2026-10-06，延迟分析与换供应商后更新）**：F4 延迟根因已定位并解决（换 DeepSeek，FAST 实测 4.6–13.6s/条，落入 `timeout_s=30`），但 G0 按原标准复评**仍未通过**（命中率 2/6 且跨轮翻转），`agentic_search` 维持 `enabled: false`，S2/S3 不启动。详见下方「S0 延迟分析与优化」。

#### S0 延迟分析与优化（2026-10-06）

**方法**：新增 `scripts/sirchmunk/latency_report.py`，解析 sidecar stdout 日志，按 `[FAST] Starting → Step1 → Step3 → Search complete` 时间戳把每次检索拆为「查询分析 LLM / grep（rg+rga 扫描与证据抽取）/ 回答 LLM」三段，并统计 LLM HTTP 请求数、流式断连（`Connection error`）与 openai SDK 重试次数。该脚本已用 2026-10-05 的 12 次检索日志验证，输出与逐条人工对账一致。

**12 次干净检索分段（glm-5.3-flash，2026-10-05 日志，两次遍历 6 题）**：

| # | 查询分析 LLM s | grep s | 回答 LLM s | 合计 s | 证据字符 | LLM 请求 | 断连 | SDK 重试 |
|---|----------------|--------|------------|--------|----------|----------|------|----------|
| 1 | 76.90 | 1.36 | 85.27 | 163.54 | 1,850 | 2 | 0 | 0 |
| 2 | 97.59 | 0.08 | 23.11 | 120.77 | 26,000 | 2 | 0 | 1 |
| 3 | 32.31 | 0.09 | 46.56 | 78.96 | 40,021 | 2 | 0 | 0 |
| 4 | 32.34 | 0.07 | 94.23 | 126.64 | 40,028 | 3 | 1 | 0 |
| 5 | 47.83 | 0.12 | 66.54 | 114.48 | 40,007 | 3 | 1 | 0 |
| 6 | 32.11 | 0.09 | 44.80 | 77.01 | 1,850 | 2 | 0 | 0 |
| 7 | 90.37 | 0.07 | 84.89 | 175.33 | 40,021 | 2 | 0 | 0 |
| 8 | 219.60 | 0.07 | 36.42 | 256.10 | 40,000 | 2 | 0 | 1 |
| 9 | 27.21 | 0.08 | 72.77 | 100.05 | 40,021 | 3 | 1 | 0 |
| 10 | 23.63 | 0.09 | 97.20 | 120.92 | 40,028 | 3 | 1 | 0 |
| 11 | 88.34 | 0.08 | 70.02 | 158.43 | 40,007 | 2 | 0 | 0 |
| 12 | 36.26 | 0.07 | 153.44 | 189.78 | 1,850 | 4 | 2 | 0 |

结论：rg/rga 扫描仅 0.07–1.36s，**延迟几乎全部来自两次 LLM 调用**（23–220s/次）；12 次中 7 次遇断连或 SDK 重试，每次断连整轮调用重来（含已产生的 reasoning 全部重算）。

**GLM 探测结论（直连 `open.bigmodel.cn`，同一小 prompt）**：

- `glm-5.3-flash`：`thinking.type=disabled` 返回错误码 1210（强制思考，不可关闭）；默认 8.96s / 285 reasoning tokens；`reasoning_effort=low` 1.5s / 10 tokens——但上游 `zhipu` profile 无 `thinking_param`，sidecar 无法传入该参数。
- `glm-4-flash` 3.16s（设计上非思考模型）；`glm-4.5-flash` / `glm-4.5-air` 1.0–1.4s 但默认思考，上游同样无法关闭。
- 换 GLM 型号只能缓解不能根治（`glm-4-flash` 3.2s 且为老模型），故不采用。

**Q6 473s 作废说明**：2026-10-05 晚 F1 验证跑出的 473s 不可信——`pmset -g log` 确认 22:52 起多次 Idle Sleep / Maintenance Sleep / DarkWake；sidecar 日志时间戳跨度 1,940s 而 `perf_counter` 仅计 473s（macOS 睡眠期间 `mach_absolute_time` 暂停），两者均不代表真实处理耗时。为此 `compare.py` 已增加墙钟对照：`time.time()` 与 `perf_counter` 差值 >5s 的条目标记「睡眠疑似」，复测统一用 `caffeinate -i` 包住运行。

**决策：换供应商 DeepSeek `deepseek-v4-flash`**。上游 `deepseek` profile 对 `deepseek-v4-*` 自动发送 `extra_body={"thinking": {"type": "disabled"}}`（sidecar venv 内 `_build_request_kwargs` 验证），**零代码改动**，仅改 `.env-sirchmunk`。直连探测：`thinking: disabled` 0.85–1.02s / 0 reasoning tokens；不传思考参数 1.51s / 199 reasoning tokens，确认开关生效。

**6 题复测（2026-10-06，`caffeinate -i` 下 FAST，`exclude_patterns` 生效，sidecar `b314e11`）**：

| id | 类别 | 命中 | 耗时 ms | 查询分析/grep/回答 s | Token | 首个证据文件 |
|----|------|------|---------|----------------------|-------|--------------|
| Q1 | identifier | ✅ | 9,422 | 1.16 / 0.12 / 8.13 | 28,614 | `src/ingestion/storage/bm25_indexer.py` |
| Q2 | identifier | ❌ | 4,632 | 1.04 / 0.03 / 3.56 | 16,553 | `DEV_SPEC.md` |
| Q3 | format | ❌ | 6,688 | 1.20 / 0.05 / 5.44 | 37,938 | `FUTURE_OPTIMIZATION_ROADMAP.md` |
| Q4 | format | ✅ | 6,438 | 1.18 / 0.03 / 5.22 | 37,555 | `FUTURE_OPTIMIZATION_ROADMAP.md` |
| Q5 | multihop | ❌ | 9,448 | 3.24 / 0.03 / 6.17 | 36,180 | `DEV_SPEC.md` |
| Q6 | multihop | ❌ | 13,617 | 1.40 / 0.09 / 12.12 | 38,379 | `DEV_SPEC.md` |

断连 0 次、SDK 重试 0 次；每条固定 2 次 LLM 调用；合计耗时 4.6–13.6s/条。分类命中：identifier 1/2，format 1/2，multihop 0/2。

**闸门 G0 复评（按原标准，如实记录）**：**仍未通过。**依据：

- 延迟障碍已解除（77–256s → 4.6–13.6s，落入 `timeout_s=30`），但 G0 的通过标准是「至少一类问题存在明确增益」，本次命中率 2/6，无一类达到 2/2；
- 跨轮非确定性依旧：与 2026-10-05 两轮对照，Q1 ❌❌→✅、Q2 ✅❌→❌、Q6 ❌❌→❌（三空），Q4 三轮稳定命中但 format 类主链路本可覆盖（原判断不变）；multihop 三轮 0/6，多跳能力未见兑现；
- Token 成本 16.5k–38.4k/条，与 GLM 时期相当。

**处置**：`agentic_search` 维持 `enabled: false`，S2/S3 不启动。剩余障碍从延迟（F4，已解决）转移为**命中质量与非确定性（F5）**；重开 G0 的前置条件为 F5 的方法学改进（每类 ≥5 题、多轮运行、裸 rg 基线对照）及复测预算确认。

**`timeout_s` 判定与建议**：FAST 实测上限 13.6s（DeepSeek、40k 字符证据上限下），`timeout_s=30` 保留 ≈2.2× 余量，**建议维持 30 不变**（无证据支持收紧或放宽）。补充口径：①FAST 模式上游不绑定 `max_token_budget`（预算绑定仅发生在 DEEP 分支，`search.py:2450-2462`），故 16–38k token/条的 FAST 消耗不会触发 `LLMTokenBudgetExceeded`，其单条成本上限实际由 40k 字符证据上限与 2 次固定 LLM 调用决定；②DEEP 模式（数十秒）本就被 `allow_deep=false` 门控，`timeout_s=30` 对 DEEP 不适用属预期行为。

**G0 未通过机制分析（2026-10-06 补充取证，针对命中质量）**：

1. **FAST 单文件结构是 multihop 0/6 的根因（结构性，非偶发）**：FAST 每次检索只选**一个** best file 构建证据（≤40k 字符），而 Q5/Q6 的期望来源各为 3 个 `src/` 文件，best file 选错即必挂——两题日志均显示选中 `DEV_SPEC.md`。跨文件迭代本就是 DEEP/ReAct 的设计目标（`allow_deep=false` 门控中）。
2. **词法文件选择：提及关键词的文档击败实现文件**：Q2 期望 `protocol_handler.py`（`-32602` 定义于该文件，已核实 `:34`），但选择器按词法得分选中 `DEV_SPEC.md`（文档措辞与问题关键词更贴近）。只有**字面标识符**能让 grep 直达真文件——Q1 命中正是 grep fallback 路径（`bm25_indexer.py` 15 hits）。这解释了 identifier 类「应强却 1/2」。
3. **问题集自身存在坏题**：Q3 问「README 中摄取 CLI 的验收标准」，核实 README 无任何 `acceptance/验收` 字样——期望来源在语料中不存在，属 golden 集缺陷（F5 实例），format 类被拖为 1/2。
4. **非确定性机制**：LLM 关键词抽取逐轮不同（三轮 Primary/Fallback 关键词列表均不同）→ 文件打分随之漂移 → 命中翻转（Q1 ❌❌→✅、Q2 ✅❌→❌）。翻转源于此，而非网络抖动。

**重开 G0 的候选路径（按性价比排序）**：①先修问题集——逐题核实 `expected_sources` 真实存在于语料（剔除/修正 Q3 式坏题），零成本；②裸 rg 基线对照（F5 原计划）——Q1 类标识符题 rg 秒级可中，若基线能到 3–4/6，sidecar 增益论证不成立，这是 G0 真正该比的对象；③DEEP 模式单跑 Q5/Q6 各一次，验证多跳是否兑现（每题数十秒 + token，可作决策依据）；④①–③ 后仍无明确增益，则维持 `enabled: false` 收口，等真实语料场景再评估。

**G0 重开复测与最终裁决（2026-10-06，候选路径 ①–④ 全部执行完毕）**：

① 修问题集（commit `b3aadde`）：Q3 原题问「README 运行手册中摄取 CLI 的验收标准」，核实 README 无任何 acceptance/验收内容，属永不可命中的坏题；重写为 README 运行手册中真实存在的 Dashboard 启动命令考点（保持 format 类考察 Markdown 检索的初衷）。其余 5 题 expected_sources 逐题核实全部真实（Q1 `bm25_indexer.py:70` k1/b 默认值、Q2 `protocol_handler.py:34`、Q4 README 工具表、Q5/Q6 三个文件路径与数据流均存在）。

② 裸 rg 基线（commit `b151801`，`scripts/sirchmunk/rg_baseline.py`）：确定性分词（无 LLM）+ `rg -il`，与 compare.py 同口径排除 `tests/fixtures/**`，source 级命中（≥50% expected_sources 出现在 rg 文件列表）。结果：**6/6 全中**（identifier 2/2、format 2/2、multihop 2/2），每题 8–20 ms、零成本。方法学注脚：OR 关键词较宽泛，但这正是「词法下界」该有的姿态。结果存 `scripts/sirchmunk/rg_baseline_results.json`。

③ DEEP 单跑 Q5/Q6（commit `828df67`，sidecar `b314e11`，DeepSeek，exclude 生效，caffeinate 下无睡眠）：

| 题 | 命中 | 耗时 | LLM 调用 | Token | 说明 |
|----|------|------|----------|-------|------|
| Q5 | ✅（multihop 首次命中） | 12.8 s | 6 | 66,421 | 多跳能力确实存在，但 token ≈ FAST 的 2 倍 |
| Q6 | ❌ | 12.1 s | 0 | 0 | 走 DocQA 直析文档路径，见下两条 |

DEEP 补充发现：Q6 为**「答对引错」**——答案内容完整梳理了图片引用流，但引用 DEV_SPEC.md 等文档而非期望的 3 个 `src/` 实现文件，source 级 <50% 判负；且 **DocQA 支路记账异常**——telemetry 记 0 次 LLM 调用 / 0 token，该路径的 LLM 消耗未进 `total_llm_tokens`，凡涉成本口径的对比均须对此打折扣。

三方对照：

| 方法 | 命中（source 级） | 单条成本 | 单条延迟 |
|------|------------------|----------|----------|
| 裸 rg 基线（无 LLM） | **6/6** | 零 | 8–20 ms |
| FAST（deepseek-v4-flash） | 2/6 | 16–38k token | 4.6–13.6 s |
| DEEP（Q5/Q6 单跑） | multihop 1/2 | 66k token（Q5） | 12.8 s |

④ **最终裁决：G0 关闭（不通过，不再重测）。**依据：免费、确定性、毫秒级的裸 rg 在同一问题集上 6/6，严格优于两条 LLM 路径——sidecar 的增益论证在本语料上不成立。处置：

- `agentic_search` 维持 `enabled: false` 为永久默认（sidecar 环境与 `scripts/sirchmunk/` 脚本保留，含 rg 基线供后续语料复用）；
- S2/S3 由「门控未启动」正式改为**不启动**；
- **重开条件**（取代 F5 方法学改进）：真实业务语料（非本仓库自指语料）+ 存在 rg 基线跑不过的场景（如跨格式语义多跳、对非文本内容的自然语言提问），且复测必须以 rg 基线为必列对照项。




#### 阶段 S2：HybridSearch 条件第三路（P2，门控：G1 通过后启动）

> **进度备注（2026-10-06，显式重启）**：G0 终裁本为「S2/S3 不启动」；应用户显式指令重启 **S2**（第三路工程能力落地，`enabled=false` 默认关闭的口径不变），**S3 暂缓**（评估 A/B 与 Dashboard 展示待 S2 验收后再议）。S2 已全部完成（S2.1–S2.5，2026-10-06），全套件 1226 passed / 3 skipped 离线全绿。注意：重启不推翻 G0 结论——agentic 路默认仍关闭，S2 交付的是「可启用」的完整第三路链路，是否启用仍由真实语料场景决定。

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 |
|---------|---------|------|---------|---------|
| S2.1 | `RRFFusion.fuse_routes`（N 路融合） | [x] | 2026-10-06 | 新增 `fuse_routes(routes: dict[str, Sequence[RetrievalResult]])`；纯函数 `rrf_fuse_rankings` 数学零改动；payload 取首个出现路由（dense>sparse>agentic 优先级，`ROUTE_PRIORITY` 常量、与 dict 传入顺序无关）；trace details 含各路计数（`routes: {...}`）；既有 `fuse(dense, sparse)` 改为委托 `fuse_routes` 且行为与测试零改动（trace 旧平铺形态由 fuse 本地记录保持）；单测手工基准值（+14 测试） |
| S2.2 | `AgenticTrigger` 触发策略 | [x] | 2026-10-06 | `tool_only / fallback / always` 三策略（新增 `agentic_trigger.py`，返回 `TriggerDecision(triggered, reason)`，非法 fail-fast）；fallback 判定信号与阈值**在独立校准集上标定**（cross-encoder 分数是未校准 logits，禁用 golden set 调参——上游 AGENTS.md 9.5 纪律同理适用；默认 `trigger_score_threshold=0.5` 为未校准占位，模块 docstring/settings.py/settings.yaml 三处标注）；rerank 后分数触发需二次融合+重排的成本写入配置注释；单测策略矩阵（+35 测试） |
| S2.3 | HybridSearch 可选第三路 | [x] | 2026-10-06 | 注入可选 `agentic_retriever`（鸭子类型 `retrieve(query, top_k, trace=...)`；`BaseAgenticSearch` 经 converter 包装后注入，HybridSearch 不 import libs/agentic_search）；触发→第三路召回→`fuse_routes`→统一 rerank（rerank 在上层 `query_knowledge_hub`，第三路结果自动进同一管线）；失败降级泛化为 N 路（dense+sparse 全失败仍抛 HybridSearchError）；`last_agentic_results`/`last_trigger_decision`/`routes.agentic` trace；`enabled=false` 或未触发时与基线行为逐字节一致（trace stages 剥离 elapsed_ms 后全列表相等断言）；既有 hybrid 集成用例 21/21 零回归（+12 测试） |
| S2.4 | 去重与上下文扩展 | [x] | 2026-10-06 | 后置去重（新增 `agentic_dedup.py`）：`source_path` + 文本重叠判定（重叠率 ≥0.8 判重，`SequenceMatcher` 最长匹配块覆盖比，常量注释）；Dense/Sparse chunk 级结果永不丢弃，agentic 证据仅补位；agentic snippet 按 char 偏移向宿主文件以 snippet 为中心扩展至 ≈`chunk_size`（Cross-Encoder 评分公平；metadata 追加 `context_expanded`/`expanded_char_start`/`expanded_char_end`，chunk_id 不变）；扩展失败（文件不可读/symlink 逃逸/白名单外/`denied_substrings`）经 PathGuard fail-closed 保留原 snippet 不抛错；仅第三路触发且存活时才有开销，未触发路径零改动（+21 测试） |
| S2.5 | `query.py --agentic` | [x] | 2026-10-06 | `--agentic`（启用第三路：`AgenticRouteAdapter` 把 `BaseAgenticSearch` 包装成 retrieve 形态注入 HybridSearch）+ `--verbose` 打印 agentic 路中间结果（触发状态/证据文件/Token/耗时，Token 取 search 内部真正执行的适配器）；`enabled=false` 时退出码 2 并提示改 `config/settings.yaml`；退出码语义沿用 D7 矩阵（0/1/2，无新增）；注入 fake 后端离线单测（+8 测试）；未传 `--agentic` 时行为与基线一致 |

#### 阶段 S3：评估 A/B 与 Dashboard（P3，门控：S2 零回归后启动）

> **进度备注（2026-10-06，S3 完成与 A/B 数据结论）**：S3.1–S3.3 全部完成，全套件离线全绿（见 S3.3 行）。**A/B 数据结论：离线评估框架与指标口径（source 级 hit/MRR、延迟/Token 对比列）已交付；但真实 sidecar A/B 实验未执行——受 `RAG_AGENTIC_INTEGRATION` 门控与 G0 终裁约束（本仓库自指语料上裸 rg 6/6 严格优于 sidecar，agentic 路默认关闭），无实测数据可回填，本文档不记录任何编造数据。** `--retrieval-mode agentic/hybrid+agentic` 是否产生有效结果取决于真实 sidecar 是否启用，该决策由真实语料场景决定（重开 G0 即重测）。

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 |
|---------|---------|------|---------|---------|
| S3.1 | 评估 A/B（source 级指标） | [x] | 2026-10-06 | `scripts/evaluate.py --retrieval-mode hybrid\|agentic\|hybrid+agentic`（`hybrid` 为默认基线，行为零回归）；**source 级** hit/MRR（基于 `expected_sources`）与 chunk 级指标并列不混算（agentic 确定性 ID 与 `expected_chunk_ids` 永不匹配）；报告含延迟与 Token 对比列；另有 `--agentic-trigger` 可覆盖 `settings.agentic_search.trigger`（如 `always` 逐题触发）；fake runner 离线单测；真实 sidecar 永不自动调用（`RAG_AGENTIC_INTEGRATION=1` 门控） |
| S3.2 | Dashboard agentic 展示 | [x] | 2026-10-06 | Query Traces 页基于 trace `method` 动态渲染 `agentic_search` 阶段（模式/Token/调用次数/证据文件列表），无该阶段时自然隐藏；Overview 配置卡显示 agentic 开关与 backend；AppTest 冒烟覆盖 |
| S3.3 | 文档收口与决策记录 | [x] | 2026-10-06 | README「Agentic 检索（sidecar）」小节增量增补第三路内容（配置项/CLI 用法/Dashboard 展示/安全边界与默认关闭口径/真实 sidecar 门控）；USAGE_GUIDE 补 `--agentic` 与 `--retrieval-mode` 用法并同步当前状态；FUTURE_OPTIMIZATION_ROADMAP 6.3 Agentic tools 标记落地状态；本文档进度表勾完 + A/B 数据结论写回第 1.1 节（见上方进度备注）；全套件 `pytest tests -q` 离线全绿（**1245 passed / 3 skipped**） |

#### 阶段 S4：Dashboard 三模式在线检索（P4）

> **接口冻结与并行说明**：S4 启动前先在 3.3 模块清单冻结 `SearchOutcome` 与 `SearchService` 接口；随后 **S4.1（服务层）与 S4.2（页面 UI，基于注入的 fake service 开发）可并行各开一个 agent**，最后由 S4.3 单独收口。S4 不改动检索核心（`core/`、`mcp_server/`、`libs/`），不新增依赖，默认配置不变（`agentic_search.enabled: false`，默认仅 Normal 可用）。

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 |
|---------|---------|------|---------|---------|
| S4.1 | `SearchService` 三模式分发 + 门控 | [ ] | — | normal/fast/deep 正确路由到 `QueryKnowledgeHubTool` / `AgenticSearchTool`；可用性矩阵覆盖 `enabled`、`allow_deep` 的四种组合（`availability(settings)` 返回各模式可用状态与原因）；结果归一化为 `SearchOutcome`（成功、`isError`、`ToolInvalidParams`、空结果四类）；离线单测使用 Fake 后端与 fake HybridSearch，不触网 |
| S4.2 | 「在线检索」页 UI + 导航注册 | [ ] | — | 模式按钮组默认选中 Normal（primary 高亮）；Fast 需 `enabled=true` 否则置灰（help 提示开启 `agentic_search.enabled` 并启动 sidecar），Deep 另需 `allow_deep=true` 否则置灰（提示开启 `allow_deep`）；collection 输入仅在 Normal 显示；Deep spinner 文案提示「可能需要数十秒」；结果区按模式渲染（共有：Markdown 答案 + 引用表来源/页码/分数；Fast/Deep 额外：agentic 答案、模式、Token、LLM 调用次数、耗时、证据文件）；`isError`/参数非法经 `st.error` 友好展示，页面不崩溃；trace 写入后可在 Query 追踪页查看各阶段；`render(*, settings=None, search_service=None)` 支持注入 |
| S4.3 | AppTest 冒烟 + 文档收口 | [ ] | — | 冒烟覆盖：默认渲染、agentic 关闭时 Fast/Deep 置灰、注入 fake service 后三模式检索、错误展示；README/USAGE_GUIDE 增补「在线检索」页用法；本文档 S4 表格勾选；全套件 `pytest tests -q` 离线全绿 |

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
