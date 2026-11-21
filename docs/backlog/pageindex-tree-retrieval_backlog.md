# PageIndex 树检索 sidecar 接入提案（pageindex-tree-retrieval）

> 起源：会话内调研（2026-11-21，非文件来源）：阅读上游 [VectifyAI/PageIndex](https://github.com/VectifyAI/PageIndex)（main `add6b17`，2026-11-21；PyPI 最新 `0.7.4`）的 README 与 `pageindex/local_api.py`、`local_store.py`、`agent_tools.py`、`page_index_md.py`、`client.py` 源码，对照本仓库 `src/libs/agentic_search/`（工厂 / HTTP 后端 / 适配器 / 契约类型）、`scripts/sidecar_manager.py`、`scripts/sirchmunk/start_sidecar.sh` 与 Sirchmunk sidecar 架构决策（[2026-10-05-sirchmunk-http-sidecar.md](../../.agents/notes/implemented/architecture/2026-10-05-sirchmunk-http-sidecar.md)）。依据规格：[03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md](../frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md)（进程隔离红线、契约风格）、[18_AGENTIC_COLLECTION_ISOLATION_SPEC_frozen.md](../frozen/18_AGENTIC_COLLECTION_ISOLATION_SPEC_frozen.md)（collection 定界）。
>
> **状态：草案**。三问中第 1 问仅有结构性推断、尚无实测失败样例，第 3 问依赖 PI0 预研门；在 PI0 产出对比数据前不进入排期。任务前缀 `PI` 为暂定，立项时核查冲突。

## 1. 想法

把 PageIndex 的「树索引 + LLM 沿树推理」作为**第四种检索能力**接入，形态与 Sirchmunk 相同：独立 venv 的本机 HTTP sidecar，主 venv 只经 HTTP 调用。目标是补上现有三路（Dense / BM25 / Sirchmunk 词法 agentic）都不覆盖的**长结构化文档按章节定位**与**页 / 行级引用**。

- 不使用 PageIndex 的答案生成（`chat()` 产出的回答与本项目 AnswerGenerator 重叠），只取其**检索结果**（命中节点 + 原文 + 页码 / 行号）。
- 不放进现有 Sirchmunk sidecar 进程：该进程是上游 `sirchmunk serve`（或 KE 系列的 `evolve_launcher.py`），本项目不拥有其 ASGI 应用，两者依赖也不应混装。

## 2. 上游现状（2026-11-21 核实）

| 项 | 现状 | 出处 |
|----|------|------|
| 原理 | 每篇文档生成树状目录索引（节点：`title` / `node_id` / `start_index` / `end_index` / `summary`），检索时 LLM 读树选节点，再读对应页原文；无向量、无切块 | README「What is PageIndex」 |
| 本地索引格式 | **只接受 PDF**：`submit_document` 对非 `.pdf` 抛 `PageIndexAPIError("only PDF files are supported in local mode")` | `local_api.py` `submit_document` |
| 本地 PDF 建树 | 默认 flash 模式：版面解析（pypdfium2）出结构，LLM 只写节点摘要与精修；约 $0.001/页，9–1098 页耗时约 13 s–4.5 min | `local_api.py` `_index_flash`；README Benchmarks |
| Markdown | 只有底层函数 `md_to_tree`（按 `#` 标题与整行粗体建树，节点带 `line_num`）；**不进 DocStore**，没有 `chat()` / agent 工具；函数内有裸 `print`；[Issue #609](https://github.com/VectifyAI/PageIndex/issues/609)（open）：首个标题前的正文丢失、Setext 标题不识别 | `page_index_md.py` |
| 本地存储 | `./.pageindex/docs/<doc_id>/{doc,tree,pages}.json` + `manifest.json`，fcntl 跨进程锁；支持每文档 `metadata` dict；**不支持 collection / folder** | `local_store.py`；`local_api.py` |
| 检索接口 | 只读 agent 工具 `browse_documents` / `get_document` / `get_document_structure` / `get_page_content`（`remove_document` 需 `include_management`）；`chat()` 经 openai-agents + LiteLLM 跑 agent，`citations=True` 时输出 `<cite doc= page=/>`，`resolve_citations()` 可解析 | `agent_tools.py` `TOOL_CONTRACT`；README Step 3 |
| 模型接入 | LiteLLM 命名；`index_backend` / `chat_backend` 透传 `api_key` / `api_base`（可接 OpenAI 兼容端点，如智谱）；上游建议检索侧用「能负担的最好模型」 | `client.py` 构造参数文档 |
| 云端（付费） | 免 OCR 多模态、框选引用、collection / metadata、MCP server、Page Search API；文档需上传至 PageIndex | README「PageIndex Cloud」 |
| 许可 / 运行时 | MIT；Python ≥ 3.10；依赖 litellm、openai、openai-agents、mcp、PyPDF2、pypdfium2 等 | `pyproject.toml` |
| 迭代速度 | 592 次提交，仓库 `pyproject.toml` 写 `0.2.10` 而 PyPI 为 `0.7.4`，版本口径不一致 | GitHub API；PyPI |

## 3. 与本项目的互补点

| 能力 | 本项目现状 | PageIndex 补什么 |
|------|-----------|------------------|
| 长文档章节定位 | Dense + BM25 按 chunk 召回（chunk_size 1000）；Sirchmunk 按 rg 词法找文件后 LLM 迭代，均不感知章节结构 | 沿目录树推理，适合「哪一节讲 X」、跨章节归纳类问题 |
| 引用粒度 | chunk，或 `AgenticEvidence.char_start/char_end`（抽取文本字符偏移，无页码 / 行号，见 03 规格 4.6） | PDF 页级、Markdown `line_num` + 节点标题，更贴「引用必须可溯源」 |
| 文档大纲 | `get_document_summary` 仅摘要 | 树形大纲可作为摘要增强或新增「文档大纲」MCP 工具 |
| 外部 agent 自主推理 | MCP 客户端只能拿到检索结果 | 可把读树 / 读页工具暴露给 MCP 客户端，由客户端模型推理，本项目不出该部分 LLM 费用 |
| 模式路由 | Chat 三档 `hybrid` / `agentic_fast` / `agentic_deep`（`core/settings.py` `CHAT_RETRIEVAL_MODES`）+ MR 的 `auto` | 候选新增一档「结构化长文档」供 Mode Router 定档 |

语料适配（2026-11-21 `data/documents` 实测）：29 个 `.md` / 1 `.docx` / 1 `.pptx` / 1 `.xlsx` / 1 `.html`，**无 PDF**。大型 Markdown 有结构可用：最大 1.7 MB / 1045 个标题行、470 KB / 253、351 KB / 166、315 KB / 187；其余多为 3–50 KB 的短笔记，树检索预期无收益。结论：**Markdown 支持是本提案的主路径**，恰是上游最弱的一环。

## 4. 候选接入架构

```
主 venv（永不 import pageindex）
  MCP tool / AgenticRouteAdapter / Chat retriever
     └─ PageIndexHttpBackend（httpx，工厂键 pageindex_http）
            │ 127.0.0.1:<port> + Bearer
            ▼
.venv-pageindex（本项目自写的轻量 HTTP 服务，非上游提供）
  ├─ POST   /index   摄取成功后建树：PDF → PageIndexClient.submit_document；
  │                  MD → 预处理 + md_to_tree + 自建存储（绕开 #609）
  ├─ DELETE /index   删除联动
  ├─ POST   /search  检索循环 → evidences（source_path + page/line + node 标题 + 原文）
  └─ GET    /status  探活（复用 sidecar_manager 探活模式）
```

进程隔离的理由与 Sirchmunk 同构（见架构决策笔记）：`md_to_tree` 裸 `print` 会污染 MCP stdio；litellm / openai-agents / mcp 重依赖不进主 venv；`submit_document` 有 multiprocessing spawn 约束。红线可扩展 `scripts/check_redlines.py` 增加 `pageindex` import 检查。

## 5. 契约差距（立项前需逐项定案）

| # | 差距 | 现状 | 候选方向 |
|---|------|------|---------|
| G1 | 单后端路由 | `AgenticSearchFactory.create` 只按 `agentic_search.backend` 取一个后端 | 新增独立配置节（如 `tree_search:`）与独立工厂，或把 backend 扩为列表；涉及 `core/settings.py` 配置模型 |
| G2 | 请求模型 | `AgenticSearchRequest` 以 `paths` + `mode ∈ {FAST, DEEP}` 建模 | sidecar 维护 `source_path ↔ doc_id`（利用上游 `metadata`）；AC 规格的 `CollectionPathResolver` 产出路径集合后换成 doc_id 列表实现 collection 定界（上游本地模式不支持 collection） |
| G3 | 证据契约 | `AgenticEvidence` 仅 `source_path` / `snippet` / `score` / `char_start` / `char_end` | 只增不改：可选 `page` / `line_start` / `line_end` / `section_title` / `node_id`；`evidence_to_retrieval_results` 的 metadata 同步只增 |
| G4 | 证据获取 | `chat()` 产出答案 + 引用标签，不直接给证据列表 | 优先自建检索循环（`agent_tools()` + 本项目 LLM），记录实际 `get_page_content` 读取的页作为证据；备选：解析 `<cite>` 后回读页面 |
| G5 | 索引生命周期 | Sirchmunk 无索引；`DocumentManager` 协调 4 个存储删除；SHA256 增量跳过 | 摄取成功后异步建树（失败不阻塞主摄取）、删除联动第 5 个存储、增量口径对齐 SHA256；需读 04 规格确认挂点 |
| G6 | 超时与成本 | `agentic_search.timeout_s: 30`；一次树检索为多轮 LLM 调用 | 独立 `timeout_s` / 轮数上限 / token 预算；trace 记 `llm_call_count` / `total_llm_tokens` |

## 6. 提案三问（草案级回答）

1. **解决了哪个已观察到的问题？** 结构性推断：语料中有 1.7 MB / 千级标题的长 Markdown，chunk 召回丢失章节上下文，Sirchmunk 证据只有字符偏移、无章节 / 行号。**尚无实测失败样例**，需 PI0 在真实问题上确认现有三路确实答错或引用不可读。
2. **是否有更简单的方案？** 候选更小改动：(a) MarkdownLoader / splitter 给 chunk 补「标题路径」metadata，并在引用里显示章节与行号——不引入新进程，能覆盖「引用可读性」一半诉求；(b) 不建 sidecar，只在 MCP 客户端侧旁路挂 PageIndex（零代码，但进不了 trace / Hybrid 融合 / collection 隔离，且本地 MCP 仅云端提供）。PI0 需把 (a) 一并纳入对照，(a) 达标则本提案止于分析。
3. **如何确认有效？** PI0 预研门：选最大的 3 篇 Markdown + 1 篇长 PDF（可用上游 `examples/documents/2023-annual-report.pdf`），起草 20–30 条章节定位 / 跨节归纳类问题（用户逐条确认，对齐 MR0.2 先例），对比 Hybrid、Sirchmunk DEEP、方案 (a)、PageIndex 四路的命中率、引用可定位率、延迟与 token；门槛立项时由用户终裁。

## 7. 待决问题（立项前终裁）

| # | 问题 | 选项 |
|---|------|------|
| D1 | 本地还是云端 | 本地（数据不出本机，仅外发 LLM；需自建 Markdown 路径）/ 云端（多模态 + collection + 框选引用，付费且文档上传至 PageIndex） |
| D2 | 接入面 | 仅 MCP 独立工具 / 并入 Hybrid 第三路之外的第四路 / Chat 新增模式（含 Mode Router 候选） |
| D3 | Markdown 路径 | 自建 md 树存储 + 检索循环 / 等上游 #609 修复并支持 md 入库 / 只做 PDF |
| D4 | 检索模型 | 复用智谱 GLM（OpenAI 兼容端点，工具调用质量待测）/ 单独配置更强模型 |
| D5 | 建树时机 | 摄取时同步 / 摄取后异步任务 / 首次查询懒建 |
| D6 | 上游版本 | 锁定 PyPI 版本号（仓库与 PyPI 版本口径不一致，需确认以哪个为准） |

## 8. 草拟任务（立项后改写）

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|---------|------|
| PI0 | 门控预研：sidecar venv 内脚本跑四路对照（§6 第 3 问） | 对照表落盘（命中率 / 引用可定位率 / P50·P95 延迟 / token）；用户依门槛裁定立项或止于分析 | 未开始 |
| PI1 | sidecar 服务骨架：`.venv-pageindex` 安装脚本、`/status` `/index` `/search`、Bearer、仅 loopback | 本机冒烟可建树并检索；`check_redlines.py` 新增 pageindex import 规则通过 | 未开始 |
| PI2 | 主 venv 后端：`PageIndexHttpBackend` + 配置节 + 契约字段只增（G1/G3） | `httpx.MockTransport` 离线单测覆盖鉴权 / 超时 / 不可达 / 畸形响应；既有 agentic 测试语义不变 | 未开始 |
| PI3 | 索引生命周期：摄取后建树 + 删除联动 + 增量（G5） | 摄取 → 可检索、删除 → 不再命中；建树失败不影响主摄取结果 | 未开始 |
| PI4 | 接入面（依 D2）+ collection 定界（G2） | 带 collection 的检索只返回定界集内证据（对齐 AC 规格 D3/D5 口径） | 未开始 |
| PI5 | 门控真实冒烟（环境变量门控，默认跳过）+ USAGE_GUIDE 回写 | 门控冒烟通过；全量离线 pytest 与 CI 禁网命令全绿 | 未开始 |

## 9. Out of Scope（草案）

- 使用 PageIndex 的答案生成（`chat()` 回答文本）替代本项目 AnswerGenerator。
- 在主 venv 安装或 import `pageindex`。
- 把 PageIndex 装进现有 Sirchmunk sidecar 进程或其 venv。
- 替换现有 Dense / BM25 / Sirchmunk 任一路检索。
- 云端 Page Search / Page Extract API（D1 选云端时另议）。

## 10. 隐私注记

- 本地模式：建树时节点文本发给索引模型（摘要），检索时命中页原文发给检索模型；外发边界与 Sirchmunk 相同（见 [14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md](../frozen/14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md) §8），默认关闭。
- 云端模式：**原文件整体上传至 PageIndex**，暴露面显著大于本地模式，需用户单独确认。
- 树索引（含 LLM 摘要）落盘在 sidecar 存储目录，须纳入 PathGuard `denied_substrings` 与 `.gitignore`。

## 11. 流转记录

- 2026-11-21：草案创建（会话内调研沉淀；上游核实基于 main `add6b17` 源码，语料统计基于本地 `data/documents`）。
