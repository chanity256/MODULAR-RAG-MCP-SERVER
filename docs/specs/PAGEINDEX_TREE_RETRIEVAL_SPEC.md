<!-- 规格定位：以独立 venv 的本机 HTTP sidecar 接入 PageIndex「树索引 + LLM 沿树推理」，在 Web UI Chat 新增 `tree` 检索模式，补长结构化文档的章节定位与页 / 行级引用。基线：S / SX / WC / LG / WS / SR / CMC / MR / AR / RA / AC 均已收口冻结。与既有规格的关系：独立增量；不改 Sirchmunk agentic 契约（`libs/agentic_search/` 与 `agentic_search` 配置节）、不改三档既有模式与 `auto` 的行为契约、不改 MCP 工具、不改摄取管线与 DocumentManager。 -->

# PageIndex 树检索规格 (PAGEINDEX_TREE_RETRIEVAL_SPEC)

> 版本：0.1 — 由 backlog 提案立项为可执行规格；PI0 为立项门（四路对照不达标即搁置），PI0 通过前 PI1 起不开工
>
> 起源与决策依据：由 backlog/pageindex-tree-retrieval_backlog.md 单文件晋升，原文见 git 历史；提案起源为会话内调研（2026-11-21，非文件来源：上游 [VectifyAI/PageIndex](https://github.com/VectifyAI/PageIndex) main `add6b17` 源码 + 本仓库 agentic_search / sidecar 架构核对）。依据 [03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md](../frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md)（进程隔离红线、HTTP sidecar 契约风格）、[18_AGENTIC_COLLECTION_ISOLATION_SPEC_frozen.md](../frozen/18_AGENTIC_COLLECTION_ISOLATION_SPEC_frozen.md)（collection 定界 D3/D5）、[15_MODE_ROUTER_SPEC_frozen.md](../frozen/15_MODE_ROUTER_SPEC_frozen.md)（Chat 模式常量六处登记、auto 候选口径、立项门先例）、[11_CHAT_MODE_COLORS_SPEC_frozen.md](../frozen/11_CHAT_MODE_COLORS_SPEC_frozen.md)（模式配色规则）、[16_ROOT_ANCHOR_SPEC_frozen.md](../frozen/16_ROOT_ANCHOR_SPEC_frozen.md)（相对路径按项目根解析）
>
> 基线：`c732778`（fork/main），上述系列均已收口冻结；`tree_search` 配置节、`tree` 模式与 PageIndex sidecar 均不存在
>
> 日期：2026-11-21

红线：主 venv 永不 `import pageindex`（及其 litellm / openai-agents 依赖链），只经 HTTP 调用独立进程；`tree_search.enabled: false`（默认）时零加载，存量行为（capabilities、模式选择器、auto 候选、MCP tools/list）逐字节不变。

## 目录

- 1 项目概述（1.1 结论速览、1.2 背景与现状、1.3 决策依据与终裁、1.4 对提案的调整）
- 2 核心目标与范围（2.1 In Scope、2.2 Out of Scope）
- 3 技术选型与模块设计（3.1 运行架构、3.2 模块设计、3.3 配置契约、3.4 sidecar HTTP 契约、3.5 涉及文件清单、3.6 选型理由）
- 4 测试方案
- 5 任务排期与进度
- 6 验收清单
- 7 实现合入后的回写清单
- 8 隐私注记
- 9 风险与开放问题（本规格追加章节）

---

## 1. 项目概述

### 1.1 结论速览

| 问题 | 结论 |
|------|------|
| 要解决什么问题？ | 语料中有千级标题的长 Markdown（最大 1.7 MB / 1045 个标题行），Dense + BM25 按 1000 字切块召回会丢章节上下文，Sirchmunk 证据只有抽取文本字符偏移；「哪一节讲 X」「跨章节归纳」类问题缺少按结构定位的检索路径，引用也无法落到章节 / 行号。**尚无实测失败样例**，由 PI0 在真实问题上确认 |
| 定位是什么？ | Web UI Chat 的第五档请求值 `tree`（与 `hybrid` / `agentic_fast` / `agentic_deep` / `auto` 并列）：sidecar 沿文档树选节点返回证据，答案仍由本项目 AnswerGenerator 生成；不替换任何既有模式 |
| 实施范围（已决策） | PI0 为立项门（门槛按 D9 终裁，不达标即搁置，本规格止于预研并移入派生台账）；PI1–PI6 为承诺任务（门控通过后）；PI7 含门控真实冒烟。仅本地模式（D1），MCP / Hybrid 第四路 / auto 候选不做（D2、D10） |
| 主要代价 | 多维护一个进程与一个 venv；建树要把文档全文送 LLM 写节点摘要（一次性，按 token 计费）；一次树检索为多轮 LLM 调用（预期十秒到数十秒级）；Markdown 存储与检索循环需自写（上游本地模式不支持） |

### 1.2 背景与现状

**上游现状**（2026-11-21 核实，main `add6b17`；PyPI 最新 `0.7.4`，仓库 `pyproject.toml` 写 `0.2.10`，口径不一致）：

| 项 | 现状 | 出处 |
|----|------|------|
| 原理 | 每篇文档生成树状目录索引（节点 `title` / `node_id` / `start_index` / `end_index` / `summary`），检索时 LLM 读树选节点再读原文；无向量、无切块 | README |
| 本地 PDF | `submit_document` 只接受 `.pdf`；默认 flash 模式版面解析出结构，LLM 只写摘要与精修，约 $0.001/页 | `local_api.py` |
| 本地 Markdown | 只有底层函数 `md_to_tree`（按 `#` 标题与整行粗体建树，节点带 `line_num`），**不进 DocStore**、无检索工具；函数内有裸 `print`；[Issue #609](https://github.com/VectifyAI/PageIndex/issues/609)（open）：首个标题前正文丢失、Setext 标题不识别 | `page_index_md.py` |
| 本地存储 | `<storage>/docs/<doc_id>/{doc,tree,pages}.json` + `manifest.json`，fcntl 锁；每文档可挂 `metadata`；不支持 collection | `local_store.py` |
| 检索接口 | 只读工具 `browse_documents` / `get_document` / `get_document_structure` / `get_page_content`（只覆盖 DocStore 中的 PDF）；`chat()` 经 openai-agents + LiteLLM 生成答案 | `agent_tools.py`、README |
| 模型接入 | LiteLLM 命名，`index_backend` / `chat_backend` 透传 `api_key` / `api_base`（可接 OpenAI 兼容端点） | `client.py` |
| 许可 / 运行时 | MIT；Python ≥ 3.10 | `pyproject.toml` |

**本仓库现状**（基线 `c732778`）：

- Chat 模式常量分散六处（MR 规格 1.2 第 5 条，现状未变）：`src/core/chat/retriever.py:31` `RETRIEVAL_MODES`（引擎校验 + capabilities 过滤）、`src/core/settings.py:352` `CHAT_RETRIEVAL_MODES`、`src/api/chat_wiring.py:21` `_ALL_MODES`（capabilities `retrievals` 与 auto 候选共用）、`src/api/schemas/chat.py:25` `RetrievalMode`、`frontend/lib/chat/types.ts:3-5`、`frontend/lib/chat/modes.ts`（`LABELS` / `buildModeOptions`）。
- auto 候选：`src/api/chat_wiring.py:160-175` 取 `_ALL_MODES ∩ 已注册 ∩ 门控`；`src/libs/mode_router/routing_question.py` `instructions_and_criteria` 对缺 criteria 文案的候选**抛 `ValueError`**——新模式若进入 `_ALL_MODES`，auto 每轮都会决策失败回退。
- 检索器装配：`src/observability/dashboard/services/chat_retrievers.py:208-230` `build_chat_retrievers` 按配置注册；`ChatRetriever` 协议见 `src/core/chat/retriever.py:99-120`。
- 答案路径：`src/core/chat/engine.py:360-365`，`ChatRetrievalResult.answer` 为空时走 `AnswerGenerator`，引用取 `AnswerGenerator.citations(results)`；`Citation`（`src/core/response/citation_generator.py:35-53`）只有 `id / source / page / chunk_id / text / score`，`page` 取自 `RetrievalResult.metadata["page"]`。
- 进度流：`engine.py:552` 对非 hybrid 模式且检索器声明 `on_log` 时开启 `search_log` 帧。
- 摄取历史：`src/libs/loader/file_integrity.py` `SQLiteIntegrityChecker`（v2 success 记录含 `source_path` / `file_hash` / collection）；`src/libs/agentic_search/collection_paths.py` `CollectionPathResolver.resolve(collection)` 已能把 collection 推导为 PathGuard 校验后的绝对路径集合。
- 语料（`data/documents` 实测）：29 个 `.md`、各 1 个 `.docx` / `.pptx` / `.xlsx` / `.html`，**无 PDF**；大型 Markdown 1.7 MB / 1045 个标题行、470 KB / 253、351 KB / 166、315 KB / 187，其余多为 3–50 KB 短笔记。

### 1.3 决策依据与终裁

提案三问的答案：

1. **已观察到的问题**：见 1.1、1.2（结构性推断，PI0 负责补实测样例）。
2. **更简单的方案**：
   - **方案 (a)**：MarkdownLoader / splitter 给 chunk 补「标题路径」metadata、引用显示章节与行号。不引入新进程，能覆盖引用可读性诉求，但不改变召回方式。PI0 以离线近似方式纳入对照（3.6 第 4 条）；(a) 达标则本规格止于预研。
   - **MCP 客户端旁路挂 PageIndex**：零代码，但上游本地模式无 MCP server（仅云端提供），且进不了 trace / Chat / collection 隔离。不采用。
3. **如何确认有效**：PI0 四路对照（Hybrid、Sirchmunk DEEP、方案 (a) 离线近似、PageIndex 树检索原型）在用户逐条确认的问题集上比较命中率、引用可定位率、P50 / P95 延迟与 token；端到端由 PI7 门控冒烟确认 Chat `tree` 模式可用且引用落到章节 / 行或页。

| 编号 | 决策 | 终裁 |
|------|------|------|
| D1 | 本地还是云端 | **本地模式**（2026-11-21，用户拍板）。文档与树索引留在本机，仅 LLM 调用外发；云端（原文件上传 PageIndex、付费）不做 |
| D2 | 首期接入面 | **Chat 新增模式 `tree`**（2026-11-21，用户拍板）。MCP 独立工具、Hybrid 第四路融合不做；Query Playground 不做（2.2） |
| D3 | Markdown 路径 | **自建 MD 树存储 + 检索循环**（2026-11-21，用户拍板）。MD 经预处理（绕开 #609）后调用上游 `md_to_tree` 建树，存入 sidecar 自建存储；PDF 走上游 `PageIndexClient.submit_document` 与 DocStore；检索循环两种格式统一自建（D7） |
| D4 | 检索 / 摘要模型 | **sidecar 独立 env 配置，默认智谱 GLM（OpenAI 兼容端点，经 LiteLLM `openai/<model>` + `api_base`）**（规格设计决策，与 Sirchmunk sidecar「LLM 配置独立于 `settings.llm`」同口径）。PI0 实测工具调用 / JSON 输出质量，终裁回填本行；换模型只改 env，不改代码 |
| D5 | 建树时机 | **对账同步命令 `scripts/pageindex_sync.py`**（规格设计决策）：以摄取历史 v2 success 记录为准，`file_hash` 变化即重建、历史中已不存在的即删除、非 `.pdf` / `.md` / `.markdown` 跳过并计数。首期不改摄取管线与 DocumentManager（04 规格的 7 阶段与删除链路不动）；摄取后自动触发列入 2.2 |
| D6 | 上游版本 | **锁定 PyPI 精确版本**，PI0.1 实装时确定并回填本行（`setup_venv.sh` 写死版本、`start_sidecar.sh` 启动日志打印实际版本，对齐 KE0 先例） |
| D7 | 检索循环形态 | **自建「选文档 → 选节点 → 读原文 → 判是否足够」循环**（规格设计决策）：不用上游 `chat()`（会生成答案，与 AnswerGenerator 重叠）与 `agent_tools()`（只覆盖 DocStore 中的 PDF，D3 的 MD 存储用不上）。LLM 调用经注入的 `complete(prompt) -> str`，循环本体是不 import pageindex 的纯模块，可在主 venv 离线单测 |
| D8 | 证据到引用 | **`RetrievalResult.metadata` 承载 `retrieval_route="tree"` / `source_path` / `page` / `section` / `line_start` / `line_end` / `node_id`；`Citation` 只增可选字段 `section` / `line_start` / `line_end`，值为 `None` 时 `to_dict()` 不输出该键**（规格设计决策）：既有 hybrid / agentic 引用的 `to_dict()` 逐字节不变，WS `sources` 帧只增不改 |
| D9 | PI0 门槛 | **本规格只固定度量口径（3.6 第 4 条），数值门槛在 PI0 基线跑出后与用户共同拍板**（规格设计决策，沿用 MR D2 先例）。门槛终裁前 PI1 不开工；终裁回填本行与 PI0.3 行 |
| D10 | auto 候选 | **`tree` 不进入 auto 候选**（规格设计决策，依据 1.2 第 2 条）：`_ALL_MODES` 保持三档并改名语义为「auto 候选与既有三档」，`tree` 单独追加到 capabilities `retrievals`；纳入候选需新增 criteria 文案并按 MR D2 门槛复核，另立提案 |

### 1.4 对提案的调整

1. **G1（单后端路由）**：提案候选方向之一是把 `agentic_search.backend` 扩为列表。不采用：新建独立 `src/libs/tree_search/` 与 `tree_search` 配置节，`AgenticSearchFactory` 与 `agentic_search` 节不动，Sirchmunk 既有契约与测试零影响。
2. **G3（证据契约）**：提案写「`AgenticEvidence` 只增字段」。改为 `libs/tree_search` 自有 `TreeEvidence`，`AgenticEvidence` 不改。
3. **G4（证据获取）**：提案优先方案为 `agent_tools()` + 本项目 LLM。`agent_tools()` 只能读上游 DocStore（PDF），D3 的 MD 自建存储无法复用，改为 D7。
4. **G5（索引生命周期）**：提案写「摄取后异步建树 + 删除联动第 5 个存储」。改为 D5 对账同步，不改摄取管线与 DocumentManager。
5. **决策编号**：提案待决的 D1–D6 在本规格沿用原编号并终裁或给出设计决策，D7–D10 为立项时新增。

## 2. 核心目标与范围

### 2.1 In Scope

| 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|--------|-----------|
| 预研 | 四路对照立项门 | 门控（PI0，立项门） | sidecar venv + 版本锁定、用户确认的问题集、四路对照基线；门槛按 D9 终裁，不达标即搁置 |
| sidecar 核心 | 纯模块（MD 预处理 / 树存储 / 检索循环） | 承诺（PI1） | 不 import pageindex 的三个纯模块，主 venv 离线单测覆盖 |
| sidecar 服务 | HTTP 服务 + pageindex 适配层 + 运维脚本 + 红线 | 承诺（PI2） | 仅 loopback + Bearer 的 `/status` `/documents` `/index` `/search`；`check_redlines.py` 增 pageindex import 规则（精确白名单） |
| 主 venv 后端 | `libs/tree_search/` + `tree_search` 配置节 | 承诺（PI3） | `BaseTreeSearch` / `TreeEvidence` / HTTP 后端 / fake 后端 / 工厂；配置缺失走默认、非法值 fail-fast |
| 索引同步 | `scripts/pageindex_sync.py` | 承诺（PI4） | 摄取历史 → sidecar 索引的幂等对账（新增 / 重建 / 删除 / 跳过） |
| Chat 后端 | `tree` 模式接入 | 承诺（PI5） | 常量、配置校验、检索器、capabilities、schema、`Citation` 只增字段；collection 定界与 PathGuard 过滤 |
| Chat 前端 | 模式选择器与引用展示 | 承诺（PI6） | Tree 选项与配色（对比度 ≥ WCAG AA）、引用显示章节 / 行号 |
| 验证与文档 | 门控冒烟 + USAGE_GUIDE | 承诺（PI7，其中真实冒烟门控） | `RAG_PAGEINDEX_INTEGRATION=1` 真实 sidecar 冒烟；USAGE_GUIDE 新增章节 |

### 2.2 Out of Scope（明确不做）

- **PageIndex 云端**（D1）：原文件上传、Page Search / Page Extract API、框选引用、多模态。
- **使用上游答案生成**：`chat()` / `run_responses()` 等生成的回答文本不进入 Chat；答案只由 AnswerGenerator 生成。
- **MCP 工具**（D2）：不新增 `tree_search` 工具，`query_knowledge_hub` / `agentic_search` 输入 schema 与行为不变。
- **Hybrid 第四路融合**（D2）：`HybridSearch` 与 `agentic_search.trigger` 不感知树检索。
- **auto 候选**（D10）：`routing_question.py` 文案与 MR 门槛不动。
- **Query Playground、`scripts/query.py`、`scripts/evaluate.py` 感知 `tree`**：调试 / CLI / 评估链路保持现状。
- **改摄取管线或 DocumentManager**（D5）：摄取后自动建树、删除时同步删索引列为后续，由对账命令覆盖。
- **`start_api.py --manage-sidecar` 托管 PageIndex sidecar**：首期手动启动（`scripts/pageindex/start_sidecar.sh`），托管编排列为后续。
- **`tree` 模式的进度流（`search_log` 帧）**：sidecar `/search` 为同步端点，Chat 只下发既有 `status` 帧；流式列为后续。
- **`.docx` / `.pptx` / `.xlsx` / `.html` 建树**：对账时跳过并计数。
- **在主 venv 安装或 import `pageindex`**，以及把 PageIndex 装进 Sirchmunk sidecar 进程或其 venv。
- **回改冻结规格**：CMC 三档配色、MR auto violet 配色、AC 定界语义、Sirchmunk 契约均不动；`tree` 配色是本规格新行为。

## 3. 技术选型与模块设计

### 3.1 运行架构

```
Web UI Chat（选 Tree）
   │ WS /api/v1/chat/ws  {retrieval: "tree", collection?}
   ▼
主 venv（永不 import pageindex）
  ChatEngine ──► TreeChatRetriever（services/chat_retrievers.py）
                   ├─ collection → CollectionPathResolver → 定界 source_paths（空集 → 不调用，D3 先例）
                   ├─ PageIndexHttpBackend（libs/tree_search，httpx，Bearer）
                   └─ PathGuard 过滤证据 → RetrievalResult(metadata: page/section/line_*)
                 ──► AnswerGenerator（生成答案 + Citation 只增字段）
                                   │ 127.0.0.1:8585
                                   ▼
.venv-pageindex（scripts/pageindex/，本项目自写服务）
  server.py（FastAPI）── /status /documents /index /search
     ├─ pi_adapter.py（唯一 import pageindex 处）
     │     ├─ PDF：PageIndexClient.submit_document / get_document_structure / get_page_content
     │     ├─ MD ：md_to_tree（输入经 md_prep 预处理）
     │     └─ LLM：litellm.completion → complete(prompt) -> str
     ├─ md_prep.py   （纯：首段正文补节点、Setext → ATX、标题行号映射）
     ├─ tree_store.py（纯：source_path ↔ doc_id ↔ file_hash 映射 + MD 树 JSON 存储）
     └─ tree_loop.py （纯：选文档 → 选节点 → 读原文 → 判是否足够，max_loops / token 预算）
  存储：data/db/pageindex/（已被 denied_substrings 的 data/db 覆盖）

离线批处理：scripts/pageindex_sync.py ──读摄取历史 v2──► POST/DELETE /index
```

### 3.2 模块设计

**sidecar 纯模块（PI1）**：

- `md_prep.py`：`prepare(markdown: str) -> PreparedMarkdown`。首个标题前的非空正文包成 `# <文件名>` 节点（对齐上游 PDF 路径的 Preface 语义）；Setext 标题改写为 ATX；记录预处理后行号 → 原文行号的映射，证据回填原文行号。
- `tree_store.py`：JSON 文件存储，记录 `{source_path, file_hash, format, doc_id, indexed_at, node_count}`；MD 树与节点原文存本模块，PDF 只存映射（树在上游 DocStore）。原子写、单进程内锁。
- `tree_loop.py`：`search(query, docs, *, complete, read_node, max_nodes, max_loops, max_token_budget) -> LoopResult`。
  1. 候选文档超过 1 篇时，给 LLM 文档描述列表，选出相关文档（JSON `doc_ids`）；
  2. 每篇选中文档给出不含正文的树（标题 + 摘要 + 节点 ID），LLM 返回 JSON `node_ids`；
  3. `read_node` 读原文，单节点超过 `max_evidence_chars` 时优先下钻子节点，仍超限则截断并标记；
  4. LLM 判断证据是否足够，不够且未超 `max_loops` / 预算则回到第 2 步补选。
  
  LLM 输出非法 JSON 时重试一次，仍非法则返回已得证据并在 telemetry 记 `invalid_output`。证据按选中顺序给出 `score = 1 / rank`。

**sidecar 服务（PI2）**：`server.py` 只做 HTTP 编排与鉴权；`pi_adapter.py` 是唯一 import `pageindex` / `litellm` 的文件，向纯模块注入 `complete` 与 `read_node`。`md_to_tree` 的裸 `print` 只落在 sidecar 进程 stdout，不影响主进程。

**主 venv `libs/tree_search/`（PI3）**：与 `libs/agentic_search/` 同构但独立：

- `base_tree_search.py`：`TreeSearchRequest(query, source_paths: list[str] | None, max_nodes, max_loops, max_token_budget)`、`TreeEvidence(source_path, text, score, node_id, section, page_start, page_end, line_start, line_end)`、`TreeSearchResponse(evidences, llm_call_count, total_llm_tokens, elapsed_ms, telemetry)`；错误分型 `TreeSearchTimeoutError` / `TreeSearchBackendError` / `TreeSearchResponseError`。
- `http_backend.py`（工厂键 `pageindex_http`）、`fake_backend.py`（测试注入）、`tree_search_factory.py`、`converter.py`（`TreeEvidence → RetrievalResult`，metadata 见 D8；`page` 取 `page_start`）。

**索引同步（PI4）**：`scripts/pageindex_sync.py [--collection C] [--dry-run]`：读 `SQLiteIntegrityChecker` 的 v2 success 记录，相对 `source_path` 按项目根解析（RA 契约），逐条经 PathGuard 校验；对比 sidecar `/documents` 得出新增 / 重建 / 删除 / 跳过四类并执行，输出计数摘要；单文档失败不中断，退出码非零表示有失败。

**Chat 接入（PI5）**：

- `core/chat/retriever.py` 增 `RETRIEVAL_TREE = "tree"` 并追加进 `RETRIEVAL_MODES`；`core/settings.py` `CHAT_RETRIEVAL_MODES` 同步追加，`chat.default_retrieval: tree` 要求 `tree_search.enabled: true`（否则 fail-fast），`mode_router.fallback_retrieval` 仍只接受三档。
- `api/chat_wiring.py`：`_ALL_MODES` 保持三档（auto 候选口径不变，D10）；`tree_search.enabled` 时 capabilities `retrievals` 末尾追加 `tree`，并新增 `tree_enabled` 字段（只增）。
- `api/schemas/chat.py`：`RetrievalMode` 追加 `"tree"`。
- `services/chat_retrievers.py`：`tree_search.enabled` 时注册 `TreeChatRetriever`，`answer=None`，走 AnswerGenerator 路径；不声明 `on_log`。
- `core/response/citation_generator.py`：`Citation` 增可选 `section` / `line_start` / `line_end`，`None` 不输出（D8）。

**前端（PI6）**：`types.ts` / `modes.ts` 加 `tree`（标签 `Tree`，`tree_enabled` 为真且 `retrievals` 含 `tree` 才出现）；`ModeSelector.tsx` 选中态 rose 色系（light `bg-rose-100 text-rose-800`，dark `dark:bg-rose-500/20 dark:text-rose-200`，实测对比度 ≥ AA 后回填）；引用卡片在有 `section` / `line_start` 时显示「章节 · 第 N–M 行」。

### 3.3 配置契约

```yaml
# PageIndex 树检索（独立 HTTP sidecar，可选；enabled=false 时零加载、Chat 不出现 Tree）
tree_search:
  enabled: false
  backend: pageindex_http          # 唯一生产后端（fake 仅供测试注入）
  base_url: http://127.0.0.1:8585
  api_token: ${PAGEINDEX_API_TOKEN} # 从环境变量解析，不落盘
  timeout_s: 60                     # 整次 /search 总超时
  index_timeout_s: 900              # pageindex_sync 单文档 /index 超时
  max_nodes: 5
  max_loops: 3
  max_token_budget: 32000
  allowed_roots:                    # PathGuard 白名单（enabled 时必须非空）
    - ./data/documents
    - ./data/uploads
  denied_substrings:
    - .env
    - .git
    - data/db
```

校验：节缺失走上表默认；`enabled: true` 时 `base_url` 必须是 `http://127.0.0.1` / `http://localhost` / `http://[::1]`，`allowed_roots` 非空；`timeout_s` / `index_timeout_s` / `max_nodes` / `max_loops` / `max_token_budget` 为正数；未知 `backend` fail-fast。sidecar 侧 env（`scripts/pageindex/env.example`）：`PAGEINDEX_API_TOKEN`、`PAGEINDEX_HOST` / `PAGEINDEX_PORT`、`PAGEINDEX_STORAGE`（默认 `data/db/pageindex`）、`LLM_MODEL` / `LLM_API_BASE` / `LLM_API_KEY`（D4）。

### 3.4 sidecar HTTP 契约

统一响应 `{"success": bool, "data": ..., "error": str | null}`；全部端点要求 `Authorization: Bearer <token>`，绑定 127.0.0.1。

| 端点 | 请求 | 响应 `data` |
|------|------|------------|
| `GET /api/v1/status` | — | `{version, pageindex_version, model, documents}` |
| `GET /api/v1/documents` | — | `[{source_path, file_hash, format, doc_id, node_count, indexed_at}]` |
| `POST /api/v1/index` | `{source_path(绝对), file_hash}` | `{doc_id, format, node_count, llm_tokens}`；同 `file_hash` 已存在则幂等返回 |
| `DELETE /api/v1/index` | `{source_path}` | `{removed: bool}` |
| `POST /api/v1/search` | `{query, source_paths: [绝对] \| null, max_nodes, max_loops, max_token_budget}` | `{evidences: [TreeEvidence], llm_call_count, total_llm_tokens, telemetry}` |

`source_paths: null` 表示全部已索引文档；非空时只在其中检索，未索引的路径忽略并计入 `telemetry.unindexed`。主 venv 侧错误映射与 Sirchmunk 后端一致：超时 → `TreeSearchTimeoutError`，连接失败 / 4xx / 5xx / `success=false` → `TreeSearchBackendError`，非 JSON / 结构畸形 → `TreeSearchResponseError`；ChatEngine 按既有检索失败分支处理（默认 error 帧，`chat.fallback_to_chat_only: true` 时降级纯聊天）。

### 3.5 涉及文件清单

| 位置 | 文件 | 变更 |
|------|------|------|
| sidecar | `scripts/pageindex/{setup_venv.sh,start_sidecar.sh,env.example,server.py,pi_adapter.py,md_prep.py,tree_store.py,tree_loop.py}` | 新增 |
| sidecar | `scripts/pageindex/poc_compare.py` + 问题集 `tests/fixtures/pageindex/poc_questions.jsonl` | 新增（PI0） |
| 主 venv | `src/libs/tree_search/*` | 新增 |
| 主 venv | `src/core/settings.py`、`config/settings.yaml` | 新增 `tree_search` 节；`CHAT_RETRIEVAL_MODES` 追加 |
| 主 venv | `src/core/chat/retriever.py`、`src/api/chat_wiring.py`、`src/api/schemas/chat.py`、`src/observability/dashboard/services/chat_retrievers.py`、`src/core/response/citation_generator.py` | 只增（见 3.2） |
| 脚本 | `scripts/pageindex_sync.py`、`scripts/check_redlines.py` | 新增 / 增规则 |
| 前端 | `frontend/lib/chat/{types,modes,normalize}.ts`、`frontend/components/chat/ModeSelector.tsx`、引用卡片组件 | 只增 |
| 测试 | `tests/unit/test_tree_search_*.py`、`tests/unit/test_pageindex_sidecar_*.py`、`tests/unit/test_pageindex_sync.py`、`tests/integration/test_tree_search_http.py`、`tests/e2e/test_pageindex_integration.py`（门控）、前端 vitest / Playwright 用例 | 新增 |
| 其他 | `.gitignore`（`.venv-pageindex/`、`.env-pageindex`） | 追加 |

### 3.6 选型理由

1. **独立 sidecar 而非进程内 import**：`md_to_tree` 的裸 `print` 会污染 MCP stdio（API 进程与 MCP server 共用 `src/`）；litellm / openai-agents / mcp 依赖链重且迭代快；`submit_document` 受 multiprocessing spawn 约束。与 Sirchmunk 决策同构，见 [2026-10-05-sirchmunk-http-sidecar.md](../../.agents/notes/implemented/architecture/2026-10-05-sirchmunk-http-sidecar.md)。
2. **不复用 Sirchmunk sidecar 进程**：其 ASGI 应用是上游 `sirchmunk serve`（或 KE 的 `evolve_launcher.py`），本项目不拥有；两套依赖混装会让任一侧升级互相阻塞。
3. **独立 `libs/tree_search/` 而非扩展 `libs/agentic_search/`**：两者请求模型（paths + FAST/DEEP vs source_paths + 节点预算）与证据形态（字符偏移 vs 章节 / 页 / 行）不同；独立后 AC / AR / SX 冻结契约与测试零改动，代价是 PathGuard / CollectionPathResolver 跨 lib 复用（直接 import，二者不依赖 sirchmunk）。
4. **PI0 度量口径**（D9 只固定口径）：
   - 问题集：从最大的 3 篇 Markdown + 1 篇长 PDF（上游 `examples/documents/2023-annual-report.pdf`，只放 sidecar 工作区不入库）起草 20–30 条章节定位 / 跨节归纳题，每题标注目标章节（MD 行范围或 PDF 页范围），由用户逐条确认（MR0.2 先例）。
   - 命中率：返回证据与目标范围有交集的题目占比。
   - 引用可定位率：引用能直接给出章节与行号（或页码）的占比；方案 (a) 用离线近似计算：把 Hybrid 命中 chunk 在源文件中定位后推导标题路径，不改摄取代码。
   - 延迟 P50 / P95、单题 token；Sirchmunk DEEP 取 telemetry，树检索取原型计数。
   - 一次性建树成本（token 与耗时）单列。

## 4. 测试方案

- **离线确定性测试**（全部进 `pytest tests`，零网络、零真实模型，CI 禁网命令下通过）：
  - sidecar 纯模块（PI1）：`md_prep` 覆盖 #609 两种用例与行号映射；`tree_store` 原子写 / 幂等 / 删除；`tree_loop` 用 fake `complete` 覆盖选文档、选节点、下钻、截断、`max_loops` / 预算截停、非法 JSON 重试与降级。测试 import 纯模块时不得触发 `pageindex` / `litellm` import（测试断言 `sys.modules`）。
  - sidecar 服务（PI2）：`server.py` 以注入的 fake adapter 跑 FastAPI TestClient（鉴权 401、loopback 绑定参数、各端点 schema）；`pi_adapter.py` 不进主 venv 测试。
  - 主 venv 后端（PI3）：`httpx.MockTransport` 覆盖 Bearer、超时、连接拒绝、5xx、`success=false`、非 JSON、契约 fixture 解析（`tests/fixtures/pageindex/contract_search_response.json`，PI2 真实响应抓取后替换）；线程 HTTP stub 集成测试走真实 httpx 链路；配置节默认值与 fail-fast 校验。
  - 索引同步（PI4）：fake 历史表 + fake 后端覆盖新增 / 重建 / 删除 / 跳过 / 单条失败不中断 / `--dry-run` 无写入。
  - Chat（PI5）：fake 树检索后端驱动 ChatEngine，断言 `tree` 模式帧序列、引用含 `section` / `line_*`、collection 空定界集不调用后端、PathGuard 剔除越权证据、后端异常走既有失败分支。
  - 前端（PI6）：vitest 覆盖 `buildModeOptions`（`tree_enabled` 真 / 假）、`resolveMode`、配色类名、引用展示；Playwright 假后端新增 tree 用例（opt-in，需先 build）。
  - 红线（PI2）：`check_redlines.py` 新规则的正反样例单测。
- **真实依赖冒烟**：`RAG_PAGEINDEX_INTEGRATION=1`（默认跳过，需 sidecar 已启动且已同步）：真实 sidecar `/status`、对一篇 MD 与一篇 PDF 检索命中并带行号 / 页码；Chat `tree` 模式端到端出答案与引用。PI0 对照脚本只在 sidecar venv 手动运行，不进 pytest。
- **既有测试语义**（本规格不得破坏）：
  - `tree_search` 节缺失或 `enabled: false` 时：`compute_capabilities` 输出、`build_chat_retrievers` 注册集合、前端模式选项、MCP `tools/list` 与基线逐字节一致。
  - auto 候选仍取三档（`_ALL_MODES` 不含 `tree`），`routing_question.py` 文案与 MR 测试不变。
  - `Citation.to_dict()` 对既有 hybrid / agentic 结果输出键集合不变。
  - Sirchmunk：`libs/agentic_search/` 契约、`AgenticSearchFactory` 注册表、AC 定界语义与相关测试不变。
  - `check_redlines.py` 既有三条规则判定不变。
  - CMC 三档与 auto violet 配色测试不变。
  - 全量 `pytest tests -q` 与 CI 禁网命令保持全绿，计数只增不减。

## 5. 任务排期与进度

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|----------|------|
| PI0.1 | sidecar venv 与版本锁定：`setup_venv.sh` / `env.example` / `.gitignore`，D6 终裁 | `bash scripts/pageindex/setup_venv.sh` 在干净机器建出 `.venv-pageindex`，`pip show pageindex` 版本与脚本锁定一致；D6 行回填版本号 | 未开始 |
| PI0.2 | 问题集起草与确认 | `tests/fixtures/pageindex/poc_questions.jsonl` 20–30 条，每条含目标章节范围，用户逐条确认（记录确认日期） | 未开始 |
| PI0.3 | 四路对照与立项门终裁 | `poc_compare.py` 输出对照表（命中率 / 引用可定位率 / P50·P95 / 单题 token / 建树成本）落盘本规格附录；D4、D9 终裁回填；不达标则本规格止于预研并建派生台账 | 未开始 |
| PI1 | sidecar 纯模块 `md_prep` / `tree_store` / `tree_loop` | 第 4 章 PI1 用例全过；纯模块 import 不引入 `pageindex` / `litellm` | 未开始 |
| PI2 | sidecar 服务、`pi_adapter`、`start_sidecar.sh`、红线规则 | 本机启动后 `/status` 200、无 token 401；对 1 篇 MD + 1 篇 PDF 建树并检索返回带行号 / 页码证据；`python scripts/check_redlines.py` 0 违规且新规则正反样例单测通过；契约 fixture 替换为真实响应 | 未开始 |
| PI3 | `libs/tree_search/` + `tree_search` 配置节 | MockTransport / stub 集成测试全过；默认配置下 `load_settings()` 结果除新增节外不变；非法配置 fail-fast 单测覆盖 3.3 每条校验 | 未开始 |
| PI4 | `scripts/pageindex_sync.py` | 离线单测覆盖四类动作与 `--dry-run`；对真实 sidecar 跑两次，第二次新增 / 重建计数为 0 | 未开始 |
| PI5 | Chat 后端 `tree` 模式 | 第 4 章 PI5 用例全过；既有测试语义清单逐条有测试锁定或复跑通过 | 未开始 |
| PI6 | Chat 前端 Tree 选项、配色、引用展示 | `npm run typecheck && npm run lint && npm test` 全绿；rose 选中态 light / dark 实测对比度 ≥ 4.5:1 并回填；Playwright tree 用例通过 | 未开始 |
| PI7 | 门控真实冒烟 + 文档回写 | `RAG_PAGEINDEX_INTEGRATION=1` 冒烟通过并记录延迟与 token；USAGE_GUIDE 新章节与 README 回写完成（第 7 章）；全量禁网 pytest + 红线全绿 | 未开始 |

> 进度备注：交付后在各任务下回填状态与关键实测数据；全部收口后本规格移入 `docs/frozen/` 并更新 MOC.md。

## 6. 验收清单

- [ ] PI0 立项门通过，门槛与对照数据记录在本规格（或已按「止于预研」收口）。
- [ ] `tree_search.enabled: false` 时 Chat、MCP、capabilities 与基线逐字节一致。
- [ ] `tree_search.enabled: true` 且 sidecar 已同步时，Chat 选 Tree 能对长 Markdown 给出带「章节 · 行号」的引用，对 PDF 给出页码引用。
- [ ] 带 collection 的 Tree 检索只返回该 collection 定界集内证据；空定界集不调用 sidecar。
- [ ] `pageindex_sync.py` 幂等：连续两次运行第二次无新增 / 重建。
- [ ] 主 venv 无 `pageindex` / `litellm` import（红线脚本 0 违规）。
- [ ] `pytest tests -q` 与 CI 禁网命令全绿；前端 typecheck / lint / vitest 全绿。
- [ ] 回写完成（第 7 章）。

## 7. 实现合入后的回写清单

- `docs/reference/USAGE_GUIDE.md`：新增章节「Tree 检索模式（PageIndex sidecar）」——venv 搭建、env、启动、`pageindex_sync.py` 用法、`tree_search` 配置、隐私提示、门控冒烟。
- `README.md`：配置速查与 Web UI 模式说明补 Tree；开发文档导航表更新本规格状态。
- `config/settings.yaml`：`tree_search` 节（默认 `enabled: false`）与注释。
- `AGENTS.md`：仅在本规格冻结时新增一行「编码前必读」条目（docs-governance §3 第 5 条），开发中不改。
- `docs/MOC.md`：分区说明与谱系表随状态更新。

## 8. 隐私注记

- **暴露面**：sidecar 只绑定 127.0.0.1 并要求 Bearer；主 API 不新增公开路由，Chat 只多一个请求值。树索引（含 LLM 摘要与 MD 节点原文）落盘 `data/db/pageindex/`，已被 `denied_substrings` 的 `data/db` 覆盖，不会被 agentic 检索读到；该目录不入 git。
- **外发边界**：建树时，MD 每个超过摘要阈值的节点原文、PDF 页文本发给 D4 配置的摘要模型——**等于把已同步文档的全文发给外部 LLM 一次**；检索时，文档描述、树结构（标题 + 摘要）与选中节点原文发给检索模型。与 Sirchmunk 外发边界同类（见 [14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md](../frozen/14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md) §8），但建树的一次性全文外发是新增项，USAGE_GUIDE 必须写明。
- **开关策略**：`tree_search.enabled: false` 默认关闭；`pageindex_sync.py` 只由用户手动运行，不在摄取时自动触发（D5），启用即视为接受上述外发。云端模式不做（D1）。

## 9. 风险与开放问题

| # | 风险 / 问题 | 应对 |
|---|------------|------|
| R1 | 上游迭代快，仓库与 PyPI 版本口径不一致 | D6 锁定精确版本；`pi_adapter.py` 收敛全部上游调用，升级时只改一处并重抓契约 fixture |
| R2 | GLM 的 JSON / 选节点质量不足 | PI0 实测（D4）；`tree_loop` 非法输出重试 + 降级；换模型只改 env |
| R3 | 超大节点（顶层章节数十万字）撑爆上下文 | `max_evidence_chars` + 优先下钻子节点 + 截断标记；`md_to_tree` 可选 thinning 参数在 PI1 评估 |
| R4 | 一次树检索超过 `timeout_s` | `max_loops` / `max_token_budget` 截停；超时走既有失败分支；PI0 / PI7 记录 P95 后校准默认值 |
| R5 | 建树成本：1.7 MB Markdown 摘要 token 量可观 | PI0.3 单列建树成本；同步命令支持 `--collection` 分批；`file_hash` 未变不重建 |
| R6 | sidecar 内并发：上游 DocStore 锁 + 单进程同步端点，不适合并发 | 首期单用户本机场景接受串行；需要并发时另议 |
| R7 | #609 上游修复后预处理与上游行为重叠 | `md_prep` 规则幂等（对已规范的 ATX 输入无副作用），升级版本时复跑 PI1 用例 |
