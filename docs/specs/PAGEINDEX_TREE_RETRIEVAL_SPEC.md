<!-- 规格定位：以独立 venv 的本机 HTTP sidecar 接入 PageIndex「树索引 + LLM 沿树推理」，在 Web UI Chat 新增 `longdoc` 检索模式，补长结构化文档的章节定位与页 / 行级引用。基线：S / SX / WC / LG / WS / SR / CMC / MR / AR / RA / AC 均已收口冻结。与既有规格的关系：独立增量；不改 Sirchmunk agentic 契约（`libs/agentic_search/` 与 `agentic_search` 配置节）、不改三档既有模式与 `auto` 的行为契约、不改 MCP 工具、不改摄取管线与 DocumentManager。 -->

# PageIndex 树检索规格 (PAGEINDEX_TREE_RETRIEVAL_SPEC)

> 版本：1.1 — 0.1 由 backlog 提案立项为可执行规格；0.2（2026-11-21）按规格评审修订（评审意见逐条对照基线代码核实后采纳）：消除 capabilities「逐字节不变」与新增 `longdoc_enabled` 字段的矛盾，写明需同步的既有测试断言，补齐错误分类、`ChatCapabilities` schema、配置锚定与入口预建、上下文位置头、前端改动面、trace 打点、同步命令删除边界、sidecar 契约边界与 PI0 可执行性细节，修正 Chat WS 路径与语料计数口径。PI0 为立项门（四路对照不达标即搁置），PI0 通过前 PI1 起不开工；0.3（2026-11-22）PI0.3 四路对照完成，基线落盘第 10 章附录，D9 终裁延后至新增的 PI0.4（用户裁决），PI1 起仍不开工；0.4（2026-11-22）方向变更（用户裁决）：MD 不再经上游 `md_to_tree` 建树，改为转换成派生 PDF 后与原生 PDF 同走 `PageIndexClient.submit_document` / DocStore——D3 改写，新增 D11（转换链）/ D12（派生 PDF 存放与证据回映射），D5 / D7 / D8 / G4 随之调整；0.5（2026-11-22）按源码与语料复核修订：D11 依据 `pageindex 0.7.4` Flash 源码（印刷目录页被当 boilerplate 过滤、优先消费 PDF Embedded Bookmarks、支持 `summary=False, optimize=False` 零 LLM 建树）改为对比 PDF Bookmarks / Outline 注入方案而非印刷目录，Chromium 改优先系统浏览器 + 独立 `--user-data-dir`；拆出 **PI0.4a（零 token 试转验结构）→ PI0.5（零 token 修口径与原型）→ PI0.4b（单篇花 token 建树实测）→ PI0.6 → PI0.7**；修正 MD 候选集（「剔除纯子集 `13_面试实战.md`，保留独立修订版 `应用开发项目与开源架构解析.md`」）、补齐 PI0.5 四处遗漏的脚手架/口径缺陷（`hybrid` 误计离线 `locatable`、`tree/pdf` 漏传 `get_pdf_page`、`synthesize` 多目标章节覆盖率、`submit_document` token 采集与并发限流/审核降级），以及 D12 `Preface`/`(intro)` 合成节点回定位、原 MD 漂移回退、`compute_capabilities` 冷启动缺省推断等边界；详见 10.4；0.6（2026-11-22）PI0.4a 完成：1.7 MB 篇转出 306 页派生 PDF（全链 ~10s、零 LLM），三种 Outline 方案实测落盘 10.5——纯 CSS 检测否决（417 节点 / 回定位 82.5%），Chromium CDP 与 `pypdf` 注入 Bookmarks 均达 `toc_source=bookmarks`、回定位 ~95%（建议默认 v3 `pypdf` 注入，待用户终裁），D11 / 10.4 回填实测数据；0.7（2026-11-22）PI0.5 完成（零 token）：0.5 口径修正全部落地（top-k 统一、双口径、locate/synthesize 分列 + section recall、证据精度、locatable 生产口径分路）、TreeSearcher 调优（判足够闭环 / max_nodes 硬截断 / 调用前预算检查 / 单层预筛 / 多文档并行 / D12 回映射 + 部件形归一化）、D7 复核回填（维持自建）、重算表落盘 10.6、fake LLM 冒烟通过；0.8（2026-11-22）PI0.4b 完成（真实 token，预授权执行）：单篇 1.7 MB 派生 PDF 经 `submit_document` 建树 merge vs full 对比——结构逐项相同（424 节点 / 深 6 / 回定位 88.0%），merge 省 0.9% token 与 1.1 分钟，胜出；发现 merge 亦不改写标题的假设不成立（~12% 节点标题被 LLM 重写且两次运行措辞不同），与上游同名派生 PDF 重复入 DocStore 的去重缺口（`<hash>_1.pdf`）；落盘 10.7；0.9（2026-11-22）PI0.6 完成（真实 token，预授权执行）：全量候选集 merge 档建树（新增 54.5 万 token：修订版 107 节点 / 4.2 min、`05_Agent.md` 112 节点 / 4.8 min）与 30 题四路复测——**tree/md hit 0.64（旧管线 0.28、hybrid 0.32）、token 中位 8.4K（旧管线 69.6K）、section recall 0.607、locatable 1.00、25/25 零失败**；3.3 校准回填（`timeout_s` 60→240、`index_timeout_s` 900→1800、`max_token_budget` 维持 32K）；PDF 补题用户裁决**不补**（维持 5 题）；D9 终裁材料已齐，拍板在 PI0.7；落盘 10.8；1.0（2026-11-23）PI0 收口（PI0.7）：D9 终裁**达标、PI1 开工**（门槛五条回填 D9 行：md hit ≥ 0.5 / locatable = 1.00 / sRec ≥ 0.5 / 成本按校准默认值封顶 / 建树量级接受），D4 终裁确认 `openai/glm-5.3-flash`，flash 混合方案终裁维持 submit 基线（降台账可选优化），10.3 ①–⑤ 处置归位派生台账 `docs/backlog/pageindex-followups_backlog.md`，收尾清理逐项确认执行完毕（临时工作区与旧 MD 树缓存已删、默认库保留 `05_Agent.md`）；1.1（2026-11-23）PI1 完成：sidecar 四模块落地（`heading_index` / `md_render` / `tree_store` / `tree_loop`，`tree_loop` 自 PI0.6 实测形态移植），单测 58 例全绿（含 `sys.modules` 红线断言），`map_headings_to_pages` 修正原型逐页 joined 窗口的系统性 N−1 页偏移（两段式：全页精确优先、跨界窗口兜底）
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
- 10 附录：PI0 四路对照基线与 0.4/0.5 方向修订（本规格追加章节）

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
| 本地 PDF | `submit_document` 只接受 `.pdf`；默认 flash 模式优先消费 **PDF 内嵌 Bookmarks（Outline）**（`use_embedded_toc=True`，`toc_source` 为 `bookmarks` / `hybrid`），其次走版面字号/样式检测（`detected`），**印刷目录页由 `mark_toc_and_boilerplate` 当噪音过滤**；若无层级结构且页数 > `FLAT_TREE_MAX_NODES`（10）则被 `flash_rejection_reason` 拒绝；`page_index_flash(pdf, summary=False, optimize=False)` 支持**零 LLM 纯本地结构提取**；正常 `submit_document` 由 LLM 写摘要与精修（`optimize="full"` / `"merge"` / `"off"`），返回值含 `usage` | `local_api.py`、`flash/` |
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
- capabilities：`GET /api/v1/chat/capabilities` 绑定 `response_model=ChatCapabilities`（`src/api/routes/chat.py:76-78`；模型在 `src/api/schemas/chat.py:81-89`，未声明字段会被静默丢弃）；`src/api/chat_wiring.py:34-35` 在冷启动懒初始化前（`available=None`）仅根据 `agentic_enabled` 缺省推断 `available`；`tests/integration/test_api_chat.py:521-532` 与 `frontend/lib/chat/normalize.test.ts:164-185` 均为整字典精确断言。
- 上下文头：`AnswerGenerator.build_context()`（`src/core/chat/answer_generator.py:68-74`）只渲染 `[id] source, p.N`，不含章节与行号。
- Markdown 加载现状：`src/libs/loader/markdown_loader.py` 已解析并写入文档级 `metadata["heading_outline"]`（1–6 级 ATX 标题，跳过围栏代码块），但 splitter 未给各 chunk 追踪当前标题栈与起止行号，故产品链路 chunk 无 `section` / `line_start` / `line_end`。
- 白名单根：`PathGuard` 对不存在的根直接抛 `PathGuardError`（`src/libs/agentic_search/path_guard.py:57-58`）；相对 `allowed_roots` 在 `load_settings()` 时经 `resolve_data_path` 锚定项目根（`src/core/settings.py:898`，RA2）；`scripts/start_api.py:94` 只对 `settings.agentic_search` 调 `ensure_local_roots`，该函数按 `enabled` / `allowed_roots` / `denied_substrings` 属性鸭子类型读取，可直接复用。
- 前端：`frontend/lib/chat/modes.ts:45` 在 `agentic_enabled` 为假时提前返回；引用标签由 `frontend/lib/chat/citations.ts` `citationLabel()` 统一生成（`SourcesPanel.tsx` 与 `MarkdownContent.tsx` 共用，文案为英文，如 `a.pdf p.4`）；`normalizeCitation` 只保留已知字段；`SourcesPanel.tsx:66` 以 `${id}-${chunk_id}` 作 React key。
- 摄取历史 v2 主键为 `(source_path, collection)`（`src/libs/loader/file_integrity.py:75`），同一物理文件可在多个 collection 各有一条 success 记录。
- Chat WS 端点为 `WS /api/v1/chat`（`src/api/routes/chat.py:48`）；带 `/ws` 后缀的是 Playground Deep 流式端点 `WS /api/v1/search/ws`。
- 摄取历史：`src/libs/loader/file_integrity.py` `SQLiteIntegrityChecker`（v2 success 记录含 `source_path` / `file_hash` / collection）；`src/libs/agentic_search/collection_paths.py` `CollectionPathResolver.resolve(collection)` 已能把 collection 推导为 PathGuard 校验后的绝对路径集合。
- 语料（`data/documents` 实测）：29 个 `.md`、各 1 个 `.docx` / `.pptx` / `.xlsx` / `.html`，**无 PDF**；大型 Markdown 1.7 MB（1,703,013 B）/ 1045 个以 `#` 起始的行、470 KB / 253、351 KB / 166、315 KB / 187，其余多为 3–50 KB 短笔记。计数口径为「行首第一个字符即 `#`」；最大一篇另按两种口径复核：先去掉行首空白再判断为 1047，只计围栏代码块外的合法 ATX 标题为 1036（后三篇行首口径与 ATX 口径一致）。注：`章节拆分/13_面试实战.md`（351 KB）为 1.7 MB 原大文件的纯子集，而 `章节拆分/应用开发项目与开源架构解析.md`（470 KB，修改时间晚两个月）为增补重排后的独立修订版，含原大文件不存在的章节（如 `3 模块化RAG项目`、`1.3 RSI 进展`，见 10.4 核实）。

### 1.3 决策依据与终裁

提案三问的答案：

1. **已观察到的问题**：见 1.1、1.2（结构性推断，PI0 负责补实测样例）。
2. **更简单的方案**：
   - **方案 (a)**：在既有 `MarkdownLoader` 文档级 `heading_outline` 基础上，由 splitter 给 chunk 补「标题路径 + 起止行号」metadata、引用显示章节与行号。不引入新进程，工程量轻，能覆盖引用可读性诉求，但不改变召回方式。PI0 以离线近似方式纳入对照（3.6 第 4 条）；(a) 达标则本规格止于预研。
   - **MCP 客户端旁路挂 PageIndex**：零代码，但上游本地模式无 MCP server（仅云端提供），且进不了 trace / Chat / collection 隔离。不采用。
3. **如何确认有效**：PI0 四路对照（Hybrid、Sirchmunk DEEP、方案 (a) 离线近似、PageIndex 树检索原型）在用户逐条确认的问题集上比较命中率、引用可定位率、P50 / P95 延迟与 token；端到端由 PI7 门控冒烟确认 Chat `longdoc` 模式可用且引用落到章节 / 行或页。

| 编号 | 决策 | 终裁 |
|------|------|------|
| D1 | 本地还是云端 | **本地模式**（2026-11-21，用户拍板）。文档与树索引留在本机，仅 LLM 调用外发；云端（原文件上传 PageIndex、付费）不做 |
| D2 | 首期接入面 | **Chat 新增模式 `longdoc`**（2026-11-21，用户拍板；模式名由初稿 `tree` 更名为 `longdoc`，后端能力命名 `tree_search` 不变）。MCP 独立工具、Hybrid 第四路融合不做；Query Playground 不做（2.2） |
| D3 | Markdown 路径 | **MD 转派生 PDF 后与原生 PDF 同走上游 DocStore**（2026-11-22 用户再拍板，取代 2026-11-21 的「自建 MD 树存储 + 检索循环」）：MD 按 D11 转为派生 PDF，调用 `PageIndexClient.submit_document` 建树，与原生 PDF 共用 DocStore 与读取接口；上游 `md_to_tree` 管线废弃（绕开 #609 的 `md_prep` 预处理随之删除，R7 作废）；派生 PDF 存放与证据回映射见 D12；检索循环两种格式统一自建（D7）。依据见 10.4（PI0.3 实测：MD 树 1.7 MB 篇 1,881 节点、建树 61 分钟、单题 token 中位 69.6K；原生 PDF 222 页 177 节点、建树 33 秒）。新风险：转换后的结构识别质量与建树成本未知，由 PI0.4a（零 token 验结构）与 PI0.4b（花 token 测摘要成本）分两步验证（R8） |
| D4 | 检索 / 摘要模型 | **sidecar 独立 env 配置，默认智谱 GLM（OpenAI 兼容端点，经 LiteLLM `openai/<model>` + `api_base`）**（规格设计决策，与 Sirchmunk sidecar「LLM 配置独立于 `settings.llm`」同口径）。PI0 实测工具调用 / JSON 输出质量，终裁回填本行；换模型只改 env，不改代码。**PI0 实测（2026-11-22）**：`openai/glm-5.3-flash`（Coding Plan 端点）下树检索逐层下钻全程 0 次非法 JSON 输出（telemetry `invalid_output: 0`）、选文档 / 选节点 / 页码证据读取正常，JSON 可用性初测通过；正式终裁与 D9 一并在 PI0.7 回填（0.4 拆分后）。**终裁（2026-11-23 用户拍板）：确认 `openai/glm-5.3-flash`**——PI0.6 全量复测 30+5 题零失败、0 次非法 JSON、零审核拦截，换模型只改 sidecar env 不改代码 |
| D5 | 建树时机 | **对账同步命令 `scripts/pageindex_sync.py`**（规格设计决策）：以摄取历史 v2 success 记录为准，`file_hash` 变化即重建、历史中已不存在的即删除、非 `.pdf` / `.md` / `.markdown` 跳过并计数。首期不改摄取管线与 DocumentManager（04 规格的 7 阶段与删除链路不动）；摄取后自动触发列入 2.2。**0.4 补充（D3 变更）**：MD 的 `file_hash` 仍取原 MD 的 SHA256，派生 PDF 按该值命名（D12）；MD 内容变化即重新转换并重建，删除 MD 索引时连带删除派生 PDF；转换失败单列计数，与建树失败区分 |
| D6 | 上游版本 | **锁定 PyPI 精确版本 `0.7.4`**（2026-11-21 PI0.1 终裁：PyPI 最新稳定版，2026-11-19 发布，`requires_python >=3.10`；上游仓库 `pyproject.toml` 写 `0.2.10` 与发布口径不一致，按 PyPI 为准。`setup_venv.sh` 写死版本、`start_sidecar.sh` 启动日志打印实际版本，对齐 KE0 先例）。已实测该版本导出 `PageIndexClient.submit_document` / `get_document_structure` / `get_page_content` 与 `pageindex.page_index_md.md_to_tree`（含 `if_thinning` / `if_add_node_summary` / `if_add_node_id` 参数，PI1 建树所需齐备） |
| D7 | 检索循环形态 | **自建「选文档 → 选节点 → 读原文 → 判是否足够」循环**（规格设计决策）：不用上游 `chat()`（会生成答案，与 AnswerGenerator 重叠）与 `agent_tools()`（只覆盖 DocStore 中的 PDF，D3 的 MD 存储用不上）。LLM 调用经注入的 `complete(prompt) -> str`，循环本体是不 import pageindex 的纯模块，可在主 venv 离线单测。**0.4 重估**：D3 变更后全部文档进 DocStore，原先否决 `agent_tools()` 的「覆盖不到 MD」理由失效；自建结论暂维持，理由改为 PI0.3 实测需要的逐层下钻、每题 token 预算与调用前检查、证据回映射（D12）上游工具循环均不提供。`agent_tools()` 的只读工具能否直接充当 `read_node` / 结构读取的实现由 PI0.5 复核，结论回填本行。**PI0.5 复核（2026-11-22）**：`agent_tools.py` 的只读工具（`browse_documents` / `get_document` / `get_document_structure` / `get_page_content`）是面向 openai-agents 循环的 MCP 工具封装（schema + `next_steps` 提示 + JSON 信封），底层就是 `PageIndexClient` 同名方法——「结构读取 / 读原文」直接调 client 方法即可，不必经工具封装层；逐层下钻、每题 token 预算与调用前检查、D12 证据回映射上游均不提供。**结论维持自建循环（D7 不变）** |
| D8 | 证据到引用 | **`RetrievalResult.metadata` 承载 `retrieval_route="longdoc"` / `source_path` / `page` / `page_end` / `section` / `line_start` / `line_end` / `node_id`；`Citation` 只增可选字段 `section` / `line_start` / `line_end`，值为 `None` 时 `to_dict()` 不输出该键**（规格设计决策）：既有 hybrid / agentic 引用的 `to_dict()` 逐字节不变，WS `sources` 帧只增不改。**0.4 补充**：MD 证据（派生 PDF 建树）的 `page` / `page_end` 置 `None`（派生 PDF 页码对用户无意义），`section` / `line_*` 由 D12 标题回定位得出，`source_path` 为原 MD 路径；页码只出现在原生 PDF 证据上 |
| D9 | PI0 门槛 | **本规格只固定度量口径（3.6 第 4 条），数值门槛在 PI0 基线跑出后与用户共同拍板**（规格设计决策，沿用 MR D2 先例）。门槛终裁前 PI1 不开工；终裁回填本行与 PI0.3 行。**2026-11-22 基线已出（第 10 章附录）**：树检索 md hit 0.28 低于 hybrid 0.40、P50（53s vs 0.8s）与单题 token（~6 万 vs 0）高一个数量级，locatable 为优势（0.68 vs 0.52；0.5 复核发现 hybrid 0.52 实为误计 `plan_a` 离线定位，见 10.1）；用户裁决终裁延后，PI1 起仍不开工。**0.4/0.5 方向变更后**：终裁改在新管线复测（PI0.6）完成后的 PI0.7 进行，门槛按 PI0.5 修正后的口径拍板。**PI0.6 完成（10.8）：终裁材料已齐**（新管线四路对照表、建树成本、locatable / section recall 优势与 P50·token 代价的量化），PI0.7 拍板。**终裁（2026-11-23 用户拍板）：达标，PI1 开工**。门槛（按 PI0.6 实测数值 + 余量，口径为 3.6 第 4 条 / 10.8 表）：① md hit ≥ 0.5（实测 0.64）；② locatable = 1.00（实测 1.00，生产口径唯一达标路）；③ synthesize 场景 section recall ≥ 0.5（实测 0.607）；④ 单题成本以校准后默认值封顶（`timeout_s` 240 / `max_token_budget` 32000，实测 max 21,285 token / 198.6s）；⑤ 建树成本按 D11 实测量级接受（~200–330K token / 篇，merge 档）。终裁同时确认：flash 混合方案不作为基线（维持 submit 全量摘要，见 10.7 ③）；过程发现归位派生台账 `docs/backlog/pageindex-followups_backlog.md` |
| D10 | auto 候选 | **`longdoc` 不进入 auto 候选**（规格设计决策，依据 1.2 第 2 条）：`_ALL_MODES` 保持三档并改名语义为「auto 候选与既有三档」，`longdoc` 单独追加到 capabilities `retrievals`；纳入候选需新增 criteria 文案并按 MR D2 门槛复核，另立提案 |
| D11 | MD → PDF 转换链 | **`pandoc` 转独立 HTML → headless Chromium `--print-to-pdf` + PDF Embedded Bookmarks（Outline）写入**（0.5 修订，PI0.4a 零 token 实测后终裁回填）：pandoc 3.x 处理 GFM（中文、表格、围栏代码块）；HTML 内嵌打印 CSS（代码块 `pre-wrap` 防截断、`h1`–`h6` 保持层级字号、不强加分页）。**为什么不再对比印刷目录页（0.5 纠偏）**：核查 `pageindex 0.7.4` `flash/` 源码，Flash 模式把印刷目录页当 TOC boilerplate 过滤（`flash/main.py:164`），真正优先消费的是 **PDF 内嵌 Bookmarks（Outline）**（`2023-annual-report.pdf` 实测自带 50 条 Bookmarks），其次才是字号版面检测。因此 PI0.4a 改为对比三种 Outline 路径：① 纯 CSS 字号版面检测（无 Outline）；② Chromium / CDP 生成 Document Outline；③ 转换后由原 MD 标题树 + 页码锚点经 `pypdf` 写入 PDF Bookmarks（有望实现标题层级零失真、回定位率接近 100%）。浏览器探测顺序：`PAGEINDEX_CHROMIUM` > 本机系统 Chrome / Chromium > 可选 sidecar venv playwright 托管浏览器（避免强制安装 ~300 MB 浏览器）；调用时强制 `--headless=new`、`--disable-gpu`、独立临时 `--user-data-dir`（防与桌面 Chrome 抢 profile 锁）与硬超时。本机无 xelatex / wkhtmltopdf / weasyprint，不作候选。转换在 sidecar 进程内经子进程调用，主 venv 不感知。**PI0.4a 实测（2026-11-22，10.5）**：三方案在 1.7 MB 篇上均 306 页、全链 ~10s 零 LLM；纯 CSS 检测（无 Outline）`toc_source=detected` 417 节点 / 回定位 82.5%，标题噪声明显（整行粗体与 front matter 混入），**否决**；Chromium CDP `generateDocumentOutline` 与 `pypdf` 注入 Bookmarks 均触发 `toc_source=bookmarks`、回定位 95.6% / 95.4%，**终裁（2026-11-22 用户拍板）：默认 `pypdf` 注入**（全离线确定性、标题层级零失真、无 CDP 依赖），回定位可接受线 ~95%（两类失败均可修，见 10.5）。**PI0.4b 实测（2026-11-22，10.7）**：派生 PDF 经 `submit_document`（merge vs full）建树 424 节点 / 深 6 / 回定位 88.0%，merge 1.106M token 16.2 分钟 vs full 1.116M token 17.3 分钟、结构与回定位完全一致，**merge 胜出**（full 的 expand 未增节点）；摘要成本量级 = 单篇百万 token 级（R5 实测坐实）。注意：flash 零 LLM 树（1,375 节点、回定位 95.4%、零 token）保真度高于 submit 树但无摘要、节点更细；「flash 结构 + 按需摘要」的混合方案是否优于 submit 全量摘要，列为 PI0.6 / PI0.7 的用户裁决项。**PI0.4b 出口用户终裁（2026-11-22）**：建树成本 / 质量可接受（预授权范围外数值已确认）、**merge 确认为默认 optimize 档**（POC `--submit-optimize` 默认值已随 PI0.6 改为 `merge`，`full` 仅作对照复现时显式传入） |
| D12 | 派生 PDF 存放与证据回映射 | **派生 PDF 存 `<PAGEINDEX_STORAGE>/derived/<md_sha256>.pdf`，证据一律回映射到原 MD**（0.4 设计决策，0.5 补齐边界）：① 不放 `data/documents/` / `data/uploads/`，否则 hybrid 摄取与 Sirchmunk 检索会读到重复内容；`PAGEINDEX_STORAGE` 默认在 `data/db/` 下，已被 `denied_substrings` 隔离且不入 git；按内容哈希命名直接对接 D5 对账。② sidecar 映射表记 `{source_path(原 MD 绝对路径), file_hash(MD SHA256), format: "md", derived_pdf, doc_id}`；证据 `source_path` 必须是原 MD 路径——派生 PDF 路径含 `data/db`，流到主 venv 会被 PathGuard 整条剔除（10.3 ⑤），也与 collection 定界集对不上。③ 位置字段：`heading_index` 解析原 MD 标题（含 Setext，跳过围栏代码块）得到「标题路径 → 行区间」，树节点标题按归一化文本加父路径消歧回定位为 `line_start` / `line_end`；对上游 Flash 自动插入的合成节点显式规则化映射：`Preface`（`flash/api.py:164`）映射至首个标题前正文、`<parent title> (intro)`（`flash/api.py:151`）映射至父标题到首个子标题前区间；PI0.4b 一并评估 `optimize="merge"`（确定性合并、不改写标题、省 LLM）与 `optimize="full"` 的取舍；回定位失败只给 `section`，telemetry 计 `unlocated`。**PI0.4b 实测修正（10.7）**：`merge` 不改写标题的假设不成立——51/424（~12%）节点标题被 LLM 重写且 merge / full 两次运行措辞不同（`full` 多为 expand 改写、`merge` 侧疑为摘要环节顺手改写），这部分标题无法精确回定位原 MD 标题，只能落到页 / 章节近似；`heading_index` 需保留模糊匹配（归一化 + 包含关系）作为兜底。④ MD 节点原文优先按回定位的行区间从原 MD 读取（避开 PDF 文本抽取的断行与页眉页脚噪声）；读取前校验原 MD 哈希或行边界/首行一致，若文件在同步后已被修改或越界则安全回退 `get_page_content` 并记 telemetry |

### 1.4 对提案的调整

1. **G1（单后端路由）**：提案候选方向之一是把 `agentic_search.backend` 扩为列表。不采用：新建独立 `src/libs/tree_search/` 与 `tree_search` 配置节，`AgenticSearchFactory` 与 `agentic_search` 节不动，Sirchmunk 既有契约与测试零影响。
2. **G3（证据契约）**：提案写「`AgenticEvidence` 只增字段」。改为 `libs/tree_search` 自有 `TreeEvidence`，`AgenticEvidence` 不改。
3. **G4（证据获取）**：提案优先方案为 `agent_tools()` + 本项目 LLM。`agent_tools()` 只能读上游 DocStore（PDF），D3 的 MD 自建存储无法复用，改为 D7。**0.4 重估**：D3 变更后 MD 也进 DocStore，此否决理由失效，最终取舍见 D7 的 0.4 重估。
4. **G5（索引生命周期）**：提案写「摄取后异步建树 + 删除联动第 5 个存储」。改为 D5 对账同步，不改摄取管线与 DocumentManager。
5. **决策编号**：提案待决的 D1–D6 在本规格沿用原编号并终裁或给出设计决策，D7–D10 为立项时新增，D11–D12 为 0.4 方向变更时新增。

## 2. 核心目标与范围

### 2.1 In Scope

| 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|--------|-----------|
| 预研 | 四路对照立项门 | 门控（PI0，立项门） | sidecar venv + 版本锁定、用户确认的问题集、四路对照基线；0.4/0.5 起按 **PI0.4a（零 token 试转验结构）→ PI0.5（零 token 修口径与原型）→ PI0.4b（单篇花 token 建树）→ PI0.6（新管线复测）→ PI0.7（D9 终裁与收尾）** 推进，不达标即搁置 |
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

**sidecar 模块（PI1）**（0.4 按 D3 / D11 / D12 改写，原 `md_prep.py` 删除；0.5 补齐合成节点与 Outline 边界）：

- `heading_index.py`（纯，只依赖标准库）：`build(markdown: str) -> HeadingIndex` 解析 ATX 与 Setext 标题（跳过围栏代码块），每个标题记 `(level, title, line_start, line_end)`，`line_end` 为下一个同级或更高级标题的前一行，首个标题前的正文记为文件名节点；`locate(title, parent_path) -> LineSpan | None`：标题文本归一化（去首尾空白与强调符号、全角半角统一）后匹配，同名标题按父路径消歧；对上游 Flash 插入的合成节点规则化处理（`Preface` → 首个标题前正文，`<parent title> (intro)` → 父标题到首个子标题前区间）；仍歧义或未匹配返回 `None`。
- `md_render.py`：`render(md_path, out_pdf, *, runner, outline_mode) -> RenderResult(pages, elapsed_ms)`。构造 pandoc 与 Chromium 命令行（参数按列表传递，不经 shell，Chromium 强制 `--headless=new` / `--disable-gpu` / 独立临时 `--user-data-dir`，探测顺序按 D11）并生成内嵌打印 CSS 的 HTML 模板；按 PI0.4a 终裁的 `outline_mode` 写入 PDF Embedded Bookmarks（Outline）；执行由注入的 `runner` 完成（生产为带超时的 `subprocess.run`，单测注入 fake）；错误分型为 `RenderToolMissing` / `RenderTimeout` / `RenderFailed`；产物先写临时文件，再原子改名为 `<storage>/derived/<md_sha256>.pdf`。不 import pageindex。
- `tree_store.py`（纯）：JSON 文件存储，记录 `{source_path, file_hash, format(pdf|md), doc_id, derived_pdf, indexed_at, node_count}`；树本体在上游 DocStore，本模块只存映射与 MD 标题索引缓存。原子写、单进程内锁。
- `tree_loop.py`：`search(query, docs, *, complete, read_node, max_nodes, max_loops, max_token_budget) -> LoopResult`。
  1. 候选文档超过 1 篇时，给 LLM 文档描述列表，选出相关文档（JSON `doc_ids`）；
  2. 每篇选中文档给出不含正文的树（标题 + 摘要 + 节点 ID），LLM 返回 JSON `node_ids`；
  3. `read_node` 读原文（MD 优先按回定位行区间读原 MD，读前核对文件哈希或行边界/首行一致，漂移或越界回退 `get_page_content` 并记 telemetry），单节点超过 `max_evidence_chars` 时优先下钻子节点，仍超限则截断并标记；
  4. LLM 基于已读证据判断是否足够，不够且未超 `max_loops` / 预算则回到第 2 步补选；总输出节点硬截断不超过 `max_nodes`。
  5. 预算在**每次** LLM 调用前按预估 prompt token 检查，预估超出剩余预算即停止并返回已得证据（PI0.3 原型只在每轮开头检查，实测单题超支到 85.5K / 32K）；单层候选节点过多时先按标题 + 摘要做一次压缩预筛；多篇选中文档的选节点调用可并行。
  
  LLM 输出非法 JSON 时重试一次，仍非法则返回已得证据并在 telemetry 记 `invalid_output`。证据按选中顺序给出 `score = 1 / rank`。

**sidecar 服务（PI2）**：`server.py` 只做 HTTP 编排与鉴权；`pi_adapter.py` 是运行期唯一 import `pageindex` / `litellm` 的文件，向纯模块注入 `complete` 与 `read_node`（预研脚本 `poc_compare.py` 是唯一例外，见下文红线规则与 3.6 第 5 条）。调用 `PageIndexClient` 时必须显式传 `storage_path=PAGEINDEX_STORAGE`、`summary_concurrency`（避免上游默认 128 并发触发低 RPM 账户 429），并保留单节点 400 内容审核拒绝降级保护；`/index` 响应的 `llm_tokens` 取自 `submit_document` 返回的 `usage["total_tokens"]`。`md_render` 的子进程输出只落在 sidecar 进程日志，不影响主进程（0.4 起不再调用 `md_to_tree`）。

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
- `api/chat_wiring.py`：`_ALL_MODES` 保持三档（auto 候选口径不变，D10）；`tree_search.enabled` 且 `longdoc` 检索器已注册时，capabilities `retrievals` 末尾追加 `longdoc`；**同步更新 `compute_capabilities(settings, available=None)` 第 34–35 行的缺省推断分支**：`available is None` 且 `tree_search.enabled` 为真时须把 `"longdoc"` 纳入缺省推断集合，防止服务冷启动懒初始化检索器前首次拉取 capabilities 漏报 `longdoc`；新增 `longdoc_enabled: bool` 字段（值为 `tree_search.enabled`，只增；为 false 时其余键与值不变）。
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
  timeout_s: 240                    # 整次 /search 总超时（PI0.6 实测校准：tree max 198.6s / P95 137.4s，原 60 不够）
  index_timeout_s: 1800             # pageindex_sync 单文档 /index 超时（MD 含转换；PI0.6 校准：最慢单篇 merge 972s @306 页、并发 3，原 900 不够）
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
| sidecar | `scripts/pageindex/{setup_venv.sh,start_sidecar.sh,env.example,server.py,pi_adapter.py,md_render.py,heading_index.py,tree_store.py,tree_loop.py}` | 新增（0.4/0.5：`md_prep.py` 改为 `md_render.py` + `heading_index.py`；`setup_venv.sh` 增 pandoc 与系统 Chrome/Chromium 检测，playwright 可选；`env.example` 同步删旧 MD 树注释并补 D11 env） |
| sidecar / 预研 | `scripts/pageindex/poc_compare.py`、`scripts/pageindex/poc_main_routes.py` + 问题集 `tests/fixtures/pageindex/poc_questions.jsonl` | 新增（PI0；`poc_main_routes.py` 运行于主 venv，不 import pageindex） |
| 主 venv | `src/libs/tree_search/*` | 新增 |
| 主 venv | `src/core/settings.py`、`config/settings.yaml` | 新增 `tree_search` 节；`CHAT_RETRIEVAL_MODES` 追加 |
| 主 venv | `src/core/chat/retriever.py`、`src/core/chat/engine.py`（`classify_error`）、`src/core/chat/answer_generator.py`（`build_context`）、`src/api/chat_wiring.py`、`src/api/schemas/chat.py`（`RetrievalMode` + `ChatCapabilities`）、`src/observability/dashboard/services/chat_retrievers.py`、`src/core/response/citation_generator.py` | 只增（见 3.2） |
| 脚本 | `scripts/pageindex_sync.py`、`scripts/check_redlines.py`、`scripts/start_api.py`（`ensure_local_roots(settings.tree_search)`） | 新增 / 增规则 / 增一行预建 |
| 前端 | `frontend/lib/chat/{types,modes,normalize,citations}.ts`、`frontend/components/chat/ModeSelector.tsx` | 只增（`SourcesPanel.tsx` / `MarkdownContent.tsx` 经 `citationLabel()` 自动生效，不改） |
| 测试 | `tests/unit/test_tree_search_*.py`、`tests/unit/test_pageindex_sidecar_*.py`、`tests/unit/test_pageindex_sync.py`、`tests/integration/test_tree_search_http.py`、`tests/e2e/test_pageindex_integration.py`（门控）、前端 vitest / Playwright 用例 | 新增 |
| 测试 | `tests/integration/test_api_chat.py`（capabilities 精确字典补 `longdoc_enabled: false`）、`tests/unit/test_mode_router_config.py`（`RETRIEVAL_MODES` 四元组）、`frontend/lib/chat/{normalize,modes}.test.ts`、`frontend/components/chat/chat.components.test.tsx`（夹具补字段）、`tests/e2e_web/fake_api_server.py`（longdoc 支持） | 同步既有断言 / 夹具（语义不变，见第 4 章） |
| 其他 | `.gitignore`（`.venv-pageindex/`、`.env-pageindex`、`.pageindex/`） | 追加 |

### 3.6 选型理由

1. **独立 sidecar 而非进程内 import**：`md_to_tree` 的裸 `print` 会污染 MCP stdio（API 进程与 MCP server 共用 `src/`；0.4 起已不调用 `md_to_tree`，MD 转换的子进程输出同样只应落在 sidecar）；litellm / openai-agents / mcp 依赖链重且迭代快；`submit_document` 受 multiprocessing spawn 约束。与 Sirchmunk 决策同构，见 [2026-10-05-sirchmunk-http-sidecar.md](../../.agents/notes/implemented/architecture/2026-10-05-sirchmunk-http-sidecar.md)。
2. **不复用 Sirchmunk sidecar 进程**：其 ASGI 应用是上游 `sirchmunk serve`（或 KE 的 `evolve_launcher.py`），本项目不拥有；两套依赖混装会让任一侧升级互相阻塞。
3. **独立 `libs/tree_search/` 而非扩展 `libs/agentic_search/`**：两者请求模型（paths + FAST/DEEP vs source_paths + 节点预算）与证据形态（字符偏移 vs 章节 / 页 / 行）不同；独立后 AC / AR / SX 冻结契约与测试零改动，代价是 PathGuard / CollectionPathResolver 跨 lib 复用（直接 import，二者不依赖 sirchmunk）。
4. **PI0 度量口径**（D9 只固定口径）：
   - 问题集：从最大的 3 篇 Markdown + 1 篇长 PDF（上游 `examples/documents/2023-annual-report.pdf`，只放 sidecar 工作区不入库）起草 20–30 条章节定位 / 跨节归纳题，每题标注目标章节（MD 行范围或 PDF 页范围），由用户逐条确认（MR0.2 先例）。
   - PDF 对照的隔离：Hybrid 与方案 (a) 必须先摄取该 PDF 才能检索。PI0 使用临时配置文件，把向量库 / BM25 / 摄取历史 / 图片库路径指向临时目录，以独立 collection `pi0_poc` 摄取；Sirchmunk DEEP 一路把该 PDF 所在临时目录加入临时配置的 `agentic_search.allowed_roots`。跑完删除临时目录，不污染默认库，不入 git。MD 对照直接用默认库中已摄取的文档（PI0 实测默认库未含目标 4 篇，已按正常摄取路径补齐，见 10.2）。
   - 命中率：返回证据与目标范围有交集的题目占比。
   - 引用可定位率：生产口径——引用能直接给出章节与行号（或页码）的占比；`hybrid` 仅计产品自身输出的 `section` / `line_start` / `page`（现状均无，应为 0.00），方案 (a) 才计离线回定位近似（把 Hybrid 命中 chunk 在源文件中定位后推导标题路径或页码，不改摄取代码）。
   - 延迟 P50 / P95、单题 token；Sirchmunk DEEP 取 telemetry，树检索取原型计数。
   - 一次性建树成本（token 与耗时）单列（`submit_document` 取 `usage["total_tokens"]`）。
   - 0.4/0.5 口径修正（区分 `hybrid` 与 `plan_a` 的 `locatable`、`locate` / `synthesize` 分列并增报多目标章节覆盖率 section recall、统一前 k 条判 hit、新增证据精度、严格同文件 / 内容等价双口径、tree 候选剔除纯子集拆分件但保留独立修订版以维持多文档路由）由 PI0.5 落地，新旧口径对照见 10.4。
5. **PI0 不依赖 PI1 / PI2**：PI0.3 的树检索原型内置于 `scripts/pageindex/poc_compare.py`，在 sidecar venv 中直接调用上游 `md_to_tree` / `PageIndexClient` 与 litellm 独立运行（0.4 起 MD 改为转换后走 `PageIndexClient`，PI0.5 同步原型），不依赖 PI1 纯模块与 PI2 服务，避免排期倒挂。立项门通过后，PI1 / PI2 把原型中的预处理、存储与检索循环抽取为正式模块；`poc_compare.py` 作为预研存档保留，并按 3.2 列入红线精确白名单。
6. **MD 转 PDF 建树而非自建 MD 树（0.4/0.5，D3）**：PI0.3 实测上游 `md_to_tree` 把每个标题与整行粗体都建成节点，长 MD 的树过细（1,881 节点），建树慢、逐层下钻 token 失控；PDF 路径由上游 Flash 引擎消费 **PDF 内嵌 Bookmarks（Outline）** 与版面字号/样式检测建树（印刷目录页反而被当 boilerplate 过滤），节点数与页数同量级（222 页 177 节点）。转成 PDF 还让两种格式共用 DocStore 与同一读取接口，`tree_store` 不再自存 MD 树。代价是新增 pandoc / Chromium 系统依赖、一次转换与 Outline 写入，结构识别质量由 PI0.4a 零 LLM 实测验证。选 pandoc → HTML → Chromium，而不选 LaTeX / wkhtmltopdf / weasyprint，原因是本机只有前者齐备，中文走浏览器系统字体、不需要 LaTeX 字体配置（PI0.4a 验证），且打印 CSS 能控制代码块换行与标题层级（D11）。

## 4. 测试方案

- **离线确定性测试**（全部进 `pytest tests`，零网络、零真实模型，CI 禁网命令下通过）：
  - 导入方式：`pyproject.toml` 的 `pythonpath` 只含 `src`，不为此改动。主 venv 单测按既有先例导入 `scripts/pageindex/` 下的模块：用 `importlib.util.spec_from_file_location` 按文件加载（如 `tests/unit/test_measure_process.py`），或在测试模块内 `sys.path.insert` 该目录（如 `tests/unit/test_start_api_sidecar.py` 导入 `sidecar_manager`）。纯模块只依赖标准库，彼此之间用同目录导入。
  - sidecar 模块（PI1，0.4/0.5 改写）：`heading_index` 覆盖 ATX / Setext、围栏代码块内的 `#` 不计、首个标题前正文、`Preface` 与 `<parent> (intro)` 合成节点映射、同名标题按父路径消歧、归一化匹配（强调符号 / 全半角）与未匹配返回 `None`；`md_render` 注入 fake runner 覆盖命令行构造（列表传参、不经 shell、Chromium 独立临时 `--user-data-dir` 与 `--headless=new`）、浏览器探测顺序（`PAGEINDEX_CHROMIUM` > 系统 Chrome > 可选 playwright）、PDF Outline 注入、工具缺失 / 超时 / 非零退出三类错误分型、产物原子改名为 `<md_sha256>.pdf` 且位于 `<storage>/derived/` 下；`tree_store` 原子写 / 幂等 / 删除（删除 MD 记录时返回待删派生 PDF）；`tree_loop` 用 fake `complete` 覆盖选文档、选节点、同节点去重、`max_nodes` 硬截断、下钻、截断、「读原文 → 判是否足够」闭环、`max_loops` / 预算截停（含调用前预算检查：预估超预算时不发起调用）、单层预筛、非法 JSON 重试与降级；证据回映射（D12）：MD 证据 `source_path` 为原 MD 路径、永不为派生 PDF 路径（锁定 10.3 ⑤），回定位失败时 `line_*` 为 `None` 且 telemetry 计 `unlocated`，原 MD 变更或行越界时安全回退 `get_page_content`。测试 import 这些模块时不得触发 `pageindex` / `litellm` import（测试断言 `sys.modules`），也不调用真实 pandoc / Chromium。
  - sidecar 服务（PI2）：`server.py` 以注入的 fake adapter 跑 FastAPI TestClient（鉴权 401、loopback 绑定参数、各端点 schema、`POST /api/v1/index/delete`、`source_paths: []` 返回空证据且不调用 adapter 检索）；`pi_adapter.py` 不进主 venv 测试。
  - 主 venv 后端（PI3）：`httpx.MockTransport` 覆盖 Bearer、超时、连接拒绝、5xx、`success=false`、非 JSON、契约 fixture 解析（`tests/fixtures/pageindex/contract_search_response.json`，PI2 真实响应抓取后替换）；线程 HTTP stub 集成测试走真实 httpx 链路；`converter` 的 `chunk_id` 规则与 MD / PDF 两种位置字段映射；配置节默认值与 fail-fast 校验，含 `http://127.0.0.1.evil.com` 与 `https://127.0.0.1` 被拒、`api_token` 缺失或为未解析 `${...}` 被拒、相对 `allowed_roots` 锚定项目根。
  - 索引同步（PI4）：fake 历史表 + fake 后端覆盖新增 / 重建 / 删除 / 跳过 / 单条失败不中断 / `--dry-run` 无写入；同一文件在两个 collection 的记录只建一份索引；`file_hash` 冲突时以实算 SHA256 为准；`--collection C` 不发出任何删除请求（sidecar 中有其他 collection 的索引时不受影响）；入口调用 `ensure_local_roots`；MD 记录的转换失败单列计数且不中断。
  - Chat（PI5）：fake 树检索后端驱动 ChatEngine，断言 `longdoc` 模式帧序列、引用含 `section` / `line_*`、collection 空定界集不调用后端、PathGuard 剔除越权证据、定界集外证据被二次过滤剔除、`tree_search` trace 阶段字段齐全且不含 `api_token`；`classify_error` 对三类 `TreeSearch*Error` 的 code / `transient` 映射与不打堆栈；超时与 5xx 按 `pipeline_max_retries` 重试；`build_context` 对 longdoc 结果带位置头、对无新字段结果输出不变；`compute_capabilities(settings, available=None)` 在 `tree_search.enabled=True` 时缺省上报 `"longdoc"`，`GET /api/v1/chat/capabilities` 经 HTTP 返回 `longdoc_enabled`；`mode_router.fallback_retrieval: longdoc` 与 `tree_search.enabled: false` 下 `chat.default_retrieval: longdoc` 均 fail-fast。
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
| PI0.3 | 四路对照与立项门终裁 | `poc_compare.py`（内置树检索原型，在 sidecar venv 独立运行，不依赖 PI1 / PI2，见 3.6 第 5 条）输出对照表（命中率 / 引用可定位率 / P50·P95 / 单题 token / 建树成本）落盘本规格附录；PDF 对照经临时隔离库摄取、跑完清理；D4、D9 终裁回填；不达标则本规格止于预研并建派生台账 | 已完成（2026-11-22：30 题 × 4 路全跑通，对照表落盘第 10 章附录，`poc_results.json` / `tree_pdf.jsonl` 落 `data/db/pageindex/poc/`（旧 `tree/md` 未落明细，不入 git）；D4 实测回填；D9 未终裁——用户裁决转入后续任务（0.4/0.5 拆为 PI0.4a → PI0.5 → PI0.4b → PI0.6 → PI0.7），PI1 起仍不开工。脚手架三轮修复见 commit `4463c4b`：每题独立预算 + 逐层下钻、md 按证据自身文件定位与同文件归属判定、PDF 页码离线定位（PageLocator）、隔离工作区避开 PathGuard `data/db` denied substring、md 基线语料补摄取；复核发现的四处遗留缺陷记入 10.1/10.4 并由 PI0.5 修正。D9 已于 2026-11-23 终裁达标（见 D9 行与 10.8），立项门通过） |
| PI0.4a | MD → PDF 单篇试转与零 token 结构验证（D3 / D11，0.5 拆出，不花 LLM token） | 对 1.7 MB 原大文件（`大模型应用_算法学习路线+八股+面试实战4.md`）按 D11 转换，对比三种 PDF Outline 方案（纯 CSS 字号检测 / Chromium Document Outline / 原 MD 标题树经 `pypdf` 注入 Bookmarks）：记录页数、转换耗时、PDF Bookmarks 条数，调用离线零 LLM 的 `page_index_flash(pdf, summary=False, optimize=False)` 实测 `toc_source`、节点数、树深、抽样核对结构还原，并统计 `heading_index` 原型回定位成功率（含 `Preface` / `(intro)` 合成节点）；结果回填 D11 与 10.4；结构还原或回定位率不可接受（可接受线由用户拍板）则止于本任务、重议 D3，不进入 PI0.4b | 已完成（2026-11-22：实测落盘 10.5——306 页派生 PDF、全链 ~10s 零 LLM；v1 纯检测 417 节点 / 回定位 82.5% 否决，v2 CDP Outline 与 v3 `pypdf` Bookmarks 均 `toc_source=bookmarks`、回定位 95.6% / 95.4%，未回定位集中于 CJK 部件形抽取伪影与整行粗体伪标题两类、均可修；用户终裁：接受 ~95%、默认 v3 `pypdf` 注入，PI0.4b 预授权（PI0.5 完成后直接执行）） |
| PI0.5 | 度量口径修正与原型调优（不花 LLM token，承接 PI0.4a 真实树形态） | ① 口径与数据修正：修复 `hybrid` 与 `plan_a` 混算 `locatable`（`hybrid` 按真实产品输出判 `locatable`，不再吃离线 `annotate_*`）；修复 `poc_compare.py` 漏传 `get_pdf_page=get_pdf_page` 导致 PDF 证据 `text` 全空；`locate` / `synthesize` 分列汇总并增报多目标章节覆盖率（section recall）；各路统一按前 k 条证据判 hit（k 写明并与 hybrid `top_k` 对齐）；新增证据精度（目标范围内证据条数 / 返回证据条数）；严格同文件与内容等价双口径并报；**MD 候选集修订**：剔除纯子集 `13_面试实战.md`（Q21–Q25 按内容等价映回原大文件），保留独立修订版 `应用开发项目与开源架构解析.md`（含原大文件不存在的 Q13 / Q20 内容），必要时增纳 1–2 篇独立长 MD 以真测多文档路由；征得用户确认修正 PI0-Q28 题干年份笔误（`2023 年 3 月` → `2024 年 3 月`）；hybrid / plan_a 按新口径重算（旧 tree/md 无明细，10.1 保留为旧管线对照），新旧口径对照表回填 10.4。② 原型调优：`TreeSearcher` 补齐「读原文 → 判是否足够」闭环、`max_nodes` 硬截断、每次调用前检查预算、单层节点预筛、多文档并行；接入 D12 证据回映射与 `heading_index` 原型（含 `Preface` / `(intro)` 与原 MD 漂移回退）；`build_pdf_tree` 采集 `submit_document` 返回的 `usage["total_tokens"]`、传入 `summary_concurrency` 并保留 400 审核降级保护；`env.example` 同步更新。③ 脚手架：`tree_telemetry` 部分重跑按路由合并而非覆盖；上游 client 显式传 `storage_path=PAGEINDEX_STORAGE`，仓库根 `.pageindex/` 清除。④ 复核 D7 `agent_tools()` 并回填。改动后 `poc_compare.py` 以 fake LLM 冒烟通过（含 `get_pdf_page` 证据非空断言） | 已完成（2026-11-22，零 token：① 口径修正全部落地并重算——`--rescore-only` 离线重算表落盘 10.6，hybrid/agentic locatable 生产口径归 0、hit 统一 top-k=10、hitEq 暴露语料重复效应（md hitEq=1.00）、Q28 年份已修（用户确认）、候选集 = 原大文件 + 独立修订版 + 增纳 `agent八股/05_Agent.md`（用户确认）；② TreeSearcher 补齐判足够闭环 / max_nodes 硬截断 / 每次调用前预算检查 / 单层预筛（>25 先压缩到 ≤15）/ 多文档并行，接入 D12 回映射 + heading_index 原型（含 front matter 跳过、CJK 部件形归一化、`Preface`/`(intro)`、漂移回退派生 PDF 页文本）；`build_pdf_tree` 采集 `usage["total_tokens"]`、显式 `storage_path` + 低并发、限流补丁幂等化并沿用 400 审核降级；`env.example` 补 D11 三项；③ `tree_telemetry` 按路由分键合并，仓库根 `.pageindex/` 迁入 `data/db/pageindex/` 后清除（DocStore 迁移后验证可读）；④ D7 复核回填：维持自建循环；fake LLM 冒烟 PASS（md/pdf 双路证据非空，含 `get_pdf_page` 断言）） |
| PI0.4b | 单篇派生 PDF 真实建树实测（0.5 拆出，**花真实 token，执行前须用户确认**） | 对 PI0.4a 胜出方案的单篇 1.7 MB 派生 PDF 调用 `submit_document` 建树（经 PI0.5 的 `summary_concurrency` 与 token 采集）：记录摘要后节点数、树深、建树耗时与真实 token（顺带对比 `optimize="merge"` 与 `"full"` 对标题保真度与成本的影响），复核摘要与回定位成功率；结果回填 D11 / D12 与 10.4；建树成本或质量不可接受则停在本任务、与用户重议 D3 | 已完成（2026-11-22，真实 token、用户预授权：merge 424 节点 / 1.106M token / 16.2 min，full 424 节点 / 1.116M token / 17.3 min，结构与回定位完全一致（88.0%），merge 胜出；merge 不改写标题假设被证伪（51 节点标题 LLM 重写、两次运行不同）；发现上游同 PDF 重复提交不去重（`<hash>_1.pdf`）；对照 flash 零 LLM 树（95.4% / 零 token）的取舍列为 PI0.6/PI0.7 裁决项；落盘 10.7） |
| PI0.6 | 新管线全量建树与四路复测（**花真实 token，执行前须用户确认**） | 派生 PDF 建树覆盖 PI0.5 确认的多文档 MD 候选集（剔除纯子集 `13_面试实战.md`，保留独立修订版 `应用开发项目与开源架构解析.md`）+ 原生 PDF；30 题按 PI0.5 口径四路复测（tree 路为新管线），输出与 10.1 同结构的对照表（区分 `hybrid` 与 `plan_a` 的 `locatable`，增 `locate`/`synthesize` 分列、section recall、证据精度、双口径及真实建树 token），落盘 10.4；现有 PDF 题仅 5 条且三路全 1.0、无区分度，是否补 PDF 跨节归纳题由用户决定（补题须逐条确认，PI0.2 先例）；`timeout_s` / `index_timeout_s` / `max_token_budget` 默认值按实测 P95 校准并回填 3.3 | 已完成（2026-11-22，真实 token、用户预授权：建树新增 544,517 token / ~9 min（修订版 107 节点 4.2 min、`05_Agent.md` 112 节点 4.8 min，merge 档；原大文件与 PDF 缓存复用零新增）；30 题四路复测 **tree/md hit 0.64 / hitEq 0.96 / sRec 0.607 / prec 0.427 / locatable 1.00 / token 中位 8.4K**（旧管线 0.28 / — / — / — / 0.68 / 69.6K），tree/pdf 1.00 全指标，25+5 题零失败、零审核拦截；PDF 补题用户裁决不补（维持 5 题）；`timeout_s` 60→240、`index_timeout_s` 900→1800、`max_token_budget` 维持 32K 回填 3.3；落盘 10.8） |
| PI0.7 | D9 终裁、过程发现处置与收尾 | D4 / D9 终裁回填（达标则 PI1 开工；不达标则本规格止于预研并建派生台账）；10.3 ①–⑤ 按 10.4 方案表落地（派生台账条目、上游 issue 草稿、单测锁定项各自归位）；收尾清理逐项经用户确认：`/tmp/pi0_poc_ws`、`data/db/pageindex/poc/workspace/`、默认库补摄取的 MD 去留、`markitdown[pdf]` 是否进 `requirements.txt`、旧 MD 树缓存（3 份）删除时机 | 已完成（2026-11-23：D9 终裁**达标，PI1 开工**（门槛五条见 D9 行）；D4 终裁确认 `openai/glm-5.3-flash`；flash 混合方案终裁维持 submit 基线（台账可选优化，见 10.7 ③）；10.3 ①–⑤ 处置归位派生台账 [pageindex-followups_backlog.md](../backlog/pageindex-followups_backlog.md)（决议与已知限制 5 项、上游 issue 草稿 3 份、产品缺陷候选 2 项；#12 空文本防护与 images 空 list 校验入候选池，#13/#14 issue 草稿，#15 记录决议，#16 留 PI1 单测 + PI2 文档）；收尾清理经用户逐项确认执行：`/tmp/pi0_poc_ws` 与 `poc/workspace/` 已删、旧 MD 树缓存 3 份已删、默认库保留 `05_Agent.md`、`markitdown[pdf]` 维持不进 `requirements.txt`） |
| PI1 | sidecar 模块 `heading_index` / `md_render` / `tree_store` / `tree_loop`（0.4 起 `md_prep` 删除） | 第 4 章 PI1 用例全过；模块 import 不引入 `pageindex` / `litellm`；`md_render` 单测经注入 runner，不调用真实 pandoc / Chromium | 已完成（2026-11-23：四模块落地 `scripts/pageindex/`，`tree_loop` 自 PI0.6 实测形态的 `TreeSearcher` 忠实移植（判足够闭环 / 预算前置 / 预筛 / 并行 / 去重 / 硬截断，API 改为注入 `complete` / `read_node` / `node_span` 的纯模块）；单测 58 例全绿（`tests/unit/test_pageindex_*.py` 四文件，覆盖 §4 全部 PI1 用例，断言 `sys.modules` 无 pageindex / litellm）；`pytest tests` 全量回归除 2 个本地环境既有失败（`test_loader_pdf_contract` 依赖「本地无 markitdown」前提，CI 干净环境通过，与本次新增文件无关）外无回退；`check_redlines.py` 0 违规；实现偏离一处：`map_headings_to_pages` 由原型的「逐页 joined 窗口混合匹配」改为「全页精确优先、跨界窗口兜底」两段式——原型会把落在第 N 页的标题系统性映射到 N−1（plan 文件 Deviations 已记）；计划文件 `.agents/plans/PI1.md`） |
| PI2 | sidecar 服务、`pi_adapter`、`start_sidecar.sh`、红线规则 | 本机启动后 `/status` 200、无 token 401；对 1 篇 MD（经派生 PDF 建树，D3）+ 1 篇 PDF 建树并检索返回带行号 / 页码证据，MD 证据 `source_path` 为原 MD 路径；`setup_venv.sh` 检测到 pandoc 与系统 Chrome/Chromium（playwright 仅作可选回退，D11）；`python scripts/check_redlines.py` 0 违规（白名单仅 `pi_adapter.py` / `poc_compare.py`）且新规则正反样例单测通过；契约 fixture 替换为真实响应 | 未开始 |
| PI3 | `libs/tree_search/` + `tree_search` 配置节 | MockTransport / stub 集成测试全过；默认配置下 `load_settings()` 结果除新增节外不变；非法配置 fail-fast 单测覆盖 3.3 每条校验 | 未开始 |
| PI4 | `scripts/pageindex_sync.py` | 离线单测覆盖四类动作与 `--dry-run`；对真实 sidecar 跑两次，第二次新增 / 重建计数为 0 | 未开始 |
| PI5 | Chat 后端 `longdoc` 模式 | 第 4 章 PI5 用例全过；既有测试语义清单逐条有测试锁定或复跑通过（capabilities 精确断言与 `RETRIEVAL_MODES` 常量断言按第 4 章同步，其余既有断言不改）；`start_api.py` 对 `tree_search` 预建白名单根 | 未开始 |
| PI6 | Chat 前端 Longdoc 选项、配色、引用展示 | `npm run typecheck && npm run lint && npm test` 全绿；rose 选中态 light / dark 实测对比度 ≥ 4.5:1 并回填；Playwright longdoc 用例通过 | 未开始 |
| PI7 | 门控真实冒烟 + 文档回写 | `RAG_PAGEINDEX_INTEGRATION=1` 冒烟通过并记录延迟与 token；USAGE_GUIDE 新章节与 README 回写完成（第 7 章）；全量禁网 pytest + 红线全绿 | 未开始 |

> 进度备注：交付后在各任务下回填状态与关键实测数据；全部收口后本规格移入 `docs/frozen/` 并更新 MOC.md。

## 6. 验收清单

- [x] PI0 立项门通过（2026-11-23 D9 终裁达标：门槛见 D9 行，对照数据 10.8；PI1 开工）。
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
| R3 | 超大文档与超大节点：1.7 MB MD 转出的派生 PDF 预计数百页，建树成本与单节点原文都可能撑爆上下文（0.4 改写，原 `md_to_tree` thinning 应对不再适用） | PI0.4a/PI0.4b 单篇试转先测页数与成本；`max_evidence_chars` + 优先下钻子节点 + 截断标记；MD 节点原文按回定位行区间读取，可精确裁剪 |
| R4 | 一次树检索超过 `timeout_s` | `max_loops` / `max_token_budget` 截停（0.4 起每次调用前检查）；超时走既有失败分支；PI0.3 旧管线 md P95 145s；**PI0.6 新管线实测 md P50 65.2s / P95 137.4s / max 198.6s，`timeout_s` 默认 60 → 240 已回填 3.3** |
| R5 | 建树成本：1.7 MB Markdown 摘要 token 量可观；且上游 `submit_document` 默认 `summary_concurrency=128` 易触发低 RPM 账户 429 或单节点审核 400 崩溃 | PI0.3 单列建树成本（旧管线 61 分钟）；PI0.4b / PI0.6 实测新管线真实 `usage` token；显式传低 `summary_concurrency`（3–5）并保留 400 审核降级保护；同步命令支持 `--collection` 分批；`file_hash` 未变不重建 |
| R6 | sidecar 内并发：上游 DocStore 锁 + 单进程同步端点，不适合并发 | 首期单用户本机场景接受串行；需要并发时另议 |
| R7 | ~~#609 上游修复后预处理与上游行为重叠~~ | **作废（0.4）**：D3 改为 MD 转 PDF，不再调用 `md_to_tree`，`md_prep` 删除，#609 不再影响本规格 |
| R8 | 转换后结构还原失真：标题层级在 PDF 中不明显、无 Bookmarks 时超 10 页会被 Flash 拒绝、上游插入 `Preface`/`(intro)` 合成节点或 `optimize="full"` 改写标题导致回定位失败（0.4 新增，0.5 细化） | 打印 CSS 固定标题层级字号、代码块 `pre-wrap`，优先写入 PDF Embedded Bookmarks（D11）；`heading_index` 规则化映射 `Preface`/`(intro)`；PI0.4a 零 token 先验结构与回定位率，PI0.4b 对比 `optimize="merge"` 与 `"full"`；运行期 telemetry 计 `unlocated`，失败退化为只给 `section` |
| R9 | 系统工具依赖与 Chromium 运行稳定性：pandoc 与 Chromium 非 pip 依赖，版本漂移或缺失；macOS 系统 Chrome 共享默认 profile 易抢锁挂起并输出噪声（0.4 新增，0.5 细化） | 浏览器按 `PAGEINDEX_CHROMIUM` > 系统 Chrome/Chromium > 可选 playwright 探测；强制 `--headless=new` / `--disable-gpu` / 独立临时 `--user-data-dir` 与超时；`setup_venv.sh` 检测版本、`start_sidecar.sh` 启动日志打印版本；缺失时 `/index` 对 MD 返回 `render:` 前缀错误，原生 PDF 不受影响 |
| R10 | 派生 PDF 泄露进其他检索路，造成重复证据或越权路径（0.4 新增） | 只存 `<PAGEINDEX_STORAGE>/derived/`（`data/db` 下，被 `denied_substrings` 隔离），不进 `data/documents/` / `data/uploads/`；证据路径回映射由 PI1 单测锁定（D12），验收清单单列一条 |

## 10. 附录：PI0 四路对照基线（2026-11-22）

### 10.1 对照表（30 题 = MD 25 + PDF 5，用户逐条确认定稿）

| 路由 / 语料 | n | ok | hit | locatable | P50 | P95 | token/题 |
|---|---|---|---|---|---|---|---|
| tree / md | 25 | 25 | 0.28 | 0.68 | 52.9s | 145.4s | 59,870 |
| hybrid / md | 25 | 25 | 0.40 | 0.52* | 0.77s | 1.43s | 0 |
| agentic_deep / md | 25 | 25 | 0.08 | 0.32 | 6.1s | 10.5s | 32,612 |
| plan_a / md | 25 | 25 | 0.40 | 0.52 | 0.78s | 1.14s | 0 |
| tree / pdf | 5 | 5 | 1.00 | 1.00 | 10.0s | 32.0s | 2,080 |
| hybrid / pdf | 5 | 5 | 1.00 | 1.00* | 0.58s | 0.73s | 0 |
| agentic_deep / pdf | 5 | 5（有效 0） | — | — | — | — | —（上游缺陷，5 题均返回空证据，见 10.3 ③，该路判无效） |
| plan_a / pdf | 5 | 5 | 1.00 | 1.00 | 0.61s | 0.79s | 0 |

一次性建树成本（缓存复用后为零增量；PI0.3 脚手架未采集 token，均记为 0，PI0.5 修复）：1.7 MB 篇 1,881 节点 61 分钟 / 470 KB 篇 253 节点 12 分钟 / 351 KB 篇 317 节点 10 分钟 / PDF 222 页 177 节点 33 秒。

**解读与基线局限（0.5 复核补注）**：
- MD 上树检索原型 hit 未超 hybrid（0.28 vs 0.40），locatable 为优势（0.68 vs 0.52），代价是 P50 53 秒与单题 ~6 万 token（R3 风险实测坐实：大树逐层下钻 token 消耗巨大，1881 节点整树大纲单次调用即 ~30 万 token，不可行，故原型采用逐层下钻）；PDF 页定位类问题上 hybrid 已能答好，树检索无增量；agentic_deep 全面最弱。R4 的 P95 实测：树检索 145s（md），`timeout_s` 默认值改在 PI0.6 按新管线实测后校准。
- **0.5 代码复核发现的 PI0.3 脚手架局限（均由 PI0.5 修正）**：① 表中带 `*` 的 `hybrid` `locatable` 实为误把 `plan_a` 离线 `annotate_*` 算给了 `hybrid`（`poc_main_routes.py:131, 265-268` 两路同路径），真实产品口径下 `hybrid` 的 `locatable` 为 `0.00`；② `poc_compare.py:765` 漏传 `get_pdf_page=get_pdf_page`，导致 `tree_pdf.jsonl` 全 5 题证据 `text` 长度恒为 0（只测了选节点、未测读页）；③ `overlap()` 对 9 道 `synthesize` 多目标题按 `any(...)` 判 hit（碰到 1/4 小节即通过）；④ `build_pdf_tree` 硬编码 `build_tokens = 0` 未采集 `submit_document` 的 `usage`；⑤ 旧 `tree/md` 未落盘明细 JSONL（仅 `tree_pdf.jsonl` 存在），故旧管线仅保留本表汇总作对照。

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

### 10.4 方向变更：MD 转 PDF 后建树（0.4 裁决，0.5 复核修订）

**裁决**：废弃 MD 建树管线（上游 `md_to_tree`，以及原型中的 MD 树缓存；后者只作新旧对照，保留到 PI0.7 收尾）；MD 转为派生 PDF 后与原生 PDF 同走 `submit_document` / DocStore（D3、D11、D12）。执行路径调整为 **PI0.4a（零 token 试转验结构）→ PI0.5（零 token 修口径与原型）→ PI0.4b（单篇花 token 建树实测）→ PI0.6（新管线复测）→ PI0.7（D9 终裁与收尾）**，PI1 改为 `heading_index` / `md_render` / `tree_store` / `tree_loop` 四个模块。

**依据**（PI0.3 明细按目标文件重拆，hit 为严格同文件口径）：

| 目标文件（题数） | tree | hybrid | agentic_deep | plan_a |
|---|---|---|---|---|
| 1.7 MB 原大文件（12） | 4/12 | 0/12 | 2/12 | 0/12 |
| 独立修订版 `应用开发项目与开源架构解析.md`（8） | 3/8 | 8/8 | 0/8 | 8/8 |
| 纯子集拆分件 `13_面试实战.md`（5） | 0/5 | 2/5 | 0/5 | 2/5 |

- **0.5 语料包含关系核实**：`13_面试实战.md`（Q21–Q25，9 月 11 日）100% 为 1.7 MB 原大文件（9 月 4 日）的纯子集；但 `应用开发项目与开源架构解析.md`（Q13–Q20，11 月 16 日）是后续大幅增补与重排后的独立修订版——Q13（`3 模块化RAG项目`）与 Q20（`1.2 2026年 Agent 新形态` / `1.3 RSI 进展`）在原大文件中**完全不存在**（0 行命中），Q14–Q19 章节编号已重写且内容有大量更新。因此不能一刀切「剔除全部拆分件」：只剔除纯子集 `13_面试实战.md`，保留 `应用开发项目与开源架构解析.md`（并可视情况再纳 1–2 篇独立长 MD），既保证全部 25 道 MD 题有真解，又保证 PI0.6 真测多文档路由与并行。
- hybrid 在原大文件上 0/12 不等于找不到内容：这 12 题的证据都落在 `章节拆分/` 的拆分件上，被同文件归属判定判为 miss。口径问题与检索能力问题叠在一起，需要 PI0.5 用双口径拆开。
- MD 树粒度过细：`md_to_tree` 把每个 `#` 标题与整行粗体都建成节点，1.7 MB 篇 1,881 节点、建树 61 分钟；原生 PDF 222 页只有 177 节点、33 秒。
- 检索成本失控：tree/md 单题 token 中位 69.6K、最大 85.5K，超出 32K 预算（预算只在每轮开头检查）；8 题没有目标文件内的证据（原大文件 2、另两篇 6）；tree/pdf 单题 2,080。
- 机制核实（0.5）：`pageindex 0.7.4` Flash 模式过滤印刷目录页（`mark_toc_and_boilerplate`），优先消费 **PDF 内嵌 Bookmarks（Outline）**（`2023-annual-report.pdf` 实测含 50 条 Bookmarks），其次走版面样式检测，且支持 `page_index_flash(pdf, summary=False, optimize=False)` 零 LLM 验结构；D11 据此由「印刷目录对比」改为「PDF Bookmarks / Outline 写入对比」，先在 PI0.4a 零 token 验结构与回定位，再进 PI0.4b 花 token 测摘要成本。

**各问题对应方案**：

| # | 问题（出处） | 方案 | 落地任务 |
|---|---|---|---|
| 1 | MD 树粒度过细、建树慢（10.1 建树成本） | D3 改为 MD 转 PDF 建树，转换链按 D11；先经 PI0.4a 零 token 试转验结构，再经 PI0.4b 花 token 测单篇摘要成本 | PI0.4a / PI0.4b |
| 2 | 转换后的结构还原与回定位质量未知，且上游会插入 `Preface` / `(intro)` 合成节点（R8） | 对比三种 PDF Outline 注入方案（放弃会被 Flash 过滤的印刷目录）；零 LLM `page_index_flash` 抽样核对结构与 `heading_index` 回定位率；PI0.4b 对比 `optimize="merge"` 与 `"full"` | PI0.4a / PI0.4b |
| 3 | 派生 PDF 路径含 `data/db`，流到主 venv 会被 PathGuard 剔除，也对不上 collection 定界（10.3 ⑤） | D12：证据 `source_path` 回写原 MD，映射表另记 `derived_pdf`；单测锁定 | PI0.5 原型 / PI1 正式 |
| 4 | 派生 PDF 页码对用户无意义；原 MD 变更可能致行区间漂移 | MD 引用显示「章节 · 行号」（`heading_index` 回定位），页码只用于原生 PDF（D8）；读原 MD 前校验哈希或边界，漂移回退 `get_page_content`（D12） | PI0.5 / PI1 / PI5 / PI6 |
| 5 | token 预算超支（中位 69.6K、最大 85.5K，预算 32K）与原型缺「判是否足够」/`max_nodes` 硬截断 | 每次调用前检查预算、单层节点预筛、补齐「读原文 → 判是否足够」闭环与 `max_nodes` 截断 | PI0.5 原型 / PI1 `tree_loop` |
| 6 | 树检索慢（md P50 53s、P95 145s） | 多文档并行、提前停止；`timeout_s` 按新管线 P95 校准 | PI0.5 / PI0.6 |
| 7 | `13_面试实战.md` 与原文件重复，但 `应用开发项目与开源架构解析.md` 含原文件缺失章节（Q13/Q20） | 严格同文件 / 内容等价双口径并报；tree 候选仅剔除纯子集 `13_面试实战.md`，保留独立修订版 `应用开发项目与开源架构解析.md`（可选增纳独立长 MD 保多文档路由） | PI0.5 / PI0.6 |
| 8 | 各路返回条数不同、hit 口径不一；`hybrid` 误计离线 `locatable`；`synthesize` 题按 `any` 过宽 | 区分 `hybrid`（产品口径 `locatable=0`）与 `plan_a`（离线近似）；`locate` / `synthesize` 分列并增报 section recall；统一前 k 条判 hit 与证据精度 | PI0.5 |
| 9 | PDF 只有 5 题且 PI0-Q28 年份笔误（`2023 年 3 月` 实为 `2024 年 3 月`）；`tree/pdf` 漏传 `get_pdf_page` 致正文全空 | 修复漏传 `get_pdf_page` 并加非空断言；征得用户确认修正 PI0-Q28 题干年份；是否补 PDF 跨节归纳题由用户决定（逐条确认） | PI0.5 / PI0.6 |
| 10 | `tree_telemetry` 部分重跑被覆盖；上游 client 写到仓库根 `.pageindex/`；`build_pdf_tree` 未采 `usage` token 且缺并发限流/审核降级 | 按路由合并 telemetry；显式传 `storage_path=PAGEINDEX_STORAGE` 并清根目录 `.pageindex/`；采集 `submit_document` 的 `usage["total_tokens"]`、传 `summary_concurrency` 并保留 400 审核降级 | PI0.5 / PI2 |
| 11 | D7 否决 `agent_tools()` 的理由随 D3 失效 | 复核其只读工具能否作 `read_node` / 结构读取实现 | PI0.5 |
| 12 | 10.3 ① PDF 空 chunk 流到 embed 阶段才崩 | 与方向变更无关：派生台账（摄取链路空文本防护 + 单测） | PI0.7 |
| 13 | 10.3 ② sirchmunk 401 报成 500 | 上游 issue 草稿 | PI0.7 |
| 14 | 10.3 ③ sirchmunk DEEP 对 PDF 证据恒空 | 上游 issue 草稿；agentic_deep/pdf 在 PI0.6 继续判无效 | PI0.6 / PI0.7 |
| 15 | 10.3 ④ 产品链路无 PDF 页码 metadata | longdoc 页码取自树节点；主摄取链路不补（Out of Scope 不变）；hybrid 的 PDF 页码引用另立提案 | PI0.7 记录 |
| 16 | 10.3 ⑤ `data/db` 是 denied substring | 从使用约束升级为设计约束：派生 PDF 必须放在 `data/db/` 下，且绝不出现在证据路径（D12） | PI1 单测 + PI2 运维文档 |
| 17 | 临时工作区与补摄取残留 | 收尾清理，逐项经用户确认 | PI0.7（已完成 2026-11-23：`/tmp/pi0_poc_ws` 与 `poc/workspace/` 已删、旧 MD 树缓存 3 份已删、默认库保留 `05_Agent.md`、`markitdown[pdf]` 维持不进 `requirements.txt`） |

**待用户确认项**（到对应任务时再问）：

1. ~~PI0.4a 完成后：节点标题回定位成功率的可接受线、PDF Outline 写入方案的默认选项（纯检测 / Chromium Outline / `pypdf` 注入 Bookmarks），以及 PI0.4b（单篇花真实 token 建树，含 `optimize="merge"` vs `"full"` 对比）的执行时机。~~（已裁决：~95% 可接受、默认 `pypdf` 注入、PI0.4b 预授权，见 10.5）
2. PI0.5：修正 PI0-Q28 题干年份笔误（`2023 年 3 月` → `2024 年 3 月`），以及多文档 MD 候选集是否除「1.7 MB 原大文件 + `应用开发项目与开源架构解析.md`」外再纳入 1–2 篇独立长 MD。
3. ~~PI0.6：是否补充 PDF 跨节归纳题，以及复测（花真实 token）的执行时机。~~（已裁决并执行：不补题、维持 5 题记为已知限制，PI0.6 复测完成，见 10.8）
4. ~~PI0.7：D9 门槛数值与收尾清理清单。~~（已终裁：达标、门槛五条见 D9 行；清理清单逐项确认执行完毕，见任务表 PI0.7 行与 10.4 #17）

### 10.5 PI0.4a 实测：MD → 派生 PDF 三种 Outline 方案（2026-11-22，零 LLM）

实验脚手架 `data/db/pageindex/poc/poc_outline.py`（一次性预研件，不入 git、不进红线扫描范围；正式能力由 PI1 `md_render.py` / `heading_index.py` 承接）。输入：1.7 MB 原大文件；pandoc 3.11（GFM → HTML，打印 CSS：标题层级字号、`pre-wrap` 代码块、不强加分页）1.0 s 转 1.94 MB HTML；headless Chrome（系统 Chrome 157，`--headless=new` / `--disable-gpu` / 独立临时 `--user-data-dir`，输入输出经 `/tmp` 中转以绕开 macOS TCC 对 `~/Desktop` 的限制）`--print-to-pdf` 8.8 s 出 **306 页 / 20.5 MB** PDF。MD 解析 1,037 个标题（行首口径 1,045；差异为 front matter 误判与空标题，见下），首个标题前有正文（`Preface` 合成节点成立）。

| 方案 | Bookmarks | flash `toc_source` | 节点数 | 树深 | flash 耗时 | `heading_index` 回定位 |
|---|---|---|---|---|---|---|
| v1 纯 CSS 字号检测（无 Outline） | 0 | `detected` | 417 | 4 | 14.1s | 82.5%（344/417） |
| v2 Chromium CDP `generateDocumentOutline` | 1,033 | `bookmarks` | 1,309 | 7 | 16.1s | 95.6%（1252/1309） |
| v3 原 MD 标题树经 `pypdf` 注入 Bookmarks | 1,028 | `bookmarks` | 1,375 | 7 | 16.1s | 95.4%（1312/1375） |

**结论与裁决依据**：

- **v1 否决**：纯版面检测把整行粗体、front matter 混进标题（如「1. 提供可写简历的实战项目」「问题说明」多为粗体行），节点数偏少且层级浅（深 4），回定位 82.5% 不可靠。
- **v2 / v3 均触发 `toc_source=bookmarks`**，Flash 以 Bookmarks 为骨架并把版面检测出的缺欠节点嫁接回来（故节点数 1,309 / 1,375 高于各自 Bookmarks 数 1,033 / 1,028），树深 7、节点粒度与页数同量级，对比旧 `md_to_tree` 管线（1,881 节点、61 分钟建树）结构性改善明确；零 LLM flash 单次仅 14–16 s。
- **建议默认 v3（`pypdf` 注入）**：Bookmarks 与原 MD 标题层级一一对应（1,037 − 9 页映射 miss = 1,028），全离线确定性、不依赖 CDP websocket 与 Chrome 版本行为（CDP 需 ≥117 且 `generateDocumentOutline` 语义随版本变动）；转换成本 pandoc ~1 s + render ~9 s + 页映射（pypdfium2 全文提取 + 顺序匹配）可忽略。v2 保留为对照结论，不实现。
- **未回定位两类成因（均可修，PI1 `heading_index` 落地）**：① **CJK 部件形抽取伪影**——PDF 文本抽取把 见→`⻅`、门→`⻔`、风→`⻛`、长→`⻓` 等映射为 CJK 部件 Supplement 码位，25 条失败样例中占 8–11 条；归一化映射补一部首形→正字表即可回收，预计回定位率显著高于 95%。② **整行粗体伪标题**——Flash 嫁接的版面检测节点标题在原 MD 中不是 `#` 标题（`md_to_tree` 当年按整行粗体建树同源），本就无标题可回定位；按 D12④ 回退 `get_page_content` 页级读取，或 PI1 可选把整行粗体纳入 `heading_index` 索引域。
- **页映射 9 条 miss**：front matter（`modified_date:` 行被 `---` 误判为 Setext 下划线）、`**3.**** ****网页RAG项目选型**` 类强调符粘连标题、3 条空标题；`heading_index` 需跳过 front matter 与空标题（PI1 单测覆盖），页映射前同样先归一化强调符。
- 实测明细：`outline_results.json` 与三份 `{variant}_tree.json` 落 `data/db/pageindex/poc/`（不入 git）。

**PI0.4a 出口裁决（2026-11-22 用户拍板）**：① 回定位可接受线 ~95%（现值 95.4–95.6%，含可修伪影）；② 默认 Outline 方案 = v3 `pypdf` 注入（v2 CDP 保留为对照结论，不实现）；③ PI0.4b 预授权——PI0.5 完成后直接执行单篇花真实 token 建树（含 `optimize="merge"` vs `"full"` 对比），届时不再另行询问。

### 10.6 PI0.5：0.5 口径修正重算（2026-11-22，零 LLM）

对 PI0.3 已落盘的路由明细（`main_*_{md,pdf}.jsonl`、`tree_pdf.jsonl`）按 0.5 口径离线重算（`poc_compare.py --rescore-only`，不跑路由、不花 token）；`tree/md` 旧管线无明细（10.1 ⑤），新管线 tree 路在 PI0.4b / PI0.6 以真实建树复测。

| route/source | n | hit | hitEq | sRec | prec | locat | P50(ms) | P95(ms) | token/题 |
|---|---|---|---|---|---|---|---|---|---|
| hybrid / md | 25 | 0.32 | 1.00 | 0.30 | 0.128 | **0.00** | 768 | 1433 | 0 |
| agentic_deep / md | 25 | 0.08 | 0.08 | 0.08 | 0.08 | **0.00** | 6064 | 10545 | 32,612 |
| plan_a / md | 25 | 0.32 | 1.00 | 0.30 | 0.128 | 1.00 | 779 | 1135 | 0 |
| tree / pdf（旧管线） | 5 | 1.00 | 1.00 | 1.00 | 0.75 | 1.00 | 9979 | 31959 | 2,080 |
| hybrid / pdf | 5 | 1.00 | 1.00 | 0.90 | 0.42 | **0.00** | 584 | 732 | 0 |
| agentic_deep / pdf（无效路，10.3 ③） | 5 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1188 | 2217 | 1,014 |
| plan_a / pdf | 5 | 1.00 | 1.00 | 0.90 | 0.44 | 1.00 | 606 | 790 | 0 |

**新旧口径对照**（10.1 → 10.6）：

- **hybrid `locatable` 0.52\* → 0.00**（md 与 pdf 同）：误计的离线 `annotate_*` 已剥离，生产口径下 hybrid 输出无章节 / 行号 / 页码。`agentic_deep` 的 `locatable` 同理由 0.32 → 0.00（0.5 修正清单只点名 hybrid，但同一生产口径对 agentic_deep 一并适用——其输出同样无章节 / 行号 / 页码；如需保留离线口径数字以 10.1 为准）。
- **hit 统一前 k=10**（与 hybrid `top_k_final` 对齐）：hybrid/md 0.40 → 0.32、plan_a/md 0.40 → 0.32（top-k 截取后 2 题的同文件命中跌出前 10）。
- **内容等价口径（hitEq）暴露语料重复效应**：hybrid/md 与 plan_a/md 的 hitEq = 1.00——前 10 条证据里几乎总有拆分件内容等价命中，印证「严格同文件」必须作主口径、hitEq 只作备注。
- **section recall（synthesize 多目标覆盖率）**：hybrid/md 0.30 / agentic_deep 0.08——多小节归纳题上各路都只覆盖到零头；tree 路新管线该项是 PI0.6 的关键观察点。
- **证据精度**：hybrid/md 0.128（25 题前 10 条证据中仅 ~1.3 条落在目标范围）——hybrid 召回多而散；tree/pdf 0.75 明显更准。
- tree/pdf（旧管线原型）各指标在新口径下保持 1.00 hit / 1.00 locatable，precision 0.75；其 `get_pdf_page` 漏传缺陷（证据 text 全空）已由 PI0.5 修复并在 fake 冒烟中断言非空。
- 落盘：`data/db/pageindex/poc/poc_results_rescored.json`（含 metrics_note 口径说明）。

### 10.7 PI0.4b 实测：单篇派生 PDF 真实建树，merge vs full（2026-11-22，真实 token，用户预授权执行）

对 1.7 MB 原大文件的派生 PDF（306 页，v3 Bookmarks 方案）调用 `PageIndexClient.submit_document` 建树两次：`optimize="merge"` 与 `"full"`（`summary_concurrency=3`、显式 `storage_path=data/db/pageindex`、采集 `usage["total_tokens"]`）。回定位率用 `heading_index` 原型（含部件形归一化）离线复核，零 token。

| 档位 | 节点数 | 树深 | 回定位 | 精确标题节点 | 带摘要节点 | token | 耗时 | doc_id |
|---|---|---|---|---|---|---|---|---|
| `merge` | 424 | 6 | 88.0%（373/424） | 370/424 | 424/424 | 1,106,112 | 16.2 min | `pi-a93db52f…` |
| `full` | 424 | 6 | 88.0%（373/424） | 370/424 | 424/424 | 1,116,199 | 17.3 min | `pi-b26d651f…` |

**发现与结论**：

1. **merge vs full 结构完全一致**（同节点数 / 树深 / 同一批可回定位节点），full 的 expand 未净增节点；merge 省 0.9% token 与 1.1 分钟。**裁决：`merge` 为默认档**（`tree_search` 侧与 POC `--submit-optimize` 默认值对齐）。
2. **`merge` 不改写标题的假设不成立**：51 个（~12%）节点标题为 LLM 重写（两次运行对同一批节点给出不同措辞，如「大模型面试笔记内容说明与学习路线指南」vs「大模型求职笔记介绍与学习路线规划」），无法精确回定位原 MD 标题；`heading_index` 需模糊匹配兜底（归一化 + 包含关系），失败退化为 `section` + 页级读取（D12④）。
3. **对照 flash 零 LLM 树（10.5）**：submit 树 424 节点 / 回定位 88.0% vs flash 树 1,375 节点 / 95.4% / 零 token——submit 的价值在节点摘要（选节点质量）与更粗粒度（检索循环更省），flash 的价值在零成本与标题保真。**终裁（2026-11-23 用户拍板）：维持 submit 全量摘要为 PI1 基线**；「flash 结构 + 按需摘要」混合方案降为 [pageindex-followups 台账](../backlog/pageindex-followups_backlog.md)可选优化项（重开需新证据）。
4. **成本量级（R5 坐实）**：单篇 306 页摘要 ≈ 1.1M token / ~17 分钟（并发 3）；`index_timeout_s` 默认 900s 不够，PI0.6 复测后按实测校准（>1200s）。
5. **上游去重缺口（PI1 `tree_store` 需处理）**：同一派生 PDF 二次提交时上游存为 `<hash>_1.pdf` 并生成第二个 doc（两次提交得到两个 doc_id），未按内容去重；D5 对账同步以 `source_path → file_hash` 为键自行幂等，避免重复提交。
6. 明细：`data/db/pageindex/poc/cache/derived_*_submit_{merge,full}.json`（树 + usage）。

**待用户确认项（PI0.4b 出口）**：~~① 建树成本 / 质量是否可接受；② merge 设为默认档；③ PI0.6 执行时机。~~（均已裁决并执行：①② 终裁回填 D11，③ PI0.6 已完成，见 10.8）

### 10.8 PI0.6 实测：新管线全量建树与四路复测（2026-11-22，真实 token，用户预授权执行）

建树覆盖 3 篇 MD 候选集 + 原生 PDF（merge 档，`--submit-optimize merge`，`summary_concurrency=3`）：

| 文档 | pipeline | 节点数 | token | 耗时 |
|---|---|---|---|---|
| 1.7 MB 原大文件（306 页派生 PDF） | derived-pdf/submit/merge | 424 | 1,106,112（PI0.4b 缓存复用，零新增） | 16.2 min |
| 独立修订版 `应用开发项目与开源架构解析.md`（470 KB） | 同上 | 107 | 328,853 | 4.2 min |
| `agent八股/05_Agent.md`（315 KB） | 同上 | 112 | 215,664 | 4.8 min |
| 原生 PDF `2023-annual-report.pdf`（222 页） | docstore | 177 | 0（缓存复用） | 33 s |

- **本任务真实新增花费 544,517 token / ~9 分钟**（低于 0.5–1M 预估）；库内树资产合计 820 节点 / 1,650,629 token / ~25 min。`--submit-optimize` 默认值随 PI0.4b 终裁改为 `merge`（`full` 仅对照复现时显式传入）。

30 题四路复测（0.5 口径，tree 路为新管线；PI0.5 重算基线见 10.6）：

| route/source | n | hit | hitEq | sRec | prec | locat | P50 | P95 | token/题 |
|---|---|---|---|---|---|---|---|---|---|
| **tree / md（新管线）** | 25 | **0.64** | 0.96 | **0.607** | **0.427** | **1.00** | 65.2s | 137.4s | 8,906 |
| hybrid / md | 25 | 0.32 | 1.00 | 0.30 | 0.124 | 0.00 | 0.92s | 1.14s | 0 |
| agentic_deep / md | 25 | 0.04 | 0.04 | 0.04 | 0.04 | 0.00 | 6.2s | 13.1s | 34,479 |
| plan_a / md | 25 | 0.32 | 1.00 | 0.30 | 0.124 | 1.00 | 0.83s | 0.98s | 0 |
| **tree / pdf（新管线）** | 5 | 1.00 | 1.00 | 1.00 | 0.76 | 1.00 | 25.7s | 97.7s | 3,691 |
| hybrid / pdf | 5 | 1.00 | 1.00 | 0.90 | 0.42 | 0.00 | 0.52s | 0.75s | 0 |
| agentic_deep / pdf（无效路，10.3 ③ 维持） | 5 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1.26s | 1.68s | 1,010 |
| plan_a / pdf | 5 | 1.00 | 1.00 | 0.90 | 0.44 | 1.00 | 0.57s | 0.83s | 0 |

**关键观察**：

1. **tree/md 相对旧管线全面改善**（10.1 旧管线：hit 0.28 / locatable 0.68 / token 中位 69.6K / P50 53s / P95 145s）：hit 0.28 → 0.64（2.3×）、token 中位 69.6K → 8.4K（≈8×）、locatable 1.00；P50 65s 较旧管线 53s 略升（多文档路由 + 预筛开销），换来命中率与 token 双改善。25/25 题零失败、零审核拦截（`moderation_skips=0`）。
2. **题型拆分**：locate 题 hit 0.76（13/17）、synthesize 题 hit 0.38（3/8）；整体 section recall 0.607 显著高于 hybrid 0.30——多小节归纳是 tree 路的核心增量（PI0.5 判断兑现）。synthesize 仍是主要失分点（8 题中 5 题 hit 落空）。
3. **token 余量健康**：单题 max 21,285 < `max_token_budget` 32,000（1.5× 余量），预算不再成为瓶颈；`max_nodes`/`max_loops` 维持（telemetry `max_nodes_capped` 触发 13 次仍达 0.64，调大只增 token）。
4. **耗时校准（回填 3.3）**：`timeout_s` 60 → **240**（tree 实测 max 198.6s / P95 137.4s，1.2× max 余量）；`index_timeout_s` 900 → **1800**（最慢单篇 merge 972s @306 页、并发 3，覆盖 ~550 页 + 余量）；`max_token_budget` 维持 32,000。
5. **agentic_deep**：md hit 0.04（PI0.5 重算 0.08，同量级最弱）、pdf 仍恒 0；search 之外的进程内答案合成有墙钟离群（日志时间戳 Q13 ~8 min、Q15 ~20 min，未计入上表 search 耗时口径——该口径取 sidecar 返回的 `resp.elapsed_ms`）。R4 风险维持。
6. **基线口径不变**：1.7 MB 原大文件不在默认摄取库（与 PI0.3 一致；本次摄取尝试在 store 阶段失败后完整回滚，bm25 `+0 replaced` 佐证此前不在库）。失败成因为 Chroma upsert 对 `images` 空 list 元数据的非空校验（产品链路缺陷，与 10.4 #12 空文本防护同族），列 PI0.7 派生台账候选。hybrid 双口径结论不变（hitEq 1.00 = 拆分件内容等价命中）。
7. **PDF 补题决策（用户 2026-11-22 拍板）**：不补，维持 5 题，区分度不足记为已知限制（tree/pdf 与 hybrid/plan_a 同为 1.00，对 D9 门槛无增量信息）。
8. **脚手架瑕疵（已修/已知）**：① `tree_telemetry` 的 `tree/md` 与 `tree/pdf` 两键均记累计值（明细拆算：md 169 调用 / 222,658 token，pdf 19 / 18,457，合计 188 / 241,115）；② 部分重跑路径重算 tree 明细时漏传 `target_text_norm`，把 hitEq 压到与严格口径相同——已修并离线修复报告（hitEq 恢复 0.96）。
9. 明细：`poc_results.json`（四路合并报告）、`poc_results_rescored.json`（重算）、`tree_{md,pdf}.jsonl` 与 `main_*_{md,pdf}.jsonl` 落 `data/db/pageindex/poc/`（不入 git）。

**D9 终裁材料（PI0.7 拍板用）**：

- **优势**：locatable 1.00（唯一在生产输出中给出「章节 · 行号」/ 页码引用的路）；section recall 0.607（hybrid/plan_a 0.30）；md hit 0.64（hybrid 0.32 的 2 倍）；证据精度 0.427（hybrid 0.124 的 3.4 倍）。
- **代价**：单题 P50 65s vs hybrid 0.9s（~70×）；单题 ~8.9K token vs 0；建树一次性 1.65M token / 4 篇、新增一篇 ~200–330K token / 4–17 min（页数线性）。
- **风险对照**：R3（检索 token 失控）大幅缓解（69.6K → 8.4K 中位）；R4（慢）维持坐实（P50 65s）；R5（建树成本）坐实且已按实测校准超时。
- **保留裁决项**：「flash 结构 + 按需摘要」混合方案（10.7 ③）在 PI0.7 与 D9 一并裁决。
