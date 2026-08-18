## 6. 项目排期

> **排期原则（严格对齐本 DEV_SPEC 的架构分层与目录结构）**
> 
> - **只按本文档设计落地**：以第 5.2 节目录树为“交付清单”，每一步都要在文件系统上产生可见变化。
> - **1 小时一个可验收增量**：每个小阶段（≈1h）都必须同时给出“验收标准 + 测试方法”，尽量做到 TDD。
> - **先打通主闭环，再补齐默认实现**：优先做“可跑通的端到端路径（Ingestion → Retrieval → MCP Tool）”，并在 Libs 层补齐可运行的默认后端实现，避免出现“只有接口没有实现”的空转。
> - **外部依赖可替换/可 Mock**：LLM/Embedding/Vision/VectorStore 的真实调用在单元测试中一律用 Fake/Mock，集成测试再开真实后端（可选）。

### 阶段总览（大阶段 → 目的）

1. **阶段 A：工程骨架与测试基座**
   - 目的：建立可运行、可配置、可测试的工程骨架；后续所有模块都能以 TDD 方式落地。
2. **阶段 B：Libs 可插拔层（Factory + Base 接口 + 默认可运行实现）**
  - 目的：把“可替换”变成代码事实；并补齐可运行的默认后端实现，确保 Core / Ingestion 不仅“可编译”，还可在真实环境跑通。
3. **阶段 C：Ingestion Pipeline（PDF→MD→Chunk→Embedding→Upsert）**
  - 目的：离线摄取链路跑通，能把样例文档写入向量库/BM25 索引并支持增量。
4. **阶段 D：Retrieval（Dense + Sparse + RRF + 可选 Rerank）**
  - 目的：在线查询链路跑通，得到 Top-K chunks（含引用信息），并具备稳定回退策略。
5. **阶段 E：MCP Server 层与 Tools 落地**
   - 目的：按 MCP 标准暴露 tools，让 Copilot/Claude 可直接调用查询能力。
6. **阶段 F：Trace 基础设施与打点**
   - 目的：增强 TraceContext，实现结构化日志持久化，在 Ingestion + Query 双链路打点，添加 Pipeline 进度回调。
7. **阶段 G：可视化管理平台 Dashboard**
   - 目的：搭建 Streamlit 六页面管理平台（系统总览 / 数据浏览 / Ingestion 管理 / Ingestion 追踪 / Query 追踪 / 评估占位），实现 DocumentManager 跨存储协调。
8. **阶段 H：评估体系**
   - 目的：实现 RagasEvaluator + CompositeEvaluator + EvalRunner，启用评估面板页面，建立 golden test set 回归基线。
9. **阶段 I：端到端验收与文档收口**
   - 目的：补齐 E2E 测试（MCP Client 模拟 + Dashboard 冒烟），完善 README，全链路验收，确保“开箱即用 + 可复现”。


---

### 📊 进度跟踪表 (Progress Tracking)

> **状态说明**：`[ ]` 未开始 | `[~]` 进行中 | `[x]` 已完成
> 
> **更新时间**：每完成一个子任务后更新对应状态

#### 阶段 A：工程骨架与测试基座

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|---------|---------|------|---------|------|
| A1 | 初始化目录树与最小可运行入口 | [x] | 2026-08-12 | 骨架就绪：目录树/__init__.py/main.py/pyproject.toml/config/settings.yaml + 3 prompts，venv 可导入 |
| A2 | 引入 pytest 并建立测试目录约定 | [x] | 2026-08-12 | pytest 基座就绪：test_smoke_imports.py（4 用例）全绿 |
| A3 | 配置加载与校验（Settings） | [x] | 2026-08-12 | Settings dataclass + load_settings + validate_settings；缺失字段可读报错；main.py fail-fast |

#### 阶段 B：Libs 可插拔层

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|---------|---------|------|---------|------|
| B1 | LLM 抽象接口与工厂 | [x] | 2026-08-12 | BaseLLM + ChatMessage/ChatResponse + LLMFactory 注册路由；Fake provider 验证 |
| B2 | Embedding 抽象接口与工厂 | [x] | 2026-08-12 | BaseEmbedding + EmbeddingFactory 注册路由；Fake embedding 稳定向量验证 |
| B3 | Splitter 抽象接口与工厂 | [x] | 2026-08-12 | BaseSplitter + SplitterFactory 注册路由；新增 SplitterConfig（可选节，默认 recursive） |
| B4 | VectorStore 抽象接口与工厂 | [x] | 2026-08-12 | BaseVectorStore + VectorRecord/QueryResult 契约 + VectorStoreFactory 注册路由；契约测试约束 shape |
| B5 | Reranker 抽象接口与工厂（含 None 回退） | [x] | 2026-08-12 | BaseReranker + NoneReranker（保持原序）+ RerankerFactory（none 默认注册）；未知 backend 报错 |
| B6 | Evaluator 抽象接口与工厂 | [x] | 2026-08-12 | BaseEvaluator + CustomEvaluator（hit_rate/mrr/hit_at_k）+ EvaluatorFactory（custom 默认注册）；create_from_settings 批量创建 |
| B7.1 | OpenAI-Compatible LLM 实现 | [x] | 2026-08-12 | OpenAICompatibleLLM 基类 + OpenAI/Azure/DeepSeek 子类；可注入 transport mock HTTP；LLMRequestError 含 provider+错误类型 |
| B7.2 | Ollama LLM 实现 | [x] | 2026-08-12 | OllamaLLM 原生 /api/chat + 可注入 transport；连接失败/超时/错误响应可读报错且不泄露 api_key |
| B7.3 | OpenAI & Azure Embedding 实现 | [x] | 2026-08-12 | OpenAICompatibleEmbedding 基类 + OpenAI/Azure 子类；批量 embed 顺序一致；空/超长输入可配置报错或截断；Azure 复用核心逻辑 |
| B7.4 | Ollama Embedding 实现 | [x] | 2026-08-12 | OllamaEmbedding 原生 /api/embed 批量 + 旧版单条兼容；连接失败/超时/错误响应可读且不泄露 api_key |
| B7.5 | Recursive Splitter 默认实现 | [x] | 2026-08-12 | RecursiveSplitter 封装 LangChain RecursiveCharacterTextSplitter；Markdown 结构感知 separators；标题/代码块不打断 |
| B7.6 | ChromaStore 默认实现 | [x] | 2026-08-12 | ChromaStore 封装 chromadb PersistentClient；upsert 幂等、query 按 score 降序、top_k、metadata filters；持久化重开可读；14 集成测试全绿 |
| B7.7 | LLM Reranker 实现 | [x] | 2026-08-12 | LLMReranker 读取 rerank.txt 构造 prompt + 注入 LLM；解析 JSON 数组重排候选；LLM异常/解析失败/越界回退原序 + last_fallback 标记；top_m 截断；未覆盖候选追加末尾 |
| B7.8 | Cross-Encoder Reranker 实现 | [x] | 2026-08-12 | CrossEncoderReranker 可注入 scorer 打分排序；deterministic stable sort；scorer 异常/超时/长度不匹配回退原序 + last_fallback；top_m 截断；kwargs 临时覆盖 scorer |
| B8 | Vision LLM 抽象接口与工厂集成 | [x] | 2026-08-12 | BaseVisionLLM(chat_with_image + load_image/encode_base64/prepare_image/_preprocess_image 扩展点)；LLMFactory 扩展 register_vision + create_vision_llm 独立 vision registry；Fake Vision LLM 验证路由 |
| B9 | Azure Vision LLM 实现 | [x] | 2026-08-12 | AzureVisionLLM(BaseVisionLLM) 可注入 transport；Azure URL/headers(api-key)；OpenAI Vision 消息格式(text+image_url data URL)；图片预处理 Pillow 可用时压缩/不可用优雅降级；auth/network/malformed 错误可读且不泄露 api_key；chat_text 继承可用 |

#### 阶段 C：Ingestion Pipeline MVP

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|---------|---------|------|---------|------|
| C1 | 定义核心数据类型/契约（Document/Chunk/ChunkRecord） | [x] | 2026-08-16 | Document/Chunk/ChunkRecord + ImageRef 六字段规范；[IMAGE: {id}] 占位符工具（make_image_id/image_placeholder/iter_image_placeholders/parse_image_refs）；to_dict/from_dict/to_json 契约稳定且忽略未知字段；metadata 增量扩展兼容；core/__init__ 统一 re-export |
| C2 | 文件完整性检查（SHA256） | [x] | 2026-08-16 | FileIntegrityChecker 抽象 + SQLiteIntegrityChecker 默认实现；compute_sha256 流式 64KB 分块；should_skip 仅 status=success 跳过；mark_success/mark_failed 幂等覆盖（success 清 error_msg，failed 保留旧 file_path）；WAL + busy_timeout + 写锁支持单实例多线程与多连接并发；ingestion_history 表五字段契约；默认 data/db/ingestion_history.db 自动建目录 |
| C3 | Loader 抽象基类与 PDF Loader | [x] | 2026-08-16 | BaseLoader 抽象 + PdfLoader：engine=auto 优先 MarkItDown（未安装/失败自动回退 pypdf 并告警）；pypdf 硬依赖（文本提取 + XObject 图片遍历，无需 Pillow）；图片存 {images_root}/{doc_hash}/{image_id}.png（JPEG 直存，RGB/Gray 8bit 内置 PNG 编码器）；[IMAGE: {id}] 占位符按页落位 + text_offset/text_length 精确；单图/整体失败均降级不阻塞；metadata 含 source_path/doc_type/sha256/page_count(/title)；新增 pypdf>=4.0 依赖 |
| C4 | Splitter 集成（调用 Libs） | [x] | 2026-08-16 | DocumentChunker 适配层：确定性 ID {doc_id}_{index:04d}_{hash8}（唯一且可复现）；metadata 继承 + chunk_index；source_ref 溯源；图片引用按需分发（扫描 [IMAGE: id] 占位符，chunk 级 images 子集 + image_refs 列表，无占位符移除字段，幽灵引用 images 为空但 image_refs 保持一致）；SplitterFactory 配置驱动（chunk_size 改变切分数实测）；Chunk 类型契约 round-trip |
| C5 | Transform 基类 + ChunkRefiner | [x] | 2026-08-16 | BaseTransform 抽象 + ChunkRefiner（规则去噪：控制字符/HTML注释/页眉页脚/分隔线/空白折叠，代码块围栏逐字保留且幂等；LLM 重写：{text} prompt 模板去#注释+内置 fallback）；降级链：LLM 异常/空响应/工厂创建失败 → 规则结果 + refine_fallback 原因，单 chunk 异常保留原文；metadata refined_by 标记；ingestion.chunk_refiner.use_llm 配置开关（默认 false）；TraceContext 最小实现（uuid+record_stage+to_dict）；28 单测 + fixtures 8 场景 + 集成测试（真实 LLM 用例 RAG_LLM_INTEGRATION=1 启用，降级用例已验证） |
| C6 | MetadataEnricher | [x] | 2026-08-16 | 规则模式：title 取首个 Markdown 标题/首行/source stem 兜底、summary 取前若干句（≤160 字符截断+省略号）、tags 取高频拉丁词（停用词过滤，CJK 补"中文"标签，空内容 doc_type 兜底），三字段保证非空且确定性；LLM 模式：内置 prompt（{text} 占位符）要求严格 JSON，解析兼容代码围栏、字段类型校验，非法/异常/init 失败降级规则结果 + enrich_fallback 原因；enriched_by 标记（rule/llm）；单 chunk 异常保留原文；ingestion.metadata_enricher.use_llm 配置开关（默认 false）+ settings.yaml 示例；MetadataEnricherConfig 并入 IngestionConfig；trace transform 打点（llm_enriched/fallback 计数）；21 单测一次通过 |
| C7 | ImageCaptioner | [x] | 2026-08-16 | 启用模式（ingestion.image_captioner.enabled）：经 LLMFactory.create_vision_llm 逐图调用 chat_with_image，prompt 取 image_captioning.txt（{image_context} 替换为 chunk 上下文，800 字符截断；文件缺失回退内置模板），caption 写入 metadata.image_captions{image_id: caption}；降级模式（默认禁用/工厂失败/文件缺失/路径记录缺失/LLM 异常/空响应）：保留 image_refs+images 原样，标记 has_unprocessed_images=True（工厂失败附 image_caption_fallback 原因），全部成功不打标记；无图 chunk 原样透传；单 chunk 异常隔离；trace 打点（captioned/degraded 计数）；prompt 可注入测试；15 单测 |
| C8 | DenseEncoder | [x] | 2026-08-16 | 包装 BaseEmbedding（EmbeddingFactory 按 settings.embedding.provider 创建，测试可注入 fake）：encode(chunks) 依序提取 chunks.text 单批送入 embed，返回等长向量列表；fail-fast 契约校验 DenseEncodingError：向量数≠chunk 数/维度不一致/空向量；空 chunks 返回 [] 且不触发底层调用；底层异常原样传播；dimension 代理属性；trace embedding 打点（chunks/dimension/elapsed_ms）；12 单测 |
| C9 | SparseEncoder | [x] | 2026-08-16 | SparseDocStats(chunk_id/term_freqs/length) 输出契约，可直接供 C11 BM25Indexer 推导 df 与倒排表；默认分词 default_tokenize：拉丁词 [a-z0-9]+ 小写化 + CJK 逐字 unigram（与 D 阶段 QueryProcessor 粒度对齐），tokenizer 可注入；空文本明确行为：term_freqs={} 且 length=0 不抛错（空白文本同）；encode_chunks 顺序对齐 chunk_id；to_dict/from_dict 往返（容忍多余键）；确定性；trace embedding 打点（total_terms/unique_terms）；14 单测 |
| C10 | BatchProcessor | [x] | 2026-08-16 | split 契约：batch_size=2 对 5 chunks 分 [2,2,1] 三批、保序不丢不重、空输入 []；run 契约：逐批驱动 encode_fn 按输入顺序拼接，结果数守恒校验（不匹配 RuntimeError），编码异常原样传播；每批耗时记录进 trace（batch_timings_ms，空跑不打点）；默认批大小取 ingestion.batch_size（yaml 可配，正整数校验，显式参数优先，无 settings 时 32）；非法批大小 ValueError；run_dense/run_sparse 便捷路径（对接 C8/C9）；18 单测 |
| C11 | BM25Indexer（倒排索引+IDF计算） | [x] | 2026-08-16 | 消费 C9 SparseDocStats 构建倒排索引（spec 结构 {term:{idf,postings:[{chunk_id,tf,doc_length}]}}）；IDF=log((N-df+0.5)/(df+0.5)) 自然对数精确公式（df>N/2 可为负、df=N/2 恰为 0，不截断不加 1）；Okapi BM25 打分（k1=1.5/b=0.75 可配，校验 k1≥0、b∈[0,1]，qtf 查询词频乘子）；query(keywords,top_k)→[{chunk_id,score}] 降序稳定排序（同分按 chunk_id 升序），与 D3 SparseRetriever 契约对齐；build 清空重建/add 增量（同 chunk_id 替换、df=0 死词清理、IDF 随 N 全量重算，跨批次重复=幂等重摄取、单批内重复 fail-fast BM25IndexError）；空 term_freqs 文档不入索引不计入 N；save/load JSON（version=1、原子写 tmp+os.replace、自动建目录、容忍多余键、load 采纳文件 k1/b 并校验 num_docs/total_length 与 postings 一致），默认 data/db/bm25/index.json；avgdl/df/get_postings 只读视图；trace storage 打点（mode/docs/replaced/elapsed_ms，空跑不打点）；35 单测（含 C9→C11 链路与负 IDF 排序稳定性） |
| C12 | VectorUpserter（幂等upsert） | [x] | 2026-08-17 | 消费 C8 DenseEncoder 向量输出（chunks+vectors 平行等长序列）组装 VectorRecord（id+vector+text+完整 metadata 浅拷贝），经 VectorStoreFactory（settings.vector_store.backend 路由，测试可注入 fake store）调 BaseVectorStore.upsert() 分批幂等写入；幂等键=chunk.id（spec 公式 hash(source_path+chunk_index+content_hash[:8]) 由 C4 DocumentChunker 落地为 {doc_id}_{index:04d}_{content_hash[:8]}，与 C11 SparseDocStats.chunk_id/D3 get_by_ids 共用 id 空间，故不重新派生：同 chunk 重跑 id 不变、内容变更 content_hash 变化 id 变更、后端按 id 覆盖无重复记录）；批切分按 ingestion.batch_size（显式参数>settings>默认32，保序 [2,2,1] 不丢不重）；fail-fast VectorUpsertError（数量不等/空向量/维度不一致/非数值或 bool 分量/单次调用内重复 chunk_id/单 Chunk 实例/store 返回非法计数非 int 或超 [0,批大小]；跨调用重复=幂等重摄取不报错；store 异常原样传播）；空输入零统计不触库不打点；UpsertSummary(chunks/batches/written/ids 与输入同序)；trace storage 打点（method=vector_upserter，chunks/batches/batch_size/written/elapsed_ms，trace 透传 store）；33 单测（含 C8→C12 链路 fake HashEmbedding 端到端与幂等重跑） |
| C13 | ImageStorage（图片存储+SQLite索引） | [x] | 2026-08-17 | put(image_id,data,collection?,doc_hash?,page_num?,ext?,trace?) 图片字节原子落盘（tmp+os.replace）+ INSERT OR REPLACE 登记 SQLite 索引（spec 精确表结构 image_id PK/file_path/collection/doc_hash/page_num/created_at + idx_collection/idx_doc_hash，WAL+busy_timeout=5000+threading.Lock 复用 C2 SQLiteIntegrityChecker 模式，构造失败自动关连接）；目录布局统一 spec data/images/{collection}/ 与 C3 PdfLoader data/images/{doc_hash}/：collection 优先、缺省回退 doc_hash（对齐 DEV_SPEC 数据目录注释“{collection}/（实际存储在 {doc_hash}/ 子目录下）”）、两者皆缺 fail-fast；幂等性=image_id PRIMARY KEY 不重新派生（对齐 C3 make_image_id {doc_hash}_{page}_{seq}，与 ImageRef.id/[IMAGE: 占位符]/C4 image_refs 同一 id 空间）：重复 put 覆盖文件+替换行不产生重复行、目录变更的重复 put 清理旧文件防孤儿、created_at 按库内 datetime('now') 回读；扩展名默认 .png 自动补前导点（ImageRef.path 约定）；get(image_id) miss 返回 None；list_images(collection?,doc_hash?,limit?) 按 image_id 升序确定性排序（支持按 collection 批量查询）+count 过滤计数；delete 单张（行+文件，不存在返回 False 无副作用）、delete_images(col,doc_hash) 批量删除（至少一个过滤条件防全表误删，对齐 E 阶段 DocumentManager delete_images 契约）；fail-fast ImageStorageError（空/非 str id、含路径分隔符或 ./.. 的 id/collection/doc_hash/ext 防目录逃逸、空或非 bytes 数据、page_num 非非负 int、limit 非正 int）；caption 不入索引表（spec 表结构无此列——C7 caption 存 chunk metadata.image_captions，图片↔chunk 关联由 C4 占位符/image_refs 表达，索引保持最小契约）；trace storage 打点（method=image_storage，action=put（replaced 标记）/delete_images，elapsed_ms）；默认 data/images + data/db/image_index.db；48 单测（含关闭重开持久化与幂等重跑） |
| C14 | Pipeline 编排（MVP 串起来） | [x] | 2026-08-17 | IngestionPipeline.run(source_path, collection="default", on_progress?, *, force=False, trace?) -> IngestionResult（source_path/collection/doc_hash/status(success|skipped)/chunks/images/written/trace_id/elapsed_ms + to_dict，对齐 03-tech-stack 5.3 回调契约与 C15 --force/--collection 入参）；串行六阶段 integrity→load→split→transform→encode→store：C2 compute_sha256+should_skip 未变更且非 force 零成本跳过（不再 load/编码/触库，trace integrity 打 skipped 标记）；load 按扩展名路由（.pdf→PdfLoader(images_root=ImageStorage.images_root) 统一图片根目录，未知扩展 fail-fast 清晰异常，可注入 BaseLoader 支持新格式/测试）；split 用 C4 DocumentChunker 确定性 id 并把本次 collection 写入每个 chunk metadata["collection"]（供 D 阶段 BaseVectorStore.query(filters={"collection":...}) 过滤）；transform 依次 ChunkRefiner→MetadataEnricher→ImageCaptioner（C5-C7 内部降级：LLM/Vision 关闭时 refined_by/enriched_by=rule、含图 chunk 标 has_unprocessed_images=True 仍完成摄取）；encode 经 C10 BatchProcessor 分批驱动 C8 DenseEncoder + C9 SparseEncoder（保序拼接，空 chunks 零调用零触库）；store 三路持久化：BM25 已有索引文件则 load 后增量 add（同 chunk_id 替换）否则空索引 add（等价重建）再 save 原子落盘、向量 C12 幂等 upsert（chunk.id 键）、图片读 Loader 落盘字节经 C13 put 登记 SQLite 索引（目录键取 doc_hash，对齐 C3 布局与 C13『{collection}/ 实际存于 {doc_hash}/』注记；缺文件仅告警跳过不阻塞）；失败语义：各阶段异常统一包装 IngestionPipelineError（携带 stage 属性 + "stage 'x' failed for path" 信息），已知 file_hash 时 mark_failed（下次可重跑）+ trace error 打点；组件全部可构造注入（fake loader/embedding/store 即可离线单测），未注入惰性按 Settings 创建（首个 run 初始化一次，构造 Pipeline 零 IO）；注入判定必须 is not None（BM25Indexer 定义 __len__，空索引 falsy，or 会静默丢弃注入实例——已用 pick() 固化并注释）；on_progress(stage,current,total) 按 load/split/transform(1..3)/embed(按批 2*ceil(n/batch_size))/upsert(1..3) 上报，None 时行为一致；trace 顺序：pipeline 级 method=pipeline 标记 integrity/load/split/transform/embed/store 依序 + 组件 transform/embedding/storage 打点交错可验；无新增配置项（完全由现有 ingestion.*/splitter/vector_store 配置驱动）；测试落位 tests/unit/test_ingestion_pipeline.py（13 单测：spec 原定 tests/integration/ 需真实 Chroma/嵌入网络，按离线确定性纪律改为纯单元——边界 fake（Loader/Embedding/VectorStore）+ 其余 C2-C13 真实组件落 tmp_path，覆盖 happy-path 三路产物/未变更跳过/force 幂等替换/文件变更增量/LLM 关闭降级标记/空文档零触库/加载与 integrity 失败/未知扩展/trace 顺序/on_progress 序列/to_dict），真实 fixtures 端到端归 C15 e2e（tests/e2e/test_data_ingestion.py） |
| C15 | 脚本入口 ingest.py | [x] | 2026-08-17 | scripts/ingest.py CLI 入口（离线可用，与根 main.py 同款 sys.path shim，支持 `python scripts/ingest.py` 直接运行；用法对齐 I5 验收形态 `--path ... --collection test`）：--path（必填，文件或目录；目录 rglob 递归收集受支持扩展名——与 pipeline Loader 路由一致且单测含 drift guard，suffix 小写比较大小写不敏感，路径排序保证确定性，非支持扩展名静默忽略）/--collection（默认 default，透传 run 写入 chunk metadata 供 D 阶段过滤）/--force（透传强制重摄取，幂等替换）/另加 --config（默认仓库根 config/settings.yaml 绝对路径，不受 CWD 影响）与 -v/--verbose（打印 on_progress 逐阶段进度，缺省静默只出逐文件行+汇总块）；可测试性：解析/收集/聚合/渲染全部为可导入纯函数 build_parser/parse_args/collect_files/run_ingest/aggregate/render_summary/build_pipeline/main(argv, *, pipeline=None, out=print) -> int（scripts 非 package，单测经 importlib 按路径加载，注入已构建 pipeline 即零子进程零网络）；执行语义：逐文件 pipeline.run，单文件 IngestionPipelineError 映射为 failed FileOutcome 不中断批次（其余文件照常摄取），IngestSummary 聚合 ingested/skipped/failed/chunks/images/written（失败文件不计产物）+ 人读汇总块（失败文件逐条列错误）；退出码：0=全部成功或跳过（含空目录，打印 no supported files found）/1=存在失败文件/2=用法或配置错误（路径不存在、单文件扩展名不支持、ConfigError/FileNotFoundError）；无新增配置项（完全复用现有 settings）；测试：tests/unit/test_ingest_entry.py 26 单测（解析默认值与 --path 必填、collect_files 单文件/递归排序/扩展名过滤/空目录/不存在/不支持、聚合数学、渲染、退出码矩阵、单文件失败隔离、collection/force/on_progress 透传、SUPPORTED_EXTENSIONS drift guard，fake pipeline 零网络零持久化）+ tests/e2e/test_data_ingestion.py（承接 C14 遗留 spec e2e：真 PdfLoader 解析内置极简 PDF 生成器（与 C3 loader 契约测试同法，仓库无预置 PDF fixture）产出的 simple/with_images 字节流，真 DocumentChunker/规则 transform（LLM 关闭）/BatchProcessor+SparseEncoder/BM25Indexer JSON 落盘/SQLiteIntegrityChecker/ImageStorage SQLite/ChromaStore 嵌入式 PersistentClient（离线可用）全部持久化落 tmp_path，唯一注入确定性哈希 embedding——真 embedding provider 需网络；覆盖：CLI 目录摄取全链路产物断言（chroma count>0 且 query 命中并全带 metadata.collection 过滤值、BM25 索引落盘重载 num_docs 与向量数一致、图片 SQLite 登记+字节文件存在、两文件 trace id）、重跑（全新组件自磁盘构建）未变更全 skipped 且向量计数不增长、--force 重摄取幂等替换、坏 PDF 映射 failed 退出码 1 且好文件仍完整落库、单文件 --path）；真 `python scripts/ingest.py` 子进程 + 真 embedding provider 冒烟按 RAG_LLM_INTEGRATION 先例以 RAG_E2E_INTEGRATION=1 门控默认跳过（cwd 切 tmp 使 data/db 产物落临时目录），默认套件离线全绿（591 通过 2 跳过，另一跳过为既有 RAG_LLM_INTEGRATION 门控）） |

#### 阶段 D：Retrieval MVP

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|---------|---------|------|---------|------|
| D1 | QueryProcessor（关键词提取 + filters） | [x] | 2026-08-17 | core/query_engine/query_processor.py QueryProcessor（规则关键词提取 + filters 结构解析，零外部依赖）：分词与 C9 SparseEncoder.default_tokenize 精确对齐（拉丁词 [a-z0-9]+ 小写化 + CJK 逐字 unigram，token 顺序拉丁在前 CJK 在后；core 层不 import ingestion，本地实现 + 单测 drift guard 双向锁死防漂移，保证 BM25 查询词与索引词同一 token 空间）；停用词过滤 DEFAULT_STOPWORDS（英文虚词 + 中文常用虚字保守集合，实体/动词一律保留），stopwords/tokenizer 均可注入整体替换；非空保证：全部 token 被过滤时回退原始 token（"the and of" 类整句虚词查询保底可用），保序去重（dict.fromkeys 按首次出现）确定性输出；filters 可空实现：process(query, *, filters=None) 不做自然语言约束抽取（后续演进），None→{}、类型校验（非 dict TypeError）后浅拷贝透传（键集合无白名单，值语义归 D2/D3 检索器），ProcessedQuery.filters 恒为 dict；fail-fast QueryProcessingError(ValueError)：空/纯空白查询、纯标点无可提取 token 查询，非 str 查询/非 dict filters 抛 TypeError（信息可读）；core/types.py 新增 ProcessedQuery dataclass（query 原样保留供 Dense 路用/keywords 供 D3 喂 BM25Indexer.query/filters），to_dict/from_dict（忽略未知字段）/to_json/from_json 契约与既有类型一致，core/__init__ 统一 re-export，query_engine/__init__ 导出 QueryProcessor；trace 打点 query_processing 阶段（method=query_processor，details 含 keywords/keyword_count/filter_keys/elapsed_ms，对齐 F3 约定）；无新增配置项（settings 参数保留给后续查询扩展如同义词表）；测试 tests/unit/test_query_processor.py 34 单测（英文/CJK/混合提取、小写化、保序去重、全停用词回退、实词保留、确定性、原文 verbatim 保留、空/空白/纯标点/非 str fail-fast、filters 默认空 dict/浅拷贝互不影响/非 dict 报错/任意值透传、序列化往返与未知键容忍、注入点、trace 打点、C9 分词 drift guard 参数化 6 样例、C9→C11 链路真 SparseEncoder+BM25Indexer 命中验证）；全套件 623 通过 2 跳过（既有门控）|
| D2 | DenseRetriever（调用 VectorStore.query） | [x] | 2026-08-17 | core/query_engine/dense_retriever.py DenseRetriever（query embedding + VectorStore 向量检索的语义召回编排）：embedding_client/vector_store 均可注入（注入判定 is not None，未注入经 EmbeddingFactory/VectorStoreFactory 按 settings 创建，导入 libs.embedding/libs.vector_store 包触发 provider 注册）；retrieve(query, top_k, filters=None, trace=None) -> List[RetrievalResult] 内部流程 embed([query]) → store.query(vector, top_k, filters) → QueryResult→RetrievalResult(chunk_id/score/text/metadata) 字段映射（score 统一 float 化）；fail-fast：非 str/空查询与非法 top_k/filters 抛 TypeError/ValueError，embedding 单条 query 输出≠1 个向量或空向量抛 DenseRetrievalError（provider 契约破坏），store 异常原样传播（降级决策归 D5）；filters 原样透传（Chroma where Pre-filter，值语义归 store）；trace 打点 dense_retrieval 阶段（method=dense_retriever，details 含 top_k/filter_keys/hits/elapsed_ms，对齐 F3 约定）；core/types.py 新增 RetrievalResult dataclass（chunk_id/score/text/metadata，与 C4/C11/C12 共用 chunk id 空间，score 语义由产出阶段决定恒为越高越相关），to_dict/from_dict（忽略未知字段）/to_json/from_json 契约与既有类型一致，构造期校验（空 id/非数值或 bool score/非 str text/非 dict metadata fail-fast），core/__init__ 统一 re-export，query_engine/__init__ 导出 DenseRetriever；ChromaStore.query 自 B7.6 起已携带 text（All-in-One 存储，命中即可取回无需查库），D2 验收以真 Chroma 集成测试断言（含 filters 场景）无需代码改动；无新增配置项；测试 tests/unit/test_dense_retriever.py 30 单测（编排顺序与参数、字段映射、score float 化、filters 透传、空库空结果、fail-fast 参数化（空白/非 str 查询、6 种非法 top_k、非 dict filters）、embedding 契约破坏（2 向量/空向量）、store 异常传播、trace 打点、RetrievalResult 序列化往返与未知键容忍与构造校验参数化、core 包导出、工厂默认构造路由）+ tests/integration/test_chroma_store_roundtrip.py 补 2 用例（query 返回含 text+metadata、filters 命中保留 text）；全套件 655 通过 2 跳过（既有门控）|
| D3 | SparseRetriever（BM25 查询） | [x] | 2026-08-17 | core/query_engine/sparse_retriever.py SparseRetriever（BM25 打分 + 向量库按 id 取回正文的稀疏召回编排）：bm25_indexer/vector_store 均可注入（注入判定 is not None；vector_store 未注入经 VectorStoreFactory 创建；bm25_indexer 未注入创建 BM25Indexer(settings) 并尽力加载持久化索引 data/db/bm25/index.json（与 C14 Pipeline 同一路径约定），FileNotFoundError 时空内存索引起步查询返回空不报错，注入实例原样使用不做 load）；retrieve(keywords, top_k, trace=None) -> List[RetrievalResult] 内部流程 bm25_indexer.query(keywords, top_k) → [{chunk_id, score}] → vector_store.get_by_ids(chunk_ids) → 合并 score 与 text/metadata（BM25 score 降序保持，score float 化）；id 空间对齐：BM25 倒排 chunk_id 与 C12 upsert 键共用（C4 确定性 ID），All-in-One 存储命中即可取回，索引命中但库中缺失的死条目跳过（告警日志不阻塞）；空 keywords 直接返回空结果零触索引零触库；fail-fast：非列表/含非 str 或空串项的 keywords 抛 TypeError，非法 top_k 抛 ValueError；trace 打点 sparse_retrieval 阶段（method=sparse_retriever，details 含 keyword_count/top_k/hits/merged/skipped/elapsed_ms，对齐 F3）；BaseVectorStore 新增 get_by_ids(ids) 抽象方法（返回 {id,text,metadata} dict 列表，缺失 id 跳过不占位，顺序与请求一致重复去重），ChromaStore 实现经 collection.get（Chroma 拒绝重复 id，先按首次出现去重再请求，非列表/空串 id fail-fast ValueError），既有 3 处测试 fake（test_vector_store_contract/test_vector_upserter/test_ingestion_pipeline）补齐 get_by_ids 实现满足扩展契约；无新增配置项；测试 tests/unit/test_sparse_retriever.py 21 单测（真 BM25Indexer+fake store：命中合并 score/text/metadata、BM25 降序、死条目跳过、无命中/空 keywords 零触库、top_k 限制、get_by_ids 收到按 score 序命中 id 集、fail-fast 参数化（非列表/非 str 项/6 种非法 top_k）、trace 打点、默认构造无文件空起步/有文件自动加载（monkeypatch.chdir tmp）、注入 indexer 原样使用）+ tests/integration/test_chroma_store_roundtrip.py 补 5 用例（get_by_ids 返回 text+metadata、请求序保持+缺失跳过、重复 id 去重、空输入、输入校验）；全套件 681 通过 2 跳过（既有门控）|
| D4 | RRF Fusion | [x] | 2026-08-17 | core/query_engine/fusion.py RRFFusion + 纯函数 rrf_fuse_rankings（排名倒数融合，不依赖各路分数绝对值只看排名）：公式 Score(id)=Σ_routes 1/(k+Rank_route(id))，Rank 从 1 起、k 平滑常数默认 60（显式参数 > settings.retrieval.rrf_k > 默认，config/settings.yaml 补 rrf_k: 60 示例，构造期校验非负 int 非 bool）；纯函数只吃 id 排名列表便于独立单测融合数学（同一路内重复 id 只按首次出现排名计一次、空输入空输出）；RRFFusion.fuse(dense, sparse, top_k=None, trace=None) -> List[RetrievalResult] 组装 RetrievalResult：score 为 RRF 融合分数（round 6 位），text/metadata 取该 chunk 首次出现路由载荷（dense 先于 sparse，dense 路 Pre-filter 后载荷更可信）且 metadata dict 拷贝防跨突变；确定性输出：score 降序、同分按 chunk_id 升序打破平局，同输入重复调用结果完全一致；top_k 可选截断（正整数校验），dense/sparse 非列表 TypeError fail-fast；trace 打点 fusion 阶段（method=rrf，details 含 k/dense/sparse/fused/elapsed_ms，对齐 F3）；query_engine/__init__ 导出 RRFFusion/rrf_fuse_rankings/DEFAULT_RRF_K；测试 tests/unit/test_fusion_rrf.py 36 单测（纯函数手工基准值（对称两路同分、双路命中胜单路、k=0、重复 id 计一次、空输入、非法 k 参数化）、类组装（RRF 降序、同分 id 升序、payload dense 优先/sparse 兜底、单路保序+RRF 分值、双空、确定性、top_k 截断、metadata 拷贝隔离）、k 解析三级优先（无 settings 默认 60/yaml 42/replace 覆盖/显式胜 settings）+ yaml 配置驱动断言、fail-fast（非列表路由/非法 k/非法 top_k 参数化）、trace 打点）；全套件 717 通过 2 跳过（既有门控）|
| D5 | HybridSearch 编排 | [x] | 2026-08-17 | core/query_engine/hybrid_search.py HybridSearch（QueryProcessor + DenseRetriever + SparseRetriever + RRFFusion 完整编排）：search(query, top_k=None, filters=None, trace=None) -> List[RetrievalResult] 内部流程 process（D1，filters 透传解析）→ 双路召回（dense 用 pq.query+top_k_dense+filters Pre-filter 透传 store；sparse 用 pq.keywords+top_k_sparse，BM25 无 metadata 过滤能力）→ fusion.fuse（两路均有结果时 RRF；单路降级时保留存活路原始分数与排序，不做单路重打分）→ _apply_metadata_filters Post-filter 兜底（等值匹配 + 缺失字段宽松包含 missing->include 防 metadata 质量不稳误杀召回，硬约束前置 Dense 路）→ [:top_k_final]；降级策略：任一路异常捕获记录 last_route_failures 降级另一路不中断查询（告警日志），两路均失败抛 HybridSearchError（不可静默返回空），两路均空（无数据非失败）友好返回 []；组件全可注入（注入判定 is not None，缺省按 Settings 创建 QueryProcessor/DenseRetriever/SparseRetriever/RRFFusion），top_k None 取 retrieval.top_k_final、显式参数校验正整数；中间结果暴露 last_dense_results/last_sparse_results/last_fused_results/last_route_failures（供 D7 --verbose 各阶段中间产物展示，每次 search 重置）；trace：组件各自记录 query_processing/dense_retrieval/sparse_retrieval/fusion 阶段（F3 约定）+ 本类记录 hybrid 编排阶段（method=hybrid_search，details 含 routes 计数/degraded/fused/filtered/returned/top_k/elapsed_ms），trace None 时内部新建；query_engine/__init__ 导出 HybridSearch/HybridSearchError；无新增配置项（retrieval.top_k_* 驱动）；测试 tests/integration/test_hybrid_search.py 18 用例（离线真实组件：真 ChromaStore 嵌入式 tmp 持久化 + 真 BM25Indexer + 真 QueryProcessor/RRFFusion + 确定性 bag-of-words BaseEmbedding 使 Dense 相似度=词重合度可断言）：混合 Top-K 含 text+metadata 且预期 chunk 居首（azure authentication 双路命中）、top_k 限制与默认 top_k_final、纯关键词查询 BM25 强路径仍召回、非法 top_k/空查询 fail-fast、filters collection 收窄（Pre+Post 双路生效）与全排除空结果与缺失字段宽松包含（_apply_metadata_filters 单元级直测）、Dense 失败降级 sparse/Sparse 失败降级 dense（last_route_failures 断言 + 存活路分数保留）、双路失败 HybridSearchError、空库空索引友好空结果（failures 为空）、trace 五阶段齐全（query_processing/dense_retrieval/sparse_retrieval/fusion/hybrid）、last_* 中间结果与 hybrid 阶段 details 断言）；全套件 735 通过 2 跳过（既有门控）|
| D6 | Reranker（Core 层编排 + Fallback） | [x] | 2026-08-17 | core/query_engine/reranker.py Reranker（接入 libs.reranker 可插拔后端的 Core 层精排编排 + 稳定回退）：编排 RetrievalResult → RerankCandidate → backend.rerank → RetrievalResult，重排只改顺序与 score（后端分数 float 化），text/metadata 保留融合阶段原始载荷（metadata dict 拷贝防跨突变）；backend 可注入（缺省 RerankerFactory.create(settings) 按 rerank.backend 路由，导入 libs.reranker 包触发注册），backend_name 属性暴露配置名；fallback 双通道（D6 验收：后端异常不影响最终返回且标记 fallback=true）：①backend.rerank 抛异常（网络/超时/解析失败）→ 返回输入原序原分（等价回退 RRF 融合排名，系统可用性优先）+ last_fallback=True + last_error 记录异常（告警日志）；②后端内部回退透传（B7.7/B7.8 契约：LLM 解析失败/CrossEncoder scorer 异常不抛错而是原序返回并置自身 last_fallback=True）→ getattr 检测透传标记；last_fallback/last_error 每次调用重置（D7 CLI 逐次可读）；防御：后端返回输入中不存在的 id 跳过、重复 id 只出一次、合法截断（CrossEncoder top_m，长度<=输入契约）尊重不强行补齐；空候选不触后端直接空结果；fail-fast：非 str/空查询与非列表 results 抛 TypeError/ValueError；trace 打点 rerank 阶段（method=reranker，details 含 backend/candidates/returned/fallback/error?/elapsed_ms，对齐 F3 约定）；config/prompts/rerank.txt 沿用 B7.7 无改动（仅 LLM 后端启用时使用）；query_engine/__init__ 导出 Reranker；无新增配置项；测试 tests/unit/test_reranker_fallback.py 15 单测（可脚本化 fake 后端：重排+分数更新+载荷保留、候选载荷透传、空候选零触后端、NoneReranker 恒等（backend=none 默认构造）、异常回退原序+fallback 标记+last_error、内部回退标记透传、标记逐次重置（失败后换健康后端清零）、未知 id 跳过、top_m 截断尊重、重复 id 去重、fail-fast（空白/None 查询/非列表）、trace 成功与回退阶段（error 字段）、backend_name）；全套件 750 通过 2 跳过（既有门控）|
| D7 | 脚本入口 query.py（查询可用） | [x] | 2026-08-17 | scripts/query.py CLI 入口（离线可用，与 scripts/ingest.py 同款 sys.path shim 支持 `python scripts/query.py` 直接运行；用法对齐 spec 验收形态 `--query "如何配置 Azure？"`）：--query 必填/--top-k（默认 None 透传取 settings retrieval.top_k_final）/--collection（构造 filters={"collection":...} 走 Dense Pre-filter + Post-filter 兜底）/--verbose（各阶段中间结果）/--no-rerank（跳过精排）/--config（默认仓库根绝对路径不受 CWD 影响）；内部流程：加载 Settings（search/reranker 任一未注入且需要时加载一次复用）→ build_search=HybridSearch(settings)（组件内部按需创建）→（除非 --no-rerank）build_reranker=Reranker(settings)（backend 由 rerank.backend 驱动，cross_encoder 无 scorer 时构造合法、rerank 期异常由 D6 双通道 fallback 回退融合排名）→ run_query：TraceContext 贯穿 search+rerank，QueryOutcome 数据类承载 results/dense/sparse/fused/rerank_applied/rerank_fallback/rerank_backend/trace_id + to_dict；默认输出 Top-K（[序号] score=四位小数 + source_path(+page) + 单行文本摘要 160 字符折叠截断省略号），verbose 额外输出 dense/sparse/fusion 三段召回（chunk_id+score）与 rerank 段（backend+fallback 或 skipped）；无命中友好提示"未找到相关文档，请先运行 scripts/ingest.py 摄取数据"（退出码 0）；退出码语义：0=查询完成（含无命中）/1=检索链路失败（HybridSearchError 等运行时异常）/2=用法或配置错误（argparse 缺 --query、空/纯空白查询 QueryProcessingError、参数 ValueError/TypeError、配置缺失 ConfigError、组件初始化失败含 Reranker 初始化失败提示可用 --no-rerank 规避）；可测试性：build_parser/parse_args/run_query/render_result_line/render_no_results/render_verbose_sections/main(argv, *, search=..., reranker=..., out=print) 全部可导入纯函数，注入 fake search/reranker 即零网络零子进程零真实持久化；D7 期间修正 D5 HybridSearchError 语义：仅在 Dense/Sparse 两路均抛异常时抛错，一路失败+存活路零命中降级为合法空结果（对齐 spec"任一路径失败降级到单路结果"，D7 CLI 无 API key/无数据环境实测该路径给出友好提示而非误报链路故障，含回归测试）；无新增配置项；测试 tests/unit/test_query_entry.py 25 单测（解析必填与默认值与全旗标、缺 --query 退出码 2、run_query 渲染 Top-K 行与 collection filter 透传与 top_k 透传、无命中友好提示、reranker 应用顺序变化/fallback 标记/空结果跳过精排、verbose 段头（dense/sparse/fusion/rerank backend+fallback/skipped）、渲染折叠截断与缺 source/page 兜底、QueryOutcome.to_dict、main 退出码矩阵（0 注入成功/0 无命中/0 默认构建 reranker 走 tmp 配置/2 空查询/2 非法参数/2 配置缺失/1 HybridSearchError、--no-rerank 全注入时零 settings 加载 monkeypatch 断言）、真 Chroma+真 BM25+确定性 bag-of-words embedding 注入 main 的离线端到端（--no-rerank 命中 a.pdf Top-1））+ test_hybrid_search.py 补 1 用例（一路失败+存活路零命中返回空不抛错）；真 `python scripts/query.py` 冒烟：--help 正常、无 API key/无数据 --no-rerank 与默认 rerank 路径均友好提示退出码 0（dense 降级 sparse 告警可见）；全套件 776 通过 2 跳过（既有门控）|

#### 阶段 E：MCP Server 层与 Tools

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|---------|---------|------|---------|------|
| E1 | MCP Server 入口与 Stdio 约束 | [x] | 2026-08-17 | MCPServer stdio 主循环（零外部依赖，官方 mcp SDK 未安装故按协议手工实现 JSON-RPC 消息收发，E2 ProtocolHandler 落地完整解析）：stdout 每行仅输出一个 JSON-RPC 消息（响应 flush 后即写，绝不混入日志），日志全走 observability.logger（stderr）；initialize 握手返回 protocolVersion=2025-06-18 + capabilities.tools 能力声明 + serverInfo(name/version)；notifications/initialized 等无 id 通知静默无响应；shutdown 请求哨兵优雅退出（无响应）；stdin EOF 正常退出码 0；非法 JSON/非对象消息返回 -32700 Parse error；handler 异常防污染通道返回 -32603 且不泄露堆栈；message_handler 注入点供 E2 接入完整 tools/list、tools/call 与错误码规范；未接入前其余方法占位 -32601；serve(stdin?, stdout?) 可注入流便于测试，python -m mcp_server.server 可直接运行；测试 tests/integration/test_mcp_server.py 5 用例（真子进程 PYTHONPATH=src 启动：initialize 握手字段断言、通知不产生响应且不污染后续请求、EOF 退出 stderr 含启动/停止日志且 stdout 仅一行 JSON-RPC、E2 前占位方法 -32601、shutdown 干净退出 stdout 仅剩 initialize 响应）；全套件 781 通过 2 跳过（既有门控） |
| E2 | Protocol Handler 协议解析与能力协商 | [x] | 2026-08-17 | ProtocolHandler 独立可测类（不依赖 socket/进程，handle_message 吃 dict 返回响应 dict 或 None）：消息级校验（非对象/缺 jsonrpc/非 "2.0"/缺 method/非 str method → -32600 Invalid Request；params 非 object → -32602）；通知（无 id 键）一律静默返回 None（含未知方法通知，JSON-RPC 语义）；id:null 视为请求合法响应；三类核心方法路由：initialize → protocolVersion=2025-06-18 + capabilities.tools{listChanged:false} 能力协商 + serverInfo(name/version 可自定义注入)；tools/list → 已注册 ToolDefinition 的 name/description/inputSchema 列表（无 tool 时 {"tools":[]} 合法状态）；tools/call → params.name/arguments 校验后路由 handler 执行，输出包装 MCP content 数组（str→TextContent，dict 含 content 原样透传供 E3 structuredContent），缺 name/空名/非 str 名/未知 tool/arguments 非 object → -32602，handler 异常 → -32603 且错误消息仅含 tool 名不含异常文本/堆栈（防泄露）；未知请求方法 → -32601；register_tool 双风格（ToolDefinition 实例或逐字段，重复注册 ValueError，非 callable handler TypeError，inputSchema 缺省 {"type":"object","properties":{}}）；server.py 接线：MCPServer 新增 protocol_handler 参数，message_handler 缺省取 ProtocolHandler().handle_message（E1 的 default_message_handler 占位删除，常量移至 protocol_handler 并由 server re-import）；E1 集成测试同步更新（capabilities 断言 {"tools":{"listChanged":false}}、tools/list 返回空列表、未知方法 foo/bar 断 -32601）；测试 tests/unit/test_protocol_handler.py 30 单测（initialize 三用例、tools/list 空/schema/字段风格默认值/重复与非法注册参数化、tools/call 路由 str/dict 输出/默认空 arguments/未知 tool/非法参数参数化/-32603 无泄露、消息级 -32600 参数化/-32602 params/通知参数化/id:null、MCPServer 默认接线与注入接线）+ test_mcp_server.py 1 用例改写；全套件 813 通过 2 跳过（既有门控） |
| E3 | query_knowledge_hub Tool | [x] | 2026-08-17 | 新增 core/response 模块：CitationGenerator.generate(retrieval_results) 产出 1-based Citation 列表（id/source 取 metadata.source_path 缺省 source 兜底 unknown/page 非数值归 None/chunk_id/text/score 四舍五入 4 位），输入非列表或元素非 RetrievalResult 抛 TypeError；ResponseBuilder.build(results, query) 返回 MCP tool result（content[0] 为 Markdown：query 标题 + [n] source,p.N (score) + 摘要引用块，超 400 字符截断加省略号；structuredContent={query, answer, citations}，全文保留在 citations.text；空结果返回 EMPTY_RESULT_MESSAGE 友好提示而非空数组且 citations=[]；CitationGenerator 可注入）。mcp_server/tools/query_knowledge_hub.py：QueryKnowledgeHubTool 可调用类（search/reranker/settings 可注入且懒初始化——首次调用才构建 HybridSearch/Reranker/load_settings，server 启动零网络依赖；__call__(arguments) 校验 query 必填非空、top_k 正整数、collection 非空字符串，非法抛 ToolInvalidParams；TraceContext 打点；collection 转 filters={"collection": ...}；search 后 rerank_enabled 且结果非空时走 Reranker.rerank；最后 ResponseBuilder.build）。E2 增强：ProtocolHandler 新增 ToolInvalidParams(ValueError) 异常通道（handler 主动抛出映射 -32602 且带描述消息，其余异常仍 -32603 不泄露）。server.py 新增 build_default_protocol_handler() 注册 query_knowledge_hub（E4/E5 在此追加），main 改用之。集成测试更新（tools/list 断言 query_knowledge_hub schema；tools/call 参数校验 -32602 与未知 tool）并新增 tests/unit/test_response_builder.py（13 用例）与 tests/unit/test_query_knowledge_hub_tool.py（20 用例：校验参数化/Markdown+citations/空结果/top_k+collection 透传/rerank 应用与空结果跳过/懒初始化/ProtocolHandler 注册路由/-32602 映射与 -32603 无泄露）。全套件 851 通过 2 跳过。 |
| E4 | list_collections Tool | [x] | 2026-08-17 | 新增 src/mcp_server/tools/list_collections.py：ListCollectionsTool 可调用类（client 可注入且懒初始化——首次调用才按 Settings.vector_store.persist_path 构建 chromadb.PersistentClient；任何具备 list_collections() 签法的对象均可注入，测试离线零持久化）。集合来源取向量库 Chroma collections（摄取管线按 collection 写入，Chroma 为权威账本，而非 data/documents/ 目录）。输出 MCP tool result：content[0] Markdown 列表（**name** (N chunks)），structuredContent={"collections":[{"name","chunk_count"}]}（chunk_count 取 collection.count()，统计异常降级为 0 不阻塞列表；文档级统计延后到 DocumentManager 接入）；空集合返回 EMPTY_COLLECTIONS_MESSAGE 友好提示而非空数组；arguments 接受 {} 或 None，非 dict 抛 TypeError。无参数 inputSchema（{"type":"object","properties":{}}）。server.build_default_protocol_handler() 追加注册（顺序 query_knowledge_hub、list_collections），集成测试 tools/list 断言同步更新。新增 tests/unit/test_list_collections.py 7 用例（名称+统计渲染/空集合友好提示/count 失败降级 0/懒初始化/None 参数/非法参数 TypeError/ProtocolHandler 注册与调用路由）。全套件 858 通过 2 跳过。 |
| E5 | get_document_summary Tool | [x] | 2026-08-17 | 新增 src/mcp_server/tools/get_document_summary.py：GetDocumentSummaryTool 可调用类（reader 可注入且懒初始化——首次调用才构建默认 reader：chromadb.PersistentClient 扫描所有 collection 按 metadata.source_path 匹配，对齐单 collection + metadata["collection"] 标记的存储布局）。doc_id 语义为文档 source_path（与 ingestion/DocumentManager 全链路一致，query_knowledge_hub citations.source 同源）。输出 MCP tool result：content[0] Markdown（标题/Source/Collection/Chunks/Pages 区间/Tags/Summary），structuredContent={doc_id, found, title, summary, tags, source_path, collection, chunk_count, pages}（title 缺省兜底 doc_id、tags 字符串包成列表、pages 取 chunk 页码 min-max 无页码为 None、collection 取众数）；doc_id 不存在返回 isError:true + "Document not found" 内容 + structuredContent.found=false（参数合法属资源缺失，不误用 -32602）；doc_id 缺失/空白/非字符串抛 ToolInvalidParams→-32602，arguments 非 dict 抛 TypeError。server.build_default_protocol_handler() 追加注册（顺序 query_knowledge_hub、list_collections、get_document_summary），集成测试 tools/list 断言同步更新。新增 tests/unit/test_get_document_summary.py 8 用例（结构化摘要字段/Markdown 渲染/未知 doc_id isError/非法参数参数化/非 dict TypeError/metadata 缺失兜底/字符串 tags 包装/ProtocolHandler 注册路由含 -32602 与 isError 透传）。全套件 869 通过 2 跳过。 |
| E6 | 多模态返回组装（Text + Image） | [x] | 2026-08-17 | 新增 src/core/response/multimodal_assembler.py：MultimodalAssembler.assemble(tool_result, retrieval_results) 就地追加 MCP ImageContent（{"type":"image","data":<base64>,"mimeType"}）到 content 数组（content[0] 恒为 Markdown 文本，图像只作后续项，Client 兼容优先）；图片引用取各 chunk metadata.images（复用 core.types.parse_image_refs 的 ImageRef 解析与 fail-fast 校验）；mimeType 按扩展名 mimetypes.guess_type 推断、非 image/* 或未知扩展缺省 image/png；单张图片缺失/读取失败仅 warning 跳过不阻塞响应（3.5.5 降级策略）；max_images 上限保护（默认 10，超限跳过剩余并告警）；structuredContent.images 记录 {id, chunk_id, mimeType} 索引。query_knowledge_hub 接线：新增 multimodal_assembler 参数（缺省 MultimodalAssembler()，传 False 可禁用图像返回），__call__ 在 ResponseBuilder.build 后、结果非空时调用 assemble；E4/E5 不受影响。新增 tests/unit/test_multimodal_assembler.py 10 用例（文本首位+base64 roundtrip/structured images 索引/缺图跳过/无图不变/上限/扩展名 MIME/未知扩展缺省/非法 tool_result/非法 results/非法 max_images）+ test_mcp_server.py 补进程内（stdin/stdout 流注入 MCPServer）tools/call 图像返回用例（fake 检索栈 + 临时 PNG，断言 content[1] 为 image/png 且 base64 可解码、structuredContent.images 正确）。全套件 880 通过 2 跳过。至此 Phase E（E1-E6）全部完成。 |

#### 阶段 F：Trace 基础设施与打点

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|---------|---------|------|---------|------|
| F1 | TraceContext 增强（finish + 耗时统计 + trace_type） | [x] | 2026-08-17 | TraceContext 增强：trace_type(query/ingestion) 校验 + finish() 幂等计算总耗时 + elapsed_ms(stage?) + to_dict() JSON 可序列化（trace_id/trace_type/started_at/finished_at/total_elapsed_ms/stages）；TraceCollector.collect 自动 finish + 内存收集 + write_trace 持久化，写入失败仅告警不阻断主链路；16 单测全绿 |
| F2 | 结构化日志 logger（JSON Lines） | [x] | 2026-08-17 | JSONFormatter 单行 JSON（time/level/logger/message + extra 合并）+ get_trace_logger（JSONFormatter+FileHandler，幂等，路径取 settings.observability.log_file 默认 logs/traces.jsonl）+ write_trace 追加 JSONL（含 trace_type）；父目录自动创建；5 单测全绿 |
| F3 | 在 Query 链路打点 | [x] | 2026-08-17 | HybridSearch 默认创建 trace_type=query 并 finish；QueryProcessor/DenseRetriever/SparseRetriever/RRFFusion/Reranker 各阶段 record_stage（query_processing/dense_retrieval/sparse_retrieval/fusion/hybrid/rerank）含 elapsed_ms+method；集成测试断言 to_dict.trace_type==query 与全阶段；21 集成测试全绿 |
| F4 | 在 Ingestion 链路打点 | [x] | 2026-08-17 | IngestionPipeline 默认创建 trace_type=ingestion 并 finish（成功/跳过/失败均收口）；pipeline 阶段 integrity/load/split/transform/embed/store 均 record_stage（method=pipeline+details+elapsed_ms），store 承载 BM25/向量/图片三路写入（spec upsert）；TraceCollector 写入 JSONL 含 trace_type=ingestion；2 集成测试全绿 |
| F5 | Pipeline 进度回调 (on_progress) | [x] | 2026-08-17 | IngestionPipeline.run 可选 on_progress(stage,current,total)；load/split/transform(1..3)/embed(按批)/upsert(1..3) 均上报；None 不影响主链路；回调异常隔离不中断摄取；5 单测全绿 |

#### 阶段 G：可视化管理平台 Dashboard

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|---------|---------|------|---------|------|
| G1 | Dashboard 基础架构与系统总览页 | [x] | 2026-08-18 | Streamlit 六页导航 + ConfigService 组件卡片 + start_dashboard.py |
| G2 | DocumentManager 实现 | [x] | 2026-08-18 | 跨 Chroma/BM25/ImageStorage/FileIntegrity 的 list/detail/delete/stats |
| G3 | 数据浏览器页面 | [x] | 2026-08-18 | 文档列表/集合筛选/Chunk 详情/图片预览 |
| G4 | Ingestion 管理页面 | [x] | 2026-08-18 | 上传触发摄取 + on_progress 进度条 + 文档删除 |
| G5 | Ingestion 追踪页面 | [x] | 2026-08-18 | TraceService 解析 JSONL + 阶段耗时瀑布图 |
| G6 | Query 追踪页面 | [x] | 2026-08-18 | Query 历史搜索 + Dense/Sparse 对比 + Rerank 前后变化 |

#### 阶段 H：评估体系

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|---------|---------|------|---------|------|
| H1 | RagasEvaluator 实现 | [x] | 2026-08-18 | src/observability/evaluation/ragas_evaluator.py RagasEvaluator(BaseEvaluator)：faithfulness/answer_relevancy/context_precision 三指标；metrics_fn/evaluate_fn/llm/embeddings 全可注入（离线 mock 测试）；ragas 未安装时构造不报错、evaluate 抛带 `pip install ragas` 提示的 ImportError；evaluate(query,retrieved_ids,golden_ids,answer 必填, contexts 缺省回退 retrieved_ids, top_k 截断 contexts)；非数值结果过滤保持 float 契约；trace evaluation 阶段打点；EvaluatorFactory 注册 ragas backend（create/create_from_settings 可路由）；28 单测，全套件 962 通过 2 跳过 |
| H2 | CompositeEvaluator 实现 | [x] | 2026-08-18 | src/observability/evaluation/composite_evaluator.py CompositeEvaluator(BaseEvaluator)：多 evaluator 线程池并行（单 evaluator 免池直跑）；指标合并 dict[str,float]（非数值过滤，跨 evaluator 重复键对称加 "{name}." 前缀消歧）；单 evaluator 异常隔离降级不阻断（last_errors 逐次重置）；from_settings 按 evaluation.backends 经 EvaluatorFactory 批量组合；trace evaluation 阶段打点（method=composite_evaluator，details 含 evaluators/metrics_by_evaluator/errors）且 trace 透传子评估器；构造 fail-fast（空列表 ValueError/非 BaseEvaluator TypeError）；15 单测（含真 CustomEvaluator 契约与仓库默认 settings [ragas, custom] 组合验证），全套件 977 通过 2 跳过 |
| H3 | EvalRunner + Golden Test Set | [x] | 2026-08-18 | src/observability/evaluation/eval_runner.py EvalRunner(settings, hybrid_search?, evaluator?)+load_golden_test_set（spec JSON 契约：test_cases/query/expected_chunk_ids/expected_sources 可选，非空校验 fail-fast）+GoldenTestCase/QueryEvalResult/EvalReport（to_dict JSON 可序列化）；run(test_set_path, top_k?, filters?) 逐条 HybridSearch.search→evaluator.evaluate（缺省 CustomEvaluator 离线可用；composite 时 ragas 缺 answer 由 H2 隔离降级），逐指标均值聚合（hit_rate/mrr/hit_at_k），单 query 异常（HybridSearchError 等）记录 error 不中断批次；tests/fixtures/golden_test_set.json 3 条样例（expected_chunk_ids 留空待 H5 摄取后回填）；scripts/evaluate.py CLI（sys.path shim 同款，--test-set 默认 settings.evaluation.golden_test_set 相对仓库根解析/--top-k/--collection filters/--evaluator custom|ragas|composite 默认 composite/--config；build_parser/parse_args/resolve_test_set_path/build_runner/render_report/main(argv, runner?, settings?, out?) 全可导入纯函数注入 fake runner 离线单测；退出码 0 完成/1 整批失败/2 用法配置错误）；修复 evaluator_factory→observability 循环导入（ragas ctor 延迟导入）；`python scripts/evaluate.py` 实测可运行（空库输出 0.0000 指标块退出码 0）；tests/unit/test_eval_runner.py 15 单测 + test_evaluate_entry.py 13 单测，全套件 1005 通过 2 跳过 |
| H4 | 评估面板页面 | [x] | 2026-08-18 | src/observability/dashboard/pages/evaluation_panel.py 替换占位：左侧控制区（评估后端 selectbox composite/custom/ragas 对齐 scripts/evaluate.py、golden test set 路径默认 settings.evaluation.golden_test_set 仓库根解析、Top-K（0=默认）、限定集合 filters）+「运行评估」按钮（run_eval 可注入离线测试；spinner；路径不存在/异常 st.error 不崩溃）；结果区：聚合指标 st.metric 卡片 + 各 Query 明细表（hit_rate/mrr/first_expected/retrieved_top1/error）+ 会话内历史记录（session_state，多次运行 hit_rate/mrr 历史趋势 line_chart + dataframe）；evaluator_for_backend/default_test_set_path 纯函数可测；tests/unit/test_evaluation_panel.py 7 单测（后端路由参数化含 composite 按 settings.backends 组合、路径解析与绝对路径透传），页面主体按 spec 手动验证；全套件 1012 通过 2 跳过 |
| H5 | Recall 回归测试（E2E） | [x] | 2026-08-18 | tests/e2e/test_recall.py：离线真实组件栈（真 ChromaStore 嵌入式 tmp + 真 BM25Indexer + 真 QueryProcessor/RRFFusion/HybridSearch/EvalRunner/CustomEvaluator，唯一注入确定性 bag-of-words embedding 同 test_hybrid_search.py 手法）对 6-chunk 语料跑 5 条 golden set（tmp 内生成，expected_chunk_ids 为语料确定性 id）；阈值写死测试内：hit_at_k@5 ≥ 0.8 且 mrr ≥ 0.5 且全部 case 零错误；报告 to_dict JSON 可序列化断言；tests/fixtures/golden_test_set.json 补齐至 5 条（新增 incremental ingestion / RRF fusion 用例，expected_chunk_ids 待真实摄取后回填）；2 e2e 用例，全套件 1014 通过 2 跳过。阶段 H（H1-H5）全部完成 |

#### 阶段 I：端到端验收与文档收口

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|---------|---------|------|---------|------|
| I1 | E2E：MCP Client 侧调用模拟 | [ ] | | |
| I2 | E2E：Dashboard 冒烟测试 | [ ] | | |
| I3 | 完善 README（运行说明 + MCP + Dashboard） | [ ] | | |
| I4 | 清理接口一致性（契约测试补齐） | [ ] | | |
| I5 | 全链路 E2E 验收 | [ ] | | |

---

### 📈 总体进度

| 阶段 | 总任务数 | 已完成 | 进度 |
|------|---------|--------|------|
| 阶段 A | 3 | 3 | 100% |
| 阶段 B | 16 | 16 | 100% |
| 阶段 C | 15 | 15 | 100% |
| 阶段 D | 7 | 7 | 100% |
| 阶段 E | 6 | 6 | 100% |
| 阶段 F | 5 | 5 | 100% |
| 阶段 G | 6 | 6 | 100% |
| 阶段 H | 5 | 0 | 0% |
| 阶段 I | 5 | 0 | 0% |
| **总计** | **68** | **52** | **76%** |


---

## 阶段 A：工程骨架与测试基座（目标：先可导入，再可测试）

### A1：初始化目录树与最小可运行入口
- **目标**：在 repo 根目录创建第 5.2 节所述目录骨架与空模块文件（可 import）。
- **修改文件**：
  - `main.py`
  - `pyproject.toml`
  - `README.md`
  - `.gitignore`（Python 项目标准忽略规则：`__pycache__`、`.venv`、`.env`、`*.pyc`、IDE 配置等）
  - `src/**/__init__.py`（按目录树补齐）
  - `config/settings.yaml`（最小可解析配置）
  - `config/prompts/image_captioning.txt`（可先放占位内容，后续阶段补充 Prompt）
  - `config/prompts/chunk_refinement.txt`（可先放占位内容，后续阶段补充 Prompt）
  - `config/prompts/rerank.txt`（可先放占位内容，后续阶段补充 Prompt）
- **实现类/函数**：无（仅骨架）。
- **实现类/函数**：无（仅骨架，不实现业务逻辑）。
- **实现类/函数**：为当前项目创建一个虚拟环境模块。
 - **验收标准**：
  - 目录结构与 DEV_SPEC 5.2 一致（至少把对应目录创建出来）。
  - `config/prompts/` 目录存在，且三个 prompt 文件可被读取（即使只是占位文本）。
  - 能导入关键顶层包（与目录结构一一对应）：
    - `python -c "import mcp_server; import core; import ingestion; import libs; import observability"`
  - 可以启动虚拟环境模块
- **测试方法**：运行 `python -m compileall src`（仅做语法/可导入性检查；pytest 基座在 A2 建立）。

### A2：引入 pytest 并建立测试目录约定
- **目标**：建立 `tests/unit|integration|e2e|fixtures` 目录与 pytest 运行基座。
- **修改文件**：
  - `pyproject.toml`（添加 pytest 配置：testpaths、markers 等）
  - `tests/unit/test_smoke_imports.py`
  - `tests/fixtures/sample_documents/`（放 1 个最小样例文档占位）
- **实现类/函数**：无。
- **实现类/函数**：无（新增的是测试文件与 pytest 配置）。
- **验收标准**：
  - `pytest -q` 可运行并通过。
  - 至少 1 个冒烟测试（例如 `tests/unit/test_smoke_imports.py` 只做关键包 import 校验）。
- **测试方法**：`pytest -q tests/unit/test_smoke_imports.py`。

### A3：配置加载与校验（Settings）
- **目标**：实现读取 `config/settings.yaml` 的配置加载器，并在启动时校验关键字段存在。
- **修改文件**：
  - `main.py`（启动时调用 `load_settings()`，缺字段直接 fail-fast 退出）
  - `src/observability/logger.py`（先占位：提供 get_logger，stderr 输出）
  - `src/core/settings.py`（新增：集中放 Settings 数据结构与加载/校验逻辑）
  - `config/settings.yaml`（补齐字段：llm/embedding/vector_store/retrieval/rerank/evaluation/observability）
  - `tests/unit/test_config_loading.py`
- **实现类/函数**：
  - `Settings`（dataclass：只做结构与最小校验；不在这里做任何网络/IO 的“业务初始化”）
  - `load_settings(path: str) -> Settings`（读取 YAML -> 解析为 Settings -> 校验必填字段）
  - `validate_settings(settings: Settings) -> None`（把“必填字段检查”集中化，错误信息包含字段路径，例如 `embedding.provider`）
- **验收标准**：
  - `main.py` 启动时能成功加载 `config/settings.yaml` 并拿到 `Settings` 对象。
  - 删除/缺失关键字段时（例如 `embedding.provider`），启动或 `load_settings()` 抛出“可读错误”（明确指出缺的是哪个字段）。
- **测试方法**：`pytest -q tests/unit/test_config_loading.py`。

---

## 阶段 B：Libs 可插拔层（目标：Factory 可工作，且至少有“默认后端”可跑通端到端）

### B1：LLM 抽象接口与工厂
- **目标**：定义 `BaseLLM` 与 `LLMFactory`，支持按配置选择 provider。
- **修改文件**：
  - `src/libs/llm/base_llm.py`
  - `src/libs/llm/llm_factory.py`
  - `tests/unit/test_llm_factory.py`
- **实现类/函数**：
  - `BaseLLM.chat(messages) -> str`（或统一 response 对象）
  - `LLMFactory.create(settings) -> BaseLLM`
- **验收标准**：在测试里用 Fake provider（测试内 stub）验证工厂路由逻辑。
- **测试方法**：`pytest -q tests/unit/test_llm_factory.py`。

### B2：Embedding 抽象接口与工厂
- **目标**：定义 `BaseEmbedding` 与 `EmbeddingFactory`，支持批量 embed。
- **修改文件**：
  - `src/libs/embedding/base_embedding.py`
  - `src/libs/embedding/embedding_factory.py`
  - `tests/unit/test_embedding_factory.py`
- **实现类/函数**：
  - `BaseEmbedding.embed(texts: list[str], trace: TraceContext | None = None) -> list[list[float]]`
  - `EmbeddingFactory.create(settings) -> BaseEmbedding`
- **验收标准**：Fake embedding 返回稳定向量，工厂按 provider 分流。
- **测试方法**：`pytest -q tests/unit/test_embedding_factory.py`。

### B3：Splitter 抽象接口与工厂
- **目标**：定义 `BaseSplitter` 与 `SplitterFactory`，支持不同切分策略（Recursive/Semantic/Fixed）。
- **修改文件**：
  - `src/libs/splitter/base_splitter.py`
  - `src/libs/splitter/splitter_factory.py`
  - `tests/unit/test_splitter_factory.py`
- **实现类/函数**：
  - `BaseSplitter.split_text(text: str, trace: TraceContext | None = None) -> List[str]`
  - `SplitterFactory.create(settings) -> BaseSplitter`
- **验收标准**：Factory 能根据配置返回不同类型的 Splitter 实例（测试中可用 Fake 实现）。
- **测试方法**：`pytest -q tests/unit/test_splitter_factory.py`。

### B4：VectorStore 抽象接口与工厂（先定义契约）
- **目标**：定义 `BaseVectorStore` 与 `VectorStoreFactory`，先不接真实 DB。
- **修改文件**：
  - `src/libs/vector_store/base_vector_store.py`
  - `src/libs/vector_store/vector_store_factory.py`
  - `tests/unit/test_vector_store_contract.py`
- **实现类/函数**：
  - `BaseVectorStore.upsert(records, trace: TraceContext | None = None)`
  - `BaseVectorStore.query(vector, top_k, filters, trace: TraceContext | None = None)`
- **验收标准**：契约测试（contract test）约束输入输出 shape。
- **测试方法**：`pytest -q tests/unit/test_vector_store_contract.py`。

### B5：Reranker 抽象接口与工厂（含 None 回退）
- **目标**：实现 `BaseReranker`、`RerankerFactory`，提供 `NoneReranker` 作为默认回退。
- **修改文件**：
  - `src/libs/reranker/base_reranker.py`
  - `src/libs/reranker/reranker_factory.py`
  - `tests/unit/test_reranker_factory.py`
- **实现类/函数**：
  - `BaseReranker.rerank(query, candidates, trace: TraceContext | None = None) -> ranked_candidates`
  - `NoneReranker`（保持原顺序）
- **验收标准**：backend=none 时不会改变排序；未知 backend 明确报错。
- **测试方法**：`pytest -q tests/unit/test_reranker_factory.py`。

### B6：Evaluator 抽象接口与工厂（先做自定义轻量指标）
- **目标**：定义 `BaseEvaluator`、`EvaluatorFactory`，实现最小 `CustomEvaluator`（例如 hit_rate/mrr）。
- **修改文件**：
  - `src/libs/evaluator/base_evaluator.py`
  - `src/libs/evaluator/evaluator_factory.py`
  - `src/libs/evaluator/custom_evaluator.py`
  - `tests/unit/test_custom_evaluator.py`
- **验收标准**：输入 query + retrieved_ids + golden_ids 能输出稳定 metrics。
- **测试方法**：`pytest -q tests/unit/test_custom_evaluator.py`。

### B7：补齐 Libs 默认实现（拆分为≈1h可验收增量）

> 说明：B7 只补齐与端到端主链路强相关的默认实现（LLM/Embedding/Splitter/VectorStore/Reranker）。其余可选扩展（例如额外 splitter 策略、更多 vector store 后端、更多 evaluator 后端等）保持原排期不提前。

### B7.1：OpenAI-Compatible LLM（OpenAI/Azure/DeepSeek）
- **目标**：补齐 OpenAI-compatible 的 LLM 实现，确保通过 `LLMFactory` 可创建并可被 mock 测试。
- **修改文件**：
  - `src/libs/llm/openai_llm.py`
  - `src/libs/llm/azure_llm.py`
  - `src/libs/llm/deepseek_llm.py`
  - `tests/unit/test_llm_providers_smoke.py`（mock HTTP，不走真实网络）
- **验收标准**：
  - 配置不同 `provider` 时工厂路由正确。
  - `chat(messages)` 对输入 shape 校验清晰，异常信息可读（包含 provider 与错误类型）。
- **测试方法**：`pytest -q tests/unit/test_llm_providers_smoke.py`。

### B7.2：Ollama LLM（本地后端）
- **目标**：补齐 `ollama_llm.py`，支持本地 HTTP endpoint（默认 `base_url` + `model`），并可被 mock 测试。
- **修改文件**：
  - `src/libs/llm/ollama_llm.py`
  - `tests/unit/test_ollama_llm.py`（mock HTTP）
- **验收标准**：
  - provider=ollama 时可由 `LLMFactory` 创建。
  - 在连接失败/超时等场景下，抛出可读错误且不泄露敏感配置。
- **测试方法**：`pytest -q tests/unit/test_ollama_llm.py`。

### B7.3：OpenAI & Azure Embedding 实现
- **目标**：补齐 `openai_embedding.py` 和 `azure_embedding.py`，支持 OpenAI 官方 API 和 Azure OpenAI 服务的 Embedding 调用，支持批量 `embed(texts)`，并可被 mock 测试。
- **修改文件**：
  - `src/libs/embedding/openai_embedding.py`
  - `src/libs/embedding/azure_embedding.py`
  - `tests/unit/test_embedding_providers_smoke.py`（mock HTTP，包含 OpenAI 和 Azure 测试用例）
- **验收标准**：
  - provider=openai 时 `EmbeddingFactory` 可创建，支持 OpenAI 官方 API 的 text-embedding-3-small/large 等模型。
  - provider=azure 时 `EmbeddingFactory` 可创建，正确处理 Azure 特有的 endpoint、api-version、api-key 配置，支持 Azure 部署的 text-embedding-ada-002 等模型。
  - 空输入、超长输入有明确行为（报错或截断策略由配置决定）。
  - Azure 实现复用 OpenAI Embedding 的核心逻辑，保持行为一致性。
- **测试方法**：`pytest -q tests/unit/test_embedding_providers_smoke.py`。

### B7.4：Ollama Embedding 实现
- **目标**：补齐 `ollama_embedding.py`，支持通过 Ollama HTTP API 调用本地部署的 Embedding 模型（如 `nomic-embed-text`、`mxbai-embed-large` 等），实现 `embed(texts)` 批量向量化功能。
- **修改文件**：
  - `src/libs/embedding/ollama_embedding.py`
  - `tests/unit/test_ollama_embedding.py`（包含 mock HTTP 测试）
- **验收标准**：
  - provider=ollama 时 `EmbeddingFactory` 可创建。
  - 支持配置 Ollama 服务地址（默认 http://localhost:11434）和模型名称。
  - 输出向量维度由模型决定（如 nomic-embed-text 为 768 维），满足 ingestion/retrieval 的接口契约。
  - 支持批量 `embed(texts)` 调用，内部处理单条/批量请求逻辑。
  - 空输入、超长输入有明确行为（报错或截断策略）。
  - mock 测试覆盖正常响应、连接失败、超时等场景。
- **测试方法**：`pytest -q tests/unit/test_ollama_embedding.py`。

### B7.5：Recursive Splitter 默认实现
- **目标**：补齐 `recursive_splitter.py`，封装 LangChain 的切分逻辑，作为默认切分器。
- **修改文件**：
  - `src/libs/splitter/recursive_splitter.py`
  - `tests/unit/test_recursive_splitter_lib.py`
- **验收标准**：
  - provider=recursive 时 `SplitterFactory` 可创建。
  - `split_text` 能正确处理 Markdown 结构（标题/代码块不被打断）。
- **测试方法**：`pytest -q tests/unit/test_recursive_splitter_lib.py`。

### B7.6：ChromaStore（VectorStore 默认后端）
- **目标**：补齐 `chroma_store.py`，支持最小 `upsert(records)` 与 `query(vector, top_k, filters)`，并支持本地持久化目录（例如 `data/db/chroma/`）。
- **修改文件**：
  - `src/libs/vector_store/chroma_store.py`
  - `tests/integration/test_chroma_store_roundtrip.py`
- **验收标准**：
  - provider=chroma 时 `VectorStoreFactory` 可创建。
  - **必须完成完整的 upsert→query roundtrip 测试**：使用 mock 数据完成真实的存储和检索流程，验证返回结果的确定性和正确性。
  - 测试应覆盖：基本 upsert、向量查询、top_k 参数、metadata filters（如支持）。
  - 使用临时目录进行持久化测试，测试结束后清理。
- **测试方法**：`pytest -q tests/integration/test_chroma_store_roundtrip.py`

### B7.7：LLM Reranker（读取 rerank prompt）
- **目标**：补齐 `llm_reranker.py`，读取 `config/prompts/rerank.txt` 构造 prompt（测试中可注入替代文本），并可在失败时返回可回退信号。
- **修改文件**：
  - `src/libs/reranker/llm_reranker.py`
  - `tests/unit/test_llm_reranker.py`（mock LLM）
- **验收标准**：
  - backend=llm 时 `RerankerFactory` 可创建。
  - 输出严格结构化（例如 ranked ids），不满足 schema 时抛出可读错误。
- **测试方法**：`pytest -q tests/unit/test_llm_reranker.py`。

### B7.8：Cross-Encoder Reranker（本地/托管模型，占位可跑）
- **目标**：补齐 `cross_encoder_reranker.py`，支持对 Top-M candidates 打分排序；测试中用 mock scorer 保证 deterministic。
- **修改文件**：
  - `src/libs/reranker/cross_encoder_reranker.py`
  - `tests/unit/test_cross_encoder_reranker.py`（mock scorer）
- **验收标准**：
  - backend=cross_encoder 时 `RerankerFactory` 可创建。
  - 提供超时/失败回退信号（供 Core 层 `D6` fallback 使用）。
- **测试方法**：`pytest -q tests/unit/test_cross_encoder_reranker.py`。

### B8：Vision LLM 抽象接口与工厂集成
- **目标**：定义 `BaseVisionLLM` 抽象接口，扩展 `LLMFactory` 支持 Vision LLM 创建，为 C7 的 ImageCaptioner 提供底层抽象。
- **修改文件**：
  - `src/libs/llm/base_vision_llm.py`
  - `src/libs/llm/llm_factory.py`（扩展 `create_vision_llm` 方法）
  - `tests/unit/test_vision_llm_factory.py`
- **实现类/函数**：
  - `BaseVisionLLM.chat_with_image(text: str, image_path: str | bytes, trace: TraceContext | None = None) -> ChatResponse`
  - `LLMFactory.create_vision_llm(settings) -> BaseVisionLLM`
- **验收标准**：
  - 抽象接口清晰定义多模态输入（文本+图片路径/base64）。
  - 工厂方法 `create_vision_llm` 能根据配置路由到不同 provider（测试中用 Fake Vision LLM 验证）。
  - 接口设计支持图片预处理（压缩、格式转换）的扩展点。
- **测试方法**：`pytest -q tests/unit/test_vision_llm_factory.py`。

### B9：Azure Vision LLM 实现
- **目标**：实现 `AzureVisionLLM`，支持通过 Azure OpenAI 调用 GPT-4o/GPT-4-Vision-Preview 进行图像理解。
- **修改文件**：
  - `src/libs/llm/azure_vision_llm.py`
  - `tests/unit/test_azure_vision_llm.py`（mock HTTP，不走真实 API）
- **实现类/函数**：
  - `AzureVisionLLM(BaseVisionLLM)`：实现 `chat_with_image` 方法
  - 支持 Azure 特有配置：`azure_endpoint`, `api_version`, `deployment_name`, `api_key`
- **验收标准**：
  - provider=azure 且配置 vision_llm 时，`LLMFactory.create_vision_llm()` 可创建 Azure Vision LLM 实例。
  - 支持图片路径和 base64 两种输入方式。
  - 图片过大时自动压缩至 `max_image_size` 配置的尺寸（默认2048px）。
  - API 调用失败时抛出清晰错误，包含 Azure 特有错误码。
  - mock 测试覆盖：正常调用、图片压缩、超时、认证失败等场景。
- **测试方法**：`pytest -q tests/unit/test_azure_vision_llm.py`。

---

## 阶段 C：Ingestion Pipeline MVP（目标：能把 PDF 样例摄取到本地存储）

> 注：本阶段严格按 5.4.1 的离线数据流落地，并优先实现“增量跳过（SHA256）”。

### C1：定义核心数据类型/契约（Document/Chunk/ChunkRecord）
- **目标**：定义全链路（ingestion → retrieval → mcp tools）共用的数据结构/契约，避免散落在各子模块内导致的耦合与重复。
- **修改文件**：
  - `src/core/types.py`
  - `src/core/__init__.py`（可选：统一 re-export 以简化导入路径）
  - `tests/unit/test_core_types.py`
- **实现类/函数**（建议）：
  - `Document(id, text, metadata)`
  - `Chunk(id, text, metadata, start_offset, end_offset, source_ref?)`
  - `ChunkRecord(id, text, metadata, dense_vector?, sparse_vector?)`（用于存储/检索载体；字段按后续 C8~C12 演进）
- **验收标准**：
  - 类型可序列化（dict/json）且字段稳定（单元测试断言）。
  - `metadata` 约定最少包含 `source_path`，其余字段允许增量扩展但不得破坏兼容。
  - **`metadata.images` 字段规范**（用于多模态支持）：
    - 结构：`List[{"id": str, "path": str, "page": int, "text_offset": int, "text_length": int, "position": dict}]`
    - `id`：全局唯一图片标识符（建议格式：`{doc_hash}_{page}_{seq}`）
    - `path`：图片文件存储路径（约定：`data/images/{collection}/{image_id}.png`）
    - `page`：图片在原文档中的页码（可选，适用于PDF等分页文档）
    - `text_offset`：占位符在 `Document.text` 中的起始字符位置（从0开始计数）
    - `text_length`：占位符的字符长度（通常为 `len("[IMAGE: {image_id}]")`）
    - `position`：图片在原文档中的物理位置信息（可选，如PDF坐标、像素位置、尺寸等）
    - 说明：通过 `text_offset` 和 `text_length` 可精确定位图片在文本中的位置，支持同一图片多次出现的场景
  - **文本中图片占位符规范**：在 `Document.text` 中，图片位置使用 `[IMAGE: {image_id}]` 格式标记。
- **测试方法**：`pytest -q tests/unit/test_core_types.py`。

### C2：文件完整性检查（SHA256）
- **目标**：在Libs中实现 `file_integrity.py`：计算文件 hash，并提供“是否跳过”的判定接口（使用 SQLite 作为默认存储，支持后续替换为 Redis/PostgreSQL）。
- **修改文件**：
  - `src/libs/loader/file_integrity.py`
  - `tests/unit/test_file_integrity.py`
  - 数据库文件：`data/db/ingestion_history.db`（自动创建）
- **实现类/函数**：
  - `FileIntegrityChecker` 类（抽象接口）
  - `SQLiteIntegrityChecker(FileIntegrityChecker)` 类（默认实现）
    - `compute_sha256(path: str) -> str`
    - `should_skip(file_hash: str) -> bool`
    - `mark_success(file_hash: str, file_path: str, ...)`
    - `mark_failed(file_hash: str, error_msg: str)`
- **验收标准**：
  - 同一文件多次计算hash结果一致
  - 标记 success 后，`should_skip` 返回 `True`
  - 数据库文件正确创建在 `data/db/ingestion_history.db`
  - 支持并发写入（SQLite WAL模式）
- **测试方法**：`pytest -q tests/unit/test_file_integrity.py`。

### C3：Loader 抽象基类与 PDF Loader 壳子
- **目标**：在Libs中定义 `BaseLoader`，并实现 `PdfLoader` 的最小行为。
- **修改文件**：
  - `src/libs/loader/base_loader.py`
  - `src/libs/loader/pdf_loader.py`
  - `tests/unit/test_loader_pdf_contract.py`
- **实现类/函数**：
  - `BaseLoader.load(path) -> Document`
  - `PdfLoader.load(path)`
- **验收标准**：
  - **基础要求**：对 sample PDF（fixtures）能产出 Document，metadata 至少含 `source_path`。
  - **图片处理要求**（遵循 C1 定义的契约）：
    - 若 PDF 包含图片，应提取图片并保存到 `data/images/{doc_hash}/` 目录
    - 在 `Document.text` 中，图片位置插入占位符：`[IMAGE: {image_id}]`
    - 在 `metadata.images` 中记录图片信息（格式见 C1 规范）
    - 若 PDF 无图片，`metadata.images` 可为空列表或省略该字段
  - **降级行为**：图片提取失败不应阻塞文本解析，可在日志中记录警告。
- **测试方法**：`pytest -q tests/unit/test_loader_pdf_contract.py`。
- **测试建议**：
  - 准备两个测试文件：`simple.pdf`（纯文本）和 `with_images.pdf`（包含图片）
  - 验证纯文本PDF能正常解析
  - 验证带图片PDF能提取图片并正确插入占位符

### C4：Splitter 集成（调用 Libs）
- **目标**：实现 Chunking 模块作为 `libs.splitter` 和 Ingestion Pipeline 之间的**适配器层**，完成 Document→Chunks 的业务对象转换。
- **核心职责（DocumentChunker 相比 libs.splitter 的增值）**：
  - **职责边界说明**：
    - `libs.splitter`：纯文本切分工具（`str → List[str]`），不涉及业务对象
    - `DocumentChunker`：业务适配器（`Document对象 → List[Chunk对象]`），添加业务逻辑
  - **6 个增值功能**：
    1. **Chunk ID 生成**：为每个文本片段生成唯一且确定性的 ID（格式：`{doc_id}_{index:04d}_{hash_8chars}`）
    2. **元数据继承**：将 Document.metadata 复制到每个 Chunk.metadata（source_path, doc_type, title 等）
    3. **添加 chunk_index**：记录 chunk 在文档中的序号（从 0 开始），用于排序和定位
    4. **建立 source_ref**：记录 Chunk.source_ref 指向父 Document.id，支持溯源
    5. **图片引用按需分发**：扫描每个 chunk 文本中的 `[IMAGE: {id}]` 占位符，从 `Document.metadata["images"]` 中提取该 chunk 实际引用的 ImageRef，写入 `chunk.metadata["images"]`（仅含该 chunk 引用的子集）和 `chunk.metadata["image_refs"]`（image_id 列表）。无占位符的 chunk 不含 `images` 字段。⚠️ 不可简单整体继承或丢弃文档级 `images`，否则下游 C7 ImageCaptioner 将无法定位图片路径。
    6. **类型转换**：将 libs.splitter 的 `List[str]` 转换为符合 core.types 契约的 `List[Chunk]` 对象
- **修改文件**：
  - `src/ingestion/chunking/document_chunker.py`
  - `src/ingestion/chunking/__init__.py`
  - `tests/unit/test_document_chunker.py`
- **实现类/函数**：
  - `DocumentChunker` 类
  - `__init__(settings: Settings)`：通过 SplitterFactory 获取配置的 splitter 实例
  - `split_document(document: Document) -> List[Chunk]`：完整的转换流程
  - `_generate_chunk_id(doc_id: str, index: int, text: str) -> str`：生成稳定 Chunk ID
  - `_inherit_metadata(document: Document, chunk_index: int, chunk_text: str) -> dict`：元数据继承 + 图片引用按需分发逻辑（需要 chunk_text 来扫描 `[IMAGE: id]` 占位符）
- **验收标准**：
  - **配置驱动**：通过修改 settings.yaml 中的 splitter 配置（如 chunk_size），产出的 chunk 数量和长度发生相应变化
  - **ID 唯一性**：每个 Chunk 的 ID 在整个文档中唯一
  - **ID 确定性**：同一 Document 对象重复切分产生相同的 Chunk ID 序列
  - **元数据完整性**：Chunk.metadata 包含所有 Document.metadata 字段 + chunk_index 字段
  - **图片分发正确性**：含 `[IMAGE: id]` 占位符的 chunk 其 `metadata["images"]` 仅包含该 chunk 引用的图片子集；不含占位符的 chunk 无 `images` 字段；`metadata["image_refs"]` 列表与占位符一致
  - **溯源链接**：所有 Chunk.source_ref 正确指向父 Document.id
  - **类型契约**：输出的 Chunk 对象符合 `core/types.py` 中的 Chunk 定义（可序列化、字段完整）
- **测试方法**：`pytest -q tests/unit/test_document_chunker.py`（使用 FakeSplitter 隔离测试，无需真实 LLM/外部依赖）。

### C5：Transform 抽象基类 + ChunkRefiner（规则去噪 + LLM 增强）
- **目标**：定义 `BaseTransform`；实现 `ChunkRefiner`：先做规则去噪，再通过LLM进行智能增强，并提供失败降级机制（LLM异常时回退到规则结果，不阻塞 ingestion）。
- **前置条件**（必须准备）：
  - **必须配置LLM**：在 `config/settings.yaml` 中配置可用的LLM（provider/model/api_key）
  - **环境变量**：设置对应的API key环境变量（`OPENAI_API_KEY`/`OLLAMA_BASE_URL`等）
  - **验证目的**：通过真实LLM测试验证配置正确性和refinement效果
- **修改文件**：
  - `src/ingestion/transform/base_transform.py`（新增）
  - `src/ingestion/transform/chunk_refiner.py`（新增）
  - `src/core/trace/trace_context.py`（新增：最小实现，Phase F 完善）
  - `config/prompts/chunk_refinement.txt`（已存在，需验证内容并补充 {text} 占位符）
  - `tests/fixtures/noisy_chunks.json`（新增：8个典型噪声场景）
  - `tests/unit/test_chunk_refiner.py`（新增：27个单元测试）
  - `tests/integration/test_chunk_refiner_llm.py`（新增：真实LLM集成测试）
- **实现类/函数**：
  - `BaseTransform.transform(chunks, trace) -> List[Chunk]`
  - `ChunkRefiner.__init__(settings, llm?, prompt_path?)`
  - `ChunkRefiner.transform(chunks, trace) -> List[Chunk]`
  - `ChunkRefiner._rule_based_refine(text) -> str`（去空白/页眉页脚/格式标记/HTML注释）
  - `ChunkRefiner._llm_refine(text, trace) -> str | None`（可选 LLM 重写，失败返回 None）
  - `ChunkRefiner._load_prompt(prompt_path?)`（从文件加载prompt模板，支持默认fallback）
- **实现流程建议**：
  1. 先创建 `tests/fixtures/noisy_chunks.json`，包含8个典型噪声场景：
     - typical_noise_scenario: 综合噪声（页眉/页脚/空白）
     - ocr_errors: OCR错误文本
     - page_header_footer: 页眉页脚模式
     - excessive_whitespace: 多余空白
     - format_markers: HTML/Markdown标记
     - clean_text: 干净文本（验证不过度清理）
     - code_blocks: 代码块（验证保留内部格式）
     - mixed_noise: 真实混合场景
  2. 创建 `TraceContext` 占位实现（uuid生成trace_id，record_stage存储阶段数据）
  3. 实现 `BaseTransform` 抽象接口
  4. 实现 `ChunkRefiner._rule_based_refine` 规则去噪逻辑（正则匹配+分段处理）
  5. 编写规则模式单元测试（使用 fixtures 断言清洗效果）
  6. 实现 `_llm_refine` 可选增强（读取 prompt、调用 LLM、错误处理）
  7. 编写 LLM 模式单元测试（mock LLM 断言调用与输出）
  8. 编写降级场景测试（LLM 失败时回退到规则结果，标记 metadata）
  9. **编写真实LLM集成测试并执行验证**（必须执行，验证LLM配置）
- **验收标准**：
  - **单元测试（快速反馈循环）**：
    - 规则模式：对 fixtures 噪声样例能正确去噪（连续空白/页眉页脚/格式标记/分隔线）
    - 保留能力：代码块内部格式不被破坏，Markdown结构完整保留
    - LLM 模式：mock LLM 时能正确调用并返回重写结果，metadata 标记 `refined_by: "llm"`
    - 降级行为：LLM 失败时回退到规则结果，metadata 标记 `refined_by: "rule"` 和 fallback 原因
    - 配置开关：通过 `settings.yaml` 的 `ingestion.chunk_refiner.use_llm` 控制行为
    - 异常处理：单个chunk处理异常不影响其他chunk，保留原文
  - **集成测试（验收必须项）**：
    - ✅ **必须验证真实LLM调用成功**：使用前置条件中配置的LLM进行真实refinement
    - ✅ **必须验证输出质量**：LLM refined文本确实更干净（噪声减少、内容保留）
    - ✅ **必须验证降级机制**：无效模型名称时优雅降级到rule-based，不崩溃
    - 说明：这是验证"前置条件中准备的LLM配置是否正确"的必要步骤
- **测试方法**：
  - **阶段1-单元测试（开发中快速迭代）**：
    ```bash
    pytest tests/unit/test_chunk_refiner.py -v
    # ✅ 27个测试全部通过，使用Mock隔离，无需真实API
    ```
  - **阶段2-集成测试（验收必须执行）**：
    ```bash
    # 1. 运行真实LLM集成测试（必须）
    pytest tests/integration/test_chunk_refiner_llm.py -v -s
    # ✅ 验证LLM配置正确，refinement效果符合预期
    # ⚠️ 会产生真实API调用与费用
    
    # 2. Review打印输出，确认精炼质量
    # - 噪声是否被有效去除？
    # - 有效内容是否完整保留？
    # - 降级机制是否正常工作？
    ```
  - **测试分层逻辑**：
    - 单元测试：验证代码逻辑正确
    - 集成测试：验证系统可用性
    - 两者互补，缺一不可

### C6：MetadataEnricher（规则增强 + 可选 LLM 增强 + 降级）
- **目标**：实现元数据增强模块：提供规则增强的默认实现，并重点支持 LLM 增强（配置已就绪，LLM 开关打开）。利用 LLM 对 chunk 进行高质量的 title 生成、summary 摘要和 tags 提取。同时保留失败降级机制，确保不阻塞 ingestion。
- **修改文件**：
  - `src/ingestion/transform/metadata_enricher.py`
  - `tests/unit/test_metadata_enricher_contract.py`
- **验收标准**：
  - 规则模式：作为兜底逻辑，输出 metadata 必须包含 `title/summary/tags`（至少非空）。
  - **LLM 模式（核心）**：在 LLM 打开的情况下，确保真实调用 LLM（或高质量 Mock）并生成语义丰富的 metadata。需验证在有真实 LLM 配置下的连通性与效果。
  - 降级行为：LLM 调用失败时回退到规则模式结果（可在 metadata 标记降级原因，但不抛出致命异常）。
- **测试方法**：`pytest -q tests/unit/test_metadata_enricher_contract.py`，并确保包含开启 LLM 的集成测试用例。

### C7：ImageCaptioner（可选生成 caption + 降级不阻塞）
- **目标**：实现 `image_captioner.py`：当启用 Vision LLM 且存在 image_refs 时生成 caption 并写回 chunk metadata；当禁用/不可用/异常时走降级路径，不阻塞 ingestion。
- **修改文件**：
  - `src/ingestion/transform/image_captioner.py`
  - `config/prompts/image_captioning.txt`（作为默认 prompt 来源；可在测试中注入替代文本）
  - `tests/unit/test_image_captioner_fallback.py`
- **验收标准**：
  - 启用模式：存在 image_refs 时会生成 caption 并写入 metadata（测试中用 mock Vision LLM 断言调用与输出）。
  - 降级模式：当配置禁用或异常时，chunk 保留 image_refs，但不生成 caption 且标记 `has_unprocessed_images`。
- **测试方法**：`pytest -q tests/unit/test_image_captioner_fallback.py`。

### C8：DenseEncoder（依赖 libs.embedding）
- **目标**：实现 `dense_encoder.py`，把 chunks.text 批量送入 `BaseEmbedding`。
- **修改文件**：
  - `src/ingestion/embedding/dense_encoder.py`
  - `tests/unit/test_dense_encoder.py`
- **验收标准**：encoder 输出向量数量与 chunks 数量一致，维度一致。
- **测试方法**：`pytest -q tests/unit/test_dense_encoder.py`。

### C9：SparseEncoder（BM25 统计与输出契约）
- **目标**：实现 `sparse_encoder.py`：对 chunks 建立 BM25 所需统计（可先仅输出 term weights 结构，索引落地下一步做）。
- **修改文件**：
  - `src/ingestion/embedding/sparse_encoder.py`
  - `tests/unit/test_sparse_encoder.py`
- **验收标准**：输出结构可用于 bm25_indexer；对空文本有明确行为。
- **测试方法**：`pytest -q tests/unit/test_sparse_encoder.py`。

### C10：BatchProcessor（批处理编排）
- **目标**：实现 `batch_processor.py`：将 chunks 分 batch，驱动 dense/sparse 编码，记录批次耗时（为 trace 预留）。
- **修改文件**：
  - `src/ingestion/embedding/batch_processor.py`
  - `tests/unit/test_batch_processor.py`
- **验收标准**：batch_size=2 时对 5 chunks 分成 3 批，且顺序稳定。
- **测试方法**：`pytest -q tests/unit/test_batch_processor.py`。

---

**━━━━ 存储阶段分界线：以下任务负责将编码结果持久化 ━━━━**

> **说明**：C8-C10完成了Dense和Sparse的编码工作，C11-C13负责将编码结果存储到不同的后端。
> - **C11 (BM25Indexer)**：处理Sparse编码结果 → 构建倒排索引 → 存储到文件系统
> - **C12 (VectorUpserter)**：处理Dense编码结果 → 生成稳定ID → 存储到向量数据库
> - **C13 (ImageStorage)**：处理图片数据 → 文件存储 + 索引映射

---

### C11：BM25Indexer（倒排索引构建与持久化）
- **目标**：实现 `bm25_indexer.py`：接收 SparseEncoder 的term statistics输出，计算IDF，构建倒排索引，并持久化到 `data/db/bm25/`。
- **核心功能**：
  - 计算 IDF (Inverse Document Frequency)：`IDF(term) = log((N - df + 0.5) / (df + 0.5))`
  - 构建倒排索引结构：`{term: {idf, postings: [{chunk_id, tf, doc_length}]}}`
  - 索引序列化与加载（支持增量更新与重建）
- **修改文件**：
  - `src/ingestion/storage/bm25_indexer.py`
  - `tests/unit/test_bm25_indexer_roundtrip.py`
- **验收标准**：
  - build 后能 load 并对同一语料查询返回稳定 top ids
  - IDF计算准确（可用已知语料对比验证）
  - 支持索引重建与增量更新
- **测试方法**：`pytest -q tests/unit/test_bm25_indexer_roundtrip.py`。
- **备注**：本任务完成Sparse路径的最后一环，为D3 (SparseRetriever) 提供可查询的BM25索引。

### C12：VectorUpserter（向量存储与幂等性保证）
- **目标**：实现 `vector_upserter.py`：接收 DenseEncoder 的向量输出，生成稳定的 `chunk_id`，并调用 VectorStore 进行幂等写入。
- **核心功能**：
  - 生成确定性 chunk_id：`hash(source_path + chunk_index + content_hash[:8])`
  - 调用 `BaseVectorStore.upsert()` 写入向量数据库
  - 保证幂等性：同一内容重复写入不产生重复记录
- **修改文件**：
  - `src/ingestion/storage/vector_upserter.py`
  - `tests/unit/test_vector_upserter_idempotency.py`
- **验收标准**：
  - 同一 chunk 两次 upsert 产生相同 id
  - 内容变更时 id 变更
  - 支持批量 upsert 且保持顺序
- **测试方法**：`pytest -q tests/unit/test_vector_upserter_idempotency.py`。
- **备注**：本任务完成Dense路径的最后一环，为D2 (DenseRetriever) 提供可查询的向量数据库。

### C13：ImageStorage（图片文件存储与索引表契约）
- **目标**：实现 `image_storage.py`：保存图片到 `data/images/{collection}/`，并使用 **SQLite** 记录 image_id→path 映射。
- **修改文件**：
  - `src/ingestion/storage/image_storage.py`
  - `tests/unit/test_image_storage.py`
- **验收标准**：保存后文件存在；查找 image_id 返回正确路径；映射关系持久化在 `data/db/image_index.db`。
- **技术方案**：
  - 复用项目已有的 SQLite 架构模式（参考 `file_integrity.py` 的 `SQLiteIntegrityChecker`）
  - 数据库表结构：
    ```sql
    CREATE TABLE image_index (
        image_id TEXT PRIMARY KEY,
        file_path TEXT NOT NULL,
        collection TEXT,
        doc_hash TEXT,
        page_num INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_collection ON image_index(collection);
    CREATE INDEX idx_doc_hash ON image_index(doc_hash);
    ```
  - 提供并发安全访问（WAL 模式）
  - 支持按 collection 批量查询
- **测试方法**：`pytest -q tests/unit/test_image_storage.py`。

### C14：Pipeline 编排（MVP 串起来）
- **目标**：实现 `pipeline.py`：串行执行（integrity→load→split→transform→encode→store），并对失败步骤做清晰异常。
- **修改文件**：
  - `src/ingestion/pipeline.py`
  - `tests/integration/test_ingestion_pipeline.py`
- **测试数据**：
  - **主测试文档**：`tests/fixtures/sample_documents/complex_technical_doc.pdf`
    - 8章节技术文档（~21KB）
    - 包含3张嵌入图片（需测试图片提取和描述）
    - 包含5个表格（测试表格内容解析）
    - 多页多段落（测试完整分块流程）
  - **辅助测试**：`tests/fixtures/sample_documents/simple.pdf`（简单场景回归）
- **验收标准**：
  - 对 `complex_technical_doc.pdf` 跑完整 pipeline，成功输出：
    - 向量索引文件到 ChromaDB
    - BM25 索引文件到 `data/db/bm25/`
    - 提取的图片到 `data/images/` (SHA256命名)
  - Pipeline 日志清晰展示各阶段进度
  - 失败步骤抛出明确异常信息
- **测试方法**：`pytest -v tests/integration/test_ingestion_pipeline.py`。

### C15：脚本入口 ingest.py（离线可用）
- **目标**：实现 `scripts/ingest.py`，支持 `--collection`、`--path`、`--force`，并调用 pipeline。
- **修改文件**：
  - `scripts/ingest.py`
  - `tests/e2e/test_data_ingestion.py`
- **验收标准**：命令行可运行并在 `data/db` 产生产物；重复运行在未变更时跳过。
- **测试方法**：`pytest -q tests/e2e/test_data_ingestion.py`（尽量用临时目录）。

---

## 阶段 D：Retrieval MVP（目标：能 query 并返回 Top-K chunks）

### D1：QueryProcessor（关键词提取 + filters 结构）
- **目标**：实现 `query_processor.py`：关键词提取（先规则/分词），并解析通用 filters 结构（可空实现）。
- **修改文件**：
  - `src/core/query_engine/query_processor.py`
  - `tests/unit/test_query_processor.py`
- **验收标准**：对输入 query 输出 `keywords` 非空（可根据停用词策略），filters 为 dict。
- **测试方法**：`pytest -q tests/unit/test_query_processor.py`。

### D2：DenseRetriever（调用 VectorStore.query）
- **目标**：实现 `dense_retriever.py`，组合 `EmbeddingClient`（query 向量化）+ `VectorStore`（向量检索），完成语义召回。
- **前置任务**：
  1. 需先在 `src/core/types.py` 中定义 `RetrievalResult` 类型（包含 `chunk_id`, `score`, `text`, `metadata` 字段）
  2. 需确认 ChromaStore.query() 返回结果包含 text（当前存储在 documents 字段，需补充返回）
- **修改文件**：
  - `src/core/types.py`（新增 `RetrievalResult` 类型）
  - `src/libs/vector_store/chroma_store.py`（修复：query 返回结果需包含 text 字段）
  - `src/core/query_engine/dense_retriever.py`
  - `tests/unit/test_dense_retriever.py`
- **实现类/函数**：
  - `RetrievalResult` dataclass：`chunk_id: str`, `score: float`, `text: str`, `metadata: Dict`
  - `DenseRetriever.__init__(settings, embedding_client?, vector_store?)`：支持依赖注入用于测试
  - `DenseRetriever.retrieve(query: str, top_k: int, filters?: dict, trace?) -> List[RetrievalResult]`
  - 内部流程：`query → embedding_client.embed([query]) → vector_store.query(vector, top_k, filters) → 从返回结果提取 text → 规范化结果`
- **验收标准**：
  - `RetrievalResult` 类型已定义并可序列化
  - ChromaStore.query() 返回结果包含 `text` 字段
  - 对输入 query 能生成 embedding 并调用 VectorStore 检索
  - 返回结果包含 `chunk_id`、`score`、`text`、`metadata`
  - mock EmbeddingClient 和 VectorStore 时能正确编排调用
- **测试方法**：`pytest -q tests/unit/test_dense_retriever.py`（mock embedding + vector store）。

### D3：SparseRetriever（BM25 查询）
- **目标**：实现 `sparse_retriever.py`：从 `data/db/bm25/` 载入索引并查询。
- **前置任务**：需在 `BaseVectorStore` 和 `ChromaStore` 中添加 `get_by_ids()` 方法，用于根据 chunk_id 批量获取 text 和 metadata
- **修改文件**：
  - `src/libs/vector_store/base_vector_store.py`（新增 `get_by_ids()` 抽象方法）
  - `src/libs/vector_store/chroma_store.py`（实现 `get_by_ids()` 方法）
  - `src/core/query_engine/sparse_retriever.py`
  - `tests/unit/test_sparse_retriever.py`
- **实现类/函数**：
  - `BaseVectorStore.get_by_ids(ids: List[str]) -> List[Dict]`：根据 ID 批量获取记录
  - `ChromaStore.get_by_ids(ids: List[str]) -> List[Dict]`：调用 ChromaDB 的 get 方法
  - `SparseRetriever.__init__(settings, bm25_indexer?, vector_store?)`：支持依赖注入用于测试
  - `SparseRetriever.retrieve(keywords: List[str], top_k: int, trace?) -> List[RetrievalResult]`
  - 内部流程：
    1. `keywords → bm25_indexer.query(keywords, top_k) → [{chunk_id, score}]`
    2. `chunk_ids → vector_store.get_by_ids(chunk_ids) → [{id, text, metadata}]`
    3. 合并 score 与 text/metadata，组装为 `RetrievalResult` 列表
  - 注意：keywords 来自 `QueryProcessor.process()` 的 `ProcessedQuery.keywords`
- **验收标准**：
  - `BaseVectorStore.get_by_ids()` 和 `ChromaStore.get_by_ids()` 已实现
  - 对已构建索引的 fixtures 语料，关键词检索命中预期 chunk_id
  - 返回结果包含完整的 text 和 metadata
- **测试方法**：`pytest -q tests/unit/test_sparse_retriever.py`。

### D4：Fusion（RRF 实现）
- **目标**：实现 `fusion.py`：RRF 融合 dense/sparse 排名并输出统一排序。
- **修改文件**：
  - `src/core/query_engine/fusion.py`
  - `tests/unit/test_fusion_rrf.py`
- **验收标准**：对构造的排名输入输出 deterministic；k 参数可配置。
- **测试方法**：`pytest -q tests/unit/test_fusion_rrf.py`。

### D5：HybridSearch 编排
- **目标**：实现 `hybrid_search.py`：编排 Dense + Sparse + Fusion 的完整混合检索流程，并集成 Metadata 过滤逻辑。
- **前置依赖**：D1（QueryProcessor）、D2（DenseRetriever）、D3（SparseRetriever）、D4（Fusion）
- **修改文件**：
  - `src/core/query_engine/hybrid_search.py`
  - `tests/integration/test_hybrid_search.py`
- **实现类/函数**：
  - `HybridSearch.__init__(settings, query_processor, dense_retriever, sparse_retriever, fusion)`
  - `HybridSearch.search(query: str, top_k: int, filters?: dict, trace?) -> List[RetrievalResult]`
  - `HybridSearch._apply_metadata_filters(candidates, filters) -> List[RetrievalResult]`：后置过滤兜底
  - 内部流程：`query_processor.process() → 并行(dense.retrieve + sparse.retrieve) → fusion.fuse() → metadata_filter → Top-K`
- **验收标准**：
  - 对 fixtures 数据，能返回 Top-K（包含 chunk 文本与 metadata）
  - 支持 filters 参数（如 `collection`、`doc_type`）进行过滤
  - Dense/Sparse 任一路径失败时能降级到单路结果
- **测试方法**：`pytest -q tests/integration/test_hybrid_search.py`。

### D6：Reranker（Core 层编排 + fallback）
- **目标**：实现 `core/query_engine/reranker.py`：接入 `libs.reranker` 后端，失败/超时回退 fusion 排名。
- **修改文件**：
  - `src/core/query_engine/reranker.py`
  - `config/prompts/rerank.txt`（仅当启用 LLM Rerank 后端时使用）
  - `tests/unit/test_reranker_fallback.py`
- **验收标准**：模拟后端异常时不影响最终返回，且标记 fallback=true。
- **测试方法**：`pytest -q tests/unit/test_reranker_fallback.py`。

### D7：脚本入口 query.py（查询可用）
- **目标**：实现 `scripts/query.py`，作为在线查询的命令行入口，调用完整的 HybridSearch + Reranker 流程并输出检索结果。
- **前置依赖**：D5（HybridSearch）、D6（Reranker）
- **修改文件**：
  - `scripts/query.py`
- **实现功能**：
  - **参数支持**：
    - `--query "问题"`：必填，查询文本
    - `--top-k 10`：可选，返回结果数量（默认 10）
    - `--collection xxx`：可选，限定检索集合
    - `--verbose`：可选，显示各阶段中间结果
    - `--no-rerank`：可选，跳过 Reranker 阶段
  - **输出内容**：
    - 默认模式：Top-K 结果（序号、score、文本摘要、来源文件、页码）
    - Verbose 模式：额外显示 Dense 召回结果、Sparse 召回结果、Fusion 结果、Rerank 结果
  - **内部流程**：
    1. 加载配置 `Settings`
    2. 初始化组件（EmbeddingClient、VectorStore、BM25Indexer、Reranker）
    3. 创建 `QueryProcessor`、`DenseRetriever`、`SparseRetriever`、`HybridSearch` 实例
    4. 调用 `HybridSearch.search()` 获取候选结果
    5. 调用 `Reranker.rerank()` 进行精排（除非 `--no-rerank`）
    6. 格式化输出结果
- **验收标准**：
  - 命令行可运行：`python scripts/query.py --query "如何配置 Azure？"`
  - 返回格式化的 Top-K 检索结果
  - `--verbose` 模式显示各阶段中间结果（便于调试）
  - 无数据时返回友好提示（如"未找到相关文档，请先运行 ingest.py 摄取数据"）
- **测试方法**：手动运行 `python scripts/query.py --query "测试查询" --verbose`（依赖已摄取的数据）。
- **与 MCP Tool 的关系**：
  - `scripts/query.py` 是开发调试用的命令行工具
  - `E3 query_knowledge_hub` 是生产环境的 MCP Tool
  - 两者共享 Core 层逻辑（HybridSearch + Reranker），但入口和输出格式不同

---

## 阶段 E：MCP Server 层与 Tools（目标：对外可用的 MCP tools）

### E1：MCP Server 入口与 Stdio 约束
- **目标**：实现 `mcp_server/server.py`：遵循"stdout 只输出 MCP 消息，日志到 stderr"。
- **修改文件**：
  - `src/mcp_server/server.py`
  - `tests/integration/test_mcp_server.py`
- **验收标准**：启动 server 能完成 initialize；stderr 有日志但 stdout 不污染。
- **测试方法**：`pytest -q tests/integration/test_mcp_server.py`（子进程方式）。

### E2：Protocol Handler 协议解析与能力协商
- **目标**：实现 `mcp_server/protocol_handler.py`：封装 JSON-RPC 2.0 协议解析，处理 `initialize`、`tools/list`、`tools/call` 三类核心方法，并实现规范的错误处理。
- **修改文件**：
  - `src/mcp_server/protocol_handler.py`
  - `tests/unit/test_protocol_handler.py`
- **实现要点**：
  - **ProtocolHandler 类**：
    - `handle_initialize(params)` → 返回 server capabilities（支持的 tools 列表、版本信息）
    - `handle_tools_list()` → 返回已注册的 tool schema（name, description, inputSchema）
    - `handle_tools_call(name, arguments)` → 路由到具体 tool 执行，捕获异常并转换为 JSON-RPC error
  - **错误码规范**：遵循 JSON-RPC 2.0（-32600 Invalid Request, -32601 Method not found, -32602 Invalid params, -32603 Internal error）
  - **能力协商**：在 `initialize` 响应中声明 `capabilities.tools`
- **验收标准**：
  - 发送 `initialize` 请求能返回正确的 `serverInfo` 和 `capabilities`
  - 发送 `tools/list` 能返回已注册 tools 的 schema
  - 发送 `tools/call` 能正确路由并返回结果或规范错误
  - **错误处理**：无效方法返回 -32601，参数错误返回 -32602，内部异常返回 -32603 且不泄露堆栈
- **测试方法**：`pytest -q tests/unit/test_protocol_handler.py`。

### E3：实现 tool：query_knowledge_hub
- **目标**：实现 `tools/query_knowledge_hub.py`：调用 HybridSearch + Reranker，构建带引用的响应，返回 Markdown + structured citations。
- **前置依赖**：D5（HybridSearch）、D6（Reranker）、E1（Server）、E2（Protocol Handler）
- **修改文件**：
  - `src/mcp_server/tools/query_knowledge_hub.py`
  - `src/core/response/response_builder.py`（新增：构建 MCP 响应格式）
  - `src/core/response/citation_generator.py`（新增：生成引用信息）
  - `tests/unit/test_response_builder.py`（新增）
  - `tests/integration/test_mcp_server.py`（补用例）
- **实现类/函数**：
  - `ResponseBuilder.build(retrieval_results, query) -> MCPResponse`：构建 MCP 格式响应
  - `CitationGenerator.generate(retrieval_results) -> List[Citation]`：生成引用列表
  - `query_knowledge_hub(query, top_k?, collection?) -> MCPToolResult`：Tool 入口函数
- **验收标准**：
  - tool 返回 `content[0]` 为可读 Markdown（含 `[1]`、`[2]` 等引用标注）
  - `structuredContent.citations` 包含 `source`/`page`/`chunk_id`/`score` 字段
  - 无结果时返回友好提示而非空数组
- **测试方法**：`pytest -q tests/integration/test_mcp_server.py -k query_knowledge_hub`。

### E4：实现 tool：list_collections
- **目标**：实现 `tools/list_collections.py`：列出 `data/documents/` 下集合并附带统计（可延后到下一步）。
- **修改文件**：
  - `src/mcp_server/tools/list_collections.py`
  - `tests/unit/test_list_collections.py`
- **验收标准**：对 fixtures 中的目录结构能返回集合名列表。
- **测试方法**：`pytest -q tests/unit/test_list_collections.py`。

### E5：实现 tool：get_document_summary
- **目标**：实现 `tools/get_document_summary.py`：按 doc_id 返回 title/summary/tags（可先从 metadata/缓存取）。
- **修改文件**：
  - `src/mcp_server/tools/get_document_summary.py`
  - `tests/unit/test_get_document_summary.py`
- **验收标准**：对不存在 doc_id 返回规范错误；存在时返回结构化信息。
- **测试方法**：`pytest -q tests/unit/test_get_document_summary.py`。

### E6：多模态返回组装（Text + Image）
- **目标**：实现 `multimodal_assembler.py`：命中 chunk 含 image_refs 时读取图片并 base64 返回 ImageContent。
- **修改文件**：
  - `src/core/response/multimodal_assembler.py`
  - `tests/integration/test_mcp_server.py`（补图像返回用例）
- **验收标准**：返回 content 中包含 image type，mimeType 正确，data 为 base64 字符串。
- **测试方法**：`pytest -q tests/integration/test_mcp_server.py -k image`。

---

## 阶段 F：Trace 基础设施与打点（目标：Ingestion + Query 双链路可追踪）

### F1：TraceContext 增强（finish + 耗时统计 + trace_type）
- **目标**：增强已有的 `TraceContext`（C5 已实现基础版），添加 `finish()` 方法、耗时统计、`trace_type` 字段（区分 query/ingestion）、`to_dict()` 序列化功能。
- **修改文件**：
  - `src/core/trace/trace_context.py`（增强：添加 trace_type/finish/elapsed_ms/to_dict）
  - `src/core/trace/trace_collector.py`（新增：收集并持久化 trace）
  - `tests/unit/test_trace_context.py`（补充 finish/to_dict 相关测试）
- **实现类/函数**：
  - `TraceContext.__init__(trace_type: str = "query")`：支持 `"query"` 或 `"ingestion"` 类型
  - `TraceContext.finish() -> None`：标记 trace 结束，计算总耗时
  - `TraceContext.elapsed_ms(stage_name?) -> float`：获取指定阶段或总耗时
  - `TraceContext.to_dict() -> dict`：序列化为可 JSON 输出的字典（含 trace_type）
  - `TraceCollector.collect(trace: TraceContext) -> None`：收集 trace 并触发持久化
- **验收标准**：
  - `record_stage` 追加阶段数据（已有）
  - `finish()` 后 `to_dict()` 输出包含 `trace_id`、`trace_type`、`started_at`、`finished_at`、`total_elapsed_ms`、`stages`
  - 输出 dict 可直接 `json.dumps()` 序列化
- **测试方法**：`pytest -q tests/unit/test_trace_context.py`。


### F2：结构化日志 logger（JSON Lines）
- **目标**：增强 `observability/logger.py`，支持 JSON Lines 格式输出，并实现 trace 持久化到 `logs/traces.jsonl`。
- **修改文件**：
  - `src/observability/logger.py`（增强：添加 JSONFormatter + FileHandler）
  - `tests/unit/test_jsonl_logger.py`
- **实现类/函数**：
  - `JSONFormatter`：自定义 logging Formatter，输出 JSON 格式
  - `get_trace_logger() -> logging.Logger`：获取配置了 JSON Lines 输出的 logger
  - `write_trace(trace_dict: dict) -> None`：将 trace 字典写入 `logs/traces.jsonl`
- **与 F1 的分工**：
  - F1 负责 TraceContext 的数据结构（含 `trace_type`）和 `finish()` 方法
  - F2 负责将 `trace.to_dict()` 的结果持久化到文件
- **验收标准**：写入一条 trace 后文件新增一行合法 JSON，包含 `trace_type` 字段。
- **测试方法**：`pytest -q tests/unit/test_jsonl_logger.py`。

### F3：在 Query 链路打点
- **目标**：在 HybridSearch/Rerank 中注入 TraceContext（`trace_type="query"`），利用 B 阶段抽象接口中预留的 `trace` 参数，显式调用 `trace.record_stage()` 记录各阶段数据。
- **前置依赖**：D5（HybridSearch）、D6（Reranker）、F1（TraceContext 增强）、F2（结构化日志）
- **修改文件**：
  - `src/core/query_engine/hybrid_search.py`（增加 trace 记录：dense/sparse/fusion 阶段）
  - `src/core/query_engine/reranker.py`（增加 trace 记录：rerank 阶段）
  - `tests/integration/test_hybrid_search.py`（断言 trace 中存在各阶段）
- **说明**：B 阶段的接口已预留 `trace: TraceContext | None = None` 参数，本任务负责在调用时传入实际的 TraceContext 实例，并在各阶段记录 `method`/`provider`/`details` 字段。
- **验收标准**：
  - 一次查询生成 trace，包含 `query_processing`/`dense_retrieval`/`sparse_retrieval`/`fusion`/`rerank` 阶段
  - 每个阶段记录 `elapsed_ms` 耗时字段和 `method` 字段
  - `trace.to_dict()` 中 `trace_type == "query"`
- **测试方法**：`pytest -q tests/integration/test_hybrid_search.py`。

### F4：在 Ingestion 链路打点
- **目标**：在 IngestionPipeline 中注入 TraceContext（`trace_type="ingestion"`），记录各摄取阶段的处理数据。
- **前置依赖**：C5（Pipeline）、F1（TraceContext 增强）、F2（结构化日志）
- **修改文件**：
  - `src/ingestion/pipeline.py`（增加 trace 传递：load/split/transform/embed/upsert 阶段）
  - `tests/integration/test_ingestion_pipeline.py`（断言 trace 中存在各阶段）
- **验收标准**：
  - 一次摄取生成 trace，包含 `load`/`split`/`transform`/`embed`/`upsert` 阶段
  - 每个阶段记录 `elapsed_ms`、`method`（如 markitdown/recursive/chroma）和处理详情
  - `trace.to_dict()` 中 `trace_type == "ingestion"`
- **测试方法**：`pytest -q tests/integration/test_ingestion_pipeline.py`。

### F5：Pipeline 进度回调 (on_progress)
- **目标**：在 `IngestionPipeline.run()` 方法中新增可选 `on_progress` 回调参数，支持外部实时获取处理进度。
- **前置依赖**：F4（Ingestion 打点）
- **修改文件**：
  - `src/ingestion/pipeline.py`（在各阶段调用 `on_progress(stage_name, current, total)`）
  - `tests/unit/test_pipeline_progress.py`（新增：验证回调被正确调用）
- **实现要点**：
  - 回调签名：`on_progress(stage_name: str, current: int, total: int)`
  - `on_progress` 为 `None` 时完全不影响现有行为
  - 各阶段在处理每个 batch 或完成时触发回调
- **验收标准**：Pipeline 运行时传入 mock 回调，断言各阶段均被调用且参数正确。
- **测试方法**：`pytest -q tests/unit/test_pipeline_progress.py`。

---

## 阶段 G：可视化管理平台 Dashboard（目标：六页面完整可视化管理）

### G1：Dashboard 基础架构与系统总览页
- **目标**：搭建 Streamlit 多页面应用框架，实现系统总览页面（展示组件配置与数据统计）。
- **前置依赖**：F1-F2（Trace 基础设施）
- **修改文件**：
  - `src/observability/dashboard/app.py`（重写：多页面导航架构）
  - `src/observability/dashboard/pages/overview.py`（新增：系统总览页面）
  - `src/observability/dashboard/services/config_service.py`（新增：配置读取服务）
  - `scripts/start_dashboard.py`（新增：Dashboard 启动脚本）
- **实现要点**：
  - `app.py` 使用 `st.navigation()` 注册六个页面（未完成的页面显示占位提示）
  - Overview 页面：读取 `Settings` 展示组件卡片，调用 `ChromaStore.get_collection_stats()` 展示数据统计
  - `ConfigService`：封装 Settings 读取，格式化组件配置信息
- **验收标准**：`streamlit run src/observability/dashboard/app.py` 可启动，总览页展示当前配置信息。
- **测试方法**：手动运行 `python scripts/start_dashboard.py` 并验证页面渲染。

### G2：DocumentManager 实现
- **目标**：实现 `src/ingestion/document_manager.py`：跨存储的文档生命周期管理（list/delete/stats）。
- **前置依赖**：C5（Pipeline + 各存储模块已就绪）
- **修改文件**：
  - `src/ingestion/document_manager.py`（新增）
  - `src/libs/vector_store/chroma_store.py`（增强：添加 `delete_by_metadata`）
  - `src/ingestion/storage/bm25_indexer.py`（增强：添加 `remove_document`）
  - `src/libs/loader/file_integrity.py`（增强：添加 `remove_record` + `list_processed`）
  - `tests/unit/test_document_manager.py`（新增）
- **实现类/函数**：
  - `DocumentManager.__init__(chroma_store, bm25_indexer, image_storage, file_integrity)`
  - `DocumentManager.list_documents(collection?) -> List[DocumentInfo]`
  - `DocumentManager.get_document_detail(doc_id) -> DocumentDetail`
  - `DocumentManager.delete_document(source_path, collection) -> DeleteResult`
  - `DocumentManager.get_collection_stats(collection?) -> CollectionStats`
- **验收标准**：
  - `list_documents` 返回已摄入文档列表（source、chunk 数、图片数）
  - `delete_document` 协调删除 Chroma + BM25 + ImageStorage + FileIntegrity 四个存储
  - 删除后再次 list 不包含已删除文档
- **测试方法**：`pytest -q tests/unit/test_document_manager.py`。

### G3：数据浏览器页面
- **目标**：实现 Dashboard 数据浏览器页面（查看文档列表、Chunk 详情、图片预览）。
- **前置依赖**：G1（Dashboard 架构）、G2（DocumentManager）
- **修改文件**：
  - `src/observability/dashboard/pages/data_browser.py`（新增）
  - `src/observability/dashboard/services/data_service.py`（新增：封装 ChromaStore/ImageStorage 读取）
- **实现要点**：
  - 文档列表视图：展示 source_path、集合、chunk 数、摄入时间；支持集合筛选
  - Chunk 详情视图：点击文档展开所有 chunk，显示内容（可折叠）、metadata 字段、关联图片
  - `DataService`：封装 `ChromaStore.get_by_metadata()` 和 `ImageStorage.list_images()` 调用
- **验收标准**：可在 Dashboard 中浏览已摄入的文档和 chunk 详情。
- **测试方法**：手动验证（先 ingest 样例数据，再在 Dashboard 浏览）。

### G4：Ingestion 管理页面
- **目标**：实现 Dashboard Ingestion 管理页面（文件上传触发摄取、进度展示、文档删除）。
- **前置依赖**：G2（DocumentManager）、G3（DataService）、F5（on_progress 回调）
- **修改文件**：
  - `src/observability/dashboard/pages/ingestion_manager.py`（新增）
- **实现要点**：
  - 文件上传：`st.file_uploader` 选择文件 + 集合选择
  - 摄取触发：调用 `IngestionPipeline.run(on_progress=...)` + `st.progress()` 实时进度
  - 文档删除：在文档列表中提供删除按钮，调用 `DocumentManager.delete_document()`
- **验收标准**：可在 Dashboard 中上传文件触发摄取、看到实时进度条、删除已有文档。
- **测试方法**：手动验证（上传 PDF → 观察进度 → 删除 → 确认已移除）。

### G5：Ingestion 追踪页面
- **目标**：实现 Dashboard Ingestion 追踪页面（摄取历史列表、阶段耗时瀑布图）。
- **前置依赖**：F4（Ingestion 打点）、G1（Dashboard 架构）
- **修改文件**：
  - `src/observability/dashboard/pages/ingestion_traces.py`（新增）
  - `src/observability/dashboard/services/trace_service.py`（新增：解析 traces.jsonl）
- **实现要点**：
  - 历史列表：按时间倒序展示 `trace_type == "ingestion"` 记录
  - 详情页：横向条形图展示 load/split/transform/embed/upsert 耗时分布
  - `TraceService`：读取 `logs/traces.jsonl`，解析为 Trace 对象列表
- **验收标准**：执行 ingest 后，Dashboard 显示对应的追踪记录与耗时瀑布图。
- **测试方法**：手动验证（先 ingest → 打开 Dashboard → 查看追踪）。

### G6：Query 追踪页面
- **目标**：实现 Dashboard Query 追踪页面（查询历史、Dense/Sparse 对比、Rerank 变化）。
- **前置依赖**：F3（Query 打点）、G1（Dashboard 架构）、G5（TraceService 已实现）
- **修改文件**：
  - `src/observability/dashboard/pages/query_traces.py`（新增）
- **实现要点**：
  - 历史列表：按时间倒序展示 `trace_type == "query"` 记录，支持按 Query 关键词搜索
  - 详情页：耗时瀑布图 + Dense vs Sparse 并列对比 + Rerank 前后排名变化
- **验收标准**：执行 query 后，Dashboard 显示查询追踪详情与各阶段对比。
- **测试方法**：手动验证（先 query → 打开 Dashboard → 查看追踪）。

---

## 阶段 H：评估体系（目标：可插拔评估 + 可量化回归）

### H1：RagasEvaluator 实现
- **目标**：实现 `ragas_evaluator.py`：封装 Ragas 框架，实现 `BaseEvaluator` 接口。
- **修改文件**：
  - `src/observability/evaluation/ragas_evaluator.py`（新增）
  - `src/libs/evaluator/evaluator_factory.py`（注册 ragas provider）
  - `tests/unit/test_ragas_evaluator.py`（新增）
- **实现类/函数**：
  - `RagasEvaluator(BaseEvaluator)`：实现 `evaluate()` 方法
  - 支持指标：Faithfulness, Answer Relevancy, Context Precision
  - 优雅降级：Ragas 未安装时抛出明确的 `ImportError` 提示
- **验收标准**：mock LLM 环境下，`evaluate()` 返回包含 faithfulness/answer_relevancy 的 metrics 字典。
- **测试方法**：`pytest -q tests/unit/test_ragas_evaluator.py`。

### H2：CompositeEvaluator 实现
- **目标**：实现 `composite_evaluator.py`：组合多个 Evaluator 并行执行，汇总结果。
- **修改文件**：
  - `src/observability/evaluation/composite_evaluator.py`（新增）
  - `tests/unit/test_composite_evaluator.py`（新增）
- **实现类/函数**：
  - `CompositeEvaluator.__init__(evaluators: List[BaseEvaluator])`
  - `CompositeEvaluator.evaluate() -> dict`：并行执行所有 evaluator，合并 metrics
  - 配置驱动：`evaluation.backends: [ragas, custom]` → 工厂自动组合
- **验收标准**：配置两个 evaluator 时，返回的 metrics 包含两者的指标。
- **测试方法**：`pytest -q tests/unit/test_composite_evaluator.py`。

### H3：EvalRunner + Golden Test Set
- **目标**：实现 `eval_runner.py`：读取 `tests/fixtures/golden_test_set.json`，跑 retrieval 并产出 metrics。
- **前置依赖**：D5（HybridSearch）、H1-H2（评估器）
- **修改文件**：
  - `src/observability/evaluation/eval_runner.py`（新增）
  - `tests/fixtures/golden_test_set.json`（新增：黄金测试集）
  - `scripts/evaluate.py`（新增：评估运行脚本）
- **实现类/函数**：
  - `EvalRunner.__init__(settings, hybrid_search, evaluator)`
  - `EvalRunner.run(test_set_path) -> EvalReport`：运行评估并返回报告
  - `EvalReport`：包含 hit_rate, mrr, 各 query 结果详情
- **golden_test_set.json 格式**：
  ```json
  {
    "test_cases": [
      {
        "query": "如何配置 Azure OpenAI？",
        "expected_chunk_ids": ["chunk_abc_001", "chunk_abc_002"],
        "expected_sources": ["config_guide.pdf"]
      }
    ]
  }
  ```
- **验收标准**：`python scripts/evaluate.py` 可运行，输出 metrics。
- **测试方法**：`pytest -q tests/integration/test_hybrid_search.py` 或 `python scripts/evaluate.py`。

### H4：评估面板页面
- **目标**：实现 Dashboard 评估面板页面（运行评估、查看指标、历史对比）。
- **前置依赖**：H3（EvalRunner）、G1（Dashboard 架构）
- **修改文件**：
  - `src/observability/dashboard/pages/evaluation_panel.py`（实现：替换占位提示）
- **实现要点**：
  - 选择评估后端与 golden test set
  - 点击运行，展示评估结果（hit_rate、mrr、各 query 明细）
  - 可选：历史评估结果对比图
- **验收标准**：可在 Dashboard 中运行评估并查看指标。
- **测试方法**：手动验证。

### H5：Recall 回归测试（E2E）
- **目标**：实现 `tests/e2e/test_recall.py`：基于 golden set 做最小召回阈值（例如 hit@k）。
- **前置依赖**：H3（EvalRunner + golden_test_set）
- **修改文件**：
  - `tests/e2e/test_recall.py`（新增）
  - `tests/fixtures/golden_test_set.json`（补齐若干条）
- **验收标准**：hit@k 达到阈值（阈值写死在测试里，便于回归）。
- **测试方法**：`pytest -q tests/e2e/test_recall.py`。

---

## 阶段 I：端到端验收与文档收口（目标：开箱即用的"可复现"工程）

### I1：E2E：MCP Client 侧调用模拟
- **目标**：实现 `tests/e2e/test_mcp_client.py`：以子进程启动 server，模拟 tools/list + tools/call。
- **修改文件**：
  - `tests/e2e/test_mcp_client.py`
- **验收标准**：完整走通 query_knowledge_hub 并返回 citations。
- **测试方法**：`pytest -q tests/e2e/test_mcp_client.py`。

### I2：E2E：Dashboard 冒烟测试
- **目标**：验证 Dashboard 各页面在有数据时可正常渲染、无 Python 异常。
- **修改文件**：
  - `tests/e2e/test_dashboard_smoke.py`（新增）
- **实现要点**：
  - 使用 Streamlit 的 `AppTest` 框架进行自动化冒烟测试
  - 验证 6 个页面均可加载、不抛异常
- **验收标准**：所有页面冒烟测试通过。
- **测试方法**：`pytest -q tests/e2e/test_dashboard_smoke.py`。

### I3：完善 README（运行说明 + 测试说明 + MCP 配置 + Dashboard 使用）
- **目标**：让新用户能在 10 分钟内跑通 ingest + query + dashboard + tests，并能在 Copilot/Claude 中使用。
- **修改文件**：
  - `README.md`
- **验收标准**：README 包含以下章节：
  - **快速开始**：安装依赖、配置 API Key、运行首次摄取
  - **配置说明**：`settings.yaml` 各字段含义
  - **MCP 配置示例**：GitHub Copilot `mcp.json` 与 Claude Desktop `claude_desktop_config.json`
  - **Dashboard 使用指南**：启动命令、各页面功能说明、截图示例
  - **运行测试**：单元测试、集成测试、E2E 测试命令
  - **常见问题**：API Key 配置、依赖安装、连接问题排查
- **测试方法**：按 README 手动走一遍。

### I4：清理接口一致性（契约测试补齐）
- **目标**：为关键抽象（VectorStore / Reranker / Evaluator / DocumentManager）补齐契约测试。
- **修改文件**：
  - `tests/unit/test_vector_store_contract.py`（补齐 delete_by_metadata 边界）
  - `tests/unit/test_reranker_factory.py`（补齐边界）
  - `tests/unit/test_custom_evaluator.py`（补齐边界）
- **验收标准**：`pytest -q` 全绿，且 contract tests 覆盖主要输入输出形状。
- **测试方法**：`pytest -q`。

### I5：全链路 E2E 验收
- **目标**：执行完整的端到端验收流程：ingest → query via MCP → Dashboard 可视化 → evaluate。
- **修改文件**：无新文件，验收已有功能
- **验收标准**：
  - `python scripts/ingest.py --path tests/fixtures/sample_documents/ --collection test` 成功
  - `python scripts/query.py --query "测试查询" --verbose` 返回结果
  - Dashboard 可展示摄取与查询追踪
  - `python scripts/evaluate.py` 输出评估指标
- **测试方法**：手动全链路走通 + `pytest -q` 全量测试。

---

### 交付里程碑（建议）

- **M1（完成阶段 A+B）**：工程可测 + 可插拔抽象层就绪，后续实现可并行推进。
- **M2（完成阶段 C）**：离线摄取链路可用，能构建本地索引。
- **M3（完成阶段 D+E）**：在线查询 + MCP tools 可用，可在 Copilot/Claude 中调用。
- **M4（完成阶段 F）**：Ingestion + Query 双链路可追踪，JSON Lines 持久化。
- **M5（完成阶段 G）**：六页面可视化管理平台就绪（评估面板为占位），数据可浏览、可管理、链路可追踪。
- **M6（完成阶段 H+I）**：评估体系完整 + E2E 验收通过 + 文档完善，形成"面试/教学/演示"可复现项目。


