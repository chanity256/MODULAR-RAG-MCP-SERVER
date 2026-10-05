# 验收缺口清单

> 对照 `DEV_SPEC.md` 第 6 章排期：A1–I5 共 68/68 已完成。  
> 本文件记录「主工程已落地」后收口补强的**完成情况**（P0–P2 均已完成）。  
> 更新时间：2026-10-05（G1–G7 收口、G14–G15 补强完成；同日按状态重组分区）
>
> **分区说明**：G 编号全局稳定（git 提交与完成记录均引用）。
> 候选扩展（G8–G12）、展望项（G13）与集成门控说明已移交
> [FUTURE_OPTIMIZATION_ROADMAP.md](FUTURE_OPTIMIZATION_ROADMAP.md)（6.7/6.8 节）——
> **后续开发排期、流程与候选内容以该文档为基准**，本文档只管理已完成事项与完成记录。

## 怎么用

- 已完成项的状态标记为 `[x]`，完成详情见文末「完成记录」。
- 不改 DEV_SPEC 排期表。这些不是未完成的阶段任务，是收口后的补强。
- 后续如再有收口类补强事项，在本文件新增条目；扩展与展望类内容直接进 ROADMAP。

---

## P0：让 I5 变成「有数据」的全链路（已完成 2026-10-05）

### G1. 样例目录没有可摄取 PDF

- 状态：`[x]`
- 规格：I5 要求  
  `python scripts/ingest.py --path tests/fixtures/sample_documents/ --collection test` **成功**
- 收口前现状：目录里只有 `tests/fixtures/sample_documents/sample.md`。  
  Loader / CLI 当前只认 `.pdf`，命令退出码 0，但输出  
  `no supported files found under tests/fixtures/sample_documents/ (supported: ['.pdf'])`。
- 影响：后续 query / evaluate / Dashboard 追踪都没有真实摄取产物。
- 实施记录：放入 3 个真实 PDF（个人票据，gitignore 不入库）；过程中发现并修复
  Chroma 1.x 拒绝 list-of-dict metadata 的真实 bug（见完成记录）。

### G2. query 在空库上只返回友好提示

- 状态：`[x]`
- 规格：I5 要求  
  `python scripts/query.py --query "测试查询" --verbose` **返回结果**
- 收口前现状：无索引时退出码 0，输出「未找到相关文档」。这是正确的空库行为，不是检索成功。
- 实施记录：真实摄取后 query Top-1 命中，Dense（真实 embedding）/ Sparse 双路正常。

### G3. evaluate 指标全是 0，golden set 未回填

- 状态：`[x]`
- 规格：H3 / H5 / I5。`tests/fixtures/golden_test_set.json` 应在真实摄取后回填 `expected_chunk_ids`。
- 收口前现状：5 条用例 `expected_chunk_ids` 全空，`expected_sources` 指向仓库里不存在的文件，
  评估指标全 `0.0000`。（`tests/e2e/test_recall.py` 自己在 tmp 里造语料，不依赖这份 fixture。）
- 实施记录：golden set 重写为 5 条真实用例，hit_rate / mrr / hit_at_k 全 1.0。

### G4. Dashboard 追踪页没有真实 ingest/query trace

- 状态：`[x]`
- 规格：I5「Dashboard 可展示摄取与查询追踪」；I2 冒烟用的是临时 `traces.jsonl` + FakeDataService。
- 实施记录：总览 / 数据浏览器 / Ingestion 追踪 / Query 追踪四页经真实数据浏览器验证。

---

## P1：文档与规格不一致（已完成 2026-10-05）

### G5. README 缺 Dashboard 截图

- 状态：`[x]`
- 规格：I3 验收要求「Dashboard 使用指南：启动命令、各页面功能说明、**截图示例**」。
- 实施记录：4 张真实数据截图落 `docs/images/`，README「启动 Dashboard」章节嵌入。

### G6. README 写了「PDF / Markdown 多格式」，实现只有 PDF

- 状态：`[x]`
- 实施记录：采用文档收口路线——简历示例改为「当前默认支持 PDF 文档解析（Loader 可插拔，
  可扩展 Markdown / Word）」；补 Markdown 实现候选见 FUTURE_OPTIMIZATION_ROADMAP.md 6.8 节（G11）。

### G7. `sample.md` 自身过期

- 状态：`[x]`
- 实施记录：改为说明文件——注明非 ingest 输入、测试用 PDF 由
  `tests/fixtures/pdf_minimal.py` 生成器即时构造。

---

## P2：收口期修复与深度补强（已完成 2026-10-05）

这两项源于真实数据验收暴露的深层问题，超出原始排期视野，单独立项并已完成。

### G14. OpenAI-compatible Vision LLM provider（GLM 等）

- 状态：`[x]`
- 背景：G1 收口后，真实数据需要 Vision LLM 生成图片描述，但手头只有智谱 API key（无 Azure key、未装 Ollama）。已实测（2026-10-05）`glm-5.3-flash` 与 `glm-4v-flash` 均支持 OpenAI Vision 消息格式（`image_url` + data URL），经 `https://open.bigmodel.cn/api/paas/v4/chat/completions` 正确识图。
- 结论：**模型能力可用，但不能塞进 AzureVisionLLM**——后者与 Azure 协议硬耦合：
  - URL：`{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=...`（GLM 需要 `{base_url}/chat/completions`）
  - 认证：`api-key: {key}` 请求头（GLM 需要 `Authorization: Bearer {key}`）
  - 请求体格式完全一致（都是 OpenAI Vision 格式），payload 构建可原样复用
- 实施内容：
  1. 新增 `src/libs/llm/openai_vision_llm.py`：`OpenAICompatibleVisionLLM(AzureVisionLLM)`，仅覆盖 `_build_url()` 与 `_build_headers()`（Bearer 认证）；图片预处理 / `chat_with_image` / 响应解析全部继承。注册名 `openai`——任何 OpenAI 兼容视觉端点（智谱 / DashScope 兼容模式 / Ollama OpenAI 路由）都走这一个实现。
  2. `VisionLLMConfig` 补 `base_url` 字段（此前只有 EmbeddingConfig 有）。
  3. `config/settings.yaml` vision 切换到智谱 `glm-5.3-flash`（key 走 `${ZAI_API_KEY}` 环境变量引用，不落盘）。
  4. 新增 `tests/unit/test_openai_vision_llm.py` 14 个 mock HTTP 单测。
- 验收（已达成）：
  - 单测全绿（离线 mock）；全量回归不回退
  - 真实图片验收：真实照片（虎斑猫）经 `LLMFactory.create_vision_llm` 识图，描述准确（621 tokens）
- 后续可选：`ingestion.image_captioner.enabled: true` 后重新摄取，图片描述进正文；摄取时每张图一次 LLM 调用（Flash 价位，成本可控）

### G15. 图片占位符被切分器切碎（图片引用静默丢失）

- 状态：`[x]`
- 背景：G1 收口时发现。`[IMAGE: {image_id}]` 占位符是一段 28+ 字符的连续文本，当 chunk_size 较小（如 e2e 测试的 60）时，`RecursiveCharacterTextSplitter` 可能从占位符中间切断。C4 DocumentChunker 靠正则扫描**完整**占位符分发 `image_refs`，占位符残缺则扫描不到，该 chunk 的图片元数据静默消失，E6 多模态返回链路断裂。
- 次生问题：
  - 残缺占位符文本（如 `[IMAGE: c4e62af...`）留在 chunk 正文，对向量检索是噪声；
  - 极端情况下 chunk 只含占位符、无正文，独立成 chunk 无检索价值。
  - e2e 测试未暴露此问题，正是因为 chunk_size=60 恰好把占位符切碎、图片元数据从未到达 upsert（测试照常通过）——属于**测试盲区掩盖真实 bug**。
- 实施内容（实现时方案从"事后修复"演进为"混合保护"）：
  1. `src/ingestion/chunking/document_chunker.py`（修复落在 chunker 而非 splitter——对 recursive/semantic/fixed_length 所有切分后端生效）：
     - **混合策略**：默认原样切分零改动；仅当检测到碎片（chunk 以未闭合 `[` 片段结尾，或占位符独占 chunk）时，退化为**提取式切分**——占位符先提取（切分器看不到它，构造上不可能切断）、切分后按文本段落归属回填（占位符附着前邻文本段最后一段，图随文走；无前邻顺延后邻；全占位符文档保留独立 chunk）
     - 演进原因：初版"事后修复"（未闭合片段合并 + 后缀去重）在重复字符边界（长 id 的 `hhh...`）会误删真实文本；初版"无条件提取式"会把段内多占位符段落无谓切开。混合策略两者兼顾
  2. 测试：`test_document_chunker.py` 新增 8 个用例（纯占位符并入前邻/开头并入后邻/连续占位符/全占位符文档/尾段附着/常规输入零改动/真 RecursiveSplitter 小 chunk_size 端到端 ×2）
- 验收（已达成）：
  - `test_document_chunker.py` 22/22 全绿（含真 RecursiveSplitter chunk_size=30 < 占位符长度的端到端回归）
  - 真实组件复现：chunk_size=60 的带图 PDF 从 5 个碎片 chunk 变为 2 个完整 chunk，`image_refs` 正确写入 chunk metadata（修复前静默丢失）
  - 全量回归 1066 passed / 2 skipped
- 关联：G14 落地后 captioner 会把占位符替换为描述文本，可缓解正文污染，但占位符完整性仍是分发 `image_refs` 的前提，两个都做

## 后续开发基准

后续开发排期、开发流程与候选开发内容以 [FUTURE_OPTIMIZATION_ROADMAP.md](FUTURE_OPTIMIZATION_ROADMAP.md) 为基准（2026-10-05 起），包含：

- Dashboard 迁移 Vue 3 + FastAPI 的六个实施包（P1–P6）与迁移范围；
- 6.8 节候选扩展 backlog（G8–G12，含立项原则：坏 case 驱动）与展望项（G13）；
- 6.7 节真实 Provider 集成门控说明（`RAG_LLM_INTEGRATION` / `RAG_E2E_INTEGRATION`）；
- 两份深度调研：Sirchmunk 整合（`SIRCHMUNK_INTEGRATION_ANALYSIS.md`）、微服务演进（`MICROSERVICES_ANALYSIS.md`）。

本文件仅记录已完成事项与完成记录。

## 完成记录

| 日期 | ID | 说明 |
|------|----|------|
| 2026-10-05 | G1 | 放入 3 个真实 PDF（个人票据：佳明电子发票 + Raycast/Readwise 收据，gitignore 不入库）到 `tests/fixtures/sample_documents/`；真实摄取 3 ingested / 5 chunks / 4 images 退出码 0。过程中发现并修复真实 bug：Chroma 1.x 拒绝 list-of-dict metadata（`metadata.images`），`ChromaStore` 新增 `__json` 键后缀序列化 + 读取对称还原，补 2 个 roundtrip 回归测试（test_chroma_store_roundtrip.py）。顺带把 e2e 的极简 PDF 生成器抽为公共 helper `tests/fixtures/pdf_minimal.py` |
| 2026-10-05 | G2 | `query.py --query "发票" --collection test --verbose --no-rerank` Top-1 命中佳明发票 chunk，Dense（真实 embedding）/ Sparse 双路召回 + RRF 融合正常 |
| 2026-10-05 | G3 | golden set 重写为 5 条真实用例（中英文，锚定真实 chunk_id/source）；`evaluate.py --evaluator custom --collection test` 5/5 evaluated，hit_rate=1.0 / mrr=1.0 / hit_at_k=1.0 |
| 2026-10-05 | G4 | Dashboard 四页（总览/数据浏览器/Ingestion 追踪/Query 追踪）经真实数据人工+浏览器验证，trace/资产统计均渲染真实产物 |
| 2026-10-05 | G5 | 截图 4 张（总览/数据浏览/Query 追踪/Ingestion 追踪）落 `docs/images/`，README「启动 Dashboard」章节嵌入 |
| 2026-10-05 | G6 | README 简历示例改为「当前默认支持 PDF 文档解析（Loader 可插拔，可扩展 Markdown / Word）」 |
| 2026-10-05 | G7 | `sample.md` 改为说明文件：注明非 ingest 输入、PDF 由 e2e 生成器即时构造 |
| 2026-10-05 | 附注 | 真实 embedding 走智谱 OpenAI-compatible 端点（`embedding-3`，`base_url=https://open.bigmodel.cn/api/paas/v4`，key 经 `${ZAI_API_KEY}` 环境变量引用）；config/settings.yaml 已切换，drift guard 测试同步更新。全量回归 1044 passed / 2 skipped（+2 为 chroma 修复回归） |
| 2026-10-05 | G14 | 新增 `OpenAICompatibleVisionLLM`（继承 AzureVisionLLM，仅覆盖 `_build_url`/`_build_headers`，注册名 `openai`）；`VisionLLMConfig` 补 `base_url` 字段；settings.yaml vision 切换到智谱 `glm-5.3-flash`（key 走 `${ZAI_API_KEY}`）；新增 14 个 mock HTTP 单测。真实图片验收：用真实照片（虎斑猫）经 `LLMFactory.create_vision_llm` 识图，描述准确（621 tokens）。全量回归 1058 passed / 2 skipped |
| 2026-10-05 | G15 | `DocumentChunker` 占位符原子保护（混合策略：默认零改动，检测到碎片时退化为提取式切分）；8 个新单测含真 RecursiveSplitter chunk_size=30 端到端；真实组件复现 chunk_size=60 带图 PDF 从 5 碎片变 2 完整 chunk 且 `image_refs` 不再丢失。全量回归 1066 passed / 2 skipped |
