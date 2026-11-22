<!-- 规格定位：以独立 venv 的本机 HTTP sidecar 接入 PageIndex「树索引 + LLM 沿树推理」，在 Web UI Chat 新增 `longdoc` 检索模式，补长结构化文档的章节定位与页 / 行级引用。基线：S / SX / WC / LG / WS / SR / CMC / MR / AR / RA / AC 均已收口冻结。与既有规格的关系：独立增量；不改 Sirchmunk agentic 契约（`libs/agentic_search/` 与 `agentic_search` 配置节）、不改三档既有模式与 `auto` 的行为契约、不改 MCP 工具、不改摄取管线与 DocumentManager。 -->

# PageIndex 树检索规格 (PAGEINDEX_TREE_RETRIEVAL_SPEC)

> 版本：0.2 — 0.1 由 backlog 提案立项为可执行规格；0.2（2026-11-21）按规格评审修订（评审意见逐条对照基线代码核实后采纳）：消除 capabilities「逐字节不变」与新增 `longdoc_enabled` 字段的矛盾，写明需同步的既有测试断言，补齐错误分类、`ChatCapabilities` schema、配置锚定与入口预建、上下文位置头、前端改动面、trace 打点、同步命令删除边界、sidecar 契约边界与 PI0 可执行性细节，修正 Chat WS 路径与语料计数口径。PI0 为立项门（四路对照不达标即搁置），PI0 通过前 PI1 起不开工；0.3（2026-11-22）PI0.3 四路对照完成，基线落盘第 10 章附录，D9 终裁延后至新增的 PI0.4（用户裁决），PI1 起仍不开工；0.4（2026-11-22）方向变更（用户裁决）：MD 不再经上游 `md_to_tree` 建树，改为转换成派生 PDF 后与原生 PDF 同走 `PageIndexClient.submit_document` / DocStore——D3 改写，新增 D11（转换链）/ D12（派生 PDF 存放与证据回映射），D5 / D7 / D8 / G4 随之调整；原 PI0.4 拆为 PI0.4–PI0.7（单篇试转实测 → 度量口径修正与原型调优 → 新管线复测 → D9 终裁与收尾），PI1 范围改为转换与回映射模块（`md_prep` 删除），R3 改写、R7 作废、新增 R8–R10；变更依据与各问题方案见 10.4
>
> 起源与决策依据：由 backlog/pageindex-tree-retrieval_backlog.md 单文件晋升，原文见 git 历史；提案起源为会话内调研（2026-11-21，非文件来源：上游 [VectifyAI/PageIndex](https://github.com/VectifyAI/PageIndex) main `add6b17` 源码 + 本仓库 agentic_search / sidecar 架构核对）。依据 [03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md](../frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md)（进程隔离红线、HTTP sidecar 契约风格）、[18_AGENTIC_COLLECTION_ISOLATION_SPEC_frozen.md](../frozen/18_AGENTIC_COLLECTION_ISOLATION_SPEC_frozen.md)（collection 定界 D3/D5）、[15_MODE_ROUTER_SPEC_frozen.md](../frozen/15_MODE_ROUTER_SPEC_frozen.md)（Chat 模式常量六处登记、auto 候选口径、立项门先例）、[11_CHAT_MODE_COLORS_SPEC_frozen.md](../frozen/11_CHAT_MODE_COLORS_SPEC_frozen.md)（模式配色规则）、[16_ROOT_ANCHOR_SPEC_frozen.md](../frozen/16_ROOT_ANCHOR_SPEC_frozen.md)（相对路径按项目根解析）
>
> 基线：`c732778`（fork/main），上述系列均已收口冻结；`tree_search` 配置节、`longdoc` 模式与 PageIndex sidecar 均不存在
>
> 日期：2026-11-21

红线：主 venv 永不 `import pageindex`（及其 litellm / openai-agents 依赖链），只经 HTTP 调用独立进程；`tree_search.enabled: false`（默认）时零加载，存量行为不变：capabilities 既有字段的键与值、`retrievals` 取值不变，唯一差异是新增 `longdoc_enabled: false`（沿用 MR 规格 `auto_routing` 只增字段先例，需同步的既有断言见第 4 章）；模式选择器选项、auto 候选、MCP `tools/list` 逐字节不变。

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
- 10 附录：PI0 四路对照基线与 0.4 方向变更（本规格追加章节）

---

## 1. 项目概述

### 1.1 结论速览

| 问题 | 结论 |
|------|------|
| 要解决什么问题？ | 语料中有千级标题的长 Markdown（最大 1.7 MB / 1045 个 `#` 起始行），Dense + BM25 按 1000 字切块召回会丢章节上下文，Sirchmunk 证据只有抽取文本字符偏移；「哪一节讲 X」「跨章节归纳」类问题缺少按结构定位的检索路径，引用也无法落到章节 / 行号。**尚无实测失败样例**，由 PI0 在真实问题上确认 |
| 定位是什么？ | Web UI Chat 的第五档请求值 `longdoc`（与 `hybrid` / `agentic_fast` / `agentic_deep` / `auto` 并列）：sidecar 沿文档树选节点返回证据，答案仍由本项目 AnswerGenerator 生成；不替换任何既有模式 |
| 实施范围（已决策） | PI0 为立项门（门槛按 D9 终裁，不达标即搁置，本规格止于预研并移入派生台账）；PI1–PI6 为承诺任务（门控通过后）；PI7 含门控真实冒烟。仅本地模式（D1），MCP / Hybrid 第四路 / auto 候选不做（D2、D10） |
| 主要代价 | 多维护一个进程与一个 venv；建树要把文档全文送 LLM 写节点摘要（一次性，按 token 计费）；一次树检索为多轮 LLM 调用（预期十秒到数十秒级）；Markdown 需先转换为派生 PDF 再建树（D3 / D11，新增 pandoc 与 Chromium 两个系统工具依赖），检索循环需自写（D7） |

### 1.2 背景与现状

**上游现状**（2026-11-21 核实，main `add6b17`；PyPI 最新 `0.7.4`，仓库 `pyproject.toml` 写 `0.2.10`，口径不一致）：

| 项 | 现状 | 出处 |
|----|------|------|
| 原理 | 每篇文档生成树状目录索引（节点 `title` / `node_id` / `start_index` / `end_index` / `summary`），检索时 LLM 读树选节点再读原文；无向量、无切块 | README |
| 本地 PDF | `submit_document` 只接受 `.pdf`；默认 flash 模式版面解析出结构，LLM 只写摘要与精修，约 $0.001/页 | `local_api.py` |
| 本地 Markdown | 只有底层函数 `md_to_tree`（按 `#` 标题与整行粗体建树，节点带 `line_num`），**不进 DocStore**、无检索工具；函数内有裸 `print`；[Issue #609](https://github.com/VectifyAI/PageIndex/issues/609)（open）：首个标题前正文丢失、Setext 标题不识别。**0.4 起本规格不再使用此路径（D3）** | `page_index_md.py` |
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
- 错误分类：`src/core/chat/engine.py:109-138` `classify_error()` 只识别 `DeepDisabledError` / `LLMRequestError` / `AgenticSearch*Error` / httpx 原生异常与超时类，其余一律落 `ERROR_INTERNAL`（`transient=False`：不受 `pipeline_max_retries` 重试，且 `engine.py:398-399` 按内部异常打印堆栈）。
- 模式常量与引擎校验：`ChatEngine` 构造时拒绝 `RETRIEVAL_MODES` 之外的检索器键（`engine.py:217`），`available_retrievals` 与请求校验也以它为准（`engine.py:241`、`engine.py:274`）；`tests/unit/test_mode_router_config.py:54` 断言 `RETRIEVAL_MODES` 恰为三元组；`src/core/settings.py:1052` 的 `mode_router.fallback_retrieval` 直接复用 `CHAT_RETRIEVAL_MODES` 校验。
- capabilities：`GET /api/v1/chat/capabilities` 绑定 `response_model=ChatCapabilities`（`src/api/routes/chat.py:76-78`；模型在 `src/api/schemas/chat.py:81-89`，未声明字段会被静默丢弃）；`tests/integration/test_api_chat.py:521-532` 与 `frontend/lib/chat/normalize.test.ts:164-185` 均为整字典精确断言。
- 上下文头：`AnswerGenerator.build_context()`（`src/core/chat/answer_generator.py:68-74`）只渲染 `[id] source, p.N`，不含章节与行号。
- 白名单根：`PathGuard` 对不存在的根直接抛 `PathGuardError`（`src/libs/agentic_search/path_guard.py:57-58`）；相对 `allowed_roots` 在 `load_settings()` 时经 `resolve_data_path` 锚定项目根（`src/core/settings.py:898`，RA2）；`scripts/start_api.py:94` 只对 `settings.agentic_search` 调 `ensure_local_roots`，该函数按 `enabled` / `allowed_roots` / `denied_substrings` 属性鸭子类型读取，可直接复用。
- 前端：`frontend/lib/chat/modes.ts:45` 在 `agentic_enabled` 为假时提前返回；引用标签由 `frontend/lib/chat/citations.ts` `citationLabel()` 统一生成（`SourcesPanel.tsx` 与 `MarkdownContent.tsx` 共用，文案为英文，如 `a.pdf p.4`）；`normalizeCitation` 只保留已知字段；`SourcesPanel.tsx:66` 以 `${id}-${chunk_id}` 作 React key。
- 摄取历史 v2 主键为 `(source_path, collection)`（`src/libs/loader/file_integrity.py:75`），同一物理文件可在多个 collection 各有一条 success 记录。
- Chat WS 端点为 `WS /api/v1/chat`（`src/api/routes/chat.py:48`）；带 `/ws` 后缀的是 Playground Deep 流式端点 `WS /api/v1/search/ws`。
- 摄取历史：`src/libs/loader/file_integrity.py` `SQLiteIntegrityChecker`（v2 success 记录含 `source_path` / `file_hash` / collection）；`src/libs/agentic_search/collection_paths.py` `CollectionPathResolver.resolve(collection)` 已能把 collection 推导为 PathGuard 校验后的绝对路径集合。
- 语料（`data/documents` 实测）：29 个 `.md`、各 1 个 `.docx` / `.pptx` / `.xlsx` / `.html`，**无 PDF**；大型 Markdown 1.7 MB（1,703,013 B）/ 1045 个以 `#` 起始的行、470 KB / 253、351 KB / 166、315 KB / 187，其余多为 3–50 KB 短笔记。计数口径为「行首第一个字符即 `#`」；最大一篇另按两种口径复核：先去掉行首空白再判断为 1047，只计围栏代码块外的合法 ATX 标题为 1036（后三篇行首口径与 ATX 口径一致）。

### 1.3 决策依据与终裁

提案三问的答案：

1. **已观察到的问题**：见 1.1、1.2（结构性推断，PI0 负责补实测样例）。
2. **更简单的方案**：
   - **方案 (a)**：MarkdownLoader / splitter 给 chunk 补「标题路径」metadata、引用显示章节与行号。不引入新进程，能覆盖引用可读性诉求，但不改变召回方式。PI0 以离线近似方式纳入对照（3.6 第 4 条）；(a) 达标则本规格止于预研。
   - **MCP 客户端旁路挂 PageIndex**：零代码，但上游本地模式无 MCP server（仅云端提供），且进不了 trace / Chat / collection 隔离。不采用。
3. **如何确认有效**：PI0 四路对照（Hybrid、Sirchmunk DEEP、方案 (a) 离线近似、PageIndex 树检索原型）在用户逐条确认的问题集上比较命中率、引用可定位率、P50 / P95 延迟与 token；端到端由 PI7 门控冒烟确认 Chat `longdoc` 模式可用且引用落到章节 / 行或页。

| 编号 | 决策 | 终裁 |
|------|------|------|
| D1 | 本地还是云端 | **本地模式**（2026-11-21，用户拍板）。文档与树索引留在本机，仅 LLM 调用外发；云端（原文件上传 PageIndex、付费）不做 |
| D2 | 首期接入面 | **Chat 新增模式 `longdoc`**（2026-11-21，用户拍板；模式名由初稿 `tree` 更名为 `longdoc`，后端能力命名 `tree_search` 不变）。MCP 独立工具、Hybrid 第四路融合不做；Query Playground 不做（2.2） |
| D3 | Markdown 路径 | **MD 转派生 PDF 后与原生 PDF 同走上游 DocStore**（2026-11-22 用户再拍板，取代 2026-11-21 的「自建 MD 树存储 + 检索循环」）：MD 按 D11 转为派生 PDF，调用 `PageIndexClient.submit_document` 建树，与原生 PDF 共用 DocStore 与读取接口；上游 `md_to_tree` 管线废弃（绕开 #609 的 `md_prep` 预处理随之删除，R7 作废）；派生 PDF 存放与证据回映射见 D12；检索循环两种格式统一自建（D7）。依据见 10.4（PI0.3 实测：MD 树 1.7 MB 篇 1,881 节点、建树 61 分钟、单题 token 中位 69.6K；原生 PDF 222 页 177 节点、建树 33 秒）。新风险：转换后的结构识别质量与建树成本未知，由 PI0.4 单篇试转先测（R8） |
| D4 | 检索 / 摘要模型 | **sidecar 独立 env 配置，默认智谱 GLM（OpenAI 兼容端点，经 LiteLLM `openai/<model>` + `api_base`）**（规格设计决策，与 Sirchmunk sidecar「LLM 配置独立于 `settings.llm`」同口径）。PI0 实测工具调用 / JSON 输出质量，终裁回填本行；换模型只改 env，不改代码。**PI0 实测（2026-11-22）**：`openai/glm-5.3-flash`（Coding Plan 端点）下树检索逐层下钻全程 0 次非法 JSON 输出（telemetry `invalid_output: 0`）、选文档 / 选节点 / 页码证据读取正常，JSON 可用性初测通过；正式终裁与 D9 一并在 PI0.7 回填（0.4 拆分后） |
| D5 | 建树时机 | **对账同步命令 `scripts/pageindex_sync.py`**（规格设计决策）：以摄取历史 v2 success 记录为准，`file_hash` 变化即重建、历史中已不存在的即删除、非 `.pdf` / `.md` / `.markdown` 跳过并计数。首期不改摄取管线与 DocumentManager（04 规格的 7 阶段与删除链路不动）；摄取后自动触发列入 2.2。**0.4 补充（D3 变更）**：MD 的 `file_hash` 仍取原 MD 的 SHA256，派生 PDF 按该值命名（D12）；MD 内容变化即重新转换并重建，删除 MD 索引时连带删除派生 PDF；转换失败单列计数，与建树失败区分 |
| D6 | 上游版本 | **锁定 PyPI 精确版本 `0.7.4`**（2026-11-21 PI0.1 终裁：PyPI 最新稳定版，2026-11-19 发布，`requires_python >=3.10`；上游仓库 `pyproject.toml` 写 `0.2.10` 与发布口径不一致，按 PyPI 为准。`setup_venv.sh` 写死版本、`start_sidecar.sh` 启动日志打印实际版本，对齐 KE0 先例）。已实测该版本导出 `PageIndexClient.submit_document` / `get_document_structure` / `get_page_content` 与 `pageindex.page_index_md.md_to_tree`（含 `if_thinning` / `if_add_node_summary` / `if_add_node_id` 参数，PI1 建树所需齐备） |
| D7 | 检索循环形态 | **自建「选文档 → 选节点 → 读原文 → 判是否足够」循环**（规格设计决策）：不用上游 `chat()`（会生成答案，与 AnswerGenerator 重叠）与 `agent_tools()`（只覆盖 DocStore 中的 PDF，D3 的 MD 存储用不上）。LLM 调用经注入的 `complete(prompt) -> str`，循环本体是不 import pageindex 的纯模块，可在主 venv 离线单测。**0.4 重估**：D3 变更后全部文档进 DocStore，原先否决 `agent_tools()` 的「覆盖不到 MD」理由失效；自建结论暂维持，理由改为 PI0.3 实测需要的逐层下钻、每题 token 预算与调用前检查、证据回映射（D12）上游工具循环均不提供。`agent_tools()` 的只读工具能否直接充当 `read_node` / 结构读取的实现由 PI0.5 复核，结论回填本行 |
| D8 | 证据到引用 | **`RetrievalResult.metadata` 承载 `retrieval_route="longdoc"` / `source_path` / `page` / `page_end` / `section` / `line_start` / `line_end` / `node_id`；`Citation` 只增可选字段 `section` / `line_start` / `line_end`，值为 `None` 时 `to_dict()` 不输出该键**（规格设计决策）：既有 hybrid / agentic 引用的 `to_dict()` 逐字节不变，WS `sources` 帧只增不改。**0.4 补充**：MD 证据（派生 PDF 建树）的 `page` / `page_end` 置 `None`（派生 PDF 页码对用户无意义），`section` / `line_*` 由 D12 标题回定位得出，`source_path` 为原 MD 路径；页码只出现在原生 PDF 证据上 |
| D9 | PI0 门槛 | **本规格只固定度量口径（3.6 第 4 条），数值门槛在 PI0 基线跑出后与用户共同拍板**（规格设计决策，沿用 MR D2 先例）。门槛终裁前 PI1 不开工；终裁回填本行与 PI0.3 行。**2026-11-22 基线已出（第 10 章附录）**：树检索 md hit 0.28 低于 hybrid 0.40、P50（53s vs 0.8s）与单题 token（~6 万 vs 0）高一个数量级，locatable 为唯一优势（0.68 vs 0.52）；用户裁决终裁延后至 PI0.4（可选原型调优后复测），PI1 起仍不开工。**0.4 方向变更后**：终裁改在新管线复测（PI0.6）完成后的 PI0.7 进行，门槛按 PI0.5 修正后的口径拍板 |
| D10 | auto 候选 | **`longdoc` 不进入 auto 候选**（规格设计决策，依据 1.2 第 2 条）：`_ALL_MODES` 保持三档并改名语义为「auto 候选与既有三档」，`longdoc` 单独追加到 capabilities `retrievals`；纳入候选需新增 criteria 文案并按 MR D2 门槛复核，另立提案 |
| D11 | MD → PDF 转换链 | **`pandoc` 转独立 HTML → headless Chromium `--print-to-pdf`**（0.4 设计决策，PI0.4 实测后终裁回填）：pandoc 3.x 处理 GFM（中文、表格、围栏代码块）；HTML 内嵌打印 CSS（代码块 `pre-wrap` 防截断、`h1`–`h6` 保持层级字号、不强加分页）；Chromium 优先用 sidecar venv 内 playwright 管理的浏览器（可复现），回退系统 Chrome。目录有三种做法待 PI0.4 对比：不带目录、`--toc` 生成无页码目录、两遍渲染生成带页码目录（年报 222 页建树 33 秒部分归功于自带目录）。本机无 xelatex / wkhtmltopdf / weasyprint，不作候选。转换在 sidecar 进程内经子进程调用，主 venv 不感知 |
| D12 | 派生 PDF 存放与证据回映射 | **派生 PDF 存 `<PAGEINDEX_STORAGE>/derived/<md_sha256>.pdf`，证据一律回映射到原 MD**（0.4 设计决策）：① 不放 `data/documents/` / `data/uploads/`，否则 hybrid 摄取与 Sirchmunk 检索会读到重复内容；`PAGEINDEX_STORAGE` 默认在 `data/db/` 下，已被 `denied_substrings` 隔离且不入 git；按内容哈希命名直接对接 D5 对账。② sidecar 映射表记 `{source_path(原 MD 绝对路径), file_hash(MD SHA256), format: "md", derived_pdf, doc_id}`；证据 `source_path` 必须是原 MD 路径——派生 PDF 路径含 `data/db`，流到主 venv 会被 PathGuard 整条剔除（10.3 ⑤），也与 collection 定界集对不上。③ 位置字段：`heading_index` 解析原 MD 标题（含 Setext，跳过围栏代码块）得到「标题路径 → 行区间」，树节点标题按归一化文本加父路径消歧回定位为 `line_start` / `line_end`；回定位失败只给 `section`，telemetry 计 `unlocated`。④ MD 节点原文优先按回定位的行区间从原 MD 读取（避开 PDF 文本抽取的断行与页眉页脚噪声），失败再回退 `get_page_content` |

### 1.4 对提案的调整

1. **G1（单后端路由）**：提案候选方向之一是把 `agentic_search.backend` 扩为列表。不采用：新建独立 `src/libs/tree_search/` 与 `tree_search` 配置节，`AgenticSearchFactory` 与 `agentic_search` 节不动，Sirchmunk 既有契约与测试零影响。
2. **G3（证据契约）**：提案写「`AgenticEvidence` 只增字段」。改为 `libs/tree_search` 自有 `TreeEvidence`，`AgenticEvidence` 不改。
3. **G4（证据获取）**：提案优先方案为 `agent_tools()` + 本项目 LLM。`agent_tools()` 只能读上游 DocStore（PDF），D3 的 MD 自建存储无法复用，改为 D7。**0.4 重估**：D3 变更后 MD 也进 DocStore，此否决理由失效，最终取舍见 D7 的 0.4 重估。
4. **G5（索引生命周期）**：提案写「摄取后异步建树 + 删除联动第 5 个存储」。改为 D5 对账同步，不改摄取管线与 DocumentManager。
5. **决策编号**：提案待决的 D1–D6 在本规格沿用原编号并终裁或给出设计决策，D7–D10 为立项时新增。

## 2. 核心目标与范围

### 2.1 In Scope

| 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|--------|-----------|
| 预研 | 四路对照立项门 | 门控（PI0，立项门） | sidecar venv + 版本锁定、用户确认的问题集、四路对照基线；0.4 起追加 MD 转 PDF 单篇试转、度量口径修正与新管线复测（PI0.4–PI0.6）；门槛按 D9 终裁（PI0.7），不达标即搁置 |
| sidecar 核心 | 模块（标题索引 / MD 转换 / 映射存储 / 检索循环） | 承诺（PI1） | 不 import pageindex 的四个模块（MD 转换只经注入的子进程 runner），主 venv 离线单测覆盖 |
| sidecar 服务 | HTTP 服务 + pageindex 适配层 + 运维脚本 + 红线 | 承诺（PI2） | 仅 loopback + Bearer 的 `/status` `/documents` `/index` `/search`；`check_redlines.py` 增 pageindex import 规则（精确白名单） |
| 主 venv 后端 | `libs/tree_search/` + `tree_search` 配置节 | 承诺（PI3） | `BaseTreeSearch` / `TreeEvidence` / HTTP 后端 / fake 后端 / 工厂；配置缺失走默认、非法值 fail-fast |
| 索引同步 | `scripts/pageindex_sync.py` | 承诺（PI4） | 摄取历史 → sidecar 索引的幂等对账（新增 / 重建 / 删除 / 跳过） |
| Chat 后端 | `longdoc` 模式接入 | 承诺（PI5） | 常量、配置校验、检索器、capabilities、schema、`Citation` 只增字段；collection 定界与 PathGuard 过滤 |
| Chat 前端 | 模式选择器与引用展示 | 承诺（PI6） | Longdoc 选项与配色（对比度 ≥ WCAG AA）、引用显示章节 / 行号 |
| 验证与文档 | 门控冒烟 + USAGE_GUIDE | 承诺（PI7，其中真实冒烟门控） | `RAG_PAGEINDEX_INTEGRATION=1` 真实 sidecar 冒烟；USAGE_GUIDE 新增章节 |

### 2.2 Out of Scope（明确不做）

- **PageIndex 云端**（D1）：原文件上传、Page Search / Page Extract API、框选引用、多模态。
- **使用上游答案生成**：`chat()` / `run_responses()` 等生成的回答文本不进入 Chat；答案只由 AnswerGenerator 生成。
- **MCP 工具**（D2）：不新增 `tree_search` 工具，`query_knowledge_hub` / `agentic_search` 输入 schema 与行为不变。
- **Hybrid 第四路融合**（D2）：`HybridSearch` 与 `agentic_search.trigger` 不感知树检索。
- **auto 候选**（D10）：`routing_question.py` 文案与 MR 门槛不动。
- **Query Playground、`scripts/query.py`、`scripts/evaluate.py` 感知 `longdoc`**：调试 / CLI / 评估链路保持现状。
- **改摄取管线或 DocumentManager**（D5）：摄取后自动建树、删除时同步删索引列为后续，由对账命令覆盖。
- **`start_api.py --manage-sidecar` 托管 PageIndex sidecar**：首期手动启动（`scripts/pageindex/start_sidecar.sh`），托管编排列为后续。
- **`longdoc` 模式的进度流（`search_log` 帧）**：sidecar `/search` 为同步端点，Chat 只下发既有 `status` 帧；流式列为后续。
- **`.docx` / `.pptx` / `.xlsx` / `.html` 建树**：对账时跳过并计数。
- **在主 venv 安装或 import `pageindex`**，以及把 PageIndex 装进 Sirchmunk sidecar 进程或其 venv。
- **回改冻结规格**：CMC 三档配色、MR auto violet 配色、AC 定界语义、Sirchmunk 契约均不动；`longdoc` 配色是本规格新行为。

## 3. 技术选型与模块设计

### 3.1 运行架构

```
Web UI Chat（选 Longdoc）
   │ WS /api/v1/chat  {retrieval: "longdoc", collection?}
   ▼
主 venv（永不 import pageindex）
  ChatEngine ──► TreeChatRetriever（services/chat_retrievers.py）
                   ├─ collection → CollectionPathResolver → 定界 source_paths（空集 → 不调用，D3 先例）
                   ├─ PageIndexHttpBackend（libs/tree_search，httpx，Bearer）
                   ├─ PathGuard 过滤 + 定界集二次过滤 → RetrievalResult(metadata: page/section/line_*)
                   └─ trace.record_stage("tree_search", ...)（不 finish）
                 ──► AnswerGenerator（生成答案 + Citation 只增字段）
                                   │ 127.0.0.1:8585
                                   ▼
.venv-pageindex（scripts/pageindex/，本项目自写服务）
  server.py（FastAPI）── /status /documents /index /search
     ├─ pi_adapter.py（唯一 import pageindex 处）
     │     ├─ PDF：PageIndexClient.submit_document / get_document_structure / get_page_content
     │     ├─ MD ：md_render 转派生 PDF → 同 PDF 路径建树（D3 / D11）
     │     └─ LLM：litellm.completion → complete(prompt) -> str
     ├─ md_render.py    （pandoc → HTML → Chromium print-to-pdf，子进程 runner 注入）
     ├─ heading_index.py（纯：原 MD 标题路径 → 行区间，证据回定位，D12）
     ├─ tree_store.py   （纯：source_path ↔ doc_id ↔ file_hash ↔ derived_pdf 映射）
     └─ tree_loop.py    （纯：选文档 → 选节点 → 读原文 → 判是否足够，max_loops / token 预算）
  存储：data/db/pageindex/（含 derived/ 派生 PDF；已被 denied_substrings 的 data/db 覆盖）

离线批处理：scripts/pageindex_sync.py ──读摄取历史 v2──► POST /index、POST /index/delete
```

### 3.2 模块设计

**sidecar 模块（PI1）**（0.4 按 D3 / D11 / D12 改写，原 `md_prep.py` 删除）：

- `heading_index.py`（纯，只依赖标准库）：`build(markdown: str) -> HeadingIndex` 解析 ATX 与 Setext 标题（跳过围栏代码块），每个标题记 `(level, title, line_start, line_end)`，`line_end` 为下一个同级或更高级标题的前一行，首个标题前的正文记为文件名节点；`locate(title, parent_path) -> LineSpan | None`：标题文本归一化（去首尾空白与强调符号、全角半角统一）后匹配，同名标题按父路径消歧，仍歧义或未匹配返回 `None`。
- `md_render.py`：`render(md_path, out_pdf, *, runner, toc) -> RenderResult(pages, elapsed_ms)`。构造 pandoc 与 Chromium 命令行（参数按列表传递，不经 shell）并生成内嵌打印 CSS 的 HTML 模板；执行由注入的 `runner` 完成（生产为带超时的 `subprocess.run`，单测注入 fake）；错误分型为 `RenderToolMissing` / `RenderTimeout` / `RenderFailed`；产物先写临时文件，再原子改名为 `<storage>/derived/<md_sha256>.pdf`。不 import pageindex。
- `tree_store.py`（纯）：JSON 文件存储，记录 `{source_path, file_hash, format(pdf|md), doc_id, derived_pdf, indexed_at, node_count}`；树本体在上游 DocStore，本模块只存映射与 MD 标题索引缓存。原子写、单进程内锁。
- `tree_loop.py`：`search(query, docs, *, complete, read_node, max_nodes, max_loops, max_token_budget) -> LoopResult`。
  1. 候选文档超过 1 篇时，给 LLM 文档描述列表，选出相关文档（JSON `doc_ids`）；
  2. 每篇选中文档给出不含正文的树（标题 + 摘要 + 节点 ID），LLM 返回 JSON `node_ids`；
  3. `read_node` 读原文，单节点超过 `max_evidence_chars` 时优先下钻子节点，仍超限则截断并标记；
  4. LLM 判断证据是否足够，不够且未超 `max_loops` / 预算则回到第 2 步补选。
  5. 预算在**每次** LLM 调用前按预估 prompt token 检查，预估超出剩余预算即停止并返回已得证据（PI0.3 原型只在每轮开头检查，实测单题超支到 85.5K / 32K）；单层候选节点过多时先按标题 + 摘要做一次压缩预筛；多篇选中文档的选节点调用可并行。
  
  LLM 输出非法 JSON 时重试一次，仍非法则返回已得证据并在 telemetry 记 `invalid_output`。证据按选中顺序给出 `score = 1 / rank`。

**sidecar 服务（PI2）**：`server.py` 只做 HTTP 编排与鉴权；`pi_adapter.py` 是运行期唯一 import `pageindex` / `litellm` 的文件，向纯模块注入 `complete` 与 `read_node`（预研脚本 `poc_compare.py` 是唯一例外，见下文红线规则与 3.6 第 5 条）。`md_render` 的子进程输出只落在 sidecar 进程日志，不影响主进程（0.4 起不再调用 `md_to_tree`）。

**红线规则（PI2）**：`scripts/check_redlines.py` 新增 `no-pageindex-import` 规则，与 `no-sirchmunk-import` 同法（AST 扫描 `src` / `scripts` / `tests` / `main.py`，含 `importlib.import_module` / `__import__` 字面量），命中 `pageindex` / `litellm` 及其子模块即违规；sidecar 侧白名单按文件路径精确匹配，只含 `scripts/pageindex/pi_adapter.py` 与 `scripts/pageindex/poc_compare.py`（不得放宽到整个目录，对齐 KE1 `evolve_launcher.py` 先例）；`SKIP_DIRS` 追加 `.venv-pageindex`（与 `.venv-sirchmunk` 并列；venv 默认建在仓库根、本不在扫描范围，此项防止被放进扫描目录时误报）。

**主 venv `libs/tree_search/`（PI3）**：与 `libs/agentic_search/` 同构但独立：

- `base_tree_search.py`：`TreeSearchRequest(query, source_paths: list[str] | None, max_nodes, max_loops, max_token_budget)`、`TreeEvidence(source_path, doc_id, node_id, text, score, section, page_start, page_end, line_start, line_end)`、`TreeSearchResponse(evidences, llm_call_count, total_llm_tokens, elapsed_ms, telemetry)`；错误分型基类 `TreeSearchError`，子类 `TreeSearchTimeoutError` / `TreeSearchBackendError` / `TreeSearchResponseError`。
- `http_backend.py`（工厂键 `pageindex_http`）、`fake_backend.py`（测试注入）、`tree_search_factory.py`、`converter.py`（`TreeEvidence → RetrievalResult`，metadata 见 D8）。
- `converter.py` 映射细则：
  - `chunk_id = f"pageindex:{doc_id}:{node_id}"`。`RetrievalResult.chunk_id` 必须是非空字符串（`src/core/types.py:392`），且是前端引用 React key 的组成部分；`tree_loop` 对同一节点去重，保证一次检索内唯一。
  - `metadata["page"]` 取 `page_start`，`page_end` 另存 `metadata["page_end"]`。首期 `Citation.page` 只展示起始页（语义不变），跨页区间展示列为后续。
  - MD 证据 `page_start` / `page_end` 为 `None`，`line_start` / `line_end` 为原 MD 行号（经 `heading_index` 标题回定位得出，D12；1 起计、闭区间；回定位失败时为 `None`，只保留 `section`）；PDF 证据反之。`section` 为从根到该节点的标题路径，用 ` > ` 连接。

**索引同步（PI4）**：`scripts/pageindex_sync.py [--collection C] [--dry-run]`：入口在 `load_settings()` 后先对 `settings.tree_search` 调 `ensure_local_roots`（3.3）；读 `SQLiteIntegrityChecker` 的 v2 success 记录，相对 `source_path` 按项目根解析（RA 契约），逐条经 PathGuard 校验；对比 sidecar `/documents` 得出新增 / 重建 / 删除 / 跳过四类并执行，输出计数摘要；单文档失败不中断，退出码非零表示有失败。

- **按物理文件去重**：sidecar 索引按物理文件一份，不区分 collection（collection 只在检索时经定界集表达）。success 记录以 `normalize_history_source_path` 归一后的绝对路径为键去重。同键多条记录的 `file_hash` 不一致时，以同步时对当前文件实算的 SHA256 为准，并以该值调用 `/index`；文件已不存在（实算失败）的键按「历史中已不存在」处理。
- **删除只在全量模式执行**：不带 `--collection` 时，sidecar `/documents` 中存在、而全部 collection 的 success 记录归一后都不包含的路径才删除。带 `--collection C` 时只对 C 内的文件做新增 / 重建 / 跳过，**不删除任何索引**（按 C 过滤后做差集会误删其他 collection 的已建树索引）；此时摘要中删除计数恒为 0，并提示需全量运行才能清理。
- **MD 文件（D3 / D12）**：`/index` 由 sidecar 内部完成转换与建树，主 venv 只发原 MD 路径与 `file_hash`；摘要中转换失败单列计数；删除 MD 索引时 sidecar 连带删除派生 PDF。

**Chat 接入（PI5）**：

- `core/chat/retriever.py`：增 `RETRIEVAL_LONGDOC = "longdoc"` 并追加进 `RETRIEVAL_MODES`（扩为四元组，`RETRIEVAL_AUTO` 仍不在其中）。`ChatEngine` 的检索器键校验、`available_retrievals` 与请求校验随之接受 `longdoc`，引擎对 `longdoc` 不加额外门控。
- `core/settings.py`：
  - `CHAT_RETRIEVAL_MODES` 同步追加 `longdoc`。
  - 新增独立常量 `MODE_ROUTER_FALLBACK_MODES = ("hybrid", "agentic_fast", "agentic_deep")`，`mode_router.fallback_retrieval` 改用它校验（不再复用 `CHAT_RETRIEVAL_MODES`），保持只接受三档、报错文案不变。
  - `validate_settings()` 追加跨节校验：`chat.default_retrieval == "longdoc"` 要求 `tree_search.enabled: true`，否则 fail-fast（对齐 `settings.py:1176` 的 auto 先例）。
  - `tree_search` 节解析与校验见 3.3。
- `core/chat/engine.py`：`classify_error()` 在 agentic 分支之后追加 `TreeSearch*Error` 映射，与 agentic 同口径：`TreeSearchTimeoutError` / `TreeSearchBackendError` → `UPSTREAM`（`transient=True`，受 `pipeline_max_retries` 重试）；`TreeSearchResponseError` → `UPSTREAM`，文案「检索服务返回了无法解析的响应。」（`transient=False`）；其余 `TreeSearchError` → `UPSTREAM`（`transient=False`）。均不归入 `INTERNAL`，因此不打印异常堆栈。
- `core/chat/answer_generator.py`：`build_context()` 的位置头仅在 `citation.section` / `citation.line_start` 非空时追加，格式 `[id] source[, p.N][ · <section>][ · L<start>–<end>]`（`line_start == line_end` 时为 `L<n>`）。二者为 `None` 时输出与现状逐字节一致，hybrid / agentic 零变化。
- `api/chat_wiring.py`：`_ALL_MODES` 保持三档（auto 候选口径不变，D10）；`tree_search.enabled` 且 `longdoc` 检索器已注册时，capabilities `retrievals` 末尾追加 `longdoc`；新增 `longdoc_enabled: bool` 字段（值为 `tree_search.enabled`，只增；为 false 时其余键与值不变）。
- `api/schemas/chat.py`：`RetrievalMode` 追加 `"longdoc"`；`ChatCapabilities` 声明 `longdoc_enabled: bool = Field(description="tree_search.enabled")`（不声明会被 `response_model` 静默丢弃）。
- `services/chat_retrievers.py`：`tree_search.enabled` 时注册 `TreeChatRetriever`，`answer=None`，走 AnswerGenerator 路径；不声明 `on_log`。检索流程：
  1. 给了 `collection` 时经 `CollectionPathResolver.resolve` 得到定界集。空集不调用 sidecar，直接零证据返回（对齐 AC D3）；非空则作为 `source_paths` 发送。未给 `collection` 时发 `null`。
  2. 证据先经 `PathGuard.filter_evidence_paths`；给了 `collection` 时再按定界集二次过滤（对齐 AC2 后置过滤，防 sidecar 越界返回同根下其他 collection 的文档），剔除条数计入 trace。
  3. trace 只调用 `record_stage("tree_search", method=<backend_name>, details={...})`，不 `finish()`（D7）。`details` 含 `backend`、`collection`、`scoped_paths`、`evidences`（过滤后条数）、`filtered_out`、`unindexed`、`llm_call_count`、`total_llm_tokens`、`elapsed_ms`、`tree_telemetry`；空定界集记 `method="scoped_empty"`，后端异常记 `method="error"` 与 `error_type` 后重抛（对齐 `agentic_search` 阶段）。不记录 `api_token`、`base_url` 与证据原文。
- `core/response/citation_generator.py`：`Citation` 增可选 `section` / `line_start` / `line_end`，取自 `RetrievalResult.metadata` 同名键，`None` 不输出（D8）。

**前端（PI6）**：

- `types.ts`：`RetrievalMode` 加 `longdoc`；`ChatCapabilities` 增 `longdoc_enabled: boolean`；`Citation` 增可选 `section?` / `line_start?` / `line_end?`。
- `normalize.ts`：`isRetrievalMode` 接受 `longdoc`；`normalizeCapabilities` 解析 `longdoc_enabled`（缺省 false），`default_retrieval === "longdoc"` 仅当 `longdoc_enabled` 为真且 `retrievals` 含 `longdoc` 时保留，否则归一为 hybrid（对齐 auto 先例）；`normalizeCitation` 透传三个可选字段（类型不符则丢弃；缺失时不出键，既有引用对象不变）。
- `modes.ts`：`LABELS` 加 `longdoc: "Longdoc"`；`buildModeOptions` 的 longdoc 判断**独立于** `agentic_enabled`：把第 45 行的提前返回改为条件块，使 `agentic_search.enabled: false` 且 `tree_search.enabled: true` 时仍能出现 Longdoc。选项顺序为 Auto、Hybrid、Agentic (fast)、Agentic (deep)、Longdoc；`longdoc_enabled` 为真且 `retrievals` 含 `longdoc` 才出现。`resolveMode` 无需改动。
- `ModeSelector.tsx`：选中态 rose 色系（light `bg-rose-100 text-rose-800`，dark `dark:bg-rose-500/20 dark:text-rose-200`，实测对比度 ≥ AA 后回填）。
- `citations.ts`：`citationLabel()` 在有 `section` / `line_start` 时沿用既有英文风格，输出 `a.md · <section> · L12–45`（`line_start == line_end` 时为 `L12`；只有 `section` 时为 `a.md · <section>`；章节名照录原文标题，不翻译）。`SourcesPanel.tsx` 与 `MarkdownContent.tsx` 共用该函数，无需各自改文案；无新字段时输出与现状一致。
- 既有测试夹具同步（语义不变）：`modes.test.ts`、`chat.components.test.tsx` 的 capabilities 对象与 `normalize.test.ts:164-185` 的精确断言补 `longdoc_enabled: false`。
- Playwright 假后端 `tests/e2e_web/fake_api_server.py`：capabilities 增 `longdoc_enabled`，可按开关上报 `longdoc`，并为 longdoc 轮次返回带 `section` / `line_*` 的引用。

### 3.3 配置契约

```yaml
# PageIndex 树检索（独立 HTTP sidecar，可选；enabled=false 时零加载、Chat 不出现 Longdoc）
tree_search:
  enabled: false
  backend: pageindex_http          # 唯一生产后端（fake 仅供测试注入）
  base_url: http://127.0.0.1:8585
  api_token: ${PAGEINDEX_API_TOKEN} # 从环境变量解析，不落盘
  timeout_s: 60                     # 整次 /search 总超时
  index_timeout_s: 900              # pageindex_sync 单文档 /index 超时（MD 含转换；PI0.4 / PI0.6 实测后校准）
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

校验：

- 节缺失走上表默认；`timeout_s` / `index_timeout_s` / `max_nodes` / `max_loops` / `max_token_budget` 为正数；未知 `backend` fail-fast。
- `allowed_roots` 与 agentic 同口径锚定：相对根在 `load_settings()` 时经 `resolve_data_path` 解析为 `<项目根>/...` 绝对路径（RA2，D3-a），绝对路径原样保留，`~` 先展开。
- `enabled: true` 时（跨字段要求放最后）：
  - `base_url` 经 `urllib.parse.urlsplit` 解析，要求 `scheme == "http"` 且 `hostname` 属于 {`127.0.0.1`, `localhost`, `::1`}。不做字符串前缀匹配，`http://127.0.0.1.evil.com` 必须被拒。
  - `allowed_roots` 非空。
  - `api_token` 必填，缺失或仍是未解析的 `${...}` 占位符即 fail-fast（对齐 `mode_router.api_token`）。
- 跨节：`chat.default_retrieval: longdoc` 要求 `tree_search.enabled: true`（3.2）。

进程入口：`scripts/start_api.py` 与 `scripts/pageindex_sync.py` 在 `load_settings()` 后对 `settings.tree_search` 调 `ensure_local_roots`（AR1 先例；`enabled: false` 时为空操作），避免干净环境下 `./data/uploads` 尚不存在导致 `PathGuard` 构造失败。MCP server 与 `scripts/query.py` 不感知 `longdoc`（2.2），不加。

sidecar 侧 env（`scripts/pageindex/env.example`）：`PAGEINDEX_API_TOKEN`、`PAGEINDEX_HOST` / `PAGEINDEX_PORT`、`PAGEINDEX_STORAGE`（默认 `data/db/pageindex`）、`LLM_MODEL` / `LLM_API_BASE` / `LLM_API_KEY`（D4）；0.4 增 `PAGEINDEX_PANDOC` / `PAGEINDEX_CHROMIUM`（可选，覆盖 pandoc 与 Chromium 可执行文件路径，D11）与 `PAGEINDEX_RENDER_TIMEOUT_S`（单篇转换超时）。派生 PDF 目录固定为 `<PAGEINDEX_STORAGE>/derived/`（D12），不单独配置。

### 3.4 sidecar HTTP 契约

统一响应 `{"success": bool, "data": ..., "error": str | null}`；全部端点要求 `Authorization: Bearer <token>`，绑定 127.0.0.1。

| 端点 | 请求 | 响应 `data` |
|------|------|------------|
| `GET /api/v1/status` | — | `{version, pageindex_version, model, documents}` |
| `GET /api/v1/documents` | — | `[{source_path, file_hash, format, doc_id, node_count, indexed_at, derived_pdf?}]`（`derived_pdf` 仅 MD，供运维排查；主 venv 不得把它当证据路径） |
| `POST /api/v1/index` | `{source_path(绝对), file_hash}` | `{doc_id, format, node_count, llm_tokens, render_ms?, derived_pages?}`（后两项仅 MD）；同 `file_hash` 已存在则幂等返回；MD 转换失败返回 `success=false`，`error` 以 `render:` 前缀区分 |
| `POST /api/v1/index/delete` | `{source_path(绝对)}` | `{removed: bool}` |
| `POST /api/v1/search` | `{query, source_paths: [绝对] \| null, max_nodes, max_loops, max_token_budget}` | `{evidences: [TreeEvidence], llm_call_count, total_llm_tokens, telemetry}` |

删除走 `POST` 子路径，不用带 JSON body 的 `DELETE`：`httpx.Client.delete()` 不接受 `json=`（须改用 `client.request("DELETE", ...)`），部分代理与工具对 DELETE body 兼容性差；也不用 query param，避免绝对路径进入访问日志。

`source_paths` 三种取值：

- `null`：全部已索引文档。
- 非空列表：只在其中检索，未索引的路径忽略并计入 `telemetry.unindexed`；返回证据的 `source_path` 必须属于该列表（MD 证据为原 MD 路径，不是派生 PDF，D12）。
- 空列表 `[]`：必须 fail-closed，直接返回 `evidences: []`，**不得退化为全量检索**。主 venv 对空定界集本就不调用 sidecar（3.2），此条为纵深防御。

主 venv 不信任 sidecar 的过滤结果，仍按 3.2 做 PathGuard 过滤与定界集二次过滤。主 venv 侧错误映射与 Sirchmunk 后端一致：超时 → `TreeSearchTimeoutError`，连接失败 / 4xx / 5xx / `success=false` → `TreeSearchBackendError`，非 JSON / 结构畸形 → `TreeSearchResponseError`；ChatEngine 按既有检索失败分支处理（默认 error 帧，`chat.fallback_to_chat_only: true` 时降级纯聊天）。

### 3.5 涉及文件清单

| 位置 | 文件 | 变更 |
|------|------|------|
| sidecar | `scripts/pageindex/{setup_venv.sh,start_sidecar.sh,env.example,server.py,pi_adapter.py,md_render.py,heading_index.py,tree_store.py,tree_loop.py}` | 新增（0.4：`md_prep.py` 改为 `md_render.py` + `heading_index.py`；`setup_venv.sh` 增 pandoc 版本检测与 Chromium 安装） |
| sidecar | `scripts/pageindex/poc_compare.py` + 问题集 `tests/fixtures/pageindex/poc_questions.jsonl` | 新增（PI0） |
| 主 venv | `src/libs/tree_search/*` | 新增 |
| 主 venv | `src/core/settings.py`、`config/settings.yaml` | 新增 `tree_search` 节；`CHAT_RETRIEVAL_MODES` 追加 |
| 主 venv | `src/core/chat/retriever.py`、`src/core/chat/engine.py`（`classify_error`）、`src/core/chat/answer_generator.py`（`build_context`）、`src/api/chat_wiring.py`、`src/api/schemas/chat.py`（`RetrievalMode` + `ChatCapabilities`）、`src/observability/dashboard/services/chat_retrievers.py`、`src/core/response/citation_generator.py` | 只增（见 3.2） |
| 脚本 | `scripts/pageindex_sync.py`、`scripts/check_redlines.py`、`scripts/start_api.py`（`ensure_local_roots(settings.tree_search)`） | 新增 / 增规则 / 增一行预建 |
| 前端 | `frontend/lib/chat/{types,modes,normalize,citations}.ts`、`frontend/components/chat/ModeSelector.tsx` | 只增（`SourcesPanel.tsx` / `MarkdownContent.tsx` 经 `citationLabel()` 自动生效，不改） |
| 测试 | `tests/unit/test_tree_search_*.py`、`tests/unit/test_pageindex_sidecar_*.py`、`tests/unit/test_pageindex_sync.py`、`tests/integration/test_tree_search_http.py`、`tests/e2e/test_pageindex_integration.py`（门控）、前端 vitest / Playwright 用例 | 新增 |
| 测试 | `tests/integration/test_api_chat.py`（capabilities 精确字典补 `longdoc_enabled: false`）、`tests/unit/test_mode_router_config.py`（`RETRIEVAL_MODES` 四元组）、`frontend/lib/chat/{normalize,modes}.test.ts`、`frontend/components/chat/chat.components.test.tsx`（夹具补字段）、`tests/e2e_web/fake_api_server.py`（longdoc 支持） | 同步既有断言 / 夹具（语义不变，见第 4 章） |
| 其他 | `.gitignore`（`.venv-pageindex/`、`.env-pageindex`） | 追加 |

### 3.6 选型理由

1. **独立 sidecar 而非进程内 import**：`md_to_tree` 的裸 `print` 会污染 MCP stdio（API 进程与 MCP server 共用 `src/`；0.4 起已不调用 `md_to_tree`，MD 转换的子进程输出同样只应落在 sidecar）；litellm / openai-agents / mcp 依赖链重且迭代快；`submit_document` 受 multiprocessing spawn 约束。与 Sirchmunk 决策同构，见 [2026-10-05-sirchmunk-http-sidecar.md](../../.agents/notes/implemented/architecture/2026-10-05-sirchmunk-http-sidecar.md)。
2. **不复用 Sirchmunk sidecar 进程**：其 ASGI 应用是上游 `sirchmunk serve`（或 KE 的 `evolve_launcher.py`），本项目不拥有；两套依赖混装会让任一侧升级互相阻塞。
3. **独立 `libs/tree_search/` 而非扩展 `libs/agentic_search/`**：两者请求模型（paths + FAST/DEEP vs source_paths + 节点预算）与证据形态（字符偏移 vs 章节 / 页 / 行）不同；独立后 AC / AR / SX 冻结契约与测试零改动，代价是 PathGuard / CollectionPathResolver 跨 lib 复用（直接 import，二者不依赖 sirchmunk）。
4. **PI0 度量口径**（D9 只固定口径）：
   - 问题集：从最大的 3 篇 Markdown + 1 篇长 PDF（上游 `examples/documents/2023-annual-report.pdf`，只放 sidecar 工作区不入库）起草 20–30 条章节定位 / 跨节归纳题，每题标注目标章节（MD 行范围或 PDF 页范围），由用户逐条确认（MR0.2 先例）。
   - PDF 对照的隔离：Hybrid 与方案 (a) 必须先摄取该 PDF 才能检索。PI0 使用临时配置文件，把向量库 / BM25 / 摄取历史 / 图片库路径指向临时目录，以独立 collection `pi0_poc` 摄取；Sirchmunk DEEP 一路把该 PDF 所在临时目录加入临时配置的 `agentic_search.allowed_roots`。跑完删除临时目录，不污染默认库，不入 git。MD 对照直接用默认库中已摄取的文档（PI0 实测默认库未含目标 4 篇，已按正常摄取路径补齐，见 10.2）。
   - 命中率：返回证据与目标范围有交集的题目占比。
   - 引用可定位率：引用能直接给出章节与行号（或页码）的占比；方案 (a) 用离线近似计算：把 Hybrid 命中 chunk 在源文件中定位后推导标题路径，不改摄取代码。
   - 延迟 P50 / P95、单题 token；Sirchmunk DEEP 取 telemetry，树检索取原型计数。
   - 一次性建树成本（token 与耗时）单列。
   - 0.4 口径修正（统一 top-k、证据精度、严格同文件 / 内容等价双口径、tree 候选剔除拆分件）由 PI0.5 落地，新旧口径对照见 10.4。
5. **PI0 不依赖 PI1 / PI2**：PI0.3 的树检索原型内置于 `scripts/pageindex/poc_compare.py`，在 sidecar venv 中直接调用上游 `md_to_tree` / `PageIndexClient` 与 litellm 独立运行（0.4 起 MD 改为转换后走 `PageIndexClient`，PI0.5 同步原型），不依赖 PI1 纯模块与 PI2 服务，避免排期倒挂。立项门通过后，PI1 / PI2 把原型中的预处理、存储与检索循环抽取为正式模块；`poc_compare.py` 作为预研存档保留，并按 3.2 列入红线精确白名单。
6. **MD 转 PDF 建树而非自建 MD 树（0.4，D3）**：PI0.3 实测上游 `md_to_tree` 把每个标题与整行粗体都建成节点，长 MD 的树过细（1,881 节点），建树慢、逐层下钻 token 失控；PDF 路径由上游版面解析与目录识别建树，节点数与页数同量级（222 页 177 节点）。转成 PDF 还让两种格式共用 DocStore 与同一读取接口，`tree_store` 不再自存 MD 树。代价是新增 pandoc / Chromium 系统依赖和一次转换，结构识别质量需 PI0.4 实测。选 pandoc → HTML → Chromium，而不选 LaTeX / wkhtmltopdf / weasyprint，原因是本机只有前者齐备，中文走浏览器系统字体、不需要 LaTeX 字体配置（PI0.4 验证），且打印 CSS 能控制代码块换行与标题层级（D11）。

## 4. 测试方案

- **离线确定性测试**（全部进 `pytest tests`，零网络、零真实模型，CI 禁网命令下通过）：
  - 导入方式：`pyproject.toml` 的 `pythonpath` 只含 `src`，不为此改动。主 venv 单测按既有先例导入 `scripts/pageindex/` 下的模块：用 `importlib.util.spec_from_file_location` 按文件加载（如 `tests/unit/test_measure_process.py`），或在测试模块内 `sys.path.insert` 该目录（如 `tests/unit/test_start_api_sidecar.py` 导入 `sidecar_manager`）。纯模块只依赖标准库，彼此之间用同目录导入。
  - sidecar 模块（PI1，0.4 改写）：`heading_index` 覆盖 ATX / Setext、围栏代码块内的 `#` 不计、首个标题前正文、同名标题按父路径消歧、归一化匹配（强调符号 / 全半角）与未匹配返回 `None`；`md_render` 注入 fake runner 覆盖命令行构造（列表传参、不经 shell）、带 / 不带目录、工具缺失 / 超时 / 非零退出三类错误分型、产物原子改名为 `<md_sha256>.pdf` 且位于 `<storage>/derived/` 下；`tree_store` 原子写 / 幂等 / 删除（删除 MD 记录时返回待删派生 PDF）；`tree_loop` 用 fake `complete` 覆盖选文档、选节点、同节点去重、下钻、截断、`max_loops` / 预算截停（含调用前预算检查：预估超预算时不发起调用）、单层预筛、非法 JSON 重试与降级；证据回映射（D12）：MD 证据 `source_path` 为原 MD 路径、永不为派生 PDF 路径（锁定 10.3 ⑤），回定位失败时 `line_*` 为 `None` 且 telemetry 计 `unlocated`。测试 import 这些模块时不得触发 `pageindex` / `litellm` import（测试断言 `sys.modules`），也不调用真实 pandoc / Chromium。
  - sidecar 服务（PI2）：`server.py` 以注入的 fake adapter 跑 FastAPI TestClient（鉴权 401、loopback 绑定参数、各端点 schema、`POST /api/v1/index/delete`、`source_paths: []` 返回空证据且不调用 adapter 检索）；`pi_adapter.py` 不进主 venv 测试。
  - 主 venv 后端（PI3）：`httpx.MockTransport` 覆盖 Bearer、超时、连接拒绝、5xx、`success=false`、非 JSON、契约 fixture 解析（`tests/fixtures/pageindex/contract_search_response.json`，PI2 真实响应抓取后替换）；线程 HTTP stub 集成测试走真实 httpx 链路；`converter` 的 `chunk_id` 规则与 MD / PDF 两种位置字段映射；配置节默认值与 fail-fast 校验，含 `http://127.0.0.1.evil.com` 与 `https://127.0.0.1` 被拒、`api_token` 缺失或为未解析 `${...}` 被拒、相对 `allowed_roots` 锚定项目根。
  - 索引同步（PI4）：fake 历史表 + fake 后端覆盖新增 / 重建 / 删除 / 跳过 / 单条失败不中断 / `--dry-run` 无写入；同一文件在两个 collection 的记录只建一份索引；`file_hash` 冲突时以实算 SHA256 为准；`--collection C` 不发出任何删除请求（sidecar 中有其他 collection 的索引时不受影响）；入口调用 `ensure_local_roots`；MD 记录的转换失败单列计数且不中断。
  - Chat（PI5）：fake 树检索后端驱动 ChatEngine，断言 `longdoc` 模式帧序列、引用含 `section` / `line_*`、collection 空定界集不调用后端、PathGuard 剔除越权证据、定界集外证据被二次过滤剔除、`tree_search` trace 阶段字段齐全且不含 `api_token`；`classify_error` 对三类 `TreeSearch*Error` 的 code / `transient` 映射与不打堆栈；超时与 5xx 按 `pipeline_max_retries` 重试；`build_context` 对 longdoc 结果带位置头、对无新字段结果输出不变；`GET /api/v1/chat/capabilities` 经 HTTP 返回 `longdoc_enabled`；`mode_router.fallback_retrieval: longdoc` 与 `tree_search.enabled: false` 下 `chat.default_retrieval: longdoc` 均 fail-fast。
  - 前端（PI6）：vitest 覆盖 `buildModeOptions`（`longdoc_enabled` 真 / 假，以及 `agentic_enabled: false` 且 `longdoc_enabled: true` 时出现 Longdoc）、`resolveMode`、`normalizeCapabilities` 的 `default_retrieval: "longdoc"` 两种分支、`normalizeCitation` 新字段透传、`citationLabel` 三种格式（区间 / 单行 / 仅章节）、配色类名；Playwright 假后端新增 longdoc 用例（opt-in，需先 build）。
  - 红线（PI2）：`check_redlines.py` 新规则的正反样例单测。
- **真实依赖冒烟**：`RAG_PAGEINDEX_INTEGRATION=1`（默认跳过，需 sidecar 已启动且已同步）：真实 sidecar `/status`、对一篇 MD 与一篇 PDF 检索命中并带行号 / 页码；Chat `longdoc` 模式端到端出答案与引用。PI0 对照脚本只在 sidecar venv 手动运行，不进 pytest。
- **既有测试语义**（本规格不得破坏）：
  - `tree_search` 节缺失或 `enabled: false` 时：`compute_capabilities` 既有键与值不变、`retrievals` 不含 `longdoc`，唯一差异是新增 `longdoc_enabled: false`。沿用 MR4 先例（当时为既有精确字典断言补 `auto_routing: false`）：`tests/integration/test_api_chat.py:521-532` 与前端 `normalize.test.ts:164-185` 的精确断言各补这一键，`modes.test.ts` / `chat.components.test.tsx` 夹具同步，语义不变。`build_chat_retrievers` 注册集合、前端模式选项、MCP `tools/list` 与基线逐字节一致。
  - `RETRIEVAL_MODES` 由三元组扩为四元组：`tests/unit/test_mode_router_config.py:54` 的常量断言同步改为四元组；`RETRIEVAL_AUTO not in RETRIEVAL_MODES` 与 auto 三档候选语义不变。`mode_router.fallback_retrieval` 改用独立常量后仍只接受三档，既有校验用例不变。
  - auto 候选仍取三档（`_ALL_MODES` 不含 `longdoc`），`routing_question.py` 文案与 MR 测试不变。
  - `Citation.to_dict()` 对既有 hybrid / agentic 结果输出键集合不变；`AnswerGenerator.build_context()` 对无 `section` / `line_*` 的结果输出逐字节不变；`classify_error()` 对既有异常类型的映射不变。
  - Sirchmunk：`libs/agentic_search/` 契约、`AgenticSearchFactory` 注册表、AC 定界语义与相关测试不变。
  - `check_redlines.py` 既有三条规则判定不变。
  - CMC 三档与 auto violet 配色测试不变。
  - 全量 `pytest tests -q` 与 CI 禁网命令保持全绿，计数只增不减。

## 5. 任务排期与进度

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|----------|------|
| PI0.1 | sidecar venv 与版本锁定：`setup_venv.sh` / `env.example` / `.gitignore`，D6 终裁 | `bash scripts/pageindex/setup_venv.sh` 在干净机器建出 `.venv-pageindex`，`pip show pageindex` 版本与脚本锁定一致；D6 行回填版本号 | 已完成（2026-11-21：D6 锁定 `0.7.4`；本机实测脚本建出 `.venv-pageindex`（Python 3.12），`pip show pageindex` = 0.7.4，上游接口导入核对通过；fastapi / uvicorn 一并装入 sidecar venv 供 PI2 使用） |
| PI0.2 | 问题集起草与确认 | `tests/fixtures/pageindex/poc_questions.jsonl` 20–30 条，每条含目标章节范围，用户逐条确认（记录确认日期） | 已完成（2026-11-21 用户逐条确认定稿：30 条 = MD 25 + PDF 5，locate 21 + synthesize 9；PDF 为上游 `examples/documents/2023-annual-report.pdf`（美联储 2023 年报，222 页），落 `data/db/pageindex/poc/`（不入 git），印刷页码 + 6 = 物理页已抽样校准） |
| PI0.3 | 四路对照与立项门终裁 | `poc_compare.py`（内置树检索原型，在 sidecar venv 独立运行，不依赖 PI1 / PI2，见 3.6 第 5 条）输出对照表（命中率 / 引用可定位率 / P50·P95 / 单题 token / 建树成本）落盘本规格附录；PDF 对照经临时隔离库摄取、跑完清理；D4、D9 终裁回填；不达标则本规格止于预研并建派生台账 | 已完成（2026-11-22：30 题 × 4 路全跑通，对照表落盘第 10 章附录，`poc_results.json` / `tree_*.jsonl` 落 `data/db/pageindex/poc/`（不入 git）；D4 实测回填；D9 未终裁——用户裁决转入 PI0.4 承接后续任务（0.4 方向变更后拆为 PI0.4–PI0.7），PI1 起仍不开工。脚手架三轮修复见 commit `4463c4b`：每题独立预算 + 逐层下钻、md 按证据自身文件定位与同文件归属判定、PDF 页码离线定位（PageLocator）、隔离工作区避开 PathGuard `data/db` denied substring、md 基线语料补摄取） |
| PI0.4 | MD → PDF 单篇试转实测（D3 / D11，0.4 起由原「PI0 后续任务与立项门终裁」拆出） | 对 1.7 MB 原大文件（`大模型应用_算法学习路线+八股+面试实战4.md`）按 D11 转换，三种目录做法各出一份：记录页数、转换耗时，抽样核对标题层级在 PDF 中的视觉还原；对较优一份调用 `submit_document` 建树：记录节点数、树深、建树耗时与 token、节点标题经 `heading_index` 原型回定位的成功率（可接受线由用户拍板）；**建树花真实 token，执行前须用户确认**；结果回填 D11 与 10.4；结构还原或成本不可接受则停在本任务，与用户重议 D3 | 未开始 |
| PI0.5 | 度量口径修正与原型调优（不花 LLM token） | ① 口径：各路统一按前 k 条证据判 hit（k 写明并与 hybrid `top_k` 对齐）；新增证据精度（目标范围内证据条数 / 返回证据条数）；严格同文件与内容等价双口径并报（`章节拆分/` 拆分件与原大文件按标题路径互映）；tree 候选只含原始文档、剔除拆分件；hybrid / plan_a 按新口径重跑，旧 tree/md 不重跑（10.1 保留为旧管线对照），新旧口径对照表回填 10.4。② 原型：`TreeSearcher` 改为每次调用前检查预算、单层节点预筛、多文档并行、证据足够提前停止；接入 D12 证据回映射与 `heading_index` 原型。③ 脚手架：`tree_telemetry` 部分重跑按路由合并而非覆盖；上游 client 存储参数显式指向 `PAGEINDEX_STORAGE`，仓库根 `.pageindex/` 清除。④ 复核 D7 `agent_tools()` 并回填。改动后 `poc_compare.py` 以 fake LLM 冒烟通过 | 未开始 |
| PI0.6 | 新管线全量建树与四路复测 | 派生 PDF 建树覆盖 PoC 目标 MD（剔除拆分件）+ 原生 PDF；30 题按 PI0.5 口径四路复测（tree 路为新管线），输出与 10.1 同结构的对照表（增证据精度与双口径列）及建树成本，落盘 10.4；现有 PDF 题仅 5 条且三路全 1.0、无区分度，是否补 PDF 跨节归纳题由用户决定（补题须逐条确认，PI0.2 先例）；`timeout_s` / `index_timeout_s` / `max_token_budget` 默认值按实测 P95 校准并回填 3.3；**花真实 token，执行前须用户确认** | 未开始 |
| PI0.7 | D9 终裁、过程发现处置与收尾 | D4 / D9 终裁回填（达标则 PI1 开工；不达标则本规格止于预研并建派生台账）；10.3 ①–⑤ 按 10.4 方案表落地（派生台账条目、上游 issue 草稿、单测锁定项各自归位）；收尾清理逐项经用户确认：`/tmp/pi0_poc_ws`、`data/db/pageindex/poc/workspace/`、默认库补摄取的 MD 去留、`markitdown[pdf]` 是否进 `requirements.txt`、旧 MD 树缓存（3 份）删除时机 | 未开始 |
| PI1 | sidecar 模块 `heading_index` / `md_render` / `tree_store` / `tree_loop`（0.4 起 `md_prep` 删除） | 第 4 章 PI1 用例全过；模块 import 不引入 `pageindex` / `litellm`；`md_render` 单测经注入 runner，不调用真实 pandoc / Chromium | 未开始 |
| PI2 | sidecar 服务、`pi_adapter`、`start_sidecar.sh`、红线规则 | 本机启动后 `/status` 200、无 token 401；对 1 篇 MD（经派生 PDF 建树，D3）+ 1 篇 PDF 建树并检索返回带行号 / 页码证据，MD 证据 `source_path` 为原 MD 路径；`setup_venv.sh` 检测到 pandoc 并装好 Chromium（D11）；`python scripts/check_redlines.py` 0 违规（白名单仅 `pi_adapter.py` / `poc_compare.py`）且新规则正反样例单测通过；契约 fixture 替换为真实响应 | 未开始 |
| PI3 | `libs/tree_search/` + `tree_search` 配置节 | MockTransport / stub 集成测试全过；默认配置下 `load_settings()` 结果除新增节外不变；非法配置 fail-fast 单测覆盖 3.3 每条校验 | 未开始 |
| PI4 | `scripts/pageindex_sync.py` | 离线单测覆盖四类动作与 `--dry-run`；对真实 sidecar 跑两次，第二次新增 / 重建计数为 0 | 未开始 |
| PI5 | Chat 后端 `longdoc` 模式 | 第 4 章 PI5 用例全过；既有测试语义清单逐条有测试锁定或复跑通过（capabilities 精确断言与 `RETRIEVAL_MODES` 常量断言按第 4 章同步，其余既有断言不改）；`start_api.py` 对 `tree_search` 预建白名单根 | 未开始 |
| PI6 | Chat 前端 Longdoc 选项、配色、引用展示 | `npm run typecheck && npm run lint && npm test` 全绿；rose 选中态 light / dark 实测对比度 ≥ 4.5:1 并回填；Playwright longdoc 用例通过 | 未开始 |
| PI7 | 门控真实冒烟 + 文档回写 | `RAG_PAGEINDEX_INTEGRATION=1` 冒烟通过并记录延迟与 token；USAGE_GUIDE 新章节与 README 回写完成（第 7 章）；全量禁网 pytest + 红线全绿 | 未开始 |

> 进度备注：交付后在各任务下回填状态与关键实测数据；全部收口后本规格移入 `docs/frozen/` 并更新 MOC.md。

## 6. 验收清单

- [ ] PI0 立项门通过，门槛与对照数据记录在本规格（或已按「止于预研」收口）。
- [ ] `tree_search.enabled: false` 时 Chat 模式选项、auto 候选、MCP `tools/list` 与基线逐字节一致；capabilities 既有键与值不变，只多 `longdoc_enabled: false`。
- [ ] sidecar 超时 / 不可达时 Chat 返回 `UPSTREAM` 错误帧并按 `pipeline_max_retries` 重试，不出现 `INTERNAL`。
- [ ] `tree_search.enabled: true` 且 sidecar 已同步时，Chat 选 Longdoc 能对长 Markdown 给出带「章节 · 行号」的引用（经派生 PDF 建树，行号由原 MD 标题回定位得出，D12），对 PDF 给出页码引用。
- [ ] 派生 PDF 不进入 `data/documents/` / `data/uploads/`，不出现在 hybrid / agentic 检索结果与任何证据的 `source_path` 中。
- [ ] 带 collection 的 Longdoc 检索只返回该 collection 定界集内证据；空定界集不调用 sidecar。
- [ ] `pageindex_sync.py` 幂等：连续两次运行第二次无新增 / 重建。
- [ ] 主 venv 无 `pageindex` / `litellm` import（红线脚本 0 违规）。
- [ ] `pytest tests -q` 与 CI 禁网命令全绿；前端 typecheck / lint / vitest 全绿。
- [ ] 回写完成（第 7 章）。

## 7. 实现合入后的回写清单

- `docs/reference/USAGE_GUIDE.md`：新增章节「Longdoc 检索模式（PageIndex sidecar）」——venv 搭建、env、启动、`pageindex_sync.py` 用法、`tree_search` 配置、隐私提示、门控冒烟。
- `README.md`：配置速查与 Web UI 模式说明补 Longdoc；开发文档导航表更新本规格状态。
- `config/settings.yaml`：`tree_search` 节（默认 `enabled: false`）与注释。
- `AGENTS.md`：仅在本规格冻结时新增一行「编码前必读」条目（docs-governance §3 第 5 条），开发中不改。
- `docs/MOC.md`：分区说明与谱系表随状态更新。

## 8. 隐私注记

- **暴露面**：sidecar 只绑定 127.0.0.1 并要求 Bearer；主 API 不新增公开路由，Chat 只多一个请求值。树索引（含 LLM 摘要）与 MD 派生 PDF 落盘 `data/db/pageindex/`，已被 `denied_substrings` 的 `data/db` 覆盖，不会被 agentic 检索读到；该目录不入 git。
- **外发边界**：建树时，派生 PDF 与原生 PDF 的页文本发给 D4 配置的摘要模型（0.4 起 MD 经派生 PDF 建树，外发内容仍是 MD 全文）——**等于把已同步文档的全文发给外部 LLM 一次**；检索时，文档描述、树结构（标题 + 摘要）与选中节点原文发给检索模型。与 Sirchmunk 外发边界同类（见 [14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md](../frozen/14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md) §8），但建树的一次性全文外发是新增项，USAGE_GUIDE 必须写明。
- **开关策略**：`tree_search.enabled: false` 默认关闭；`pageindex_sync.py` 只由用户手动运行，不在摄取时自动触发（D5），启用即视为接受上述外发。云端模式不做（D1）。

## 9. 风险与开放问题

| # | 风险 / 问题 | 应对 |
|---|------------|------|
| R1 | 上游迭代快，仓库与 PyPI 版本口径不一致 | D6 锁定精确版本；`pi_adapter.py` 收敛全部上游调用，升级时只改一处并重抓契约 fixture |
| R2 | GLM 的 JSON / 选节点质量不足 | PI0 实测（D4）；`tree_loop` 非法输出重试 + 降级；换模型只改 env |
| R3 | 超大文档与超大节点：1.7 MB MD 转出的派生 PDF 预计数百页，建树成本与单节点原文都可能撑爆上下文（0.4 改写，原 `md_to_tree` thinning 应对不再适用） | PI0.4 单篇试转先测页数与成本；`max_evidence_chars` + 优先下钻子节点 + 截断标记；MD 节点原文按回定位行区间读取，可精确裁剪 |
| R4 | 一次树检索超过 `timeout_s` | `max_loops` / `max_token_budget` 截停（0.4 起每次调用前检查）；超时走既有失败分支；PI0.3 旧管线 md P95 145s，PI0.6 / PI7 按新管线记录 P95 后校准默认值 |
| R5 | 建树成本：1.7 MB Markdown 摘要 token 量可观 | PI0.3 单列建树成本（旧管线 61 分钟）；PI0.4 / PI0.6 实测新管线；同步命令支持 `--collection` 分批；`file_hash` 未变不重建 |
| R6 | sidecar 内并发：上游 DocStore 锁 + 单进程同步端点，不适合并发 | 首期单用户本机场景接受串行；需要并发时另议 |
| R7 | ~~#609 上游修复后预处理与上游行为重叠~~ | **作废（0.4）**：D3 改为 MD 转 PDF，不再调用 `md_to_tree`，`md_prep` 删除，#609 不再影响本规格 |
| R8 | 转换后结构还原失真：标题层级在 PDF 中不明显、代码块或表格跨页截断，导致上游误判结构或节点标题无法回定位（0.4 新增） | 打印 CSS 固定标题层级字号、代码块 `pre-wrap`；PI0.4 抽样核对与回定位成功率实测；运行期 telemetry 计 `unlocated`，回定位失败退化为只给 `section` |
| R9 | 系统工具依赖：pandoc 与 Chromium 不是 pip 依赖，版本漂移或缺失（0.4 新增） | `setup_venv.sh` 检测版本，Chromium 优先用 sidecar venv 的 playwright 托管版本；`start_sidecar.sh` 启动日志打印两者版本；缺失时 `/index` 对 MD 返回 `render:` 前缀错误，原生 PDF 不受影响 |
| R10 | 派生 PDF 泄露进其他检索路，造成重复证据或越权路径（0.4 新增） | 只存 `<PAGEINDEX_STORAGE>/derived/`（`data/db` 下，被 `denied_substrings` 隔离），不进 `data/documents/` / `data/uploads/`；证据路径回映射由 PI1 单测锁定（D12），验收清单单列一条 |

## 10. 附录：PI0 四路对照基线（2026-11-22）

### 10.1 对照表（30 题 = MD 25 + PDF 5，用户逐条确认定稿）

| 路由 / 语料 | n | ok | hit | locatable | P50 | P95 | token/题 |
|---|---|---|---|---|---|---|---|
| tree / md | 25 | 25 | 0.28 | 0.68 | 52.9s | 145.4s | 59,870 |
| hybrid / md | 25 | 25 | 0.40 | 0.52 | 0.77s | 1.43s | 0 |
| agentic_deep / md | 25 | 25 | 0.08 | 0.32 | 6.1s | 10.5s | 32,612 |
| plan_a / md | 25 | 25 | 0.40 | 0.52 | 0.78s | 1.14s | 0 |
| tree / pdf | 5 | 5 | 1.00 | 1.00 | 10.0s | 32.0s | 2,080 |
| hybrid / pdf | 5 | 5 | 1.00 | 1.00 | 0.58s | 0.73s | 0 |
| agentic_deep / pdf | 5 | 0 | — | — | — | — | —（上游缺陷，见 10.3 ③，该路无效） |
| plan_a / pdf | 5 | 5 | 1.00 | 1.00 | 0.61s | 0.79s | 0 |

一次性建树成本（缓存复用后为零增量）：1.7 MB 篇 1,881 节点 61 分钟 / 470 KB 篇 253 节点 12 分钟 / 351 KB 篇 317 节点 10 分钟 / PDF 222 页 177 节点 33 秒。

**解读**：MD 上树检索原型 hit 未超 hybrid（0.28 vs 0.40），locatable 为唯一优势（0.68 vs 0.52），代价是 P50 53 秒与单题 ~6 万 token（R3 风险实测坐实：大树逐层下钻 token 消耗巨大，1881 节点整树大纲单次调用即 ~30 万 token，不可行，故原型采用逐层下钻）；PDF 页定位类问题上 hybrid 已能答好，树检索无增量；agentic_deep 全面最弱。R4 的 P95 实测：树检索 145s（md），`timeout_s` 默认值需按此校准（0.4 起改为 PI0.6 按新管线实测后校准）。

### 10.2 度量口径备注（与 3.6 第 4 条的差异澄清）

- **md 基线语料**：3.6 假定「MD 对照直接用默认库中已摄取的文档」，实测默认库不含目标 4 篇——已按正常摄取路径补齐（SHA256 幂等），基线为「全默认库检索」，tree 路候选范围限 4 篇。
- **同文件归属判定**：语料存在跨文件重复内容（章节拆分件），hit / locatable 只计「证据文件 == 题目目标文件」，跨文件命中单独计数（hybrid/md 250 条结果中 151 条跨文件）。
- **PDF 页码**：摄取侧 markitdown 拍平页边界、chunker 无页追踪（产品链路无 `metadata.page`），hybrid / plan_a 页码由 `PageLocator` 离线回定位（pypdf 逐页去空白子串探针 + 多页消歧，方案 (a) 口径）；tree / pdf 页码取树节点 `start_index` / `end_index`（物理页）。
- **agentic_deep / md 摄取库**：sidecar 按 `data/documents` 允许根检索；sidecar auth middleware 将 401 报为 500（10.3 ②），token 环境变量注入后正常。

### 10.3 PI0 过程发现（0.4 起处置见 10.4 方案表，落地在 PI0.7）

| # | 发现 | 性质 | 建议处置 |
|---|------|------|---------|
| ① | PDF 空/扫描页文本 chunk 一路流到 embed 阶段才崩（`texts must be non-empty string`），IR 冻结契约对 pypdf 空文本「行为保持不变」、空文本按 load 失败记 `failed_stage` 只落在 markitdown loader | 产品缺陷（IR 健壮性） | 派生台账：摄取链路空文本防护（跳过或按 load 失败）+ 单测 |
| ② | sirchmunk auth middleware 把 `HTTPException(401)` 变成 500 `INTERNAL_ERROR`（`sirchmunk/api/main.py:107`） | 上游缺陷 | 上游 issue 候选；本项目侧已用 env 注入 token 规避 |
| ③ | sirchmunk DEEP 对 PDF 返回 summary 但证据片段恒空（"No source snippet was recorded"；另一分支报 `ConfidenceDecision` 参数缺失），md 正常 | 上游缺陷 | 上游 issue 候选；agentic_deep/pdf 路基线判无效 |
| ④ | markitdown 无页边界 + chunker 无页追踪：产品若要 PDF 页码引用（PI5 验收项），需在摄取链路传递页码 metadata | 产品缺口 | 0.4：longdoc 的 PDF 页码取树节点，MD 引用用「章节 · 行号」（D8 / D12），主摄取链路不补；hybrid 的 PDF 页码引用另立提案 |
| ⑤ | PathGuard `denied_substrings` 含 `data/db`，任何建在 `data/db/` 下的临时语料目录都会被 collection 定界整体判越权（空定界集零证据） | 使用约束（非缺陷，fail-closed 按设计工作）；0.4 起同时是设计约束（D12） | USAGE_GUIDE / PI2 运维文档写明：语料目录不得置于 `data/db/` 下；派生 PDF 必须在 `data/db/` 下，且证据路径回映射到原 MD（PI1 单测锁定） |

### 10.4 方向变更：MD 转 PDF 后建树（0.4，2026-11-22 用户裁决）

**裁决**：废弃 MD 建树管线（上游 `md_to_tree`，以及原型中的 MD 树缓存；后者只作新旧对照，保留到 PI0.7 收尾）；MD 转为派生 PDF 后与原生 PDF 同走 `submit_document` / DocStore（D3、D11、D12）。原 PI0.4 拆为 PI0.4–PI0.7，PI1 改为 `heading_index` / `md_render` / `tree_store` / `tree_loop` 四个模块。

**依据**（PI0.3 明细按目标文件重拆，hit 为严格同文件口径）：

| 目标文件（题数） | tree | hybrid | agentic_deep | plan_a |
|---|---|---|---|---|
| 1.7 MB 原大文件（12） | 4/12 | 0/12 | 2/12 | 0/12 |
| 拆分件 `应用开发项目与开源架构解析.md`（8） | 3/8 | 8/8 | 0/8 | 8/8 |
| 拆分件 `13_面试实战.md`（5） | 0/5 | 2/5 | 0/5 | 2/5 |

- hybrid 在原大文件上 0/12 不等于找不到内容：这 12 题的证据都落在 `章节拆分/` 的拆分件上，被同文件归属判定判为 miss。口径问题与检索能力问题叠在一起，需要 PI0.5 用双口径拆开。
- MD 树粒度过细：`md_to_tree` 把每个 `#` 标题与整行粗体都建成节点，1.7 MB 篇 1,881 节点、建树 61 分钟；原生 PDF 222 页只有 177 节点、33 秒。
- 检索成本失控：tree/md 单题 token 中位 69.6K、最大 85.5K，超出 32K 预算（预算只在每轮开头检查）；8 题没有目标文件内的证据（原大文件 2、两个拆分件 6）；tree/pdf 单题 2,080。
- 推断（待 PI0.4 验证）：PDF 路径的结构来自版面解析与目录识别，节点粒度更接近「章节」，有望同时降低建树与检索成本；代价是新增转换环节，结构还原质量未知。

**各问题对应方案**：

| # | 问题（出处） | 方案 | 落地任务 |
|---|---|---|---|
| 1 | MD 树粒度过细、建树慢（10.1 建树成本） | D3 改为 MD 转 PDF 建树，转换链按 D11；先拿单篇大文件试 | PI0.4 |
| 2 | 转换后的结构还原与回定位质量未知（R8） | 三种目录做法对比；抽样核对标题层级；统计节点标题回定位成功率 | PI0.4 |
| 3 | 派生 PDF 路径含 `data/db`，流到主 venv 会被 PathGuard 剔除，也对不上 collection 定界（10.3 ⑤） | D12：证据 `source_path` 回写原 MD，映射表另记 `derived_pdf`；单测锁定 | PI0.5 原型 / PI1 正式 |
| 4 | 派生 PDF 页码对用户无意义 | MD 引用显示「章节 · 行号」（`heading_index` 回定位），页码只用于原生 PDF（D8 0.4 补充） | PI1 / PI5 / PI6 |
| 5 | token 预算超支（中位 69.6K、最大 85.5K，预算 32K） | 每次调用前检查预算、单层节点预筛、证据足够提前停止 | PI0.5 原型 / PI1 `tree_loop` |
| 6 | 树检索慢（md P50 53s、P95 145s） | 多文档并行、提前停止；`timeout_s` 按新管线 P95 校准 | PI0.5 / PI0.6 |
| 7 | 拆分件与原文件内容重复，严格同文件判定把内容命中判为 miss | 严格同文件 / 内容等价双口径并报；tree 候选剔除拆分件 | PI0.5 |
| 8 | 各路返回条数不同，hit 口径不一 | 统一前 k 条判 hit；新增证据精度 | PI0.5 |
| 9 | PDF 只有 5 题，三路全 1.0，无区分度 | 是否补 PDF 跨节归纳题由用户决定（逐条确认） | PI0.6 |
| 10 | `tree_telemetry` 部分重跑被覆盖；上游 client 写到仓库根 `.pageindex/`，与 `PAGEINDEX_STORAGE` 不一致 | 按路由合并 telemetry；显式传存储参数并清掉根目录缓存 | PI0.5 |
| 11 | D7 否决 `agent_tools()` 的理由随 D3 失效 | 复核其只读工具能否作 `read_node` / 结构读取实现 | PI0.5 |
| 12 | 10.3 ① PDF 空 chunk 流到 embed 阶段才崩 | 与方向变更无关：派生台账（摄取链路空文本防护 + 单测） | PI0.7 |
| 13 | 10.3 ② sirchmunk 401 报成 500 | 上游 issue 草稿 | PI0.7 |
| 14 | 10.3 ③ sirchmunk DEEP 对 PDF 证据恒空 | 上游 issue 草稿；agentic_deep/pdf 在 PI0.6 继续判无效 | PI0.6 / PI0.7 |
| 15 | 10.3 ④ 产品链路无 PDF 页码 metadata | longdoc 页码取自树节点；主摄取链路不补（Out of Scope 不变）；hybrid 的 PDF 页码引用另立提案 | PI0.7 记录 |
| 16 | 10.3 ⑤ `data/db` 是 denied substring | 从使用约束升级为设计约束：派生 PDF 必须放在 `data/db/` 下，且绝不出现在证据路径（D12） | PI1 单测 + PI2 运维文档 |
| 17 | 临时工作区与补摄取残留 | 收尾清理，逐项经用户确认 | PI0.7 |

**待用户确认项**（到对应任务时再问）：

1. PI0.4 建树（花真实 token）的执行时机，以及节点标题回定位成功率的可接受线。
2. 目录做法的默认值（不带目录 / 无页码目录 / 两遍渲染带页码目录），按 PI0.4 数据定。
3. PI0.6 是否补充 PDF 题，以及复测（花真实 token）的执行时机。
4. PI0.7 的 D9 门槛数值与收尾清理清单。
