# 项目复习题库 — Modular RAG MCP Server

> 本题库按模块分章节，每道题标注难度（⭐基础 / ⭐⭐进阶 / ⭐⭐⭐深挖）和预估复习时长。
> 老师（Agent）根据用户当前复习章节顺序出题，不随机跳题，确保知识体系完整建立。
> 2026-11-16 全面修订：修正 9 处过时答案（BM25 持久化、MCP 工具数、ChunkID、Web UI 页面等），
> 新增第 10–14 章（Web UI / Sidecar / 日志 / 摄取健壮性 / 工程化），对齐 docs/frozen/ 各规格收口结论。

---

## 第 1 章：项目全景与设计理念

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 1-01 | 这个项目叫什么名字，整体要解决什么问题？ | ⭐ | 项目定位 | Modular RAG MCP Server，将私有文档知识库暴露为 MCP 标准接口，供 Copilot/Claude 等 AI 助手使用；Web UI（`scripts/start_api.py --serve-ui`，127.0.0.1:8600）是主界面 |
| 1-02 | RAG 是什么？和 Fine-tuning 相比最大的区别是什么？ | ⭐ | RAG 基础 | RAG=检索增强生成，实时检索外部知识，避免重新训练模型；更新成本低，幻觉可追溯 |
| 1-03 | 项目整体分哪几层架构？每层职责是什么？ | ⭐ | 分层设计 | Ingestion Pipeline（摄取）/ Query Engine（检索）+ Chat（`src/core/chat/`）/ MCP Server（协议暴露）/ API 层（`src/api/`，FastAPI）+ Web UI 前端（`frontend/`，Next 静态导出）/ 可观测性（trace + dashboard/services 共享服务层） |
| 1-04 | 为什么不用 LlamaIndex 或 LangChain 等现成框架，而要自研 Pipeline？ | ⭐⭐ | 架构决策 | 完全可控的可插拔架构，避免框架版本/依赖锁定，支持幂等设计、差量计算等自定义工程特性 |
| 1-05 | 项目有哪几类可插拔组件？每种举一个具体的可替换例子 | ⭐⭐ | 可插拔架构 | LLM(Azure→Ollama)、Embedding(OpenAI→BGE)、VectorStore(Chroma→Qdrant)、Splitter、Loader(PDF→Markdown/markitdown)、Reranker(CrossEncoder→LLM)、AgenticSearch(sirchmunk_http→fake) |
| 1-06 | 这个项目里有哪几类存储后端？各自存什么数据？ | ⭐⭐ | 数据存储 | Chroma(向量+metadata+chunk原文)、BM25 JSON 索引(`data/db/bm25/index.json`)、SQLite-图片索引(image_index.db)、SQLite-IngestionHistory v2(文件哈希/失败阶段)、SQLite-Chat 会话(chat_sessions.db)、SQLite-任务(tasks.db) |
| 1-07 | 用户从 Copilot 发问到拿到答案，整个链路经过哪些关键步骤？ | ⭐⭐ | 端到端流程 | Copilot → MCP Host → MCP Server(stdio) → QueryEngine → HybridSearch → Rerank → Response构建 → 返回带引用结果；Web UI 侧则是浏览器 → FastAPI(`/api/v1`) → services 层 → 同一 QueryEngine，Chat 走 WS 流式 |
| 1-08 | 项目的幂等性在哪里体现？为什么幂等很重要？ | ⭐⭐⭐ | 工程设计 | 文件Hash检查(早退)、ChunkID 确定性生成、Upsert语义写入；避免重复索引，支持重跑不污染数据。注意 IR 系列修正了幂等失效场景（见 9-03 与 13 章版本替换） |

---

## 第 2 章：数据摄取流水线（Ingestion Pipeline）

### 2A：Loader 阶段

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 2A-01 | Loader 阶段的职责是什么？输入输出分别是什么？ | ⭐ | Loader职责 | 输入：原始文件路径；输出：Document对象(text=Markdown + metadata含source_path/doc_type/page/images) |
| 2A-02 | 为什么 Loader 输出的格式选择了 Markdown？ | ⭐⭐ | 技术选型 | 方便配合 RecursiveCharacterTextSplitter 的 Markdown Separators，结构感知切分质量更高 |
| 2A-03 | 前置去重（File Integrity Check）是怎么工作的？用了什么算法？ | ⭐⭐ | 幂等设计 | 计算文件 SHA256 哈希 → 查 SQLite 历史表（v2 按 source_path+collection 判定）→ 已 success 则跳过，实现零成本增量更新。注意：`should_skip(file_hash, *, source_path, collection)` 需同时看 collection，否则跨集合误跳过（IR 修复项 A1） |
| 2A-04 | 历史表的结构有哪些关键字段？v2 相比旧表改了什么？ | ⭐⭐ | 存储设计 | v2 主键 `(source_path, collection)`，新增 `file_hash` / `failed_stage` / `parser_version` / `chunk_version` / `embedding_version`；旧表自动迁移改名 `_legacy_backup`；历史表是「存储里应该有什么」的唯一权威基准 |
| 2A-05 | 使用 MarkItDown 解析 PDF 的核心优势是什么？有什么局限？ | ⭐⭐ | 技术选型 | 优势：直接输出 Markdown，与 Splitter 天然兼容；局限：复杂表格/图形可能解析不完整，需 LLM 后处理 |

### 2B：Splitter 阶段

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 2B-01 | 什么是 RecursiveCharacterTextSplitter？它如何做到"语义感知切分"？ | ⭐⭐ | 切分原理 | 按层级分隔符递归尝试：Markdown标题→段落→句子→字符，在 chunk_size 限制内保持最大语义完整性 |
| 2B-02 | Chunk 必须包含哪些定位字段？为什么这些字段很重要？ | ⭐ | 元数据设计 | source/chunk_index/start_offset(或等价定位字段)；支持检索命中后溯源定位，Web UI Trace 页展示原文来源 |
| 2B-03 | 固定长度切分 vs 递归字符切分 vs 语义切分的优缺点各是什么？ | ⭐⭐⭐ | 切分策略比较 | 固定切：简单但破坏语义；递归字符：平衡，当前选择；语义切：质量最高但需Embedding计算，成本高 |
| 2B-04 | chunk_overlap 参数的作用是什么？太大或太小有什么问题？ | ⭐⭐ | 参数影响 | 确保跨边界信息不丢失；太小：边界处语义断裂；太大：冗余数据多、存储成本高 |

### 2C：Transform & Enrichment 阶段

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 2C-01 | Transform 阶段有哪 3 大增强策略？分别解决什么问题？ | ⭐⭐ | 增强策略 | ①智能重组(LLM合并碎片/去噪) ②语义元数据注入(LLM生成Title/Summary/Tags) ③多模态增强(图片Captioning) |
| 2C-02 | 什么是 Image Captioning？为什么用"图转文"而不是多模态向量？ | ⭐⭐ | 多模态策略 | 用Vision LLM描述图片→文字Caption→嵌入Chunk；无需CLIP等多模态库，复用现有文本检索链路，架构统一 |
| 2C-03 | image_index.db 的作用是什么？表里存什么？ | ⭐⭐ | 图片存储 | 存储 image_id → 文件路径映射；检索命中含图片的Chunk时，可通过image_id找到本地图片文件路径 |
| 2C-04 | 语义元数据注入产出的 Title/Summary/Tags 存在哪里？如何影响检索？ | ⭐⭐⭐ | 元数据检索 | 存入 Chunk 的 Metadata 字段；增强 Metadata Filtering 能力；Summary/Tags 也可追加进 Chunk.text 提升语义召回覆盖 |
| 2C-05 | Transform 为什么要设计为"原子化 + 幂等操作"？ | ⭐⭐⭐ | 工程健壮性 | LLM 调用不稳定，原子化支持单 Chunk 独立重试；幂等确保重跑不产生重复处理，支持中断续跑 |

### 2D：Embedding 阶段

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 2D-01 | 什么是 Dense Embedding？什么是 Sparse Embedding？各自的优势是什么？ | ⭐ | 向量基础 | Dense：高维浮点向量，捕捉语义，解决"词不同意同"；Sparse：关键词权重，捕捉精确匹配，解决专有名词 |
| 2D-02 | 为什么要做差量计算（Incremental Embedding）？是怎么实现的？ | ⭐⭐ | 成本优化 | 计算 Chunk 内容哈希(ContentHash)，仅对库中未存在的哈希调用 Embedding API，文件改名不重算 |
| 2D-03 | 批处理（Batch Processing）在 Embedding 阶段如何优化性能？ | ⭐⭐ | 工程优化 | 按 batch_size 分批调用 API，减少网络 RTT；最大化 GPU/CPU 利用率；避免单条高频调用触发限流 |
| 2D-04 | BM25 是什么算法？为什么叫"稀疏向量"？ | ⭐⭐ | BM25原理 | BM25=Best Match 25，基于词频(TF)和逆文档频率(IDF)计算相关性；大多数词权重为0，高维但稀疏 |
| 2D-05 | Embedding 存储时和什么一起原子化写入？为什么要原子化？ | ⭐⭐⭐ | All-in-One存储 | Dense Vector + Chunk原文 + Metadata 一起写入 Chroma；命中后无需二次查库，保证毫秒级响应 |

### 2E：文档生命周期管理

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 2E-01 | DocumentManager 的职责是什么？和 IngestionPipeline 有什么区别？ | ⭐⭐ | 模块边界 | Pipeline处理摄取流；DocumentManager负责摄取后的管理操作(list/detail/delete)，跨 4 存储协调删除 |
| 2E-02 | 删除一个文档需要同时清理哪 4 个存储？为什么需要协调删除？ | ⭐⭐⭐ | 跨存储协调 | Chroma(向量)/BM25(倒排索引)/ImageStorage(图片文件)/历史表(哈希记录)；确保无悬空引用，使文件可重新摄入。四者无跨库事务，靠可重入幂等 + 固定顺序保证一致（见 13-03） |
| 2E-03 | on_progress 回调的设计目的是什么？签名是怎样的？ | ⭐⭐ | 接口设计 | 供调用方实时展示进度条；签名 `on_progress(stage_name: str, current: int, total: int)`；None 时行为不变 |

---

## 第 3 章：检索查询流水线（Query Pipeline）

### 3A：查询预处理

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 3A-01 | 为什么 Query Engine 假设"输入 Query 已完成会话上下文补全"？这个职责在哪里完成？ | ⭐⭐ | 职责分离 | MCP 工具路径由上游 Client/Host 完成；Web UI Chat 路径由 `src/core/chat/context.py` 在检索前做话题过滤 + 查询改写，QueryEngine 本身保持无状态 |
| 3A-02 | 关键词提取（Keyword Extraction）在查询预处理中起什么作用？ | ⭐⭐ | 稀疏检索优化 | 提取实体/动词(去停用词)，生成稀疏检索的Token列表；提升BM25查准率，避免噪声词干扰 |
| 3A-03 | Query Expansion（同义词/别名扩展）的策略是什么？为什么稠密路不扩展？ | ⭐⭐⭐ | 扩展策略 | 扩展词合并进稀疏路(BM25 OR扩展)；稠密路保持单次调用（原始Query语义覆盖足够广，多次调用成本高） |

### 3B：混合检索（Hybrid Search）

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 3B-01 | Hybrid Search 的 Dense Route 和 Sparse Route 分别是什么？各解决什么问题？ | ⭐ | 混合检索基础 | Dense：Embedding相似度匹配语义(词不同意同)；Sparse：BM25关键词匹配(专有名词精确查找) |
| 3B-02 | RRF 算法的公式是什么？为什么用排名而不是分数直接融合？ | ⭐⭐⭐ | RRF原理 | `Score = 1/(k+Rank_Dense) + 1/(k+Rank_Sparse)`；分数量纲不同，直接融合易失衡；排名融合更鲁棒。S2 后 `fuse_routes` 支持 N 路融合（dense>sparse>agentic） |
| 3B-03 | 两路检索是并行执行还是串行执行的？为什么？ | ⭐⭐ | 性能设计 | 并行执行；Dense和Sparse彼此独立，并行可减少50%+等待时间 |
| 3B-04 | RRF 中的平滑因子 k 起什么作用？k 值越大越小怎么影响结果？ | ⭐⭐⭐ | 参数理解 | 避免排名1文档分数远高于第2名；k越大，高低排名分数差距越小（更平滑）；通常取k=60 |

### 3C：过滤与重排（Filtering & Reranking）

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 3C-01 | Metadata Filtering 的"先解析、能前置则前置"原则是什么意思？ | ⭐⭐ | 过滤策略 | 硬约束(collection/doc_type)在检索阶段Pre-filter缩小候选集；软偏好/不稳定字段做Post-filter或排序信号 |
| 3C-02 | Reranker 支持哪 3 种后端？默认是哪种？不可用时如何降级？ | ⭐⭐ | Reranker设计 | ①None ②Cross-Encoder(本地/托管) ③LLM Rerank；超时/失败必须回退到RRF排序，保证可用性 |
| 3C-03 | Cross-Encoder 和 Bi-Encoder（Dense Embedding）在架构上有什么区别？ | ⭐⭐⭐ | 模型原理 | Bi-Encoder独立编码Query和Doc(快，离线可用)；Cross-Encoder联合编码[Query,Doc]对(慢但更精准，需实时计算) |
| 3C-04 | 为什么 LLM Rerank 的候选数量要比 Cross-Encoder 更小（M<=20）？ | ⭐⭐⭐ | 成本控制 | LLM 逐对打分 Token 成本高；候选数越大成本指数级增加；需要严格结构化输出(JSON)降低解析风险 |

---

## 第 4 章：MCP 服务设计

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 4-01 | MCP 是什么协议？和普通 REST API 相比有什么优势？ | ⭐ | MCP基础 | Model Context Protocol，标准化 AI 助手与外部工具的对接；无需自建 Chat UI，直接复用 Copilot/Claude 入口 |
| 4-02 | 为什么选择 Stdio Transport 而不是 HTTP？Stdio 的工作方式是什么？ | ⭐⭐ | 传输选型 | Client 以子进程启动 Server，通过 stdin/stdout 交换 JSON-RPC；零配置、隐私安全、无需端口管理 |
| 4-03 | Stdio Transport 有什么严格约束必须遵守？ | ⭐⭐ | 实现约束 | stdout 仅输出合法 MCP 消息，禁止混入日志；日志统一写 stderr；由 e2e 逐行断言（主检查）+ `check_redlines.py` 静态扫描（兜底）双保险 |
| 4-04 | Server 对外暴露哪几个工具（Tools）？分别是什么？ | ⭐ | 工具设计 | 常驻 3 个：`query_knowledge_hub`(主检索入口) / `list_collections`(列举集合) / `get_document_summary`(获取文档摘要)；第 4 个 `agentic_search` 仅 `agentic_search.enabled=true` 时注册（缺省 false，见 11-07） |
| 4-05 | `query_knowledge_hub` 的返回格式是什么？如何体现"引用透明"？ | ⭐⭐ | 返回设计 | structuredContent 含 citations 数组(source/page/text/score)；content 数组含 Markdown 带[1]标注的人类可读版本 |
| 4-06 | 多模态图片是怎么通过 MCP 返回给 Client 的？Client 侧如何处理？ | ⭐⭐⭐ | 多模态返回 | 检索命中图片 → Server 读本地文件 → Base64 编码 → `{type:image, data:base64, mimeType:image/png}`；Client 决定渲染策略 |
| 4-07 | 为什么 MCP 返回的 content 数组第一项要始终是纯文本/Markdown 版本？ | ⭐⭐ | 兼容性设计 | 最低兼容性保证；不同 Client 的多模态支持能力不同，纯文本是最通用的降级兜底格式 |

---

## 第 5 章：可插拔架构设计

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 5-01 | 工厂模式在这个项目里是怎么实现的？举一个具体文件名 | ⭐⭐ | 工厂模式 | 读取settings.yaml中provider字段 → 工厂函数(如embedding_factory.py)动态实例化对应实现类；每类组件一个 `Base*` 抽象 + 工厂（llm/embedding/splitter/loader/vector_store/reranker/agentic_search） |
| 5-02 | 要新增一个 LLM Provider（如 Claude），需要修改哪些文件？不需要修改哪些？ | ⭐⭐⭐ | 扩展点 | 新增Provider实现类(继承BaseLLM) + 在Factory注册 + settings.yaml配置；上层业务代码**不需要修改** |
| 5-03 | `settings.yaml` 里的 `retrieval.rerank_backend` 字段有哪几个合法值？ | ⭐ | 配置结构 | `none` / `cross_encoder` / `llm` |
| 5-04 | Chroma 相对于 Qdrant/Milvus 的核心优势是什么？什么场景考虑换掉它？ | ⭐⭐ | 存储选型 | 嵌入式,pip install即用,零部署；规模到分布式/高并发/多节点时考虑换 Qdrant/Milvus |
| 5-05 | BaseEmbedding 接口最核心的方法是什么？签名是怎样的？ | ⭐⭐ | 接口设计 | `embed(texts: list[str]) -> list[list[float]]`；屏蔽不同Provider的请求格式和批处理差异 |
| 5-06 | 配置驱动切换的流程是什么？需要重启服务吗？ | ⭐ | 配置管理 | 修改settings.yaml对应字段 → 确保依赖已安装 → **重启服务** → 工厂函数自动加载新实现 |
| 5-07 | AgenticSearch 的可插拔后端有哪几个？测试怎么做到零网络？ | ⭐⭐ | 可插拔+测试 | `sirchmunk_http`（HTTP 调独立 sidecar 进程）与 `fake`（`FakeAgenticSearch` 确定性后端）；单测注入 fake 后端 + `httpx.MockTransport`，零网络零真实模型 |
| 5-08 | `BaseAgenticSearch.search_stream` 是必选方法吗？这种"可选能力"设计的好处是什么？ | ⭐⭐ | 接口演进 | 可选方法，默认实现整块回退（类似 `BaseLLM.chat_stream`）；新能力以可选方法渐进加入，不破坏既有后端实现 |

---

## 第 6 章：可观测性与 Web UI

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 6-01 | 项目里的 Trace 分哪两类？分别记录什么链路的数据？ | ⭐ | Trace设计 | Query Trace(查询链路) + Ingestion Trace(摄取链路)；每种都以trace_id为核心；Chat 每轮恰好写一条 query trace，`metadata.routes="chat"` 标识 |
| 6-02 | Query Trace 记录了哪 5 个阶段？每阶段记录了什么关键数据？ | ⭐⭐ | Query Trace | Query Processing(关键词)/Dense(分数)/Sparse(BM25分数)/Fusion(排名)/Rerank(前后排名变化+是否Fallback)；Chat 路径新增 chat_history/chat_rewrite/chat_generation 三个 stage |
| 6-03 | Ingestion Trace 记录了哪 5 个阶段？各阶段记录了什么？ | ⭐⭐ | Ingestion Trace | Load(图片数/解析器)/Split(chunk数/平均长度)/Transform(LLM处理量)/Embed(provider/批次/维度)/Upsert(upsert量/BM25更新) |
| 6-04 | Web UI 有哪些页面？Streamlit Dashboard 现在还在吗？ | ⭐⭐ | 界面架构 | Web UI 是主界面：Chat 首页 + 七页等价迁移（Overview / Data Browser / Ingestion Manager / Query Traces / Ingestion Traces / Evaluation / Query Playground）；Streamlit 已于 2026-11-13 退役（物理删除页面层与 `start_dashboard.py`），`observability/dashboard/services/` 服务层保留（API 层依赖） |
| 6-05 | 为什么说 Web UI 可以"自动适配"更换组件？具体机制是什么？ | ⭐⭐⭐ | 动态渲染 | 基于 Trace 中的 method/provider/details 字段动态渲染；换组件→Trace写新provider名→Web UI自动展示新内容，无需改UI代码 |
| 6-06 | 全链路白盒化解决了 RAG 系统的什么核心痛点？ | ⭐ | 可观测价值 | 传统 RAG 是"黑盒"，无法知道"为什么选了这些文档"；Trace 让每个中间状态可见，精准定位 Bad Case |

---

## 第 7 章：评估体系

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 7-01 | Hit Rate@K 是什么指标？衡量什么？ | ⭐ | 评估指标 | 在 Top-K 检索结果中，有多少查询至少命中了1个正确文档；衡量召回覆盖率 |
| 7-02 | MRR（Mean Reciprocal Rank）是什么？和 Hit Rate 的区别？ | ⭐⭐ | 评估指标 | MRR 关注正确答案在排名多靠前(`1/Rank`)；Hit Rate 只关注有没有命中；MRR 更关注位置质量 |
| 7-03 | Faithfulness 指标衡量什么？和 Answer Relevancy 有什么区别？ | ⭐⭐ | Ragas指标 | Faithfulness=生成答案是否有检索文档支撑(防幻觉)；Answer Relevancy=答案是否回答了问题 |
| 7-04 | 项目支持哪几个评估框架？如何同时启用多个？ | ⭐⭐ | 评估框架 | Ragas / DeepEval / 自定义指标；配置 `evaluation.backends: [ragas, custom_metrics]` 并行执行并汇总结果 |
| 7-05 | 评估闭环的核心价值是什么？"凭感觉调优"有什么风险？ | ⭐ | 评估价值 | 量化验证每次策略变更(Chunk Size/Reranker更换)的效果；凭感觉可能改了一个指标破坏另一个。本项目实证：G0 终裁与 KE2 成对实验都靠评估数据裁决（见 11-01、11-18） |

---

## 第 8 章：测试体系与工程质量

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 8-01 | 项目测试分哪三层？各层的职责是什么？ | ⭐ | 测试分层 | Unit(单元/纯逻辑) / Integration(集成/组件协作) / E2E(端到端/真实链路)；另有默认跳过、环境变量门控的真实集成测试（RAG_LLM/E2E/AGENTIC_INTEGRATION） |
| 8-02 | 为什么 BM25 要有"roundtrip"测试？这个名字意味着什么？ | ⭐⭐ | 测试策略 | 验证"写入→保存→加载→查询"完整数据往返路径，确保 JSON 序列化/反序列化没有数据丢失或精度损失 |
| 8-03 | 离线确定性测试的纪律是什么？外部依赖怎么处理？ | ⭐⭐ | 测试纪律 | 单元/集成测试零网络、零真实模型；外部依赖一律注入 fake（`httpx.MockTransport`、fake LLM/embedding 后端、`FakeAgenticSearch`）；真实模型/真实 sidecar 冒烟必须走环境变量门控 |
| 8-04 | `conftest.py` 在 pytest 中的作用是什么？项目里用它来做什么？ | ⭐⭐ | pytest机制 | 定义共享 fixtures（测试夹具）；项目里提供测试配置/Mock对象/临时路径，还有 session 级 autouse fixture 设置 `RAG_LOG_ROOT`，保证测试不写仓库 `logs/`（见 12-03） |
| 8-05 | Web UI 的冒烟测试是如何在不开真实服务的情况下测试的？ | ⭐⭐⭐ | 测试工具 | `tests/e2e_web/` 假后端启动冒烟（frontend/out 未构建则 skip）；原 Streamlit AppTest（无头渲染）已随 Streamlit 退役（2026-11-13）移除 |

---

## 第 9 章：存储与持久化架构

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 9-01 | 为什么项目选择 SQLite 而不是 MySQL/PostgreSQL？ | ⭐ | 存储选型 | 零依赖部署(pip install)，无需数据库服务；本地优先设计；支持WAL并发；可后续迁移 |
| 9-02 | WAL（Write-Ahead Logging）在 SQLite 中如何保证并发安全？ | ⭐⭐⭐ | 并发机制 | WAL模式下写入先记日志再合并，允许多Reader与1Writer并发；避免传统锁模式下的读写互斥 |
| 9-03 | ChunkID 的生成算法是什么？为什么选择这种方式？有什么已知局限？ | ⭐⭐ | ID设计 | `{doc_id}_{index:04d}_{content_hash[:8]}`（`document_chunker.py`），确定性生成支持幂等 Upsert。局限（IR-A2）：doc_id 由文档哈希派生，内容一变全部 chunk_id 变，幂等替换键失效、旧 chunk 残留——由版本替换（按旧 file_hash 定向清理）补救（见 13-04） |
| 9-04 | BM25 索引数据当前用什么格式持久化？有什么升级路径？ | ⭐⭐ | BM25持久化 | **JSON**（`data/db/bm25/index.json`，`json.dump(ensure_ascii=False)`）；升级路径：迁移至 SQLite 存储倒排索引和IDF统计，提升可靠性和查询灵活性 |
| 9-05 | `delete_document` 操作为什么要清理历史表记录？不清理会怎样？ | ⭐⭐⭐ | 数据一致性 | 历史表记录"已成功处理"；不清理则下次摄取相同文件时触发早退跳过，无法重新摄入。历史表是存在性判定的唯一权威基准，所以删除顺序中它最后清 |

---

## 第 10 章：Web UI 与多轮 Chat（WC/WP/SR/CMC 系列）

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 10-01 | Web UI 前端为什么选 React + Next 静态导出，而不是 Vue？ | ⭐ | 前端选型 | 决策 D1：为直接移植上游 sirchmunk `web/`（Apache-2.0）的 Chat UI / 主题 / markdown 渲染链（ReactMarkdown+KaTeX+Mermaid）；Next 静态导出由 FastAPI 单端口托管，无需 Node 运行时 |
| 10-02 | Web UI 由哪些页面组成？入口和默认端口是什么？ | ⭐ | 界面组成 | Chat 为首页 + 七页等价迁移（Overview/Data Browser/Ingestion Manager/Query Traces/Ingestion Traces/Evaluation/Query Playground）；`scripts/start_api.py --serve-ui`，默认 127.0.0.1:8600，`api.enabled` 默认 false 需显式开启 |
| 10-03 | Streamlit 为什么退役？退役删了什么、留了什么？ | ⭐ | 退役范围 | Web UI 成为主界面后 Streamlit 双轨维护成本高。物理删除：`dashboard/app.py`+`pages/` 七页面、`start_dashboard.py`、`streamlit` 依赖、页面级 AppTest 测试；保留：`dashboard/services/` 服务层（`src/api/` 直接依赖）、`sidecar_manager.py` |
| 10-04 | 删除 Streamlit 后，旧配置里残留的 `dashboard:` 节怎么处理？ | ⭐⭐ | 配置兼容 | 静默忽略（保留在 `Settings.raw`，不 fail-fast）——与「非法值 fail-fast」不冲突，该纪律只针对已登记字段；有用例 `test_legacy_dashboard_section_is_ignored` 锁定 |
| 10-05 | Chat 多轮对话的上下文处理"三步法"是什么？ | ⭐⭐ | 多轮机制 | ①裁剪（`history_max_turns:10` / `history_max_tokens:32000`，token 估算 len/3.5 纯函数）②话题相关性过滤（失败保留历史，fail-open）③查询改写（仅 RAG 检索前，失败用原句）——`src/core/chat/history.py` / `context.py` |
| 10-06 | ChatEngine 的重试语义是什么？为什么重试窗口止于首个 stream 帧？ | ⭐⭐ | 流式健壮性 | 瞬态错误（连接/超时/429/5xx）重试 1 次间隔 2s；真流式开始后前端已收到内容，再重试会导致重复内容，所以首个 `stream` 帧后不再重试 |
| 10-07 | LLM 真流式是怎么实现的？"假流式"指什么？ | ⭐⭐ | 流式实现 | `BaseLLM.chat_stream()` 可选方法，默认整块 yield 回退；OpenAI 兼容后端 httpx `stream:true` 解析 SSE（[DONE] 终止），Ollama 解析 NDJSON；离线测试用 `httpx.MockTransport`。上游 sirchmunk 的"整答案逐词+sleep"是假流式，本项目一律真流式 |
| 10-08 | AnswerGenerator 如何保证答案里的 [n] 引用与 citations 对齐？空检索时怎么办？ | ⭐⭐ | 引用对齐 | 检索结果构造带编号上下文，编号与 CitationGenerator 的 citation id 同源同序；系统 prompt 要求标注 [n]；空检索短路不调 LLM，直接发固定"未命中"文案（`answer_generator.py`，prompts 在 `config/prompts/chat_*.txt`） |
| 10-09 | 检索失败时 Chat 降级纯聊天的条件是什么？grounded 标志起什么作用？ | ⭐⭐ | 降级语义 | 默认发 `error` 帧不降级（守「引用必须可溯源」）；仅 `chat.fallback_to_chat_only: true` 才降级，且 `result.grounded: false` 显式标记，前端展示横幅提示 |
| 10-10 | QueryService 和 SearchService 为什么要分成两个服务？Chat 如何做到不依赖 MCP 层？ | ⭐⭐⭐ | 分层守卫 | 决策 D8：`QueryService` 返回结构化 RetrievalResult 列表供 AnswerGenerator；`SearchService`（产 MCP Markdown）原样保留给 Query Playground；`src/core/chat/` 经 `ChatRetriever` Protocol 注入检索，不 import `mcp_server`/`observability`，有分层守卫单测 |
| 10-11 | Chat 会话持久化的写入时机是什么？取消/中断时怎么处理？ | ⭐⭐ | 会话存储 | SQLite `chat_sessions.db` 两表；用户消息到达即落库，助手消息在终帧落库；取消/中断保存已生成部分并标 `metadata.cancelled: true`，服务重启不丢会话（`session_store.py`） |
| 10-12 | Chat 的三种检索模式是什么？各自成本与门控？ | ⭐ | 模式设计 | `hybrid`（默认，走主链路毫秒级零 token）/ `agentic_fast` / `agentic_deep`（复用 `AgenticSearchTool` 调 sidecar）；DEEP 受 `allow_deep` 门控，关闭发 error 帧 code `DEEP_DISABLED`；模式可用性由 `GET /api/v1/chat/capabilities` 驱动 |
| 10-13 | `WS /api/v1/chat` 的帧协议和连接模型是什么？ | ⭐⭐ | WS 协议 | 一帧一 JSON；下行 session/status/search_log/search_complete/search_error/stream/sources/result/error；上行 `user_message` + `cancel`（断开即取消）；「每条消息一个连接」，会话连续性靠 session_id；连接上限 `chat.max_connections:100`；上行帧 Pydantic 校验拒绝未知字段 |
| 10-14 | Web API 鉴权为什么用独立的 `RAG_API_TOKEN` 而不复用 sidecar 的 token？ | ⭐⭐⭐ | 安全边界 | token 会进浏览器 localStorage 与 WS URL，复用会暴露 sidecar 凭据；未解析占位符 `${...}` 视同无 token；host 非 loopback 且无 token → 启动 fail-fast（决策 D9） |
| 10-15 | 摄取/评估等写接口的任务模型是怎样的？服务重启后遗留任务怎么办？ | ⭐⭐ | 异步任务 | POST /uploads、/ingestions、/evaluations 返回 202+task_id；SQLite `tasks.db`，状态机 pending→running→succeeded/failed；重启将遗留 running 标 failed/interrupted |
| 10-16 | WP0–WP2 收口了哪三个双轨遗留差异？ | ⭐⭐ | Parity 收口 | WP0：Ingestion/Query 追踪列表服务端 offset 分页（页大小 50，越界钳制）；WP1：评估历史内联 SVG 趋势图（不引图表库，最近 10 次 succeeded）；WP2：`POST /evaluations` 提交前路径预检 422（先于 409 活跃任务检查） |
| 10-17 | Chat 模式选择器按模式着色，为什么 Tailwind 类名必须写成完整字面量？ | ⭐⭐⭐ | 前端约束 | Tailwind 3 JIT 只扫描完整类名，拼接生成的类不会被编译；用 `MODE_TONE: Record<RetrievalMode,string>` 查表（`ModeSelector.tsx:19`），Record 类型强制新增模式时补齐配色；对比度 ≥ WCAG AA（Hybrid 黄/Agentic fast 绿/Deep 蓝） |

---

## 第 11 章：Sirchmunk Sidecar 与 Agentic Search（S/SX/WS/KE 系列）

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 11-01 | Agentic 检索为什么默认关闭？G0 终裁的结论是什么？ | ⭐ | 评估裁决 | G0 终裁（2026-10-06）：裸 rg 基线 6/6 全中（8–20ms 零成本）严格优于 FAST 2/6（16–38k token/条）与 DEEP（multihop 1/2、66k token）→ sidecar 增益在本仓库自指语料上不成立；`agentic_search.enabled: false` 为永久默认 |
| 11-02 | sidecar 是什么形态？主项目为它加了哪些依赖？ | ⭐ | 架构形态 | 独立 venv 跑 `sirchmunk serve`（FastAPI，127.0.0.1 + Bearer Token），锁定上游 commit `b314e11`；主项目 `pyproject.toml` 零新增主依赖；sidecar 有独立 `LLM_*` 环境变量 |
| 11-03 | 「主 venv 永不 import sirchmunk」红线的四个实证原因是什么？ | ⭐⭐⭐ | 隔离红线 | ①构造期副作用（缺 rga 自动联网装、`reuse_knowledge=True` 构造即 import torch 下载模型）②DEEP 路径无条件 print 污染 MCP stdout ③`AsyncOpenAI` 连接池/信号量绑定首个事件循环，进程内反复 asyncio.run 不可行 ④torch/duckdb/igraph 等重依赖不进主 venv |
| 11-04 | `SirchmunkHttpBackend` 的错误分型有哪些？ | ⭐⭐ | 错误处理 | `httpx.TimeoutException` → `AgenticSearchTimeoutError`；传输错误/HTTP 4xx/5xx/`success=false` → `AgenticSearchBackendError`；非 JSON/畸形结构 → `AgenticSearchResponseError`；恒带 `Authorization: Bearer ${SIRCHMUNK_API_TOKEN}` |
| 11-05 | sidecar 请求的字段名有什么"静默失效"陷阱？ | ⭐⭐ | 接口陷阱 | 必须是 `include_patterns`/`exclude_patterns`；写错（如 `exclude`）FastAPI 对未知字段**静默丢弃**，过滤不生效还可能泄题（G0 曾因此踩坑） |
| 11-06 | PathGuard 的"双层防线"是什么？为什么需要它？ | ⭐⭐⭐ | 安全设计 | 上游 `paths` 不是安全边界（`file_read` 可读任意绝对路径，`/api/v1/search` 不执行白名单校验），所以：①调用前 `validate_paths`（realpath 归一、符号链接逃逸拒绝、强制排除 `.env`/`.git`/`data/db`，越权 fail-closed 不发请求）②调用后 `filter_evidence_paths` 静默剔除白名单外证据（防 prompt injection 泄露） |
| 11-07 | MCP `agentic_search` 工具的注册条件与降级行为是什么？ | ⭐ | 工具门控 | 仅 `agentic_search.enabled=true` 时注册进 tools/list（缺省 false，配置异常按禁用处理，维持契约）；参数校验越权 → `-32602`；sidecar 不可用 → 友好 `isError` 而非 `-32603`，不影响其余工具 |
| 11-08 | 为什么 agentic citations 的 page 字段恒为 None？ | ⭐⭐ | 引用口径 | sirchmunk 全链路无页码/行号，`start/end` 是抽取文本上的字符偏移，不冒充页码 |
| 11-09 | Agentic 作为检索第三路是怎么融入 HybridSearch 的？ | ⭐⭐ | 三路融合 | `AgenticTrigger` 三策略 `tool_only/fallback/always`；`fuse_routes` N 路 RRF（payload 取首个出现路由，dense>sparse>agentic）；`enabled=false` 或未触发时行为与基线逐字节一致；后置去重（source_path+文本重叠≥0.8，Dense/Sparse 永不丢弃、agentic 仅补位） |
| 11-10 | SX1 的 telemetry 解析做了什么转换？`zero_llm_path` 是什么含义？ | ⭐⭐ | 遥测消费 | 上游把动态 telemetry `setdefault` 合并进 `data` **顶层**（无嵌套键）；converter 白名单解析，`retrieval_logs` 降维为 `retrieval_tool_counts` 计数；`zero_llm_path` = `llm_call_count==0 且 loop_count==0`，但不等于「复用命中」（DocQA 直析/闲聊短路同值） |
| 11-11 | SX2 参数透传时，`llm_fallback` 为什么恒发 false？ | ⭐⭐ | 语义锁定 | 上游 fallback 产生无出处内容，与「引用必须可溯源」冲突，所以显式锁定 `llm_fallback: false`（红线脚本第 2 条也扫这个字面量） |
| 11-12 | SX3 知识编译实验的结论是什么？ | ⭐⭐ | 编译实验 | 编译门槛 3,000 字符且完全不处理 `.py`；FAST 文档题 token 降约 68% 但 DEEP 显著回退、`.py` 题两模式均回退 → USAGE_GUIDE 记「知识编译：当前不推荐」（GX 不通过） |
| 11-13 | SX4 簇复用的实验结论与开启陷阱是什么？ | ⭐⭐⭐ | 复用实验 | 只有 FAST 原题逐字重问命中复用（3/3、约 30ms、0 token），改写 0/3；复用无过期机制，改文件须手动清 `.cache/knowledge/`；开启用 `SIRCHMUNK_ENABLE_CLUSTER_REUSE=true`，**不可注释删行**（上游对未设置按 true 处理）+ preflight 预下载 MiniLM（约 470MB） |
| 11-14 | SX5.3 发现了什么上游缺陷？它如何影响了 WS 端点的设计？ | ⭐⭐⭐ | 流式缺陷 | sidecar 流式端点每请求稳定泄漏约 10 线程（每请求新建 `KnowledgeStorage`：1 个 DuckDB 连接 9 线程 + 1 个 parquet 同步线程，端点不调 `close()`，atexit 持引用 GC 无法回收）；同步端点 singleton 不受影响 → `api.deep_progress_stream` 默认 false，MCP tool 保持同步调用 |
| 11-15 | `scripts/sidecar_manager.py` 的探活与回收策略是什么？谁调用它？ | ⭐⭐ | 进程托管 | 探活：任何 HTTP 响应（含 401）均算存活；回收：TERM 等 10s 再 KILL；`start_api.py --manage-sidecar` 是 sidecar 托管唯一入口（默认不托管；启动失败仅降级不阻塞主服务） |
| 11-16 | `SearchLogFramer` 解决什么问题？去重窗口多大？ | ⭐⭐ | 进度帧共享 | 共享「sidecar 日志 → `search_log` 帧」转换（`src/core/chat/progress.py`），ChatEngine 与 WS 端点复用，帧协议与 0.5s 去重窗口语义一致 |
| 11-17 | 门控端点 `WS /api/v1/search/ws` 的行为约束有哪些？ | ⭐⭐ | WS 端点 | 关闭时 capabilities 不报 `progress_stream`、WS 握手拒绝（error 帧 + 1008），不触达检索链路；每连接一次检索；下行 `search_log` → `search_result` → 服务端关闭；支持 cancel 帧、Origin 校验、连接限流 4、模式门控预检（`AGENTIC_DISABLED`/`DEEP_DISABLED`） |
| 11-18 | KE1 的演化启动器为什么没有违反进程隔离红线？ | ⭐⭐ | 红线边界 | 演化在 **sidecar venv 内**的 SDK 启动器开启（`evolve_launcher.py` 包装 `AgenticSearch.__init__` 注入开关后再 import `sirchmunk.api.main`），主 venv 依旧零导入；`check_redlines.py` 的 `SIRCHMUNK_IMPORT_EXEMPTIONS` 精确白名单只放行这一个文件 |
| 11-19 | KE2 成对实验的裁决是什么？结论边界在哪？ | ⭐⭐⭐ | 实验裁决 | reword 复用命中 A 臂（只开复用）6/12 vs B 臂（复用+演化）6/12，差值 0 → 提升门未达成，verdict=insufficient_evidence；复用与演化默认均关闭，KE4 上游 PR 门控未触发。结论边界：负结果只覆盖「小语料+短预热」——复用吃查询重复度，演化吃簇的多样性积累 |

---

## 第 12 章：日志分层与 Trace 持久化（LG 系列）

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 12-01 | 项目的日志分哪两层？各自写给谁看？ | ⭐ | 分层设计 | Trace 日志 `logs/traces/traces-YYYY-MM-DD.jsonl`（JSON Lines，供 Web UI TraceService 与评估读取）+ 应用运行日志 `logs/app/<component>-YYYY-MM-DD.log`（api/mcp/ingest/query/evaluate 五个入口，仅供人工排障）；均按本地日期切分，默认保留 30 天 |
| 12-02 | 为什么不用 logging 自带的 `TimedRotatingFileHandler`？ | ⭐⭐ | 轮转设计 | 它靠**重命名**轮转，多进程（API/MCP/CLI）同写会互相覆盖；改为「日期拼文件名 + 每条记录一次 `open("a")` 单行追加」，多进程并发安全；写入失败只记 warning 不抛（旁路语义） |
| 12-03 | `RAG_LOG_ROOT` 环境变量的作用是什么？ | ⭐⭐ | 测试隔离 | `resolve_log_path()` 把相对日志路径相对该环境变量解析（未设则相对 cwd，绝对路径原样）；测试经 conftest session 级 autouse fixture 设置，保证全量离线测试不写仓库 `logs/` |
| 12-04 | `observability.log_file` 以 `.jsonl` 结尾和其他值有什么区别？ | ⭐⭐ | 路径语义 | `.jsonl` 结尾 = 单文件旧模式（兼容测试传入的 tmp 路径）；其他值 = 目录模式按日切分；默认值由 `./logs/traces.jsonl` 改为 `./logs/traces` |
| 12-05 | TraceService 读取按日切分的 trace 文件，怎么做增量解析？ | ⭐⭐ | 读取优化 | 目录模式按文件以 `(mtime_ns, size)` 缓存：历史文件不变不重解析，仅当天文件随写入失效——解决单文件全量重解析 0.45s 的线性增长问题 |
| 12-06 | `setup_app_logging` 为什么只在进程入口调用？uvicorn 有什么坑？ | ⭐⭐⭐ | 日志初始化 | uvicorn 默认 dictConfig 会设 `propagate=False` 并清掉入口 handler，吞掉应用日志；所以 `start_api.py` 传 `uvicorn.run(..., log_config=None)`；库代码 `get_logger` 不变，未调用 setup 的进程不产生日志文件 |
| 12-07 | 保留期清理的规则是什么？有什么安全边界？ | ⭐⭐ | 清理策略 | `retention_days` 默认 30，`0` = 永不清理；只匹配严格 `<prefix>-YYYY-MM-DD<suffix>` 文件名；删除失败跳过不抛；未调用 setup 的进程从不删文件 |
| 12-08 | `scripts/migrate_traces.py` 历史迁移做了什么？迁移结果说明了什么？ | ⭐⭐ | 数据迁移 | 按 `started_at` 本地日期分桶、剔除测试记录、按 trace_id 去重可重入、原文件改名 `.migrated` 备份；真实迁移 16562 条 → 保留 595 条（87% 为测试污染——反面教材：测试曾把 trace 写进真实目录） |

---

## 第 13 章：摄取健壮性与 Markdown 摄取（IR 系列）

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 13-01 | IR 系列立项时发现了哪些系统性缺陷？ | ⭐⭐ | 缺陷清单 | A1 `should_skip` 不看 collection（跨集合误跳过）、A2 文件修改后旧版本 chunk 残留（chunk_id 以 doc_hash 为前缀）、A3 删除只取第一个哈希致删除不彻底、A5 无 `failed_stage`、A7 store 部分失败无回滚 |
| 13-02 | 历史表 v2 的关键改动是什么？为什么说它是"唯一权威基准"？ | ⭐⭐ | 状态基准 | 主键改 `(source_path, collection)`，新增 `file_hash`/`failed_stage`/`parser_version`/`chunk_version`/`embedding_version`；旧表自动迁移备份；它是「存储里应该有什么」的判定依据，所以删除顺序中最后清 |
| 13-03 | 四类存储没有跨库事务，项目怎么保证删除/替换的一致性？ | ⭐⭐⭐ | 一致性设计 | 所有清理/回滚设计为**可重入幂等**，固定顺序 BM25 → Chroma → 图片 → 历史表（历史表最后，它是存在性判定基准）；不追求分布式事务，靠顺序 + 幂等 + 失败中止记 `failed_stage` |
| 13-04 | 文件被修改后重新摄取，旧版本 chunk 怎么清理？ | ⭐⭐ | 版本替换 | pipeline 在 store 前按旧 `file_hash` 定向清理三路存储（BM25 前缀删除 + Chroma 按 metadata.sha256 + 图片目录）；清理失败记 `failed_stage="store"` 并中止，不产生新旧混合态；`_doc_hashes_of` 收集全部去重哈希 |
| 13-05 | `scripts/ingest.py --sync` 的目录同步是怎么工作的？五种状态是什么？ | ⭐⭐ | 同步模式 | `--sync`（目录三向对比）/`--prune`（只清已删除）/`--dry-run`（只打印计划）互斥；五态：new/modified/unchanged/deleted/stale（stale = `embedding_version` 与当前配置不匹配，按 `version_policy.on_embedding_mismatch` 处理，默认告警跳过不自动重建） |
| 13-06 | 摄取错误的"可重试/不可重试"怎么分类？重试策略是什么？ | ⭐⭐ | 错误处理 | 可重试：网络超时/连接拒绝/5xx/SQLite busy；不可重试：解析崩溃/维度不匹配/配置非法；指数退避默认 3 次、2s 起倍增 ±20% 抖动、上限 30s；`max_attempts=1` 默认关闭；`--retry-failed` 重跑 failed 记录；store 失败按 doc_hash 回滚三存储 |
| 13-07 | 除 PDF/Markdown 外还支持哪些格式？依赖怎么管理？ | ⭐ | 多格式 Loader | `.docx/.pptx/.xlsx/.html` 走 `markitdown_loader.py`；markitdown 是 optional 依赖（extra `ingest-formats`）；缺失抛 `MarkitdownNotInstalledError`（含安装指引），pipeline 包装为 stage="load"；空文本抛错不静默入库 |
| 13-08 | Markdown Loader 的"Vault 感知"体现在哪些细节？ | ⭐⭐ | Markdown 摄取 | YAML frontmatter（坏 YAML 降级不阻塞，记 `frontmatter_parse_error`）、title 三级兜底（frontmatter > 首个 H1 > 文件名）、heading_outline、行内 tags 合并去重、wikilink 替换 + 出链收集、`![[img]]` 存在落盘 ImageRef / 缺失记 `missing_image_refs`、`![[note.md]]` 不展开仅记 embeds |
| 13-09 | `ingestion.source_filters` 的匹配规则是什么？默认排除什么？ | ⭐⭐ | 路径过滤 | include/exclude glob 对扫描根的 POSIX 相对路径 fnmatch（目录段参与），exclude 优先；默认排除 `.obsidian/**`、`.trash/**`、`templates/**`；显式单文件 `--path` 不过滤；非法配置 fail-fast |
| 13-10 | 「双登记约束」是什么？由哪个测试锁死？ | ⭐⭐⭐ | 契约测试 | pipeline 支持的扩展名必须在 `src/ingestion/pipeline.py` `_SUPPORTED_EXTENSIONS` 与 `scripts/ingest.py` `SUPPORTED_EXTENSIONS` 两处同步；`tests/unit/test_ingest_entry.py` 断言两处清单相等（含 `.md`/`.markdown`）；CLI 帮助文本从常量推导不再手写第二份 |
| 13-11 | chunk_id 不含 collection 带来什么已知遗留？ | ⭐⭐⭐ | 已知取舍 | 所有 collection 共用同一物理 Chroma collection，同文件摄取到两个 collection 会共享 chunk_id 互相覆盖 metadata；清理有防护但覆盖仍在；修复需 collection 入键 + 数据迁移，属已知遗留 |

---

## 第 14 章：工程化——CI、Evals 与流程纪律（SD/ERD 系列）

| # | 题目 | 难度 | 考察要点 | 参考答案要点 |
|---|------|------|---------|------------|
| 14-01 | CI 有哪两个 job？分别跑什么？ | ⭐ | CI 结构 | `.github/workflows/ci.yml`：`python`（3.13 + `pip install -e ".[dev,ingest-formats]"` → 禁网 pytest → `check_redlines.py`）与 `frontend`（Node 22 → `npm ci` → typecheck+lint+vitest）；concurrency 按 ref 取消旧 run，permissions 仅 `contents: read` |
| 14-02 | 禁网 pytest 命令的每个参数是为什么？ | ⭐⭐ | 禁网机制 | `--disable-socket`（pytest-socket 禁所有 socket）+ `--allow-unix-socket`（放行 asyncio socketpair）+ `--allow-hosts=127.0.0.1,localhost,::1`（loopback 放行给 e2e_web 假后端）+ `NO_PROXY='*'`（防 macOS 本地系统代理把禁网绕出去）；局限：只约束 pytest 进程内 socket，子进程不受约束 |
| 14-03 | `scripts/check_redlines.py` 检查哪三条红线？为什么用 AST 不用 grep？ | ⭐⭐ | 红线脚本 | ①主 venv 不 import sirchmunk（含 `importlib.import_module` 动态导入，白名单 `evolve_launcher.py` 唯一精确豁免）②`llm_fallback` 只允许字面量 False ③`src/mcp_server/` 禁 print/`sys.stdout`（唯一豁免 server.py 的 JSON-RPC 通道）；用 AST 是因为 grep 会被 docstring 误报（http_backend.py 文档里就写着 "import sirchmunk"） |
| 14-04 | 分支保护是怎么设计的？为什么 Code Owners review 不强制？ | ⭐⭐ | 分支保护 | fork `chanity256` 的 main：必须经 PR，python/frontend 为 required checks，`enforce_admins: true`（管理员同样受限），禁 force push；唯一 owner 即 PR 作者无法自审，CODEOWNERS 降级为「可见提示」 |
| 14-05 | 为什么推送用 SSH 而不是 HTTPS/gh token？ | ⭐⭐ | 凭据权限 | `gh` OAuth token 无 `workflow` scope，HTTPS 推 workflow 改动被拒；用 `git@github.com:chanity256/MODULAR-RAG-MCP-SERVER.git`；Actions/Secrets/分支保护只在 fork 开，上游 `origin` 只读不推 |
| 14-06 | agent evals 的运行机制和判定门是什么？ | ⭐⭐ | Agent 评测 | `evals/run.py`：fixture worktree + steering 覆盖 + 树快照 → `droid exec` → `check.py` 判定；每条用例 3 次、3 取 2；判据 ①required_reads 先读后改（跳过 isError 编辑）②redlines ③无弱化测试断言 ④fix-rounds 上限 ⑤离线 pytest ⑥diff_patterns；门 = `min_pass_rate`（0.8）+ baseline 已通过用例不得回退；触发：改 steering 文件（skill 三处/AGENTS.md/hooks）的 PR 或手动 |
| 14-07 | read_guard 先读后改守卫怎么工作？fail-open 指什么？ | ⭐⭐ | Hook 守卫 | PreToolUse 匹配 Edit/Create/ApplyPatch 按路径查必读映射（`src|scripts|tests` → 01_DEV_SPEC；摄取链路另加 04；QA 进度 → QA 计划），缺读 deny 并列出确切路径；PostToolUse 记录已读，状态按 session 隔离 `.factory/state/reads-*.jsonl`；已读 = 记录 ∪ transcript 成功 Read（覆盖 Script 模式）；Grep/cat 不算读过；坏 JSON/未知子命令/异常一律放行（fail-open）。ERD2 实证：判据①失败 15/18 → 0/27 |
| 14-08 | test_guard 是怎么防止 agent 乱改测试的？ | ⭐⭐ | Hook 守卫 | auto-builder 进入 Test & Auto-Fix 时创建 `.factory/state/fix_mode`（gitignore）；标记存在时拦截对 `tests/` 下**已有**文件的 Edit/Create/ApplyPatch 与 Execute 启发式写入（新建测试文件放行）；SessionStart 清残留标记 |
| 14-09 | skill 单一来源机制是怎么运作的？状态文件怎么处理？ | ⭐ | 同步机制 | 权威来源 `.github/skills/`，`scripts/sync_skills.py` 生成 `.claude/skills/` 与 `.github/skills/` 镜像（文本中 `.github/skills` 改写为镜像自身目录）；手改镜像被 `test_skill_mirrors.py` 拦下；单一状态文件（QA 进度、两个学习进度）只在来源保留一份，镜像中路径不改写、三份读写同一份 |
| 14-10 | SD4 定时自动化有什么使用上的"坑"？ | ⭐⭐ | 自动化运维 | 两个本机周度 automation（周一 09:00 北京时间）：followups 扫描起草 draft PR、QA 离线回归；运行时会共享工作树 `git checkout main`——**周一 09:00 前未提交的改动会致该 run 失败** |

---

## 题库统计

| 章节 | 题数 | ⭐基础 | ⭐⭐进阶 | ⭐⭐⭐深挖 |
|------|------|--------|----------|----------|
| 第1章：项目全景 | 8 | 2 | 4 | 2 |
| 第2章：摄取流水线 | 18 | 3 | 9 | 6 |
| 第3章：检索流水线 | 11 | 2 | 5 | 4 |
| 第4章：MCP服务 | 7 | 2 | 3 | 2 |
| 第5章：可插拔架构 | 8 | 2 | 5 | 1 |
| 第6章：可观测性与 Web UI | 6 | 2 | 2 | 2 |
| 第7章：评估体系 | 5 | 2 | 2 | 1 |
| 第8章：测试体系 | 5 | 1 | 3 | 1 |
| 第9章：存储架构 | 5 | 1 | 2 | 2 |
| 第10章：Web UI 与多轮 Chat | 17 | 4 | 10 | 3 |
| 第11章：Sidecar 与 Agentic Search | 19 | 3 | 11 | 5 |
| 第12章：日志分层 | 8 | 1 | 6 | 1 |
| 第13章：摄取健壮性 | 11 | 1 | 7 | 3 |
| 第14章：工程化与流程纪律 | 10 | 2 | 7 | 1 |
| **合计** | **138** | **28** | **76** | **34** |
