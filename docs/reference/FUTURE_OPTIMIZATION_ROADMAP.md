# Modular RAG MCP Server 后续优化路线图

## 1. 文档目的

本文记录项目后续候选的优化方向与决策纪律，覆盖 RAG 检索质量、MCP 能力、安全、部署、可观测性、测试工程与 embedding 模型选型。所有条目均为候选路线，而不是必须一次完成的需求。

> **拆分说明**：原第 1–5 章「Dashboard 前后端分离迁移」（Streamlit 现状限制、目标架构、页面/接口映射、P1–P6 实施包）已于 2026-10-07 拆分为独立 backlog 提案（已随 WC 系列收口冻结入 frozen/）：[dashboard-api-migration_backlog_frozen.md](../frozen/dashboard-api-migration_backlog_frozen.md)。本文章节编号（6–10）保持不变，以对齐既有规格中的交叉引用（如 6.2、6.3、6.8 与第 10 章）。

## 6. 其他可选优化方向

### 6.1 RAG 检索质量

- 建立可版本化的 Golden Test Set；
- 持续记录 Recall@K、MRR、Hit Rate、回答忠实度；
- 对 Chunk 大小、重叠、Top-K、RRF 和 Reranker 参数进行可重复实验；
- 增加查询改写、元数据过滤和置信度拒答；
- 针对表格、标题层级和跨页内容优化结构化 Chunking；
- 增加文档版本与时效性过滤，减少新旧知识混用。

原则：先通过评估数据证明瓶颈，再增加更复杂的检索链路。

### 6.2 数据摄取（已完成）

- 扩展 DOCX、Markdown、HTML、PPTX、XLSX 等 Loader；
- 增强增量更新、删除检测和失败重试；
- 为摄取任务增加幂等键和可恢复状态；
- 保存解析器版本、Chunk 策略版本和 Embedding 模型版本；
- 对失败文档提供可诊断的阶段信息。

> **已完成（IR 系列）**：本节方向已由 [04_INGESTION_ROBUSTNESS_SPEC_frozen.md](../frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md) 落地（2026-10-07）：历史表 v2（含版本三列与 `failed_stage`）、版本替换与删除修复、`--sync` / `--prune` / `--dry-run`、可重试错误退避与 `--retry-failed`、store 回滚、DOCX/PPTX/XLSX/HTML Loader（markitdown 可选依赖）。Markdown 摄取已由同规格 v1.1 承接并交付（M6 / 阶段 IR7：MarkdownLoader + `.md` 路由 + source_filters，原 OBSIDIAN V 系列 M1/M2）；chunk_id 不含 collection 的遗留问题见该规格 7.1。

### 6.3 MCP 能力

- 评估迁移至官方 MCP SDK，减少自维护协议细节；
- 在保留 stdio 的基础上按需求增加 Streamable HTTP transport；
- 增加更细粒度的 Agentic RAG tools，例如文档预览、关键词检索和事实核查（**部分落地**：Sirchmunk 整合 S2/S3 已交付 agentic 第三路——AgenticTrigger 三策略触发 + RRF N 路融合 + source 级 A/B 评估与 Dashboard 展示，见 `03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md`；G0 终裁裸 rg 严格优于 sidecar，该能力默认关闭（`enabled: false`），是否启用由真实语料决定）；
- 为 Tool schema、错误响应和多模态内容增加契约测试；
- 保持 MCP 接口与 Dashboard REST API 职责分离。

### 6.4 安全与权限

- API 默认只监听本地地址；
- 远程部署时启用 HTTPS、认证和授权；
- 对上传类型、文件大小、路径和文件名进行严格校验；
- 防止目录穿越和任意文件读取；
- API Key 仅保存在服务端，不进入前端构建产物；
- 对删除、重新摄取、运行评估等写操作记录审计日志；
- 多用户场景再引入 Collection 或 Namespace 级权限。

### 6.5 部署与运维

- 提供后端 Dockerfile 和健康检查；
- 前端生成静态资源，可由 Nginx 或 CDN 托管；
- 开发环境通过 Vite proxy 转发 API；
- 生产环境明确数据卷、配置、日志和模型缓存位置；
- 增加备份与恢复文档；
- 只有出现明确扩展需求时才拆分微服务。

### 6.6 可观测性

- 为 API 请求补充 request ID，并与现有 trace ID 关联；
- 统一结构化日志字段；
- 增加错误率、任务耗时、检索耗时和 Provider 调用指标；
- 明确敏感数据脱敏策略；
- 需要跨进程或生产环境追踪时再评估 OpenTelemetry。

### 6.7 测试与工程质量

- 保留 Python Unit / Integration / E2E 分层；
- 增加 FastAPI API 契约测试；
- 增加 TypeScript 类型检查、ESLint 和前端单元测试；
- 使用 Playwright 覆盖上传、查询 Trace、运行评估等关键流程；
- CI 分别执行 Python 与前端检查；
- 对 OpenAPI schema 进行版本变更检查，避免前后端契约静默破坏。

### 6.8 主链路 vs Agentic Sidecar 三方对比实验（G0 关闭后的候选实验）

**背景**：Sirchmunk 整合的 G0 已关闭（见 SIRCHMUNK_INTEGRATION_SPEC 第 7 章「G0 重开复测与最终裁决」）：裸 rg 基线 6/6 严格优于 sidecar FAST（2/6）与 DEEP（multihop 1/2）。但该对比有一个缺口——**主链路 RAG（Dense + BM25 + RRF + Rerank）从未参赛**：这 6 题的期望来源全是 `.py` / `.md` 文件，而主链路 Loader 当前仅支持 `.pdf`，结构性地无法在题集上评测。

**实验设计**（前置条件满足后执行）：

1. 前置：Markdown 摄取落地——**已交付**（[04_INGESTION_ROBUSTNESS_SPEC_frozen.md](../frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md) 阶段 IR7，2026-10-07：MarkdownLoader + `.md` 路由 + source_filters，承接 OBSIDIAN_RAG_SPEC M1/M2）；`.py` 等代码文件的 Code Loader 仍待评估；
2. 将仓库源码与文档摄取进独立 collection（现成链路：`ingest.py` + 智谱 embedding 端点已验证可用）；
3. 用同一份 `tests/fixtures/agentic/golden_questions.json`（必要时扩充每类题量）跑 `evaluate.py`，三方对比 source 级命中 / MRR / 延迟 / Token 成本；
4. **裸 rg 基线（`scripts/sirchmunk/rg_baseline.py`）必须列为对照项**——G0 的教训即是：任何 LLM 检索路径的优势主张，都要先过免费的词法下界这一关。

**预期与决策点**：identifier 类预期主链路 BM25 路即强（与 rg 同理）；format 类补齐 Loader 后可覆盖；multihop（跨文件多跳）是 chunk 级 Top-K 检索的固有弱项，是该实验真正要回答的问题——若主链路 + rg 后处理能覆盖，sidecar 依此彻底归档；若确有缺口，再以真实收益数据重开 sidecar 评估（而非假想优势）。

**与第 10 章决策原则的对照**：本实验回答的正是「它解决了哪个已经观察到的问题」（multihop 缺口是否真实存在）与「如何确认有效」（三方同题集量化对比）。


### 6.9 embedding 模型，是否更换为多模态模型
1. 下面是我们当前的项目架构以及实现的功能：

当前配置的是文本嵌入模型，不是直接接收图片、音频或视频的多模态嵌入模型。 配置中使用 OpenAI 兼容接口，模型为智谱的 embedding-3；代码把 chunk.text 作为字符串列表发送给嵌入接口，而不是把原始文件或图片传给它。可见 config/settings.yaml、src/ingestion/embedding/dense_encoder.py 和 src/libs/embedding/openai_embedding.py。
当前可摄取的文件格式：.pdf、.docx、.pptx、.xlsx、.html（依赖可选的 MarkItDown 安装）
解析后，系统主要把抽取出的文本切成 chunks，再做文本向量化。.md、独立图片文件（如 PNG/JPEG）、音频、视频和代码文件目前不在默认摄取格式清单中。格式路由见 src/ingestion/pipeline.py，MarkItDown 格式清单见 src/libs/loader/markitdown_loader.py。

图片处理是另一条独立链路，不代表嵌入模型是多模态的： PDF Loader 可以提取 PDF 内的图片；配置中另有 Vision LLM 用于生成图片描述，但 ingestion.image_captioner.enabled 当前为 false。而且描述会写入 metadata，向量编码器目前只编码 chunk.text，因此图片本身和描述都不会直接进入当前的文本嵌入向量。相关实现见 src/libs/loader/pdf_loader.py、config/settings.yaml 和 src/ingestion/transform/image_captioner.py。
简言之：目前是“文本嵌入 + PDF 图片提取/可选视觉描述”，不是“多模态嵌入”。


2. 参考搜集的数据，后续需要做基准测试，选取合适的嵌入模型进行基准测试，进而选取合适的模型：
README 的「运行手册 → 配置 API Key」里给了两种示例：
- OpenAI 托管：text-embedding-3-small（README 配置示例，README.md:164–169）
- 完全离线：通过 Ollama 部署 nomic-embed-text（README.md:171）
- obsidian://adv-uri?vault=Document&uid=27800ccb-7f57-49f2-aff4-0aaaaf4e2906

### 6.10 Dashboard 前后端分离迁移（已拆分为独立提案）

将 Streamlit Dashboard 迁移为 **Vue 3 + Vite + FastAPI** 前后端分离架构，是本路线图中当前优先级最高的候选方向（见第 7 章近期建议）。完整方案——Streamlit 现状限制、三层目标架构、六页面/接口映射与 P1–P6 六个可独立验收的实施包——已拆分至 [docs/frozen/dashboard-api-migration_backlog_frozen.md](../frozen/dashboard-api-migration_backlog_frozen.md)，此处不再重复。

## 7. 优先级建议

### 近期：低风险、高收益

以下前三项属于前后端分离迁移的实施包 P1–P3，完整任务拆解与验收标准见 [dashboard-api-migration_backlog_frozen.md](../frozen/dashboard-api-migration_backlog_frozen.md)：

1. 固化 Dashboard 应用服务边界；
2. 增加只读 FastAPI API；
3. 用 Vue 实现系统总览和 Trace 页面；
4. 补充 API 契约测试。

### 中期：完成前后端分离

1. 迁移数据浏览、Ingestion 和 Evaluation；
2. 实现任务状态与 SSE；
3. 增加认证、操作审计和前端 E2E；
4. 完成双轨功能等价验证。

### 长期：按真实需求选择

1. Streamlit 退役；
2. 容器化和远程部署；
3. 多用户与权限隔离；
4. 多格式深度解析；
5. Agentic RAG 与 MCP HTTP transport；
6. 分布式任务队列和完整遥测。

## 8. 暂不建议立即实施的事项

- 为了“架构先进”直接拆分多个微服务；
- 在单机任务足够时提前引入 Redis、Celery 和消息队列；
- 同时重写 Dashboard、RAG Core 和 MCP Server；
- 让 Vue 前端直接访问 ChromaDB、日志文件或服务端密钥；
- 让 Dashboard 通过 MCP stdio 代替正常的应用 API；
- 在新 Dashboard 验收前删除 Streamlit。

## 9. 总体验收目标

完成主要迁移后，应达到以下状态：

- Vue Dashboard 与 Python 后端可以独立开发、测试和部署；
- FastAPI 提供稳定、文档化、可测试的应用接口；
- 现有 RAG、Ingestion、Evaluation 和 MCP 能力得到复用，而不是重复实现；
- 长任务支持进度查询，页面刷新不会丢失状态；
- MCP Client 与 Web Dashboard 两条调用链互不干扰；
- 项目仍可在本地以较低成本运行；
- Streamlit 是否退役由功能等价验证决定，而不是由技术选型决定。

## 10. 决策原则

每项优化开始前应回答三个问题：

1. 它解决了哪个已经观察到的问题？
2. 是否有更简单、改动更小的方案？
3. 如何通过测试、指标或用户流程确认它确实有效？

如果暂时无法回答，应将该方向保留在候选列表中，而不是立即进入实现。
