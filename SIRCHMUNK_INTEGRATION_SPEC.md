# Sirchmunk 整合可行性分析

> 调研对象：[modelscope/sirchmunk](https://github.com/modelscope/sirchmunk)（`main` 分支，版本 `0.2.1+main`，Apache-2.0，Beta）
> 目标项目：Modular RAG MCP Server（当前仓库）
> 调研日期：2026-10-04

---

## 1. 结论速览

| 问题 | 结论 |
|------|------|
| 能否整合？ | **可以**。技术栈一致（Python ≥3.10，本地 venv 为 3.13），协议兼容（MIT 项目依赖 Apache-2.0 库无冲突），且本项目的可插拔架构天然留有接入点。 |
| 应该怎么定位？ | **作为“无索引 Agentic 检索”第三条路由 / 独立 MCP Tool**，与现有 Dense + BM25 + Rerank 互补，**不替换** Chroma/BM25 主链路。 |
| 推荐路径 | P0 旁路挂载（零代码）→ P1 适配器 + 新 MCP Tool → P2 作为 HybridSearch 的条件第三路（低置信/零命中时触发）→ P3 评估 A/B 与 Dashboard 展示。 |
| 主要代价 | 延迟（FAST 约 2–5s，DEEP 数十秒）、LLM 调用成本、结果非确定性、依赖较重（sentence-transformers / kreuzberg / duckdb / igraph / rg / rga），0.x API 仍在快速变化。 |

---

## 2. Sirchmunk 是什么

**一句话**：一个“不建向量索引、直接在活文件上做检索”的 Agentic Search 引擎——用 `ripgrep` / `ripgrep-all` 做词法召回，用 LLM 做查询分析、多路证据融合与 ReAct 式迭代探索，最后输出带出处的答案。

### 2.1 核心机制

| 层 | 机制 | 关键代码 |
|----|------|---------|
| 词法召回 | `rg` 处理纯文本/代码/Markdown；`rga` 处理 PDF/DOCX/EPUB/ODT；LOG/PPT(X)/XLS(X) 走原生抽取兜底 | `retrieve/text_retriever.py`（`GrepRetriever`） |
| FAST 模式 | 1 次 LLM 查询分析 → grep 定位最佳文件 → 采样上下文窗口 → 1 次 LLM 合成答案（约 2 次 LLM 调用） | `search.py` |
| DEEP 模式（默认） | LENS 多路召回：关键词片段、精确实体、目录/文件名、文档树结构、历史知识簇、跨文档主题图；加权 RRF + 置信度融合；零命中救援、多跳 re-search、数值核验、Token/循环预算 | `search.py`、`retrieve/confidence.py` |
| ReAct Agent | 推理 → 选工具（`keyword_search` / `file_read` / `knowledge_query` / `tree_navigate`）→ 观察 → 继续或作答 | `agentic/react_agent.py`、`agentic/tools.py` |
| 知识簇缓存 | 每次成功检索沉淀为 `KnowledgeCluster`（DuckDB + Parquet），相似查询 ≥0.85 直接复用、≥0.65 作为提示 | `storage/knowledge_storage.py` |
| 可选离线编译 | `sirchmunk compile` 构建文档树、摘要目录、主题图（非必需） | `learnings/compiler.py` |

> ⚠️ “Embedding-free” 并不绝对：**文档检索不依赖向量库**，但默认安装会引入 `sentence-transformers`（MiniLM-L12-v2，384 维）用于知识簇复用。可通过 `reuse_knowledge=False` 关闭。

### 2.2 对外接口

| 形式 | 入口 | 说明 |
|------|------|------|
| Python SDK | `from sirchmunk import AgenticSearch` | **仅 async**：`await searcher.search(query, paths, mode=..., response_format="context")` |
| CLI | `sirchmunk search / compile / serve / mcp serve` | |
| HTTP | `POST /api/v1/search`、`/api/v1/search/stream`（SSE） | FastAPI，支持 Bearer Token、允许路径白名单 |
| MCP | `sirchmunk mcp serve`（stdio / SSE） | 当前实际只注册 **1 个工具 `sirchmunk_search`**（README 中的 `get_cluster`/`list_clusters` 未注册） |

### 2.3 返回结构（整合时最关心）

`response_format="context"` 返回 `SearchContext`（dataclass）：

```text
SearchContext
├── answer: str                     # 最终答案
├── total_llm_tokens / llm_usages   # Token 与调用明细（调用次数 = len(llm_usages)）
├── retrieval_logs / search_history / loop_count
└── cluster: KnowledgeCluster | None
     ├── confidence: float | None
     ├── search_results: list[str]  # 贡献文件
     └── evidences: list[EvidenceUnit]
          ├── file_or_url, doc_id, summary
          ├── snippets: list[dict | str]   # dict 形如 {snippet, start, end, score, reasoning}
          └── page_range: list[int] | None
```

> ⚠️ `snippets` 注解为 `List[Dict]`，但部分代码路径会写入纯字符串，适配器必须两种都兼容；`SearchContext` 无耗时字段，需自行计时。

---

## 3. 与当前项目的对比与互补关系

| 维度 | 当前项目（Indexed RAG） | Sirchmunk（Indexless Agentic） | 互补点 |
|------|----------------------|------------------------------|--------|
| 数据准备 | 必须先 `ingest`：Loader → Chunk → Transform → Embedding → Upsert | 无需摄取，指定路径即可搜 | 新文件/未摄取文件可立即检索 |
| 格式支持 | **仅 `.pdf`**（`pipeline._SUPPORTED_EXTENSIONS = {".pdf"}`） | MD/代码/JSON/YAML/CSV/HTML/PDF/DOCX/XLSX/PPTX/EPUB… | 直接补齐路线图 6.2「多格式 Loader」与 Obsidian `.md` 缺口 |
| 召回方式 | Dense 语义 + BM25 + RRF + Cross-Encoder | grep 精确匹配 + LLM 关键词扩展 + 多路融合 + 迭代探索 | 精确标识符/代码符号/跨文件多跳问题 |
| 延迟 | 毫秒级到 1 秒级 | FAST 2–5s，DEEP 数十秒 | 不宜放入默认热路径，适合条件触发 |
| 成本 | 查询期仅 Embedding（+可选 LLM Rerank） | 每次查询 2～N 次 LLM 调用 | 需预算与开关控制 |
| 确定性 | 高（单测零网络、RRF 确定性排序） | 低（LLM 驱动） | 需隔离到独立路由，测试用 fake 注入 |
| 可观测性 | TraceContext 全链路阶段打点 | 自带 telemetry / retrieval_logs | 可映射为 trace 阶段，增强白盒化 |

---

## 4. 整合方案（由浅到深）

```diagram
┌──────────────────── MCP Client（Claude / Copilot）────────────────────┐
└───────┬─────────────────────────┬───────────────────────────┬─────────┘
        │ query_knowledge_hub     │ agentic_search（新）       │ sirchmunk_search
        ▼                         ▼                           ▼ (方案 A 旁路)
┌──────────────────────┐  ┌──────────────────────┐   ┌─────────────────────┐
│ HybridSearch         │  │ AgenticSearchTool    │   │ sirchmunk mcp serve │
│  ├ Dense (Chroma)    │  │  └ SirchmunkAdapter  │   │ （独立进程）         │
│  ├ Sparse (BM25)     │  └──────────┬───────────┘   └─────────────────────┘
│  └ Agentic（新,条件）─┼─────────────┘
│ → RRF(3路) → Rerank  │
└──────────────────────┘
```

### 方案 A：旁路挂载（P0，零代码）

在 MCP 客户端配置中并列添加 sirchmunk server，让 LLM 自主选择工具：

```json
{
  "mcpServers": {
    "modular-rag": { "command": "python", "args": ["-m", "mcp_server.server"], "env": {"PYTHONPATH": "src"} },
    "sirchmunk":   { "command": "/path/to/venv/bin/sirchmunk", "args": ["mcp", "serve"],
                     "env": { "SIRCHMUNK_SEARCH_PATHS": "/path/to/data/documents" } }
  }
}
```

- 优点：零侵入、可立即体验；依赖可装在独立 venv，不污染主项目。
- 缺点：无统一引用格式、无 trace、无法与 Rerank 融合，严格说是“并存”而非“整合”。

### 方案 B：新增独立 MCP Tool `agentic_search`（P1，推荐起点）

在本项目内封装适配器，暴露新工具，输出复用现有 `ResponseBuilder` 的 Markdown + `structuredContent.citations` 格式，并接入 TraceContext。

### 方案 C：作为 HybridSearch 的第三路召回（P2）

把 sirchmunk 证据转换为 `RetrievalResult`，参与 RRF 三路融合后统一 Rerank。**建议条件触发**而非每次都跑：

- Dense+Sparse 零命中 / 融合后 Top1 rerank 分数低于阈值；
- 查询命中精确标识符模式（代码符号、错误码、文件名）；
- `collection` 指向未摄取的“活目录”。

### 方案 D：评估与回流（P3）

- 在 `eval_runner` 增加检索模式开关，对 golden set 做 `hybrid` / `agentic` / `hybrid+agentic` A/B，量化 Recall@K、MRR、耗时、Token 成本；
- 可选：将 sirchmunk 高频命中但未摄取的文件提示给 Ingestion Manager，形成“发现 → 摄取”闭环。

---

## 5. 需要调整的模块（逐文件）

### 5.1 必改（方案 B）

| 模块 | 文件 | 改动 |
|------|------|------|
| 配置 | [config/settings.yaml](config/settings.yaml) | 新增 `agentic_search` 节（见 5.4） |
| 配置解析 | [src/core/settings.py](src/core/settings.py) | 新增 `AgenticSearchConfig` dataclass + `_build_agentic_search()`；缺省 `enabled: false` 保证向后兼容（与 `IngestionConfig` 可选节同一纪律） |
| 适配层（新增） | `src/libs/agentic_search/`：`base_agentic_search.py` / `sirchmunk_backend.py` / `agentic_search_factory.py` | 沿用 `libs/*` 的 Base + Factory 模式；`sirchmunk` 懒导入（未安装时报清晰错误）；负责 async→sync 桥接、超时、`SearchContext` → `list[RetrievalResult]` 转换 |
| LLM 配置复用 | 同上 | 由 `settings.llm`（`api_key` / `base_url` / `model`）构造 `sirchmunk.llm.OpenAIChat`，避免维护两套 LLM 配置；Azure 需确认 base_url 兼容性 |
| MCP Tool（新增） | `src/mcp_server/tools/agentic_search.py` | 参照 `query_knowledge_hub.py`：懒初始化、可注入、参数校验（`query`、`mode∈{FAST,DEEP}`、`paths` 必须在白名单内、`top_k`）、`persist_trace` |
| Tool 注册 | [src/mcp_server/server.py](src/mcp_server/server.py) `build_default_protocol_handler()` | 当 `agentic_search.enabled` 时 `register_tool(create_agentic_search_definition())` |
| 依赖 | [pyproject.toml](pyproject.toml) | 新增 optional extra：`agentic = ["sirchmunk>=0.2,<0.3"]`（锁小版本，规避 0.x 破坏性变更）；**不放入** `requirements.txt` 主依赖 |
| 测试 | `tests/unit/test_agentic_search_adapter.py`、`tests/unit/test_agentic_search_tool.py` | 注入 fake searcher 返回构造的 `SearchContext`，覆盖：dict/str 两类 snippet、空 cluster、超时、路径越权、参数校验；真实调用用 `RAG_AGENTIC_INTEGRATION` 环境变量门控（沿用 `RAG_LLM_INTEGRATION` 先例） |

### 5.2 方案 C 追加

| 模块 | 文件 | 改动 |
|------|------|------|
| 融合 | [src/core/query_engine/fusion.py](src/core/query_engine/fusion.py) | 纯函数 `rrf_fuse_rankings` **已支持 N 路**，无需改数学；仅 `RRFFusion.fuse(dense, sparse)` 签名固定两路，需新增 `fuse_routes(routes: dict[str, Sequence[RetrievalResult]])` 或可选 `extra` 参数，并扩展 trace details |
| 编排 | [src/core/query_engine/hybrid_search.py](src/core/query_engine/hybrid_search.py) | 注入可选 `agentic_retriever`；新增触发策略、`last_agentic_results`；降级逻辑从“两路”泛化为“N 路”（全部失败才抛 `HybridSearchError`）；`hybrid` 阶段 `routes` 增加 `agentic` 计数 |
| 结果契约 | [src/core/types.py](src/core/types.py) `RetrievalResult` | 结构**无需修改**；约定 metadata：`source_path`、`retrieval_route="agentic"`、`char_start/char_end`、`page_range`、`agentic_confidence`、`agentic_answer`（可选） |
| chunk_id 空间 | 适配器 | sirchmunk 证据不在 Chroma 中，需生成确定性 ID，如 `agentic:{sha1(path|start|end)}`，避免与 C4 chunk_id 空间冲突；注意同一文本被 Dense 路命中时**无法自动去重**（ID 不同），可选按 `source_path + 文本重叠` 做后置去重 |
| Rerank | [src/core/query_engine/reranker.py](src/core/query_engine/reranker.py) | 接口无需改动；但 snippet 可能很短，建议适配器按 `start/end` 扩展上下文到接近 `chunk_size`，保证 Cross-Encoder 评分公平 |
| 引用 | [src/core/response/citation_generator.py](src/core/response/citation_generator.py) | 已读取 `metadata["source_path"]`，可直接工作；可选增加行号/字符区间字段 |
| 文档摘要工具 | [src/mcp_server/tools/get_document_summary.py](src/mcp_server/tools/get_document_summary.py) | `agentic:` 前缀 ID 无法从 Chroma 查到，需在 tool 中识别并返回友好提示（或回退为直接读文件） |

### 5.3 可选增强

| 模块 | 文件 | 改动 |
|------|------|------|
| 查询追踪页 | `src/observability/dashboard/pages/query_traces.py` | 展示 `agentic_search` 阶段：模式、LLM 调用次数、Token、耗时、证据文件列表 |
| 配置展示 | `src/observability/dashboard/services/config_service.py` | 展示 agentic 开关与模式 |
| 评估 | `src/observability/evaluation/eval_runner.py`、`scripts/evaluate.py` | `--retrieval-mode hybrid|agentic|hybrid+agentic` |
| 调试 CLI | `scripts/query.py` | `--agentic` / `--verbose` 打印第三路中间结果 |
| 规格文档 | `DEV_SPEC.md` | 新增阶段（如 J：Agentic Retrieval），写清任务拆分与验收标准，继续由 auto-coder Skill 驱动实现 |

### 5.4 建议配置

```yaml
# Agentic 检索（Sirchmunk，可选；enabled=false 时完全不加载）
agentic_search:
  enabled: false
  backend: sirchmunk
  mode: FAST                 # FAST | DEEP
  trigger: fallback          # tool_only | fallback | always
  fallback_min_score: 0.2    # trigger=fallback 时：rerank Top1 低于该值才启用
  search_paths:              # 允许检索的根目录（同时作为安全白名单）
    - ./data/documents
  include: ["*.md", "*.pdf", "*.docx", "*.pptx", "*.xlsx"]
  exclude: [".git", "node_modules", ".venv", "data/db"]
  top_k_files: 5
  max_loops: 6
  max_token_budget: 32000
  timeout_s: 60
  reuse_knowledge: false     # 关闭知识簇复用，避免引入第二个 embedding 模型
  work_path: ./data/db/sirchmunk
```

### 5.5 无需改动

- **Ingestion 全链路**（Loader / Chunker / Transform / Embedding / Upsert）——sirchmunk 不依赖索引；
- **Chroma / BM25 存储**；
- **现有 3 个 MCP Tool 的契约**与既有测试语义。

---

## 6. 能带来的优点与亮点

### 6.1 功能收益

1. **零摄取即时检索**：新文档放入目录即可被搜到，弥补 SHA256 增量摄取需手动触发的空窗期。
2. **格式覆盖立刻扩展**：Markdown / Word / Excel / PPT / 代码 / 配置文件均可检索，直接覆盖路线图 6.2 与 [OBSIDIAN_RAG_SPEC.md](OBSIDIAN_RAG_SPEC.md) M1/M2 的大部分“能搜到”诉求（结构化解析仍由 MarkdownLoader 负责）。
3. **精确匹配更强**：错误码、函数名、配置键、版本号等 BM25 分词易丢失的标识符，grep 是天然强项。
4. **复杂问题多跳推理**：ReAct 迭代 + 跨文件 bridge re-search，适合“A 模块如何影响 B”的跨文档问题，弥补单轮 Top-K 召回的上限。
5. **兜底召回**：作为零命中/低置信时的 fallback，降低“检索不到就胡说”的风险，与路线图 6.1「置信度拒答」形成配合。
6. **对齐路线图 6.3**：直接提供「Agentic RAG tools / 关键词检索 / 事实核查」类能力。

### 6.2 工程与面试亮点

| 亮点 | 可讲述的点 |
|------|----------|
| **双引擎检索架构** | Indexed RAG（低延迟、确定性）+ Indexless Agentic Search（新鲜度、深度推理），按查询特征路由，兼顾成本与效果 |
| **三路 RRF 融合** | 复用已有纯函数 `rrf_fuse_rankings`，从 Dense+Sparse 平滑扩展到 Dense+Sparse+Agentic，体现可插拔设计的真实价值 |
| **成本感知路由** | 默认走毫秒级 Hybrid，仅在低置信时升级到 Agentic，Token 预算/超时/循环上限三重护栏 |
| **评估驱动决策** | 用 golden set 做 A/B，用 Recall@K、MRR、延迟、Token 数据说明何时值得启用 Agentic，而不是凭感觉 |
| **白盒可观测** | 把 Agent 的每轮工具调用、Token 消耗映射为 trace 阶段，在 Dashboard 中可视化“Agent 在想什么” |
| **安全边界** | 路径白名单、超时、依赖隔离（optional extra / 独立进程），体现生产意识 |

---

## 7. 风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| 延迟高（DEEP 数十秒） | MCP 调用超时、体验差 | 默认 FAST；`trigger: fallback`；`timeout_s` 硬超时；DEEP 仅在独立 tool 中显式选择 |
| LLM 成本 | 每次 2～N 次调用 | `max_token_budget`、`max_loops` 上限；trace 记录 Token 便于监控 |
| 非确定性 | 与项目“离线确定性测试”纪律冲突 | 单测全部 fake 注入；真实调用环境变量门控；评估多次取均值 |
| 依赖重 / 平台问题 | sentence-transformers、kreuzberg、duckdb、igraph、leiden；构造时会尝试自动安装 `rg`/`rga` | 作为 optional extra；或采用方案 A/HTTP 方式进程隔离；部署文档预装 `rg`/`rga`，禁止运行期自动安装 |
| 0.x API 变动快 | 升级易破坏 | 锁 `<0.3`；全部调用收敛在 `sirchmunk_backend.py` 单文件；适配器单测锁契约 |
| async-only API | 本项目 MCP server 为同步 stdio 循环 | 适配器内 `asyncio.run(...)`（同步上下文）；未来迁移官方 MCP SDK（异步）时可直接 `await` |
| 返回结构不稳定 | `snippets` 混合 dict/str；README 与实际 MCP 工具不一致 | 适配器防御式解析；以 `server.py` 实际注册为准 |
| 任意文件读取 | Agent 可读路径内任意文件 | `search_paths` 白名单 + 参数校验拒绝越权路径；排除 `data/db`、`.env`、密钥目录 |
| 重复证据 | 同一内容被 Dense 与 Agentic 以不同 ID 命中 | 后置按 `source_path` + 文本重叠去重，或在 Rerank 后去重 |
| 两套 LLM 配置 | 配置漂移 | 由 `settings.llm` 统一构造 `OpenAIChat` |

---

## 8. 推荐实施路线

| 阶段 | 内容 | 产出 / 验收 | 预估工作量 |
|------|------|------------|-----------|
| **P0** | 方案 A 旁路挂载，选 5–10 条 golden 问题人工对比 | 判断 sirchmunk 在本项目语料上是否有明显增益 | 0.5 天 |
| **P1** | 适配器 + `agentic_search` MCP Tool + 配置 + 单测 | `tools/list` 出现新工具；fake 注入单测通过；门控集成测试可跑通 | 2–3 天 |
| **P2** | HybridSearch 条件第三路 + N 路 RRF + 去重 + trace | 现有测试零回归；fallback 触发逻辑单测覆盖 | 2–3 天 |
| **P3** | 评估 A/B + Dashboard 展示 + DEV_SPEC 阶段文档 | 有数据支撑的启用策略；Query Traces 页可见 agentic 阶段 | 2 天 |

**决策原则**（与 [FUTURE_OPTIMIZATION_ROADMAP.md](FUTURE_OPTIMIZATION_ROADMAP.md) 一致）：先用评估数据证明瓶颈，再引入更复杂的链路。若 P0 对比无明显收益，止步于方案 A 即可。

---

## 9. 参考

- Sirchmunk README：<https://github.com/modelscope/sirchmunk/blob/main/README.md>
- `AgenticSearch.search` 签名：`src/sirchmunk/search.py`
- 返回结构：`src/sirchmunk/schema/search_context.py`、`src/sirchmunk/schema/knowledge.py`
- MCP 实际注册：`src/sirchmunk_mcp/server.py`
- 配置模板：`config/env.example`
- 本项目检索编排：[src/core/query_engine/hybrid_search.py](src/core/query_engine/hybrid_search.py)、[src/core/query_engine/fusion.py](src/core/query_engine/fusion.py)
- 本项目主工具：[src/mcp_server/tools/query_knowledge_hub.py](src/mcp_server/tools/query_knowledge_hub.py)
