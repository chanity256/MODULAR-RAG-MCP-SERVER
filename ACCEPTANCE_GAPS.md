# 验收缺口清单

> 对照 `DEV_SPEC.md` 第 6 章排期：A1–I5 共 68/68 已完成。  
> 本文件只记录「主工程已落地，但验收/文档/真实数据仍不完整」的后续事项。  
> 更新时间：2026-08-18（G1–G7 收口完成于 2026-10-05，见文末完成记录）

## 怎么用

- 每项独立，做完把状态改成 `[x]`，并在「完成记录」补一行。
- 不改排期表。这些不是未完成的阶段任务，是收口后的补强。
- 需要 API Key / 费用的项单独标了，默认离线套件不要打开。

---

## P0：让 I5 变成「有数据」的全链路

### G1. 样例目录没有可摄取 PDF

- 状态：`[x]`
- 规格：I5 要求  
  `python scripts/ingest.py --path tests/fixtures/sample_documents/ --collection test` **成功**
- 现状：目录里只有 `tests/fixtures/sample_documents/sample.md`。  
  Loader / CLI 当前只认 `.pdf`，命令退出码 0，但输出  
  `no supported files found under tests/fixtures/sample_documents/ (supported: ['.pdf'])`。
- 影响：后续 query / evaluate / Dashboard 追踪都没有真实摄取产物。
- 建议：
  1. 把 `tests/e2e/test_data_ingestion.py` 里的极简 PDF 生成器抽成公共 fixture helper，或直接落盘 2 个文件：
     - `tests/fixtures/sample_documents/simple.pdf`
     - `tests/fixtures/sample_documents/with_images.pdf`
  2. 更新 `sample.md` 注释：它只是说明文件，不是 ingest 输入。
  3. 验收命令改为对 PDF 成功写入 Chroma / BM25 / ImageStorage。
- 验收：
  ```bash
  python scripts/ingest.py --path tests/fixtures/sample_documents/ --collection test
  # 期望：至少 1 个 .pdf ingested，chunks > 0，退出码 0
  ```

### G2. query 在空库上只返回友好提示

- 状态：`[x]`
- 规格：I5 要求  
  `python scripts/query.py --query "测试查询" --verbose` **返回结果**
- 现状：无索引时退出码 0，输出「未找到相关文档」。这是正确的空库行为，不是检索成功。
- 依赖：先做 G1，并配置可用的 Embedding（或确认 Dense 降级 Sparse 后 BM25 能命中）。
- 建议验收查询（摄取完成后）：
  ```bash
  python scripts/query.py --query "RAG" --collection test --verbose --no-rerank
  # 期望：Top-K 至少 1 条，verbose 打出 dense/sparse/fusion 段
  ```
- 备注：无 API Key 时 Dense 会 401 并降级 Sparse。只要 PDF 文本被 BM25 索引，`--no-rerank` 仍应能命中。

### G3. evaluate 指标全是 0，golden set 未回填

- 状态：`[x]`
- 规格：H3 / H5 / I5。`tests/fixtures/golden_test_set.json` 应在真实摄取后回填 `expected_chunk_ids`。
- 现状：5 条用例的 `expected_chunk_ids` 都是 `[]`，`expected_sources` 指向仓库里不存在的  
  `azure_setup.pdf` / `bm25_guide.pdf` / `config_guide.pdf` / `ingest_guide.pdf` / `retrieval_guide.pdf`。  
  `python scripts/evaluate.py --evaluator custom` 输出 5/5 evaluated，指标全 `0.0000`。
- 说明：`tests/e2e/test_recall.py` 自己在 tmp 里造语料，**不依赖**这份 fixture。仓库级评估脚本才依赖它。
- 建议：
  1. 用 G1 的真实 PDF（或单独准备 5 篇短 PDF）摄取到固定 collection（例如 `eval`）。
  2. 跑一次 query，把实际 `chunk_id` 回填进 `expected_chunk_ids`。
  3. `expected_sources` 改成真实文件名。
  4. 再跑：
     ```bash
     python scripts/evaluate.py --evaluator custom --collection eval
     # 期望：hit_rate / mrr / hit_at_k 非 0；H5 阈值可参考 hit_at_k@5 ≥ 0.8、mrr ≥ 0.5
     ```

### G4. Dashboard 追踪页没有真实 ingest/query trace

- 状态：`[x]`
- 规格：I5「Dashboard 可展示摄取与查询追踪」；I2 冒烟用的是临时 `traces.jsonl` + FakeDataService。
- 现状：六页能加载（I2 已覆盖）。仓库默认 `logs/traces.jsonl` 不一定有本次验收产生的记录。
- 建议：G1 + G2 之后打开 Dashboard，人工确认：
  ```bash
  python scripts/start_dashboard.py
  ```
  - Ingestion 追踪：能看到刚摄取文件的 load/split/embed/store
  - Query 追踪：能看到刚查询的 dense/sparse/fusion（及 rerank 或 skipped）
  - 数据浏览器：能看到 `test` collection 的文档与 chunk

---

## P1：文档与规格不一致

### G5. README 缺 Dashboard 截图

- 状态：`[x]`
- 规格：I3 验收要求「Dashboard 使用指南：启动命令、各页面功能说明、**截图示例**」。
- 现状：运行手册有启动命令和六页文字说明，没有图片。
- 建议：在 `docs/images/` 放 6 张（或至少 3 张：总览 / 数据浏览 / Query 追踪），README 对应章节嵌入。

### G6. README 写了「PDF / Markdown 多格式」，实现只有 PDF

- 状态：`[x]`
- 现状：`SUPPORTED_EXTENSIONS = {".pdf"}`，`IngestionPipeline` Loader 路由也只有 `.pdf`。  
  README 简历示例仍写「支持 PDF / Markdown 多格式文档解析」。同一份 README 的 FAQ 又正确写了「目前默认实现了 PDF Loader」。
- 建议：二选一。
  - 文档收口：简历/概述改成「当前默认 PDF，Loader 可插拔扩展 Markdown / Word」。
  - 或补实现：Markdown Loader + 同步 `SUPPORTED_EXTENSIONS` + ingest 单测 drift guard。

### G7. `sample.md` 自身过期

- 状态：`[x]`
- 现状：文件仍写「C3 会补充 simple.pdf / with_images.pdf；C14 会补充 complex_technical_doc.pdf」。  
  这些 PDF 从未进仓库，只在 e2e 测试的 `tmp_path` 里生成。
- 建议：G1 落地后改这段说明，避免后人按注释去找不存在的文件。

---

## P2：规格正文有、排期未单独立项

这些不是验收失败，是「正文愿景 vs MVP 裁剪」。后续若要扩 scope，按这条清单立项即可。

| ID | 规格出处 | 现状 | 若要做 |
|----|----------|------|--------|
| G8 | 3.1.2 Query Expansion / 同义词扩展 | QueryProcessor 只做分词 + 停用词 + filters 透传 | 新增同义词表，仅扩 Sparse 路，Dense 保持单次 |
| G9 | 3.2.4 扩展 Tool | 只有 `query_knowledge_hub` / `list_collections` / `get_document_summary` | 按需加 `search_by_keyword` / `search_by_semantic` |
| G10 | 3.3 其他后端 | VectorStore 只有 Chroma；Embedding 无 BGE；Evaluator 无 DeepEval | 新 adapter + factory 注册 + 契约测试 |
| G11 | 3.1.1 Loader「多种文档」 | 只有 `PdfLoader` | 先做 Markdown（成本最低，且和 G1/G6 重叠） |
| G12 | I4 DocumentManager 契约 | `tests/unit/test_document_manager.py` 已有实现测试；I4 备注未单列 DocumentManager contract | 若要严格对齐 I4 原文，补一份跨存储 delete 契约测试 |
| G13 | 第 7 章展望 | 容器化 / 云端 / 多租户 / Agentic RAG | 保持在未来章节，不要写进当前进度表 |
| G14 | 3.3.2 Vision LLM（`vision_llm.provider: azure \| dashscope`） | Vision provider 只注册了 `azure`（AzureVisionLLM，与 Azure URL/认证硬耦合） | 新增 OpenAI-compatible Vision provider，接入智谱 GLM 等（详细方案见下） |
| G15 | 3.1.1 Splitter / C4 图片分发 | `[IMAGE: {id}]` 占位符可能被切分器从中间切断，图片引用静默丢失（详见下） | 切分时对占位符做原子保护 + 纯占位符 chunk 合并 |

### G14. OpenAI-compatible Vision LLM provider（GLM 等）——详细方案

- 状态：`[x]`
- 背景：G1 收口后，真实数据需要 Vision LLM 生成图片描述，但手头只有智谱 API key（无 Azure key、未装 Ollama）。已实测（2026-10-05）`glm-5.3-flash` 与 `glm-4v-flash` 均支持 OpenAI Vision 消息格式（`image_url` + data URL），经 `https://open.bigmodel.cn/api/paas/v4/chat/completions` 正确识图。
- 结论：**模型能力可用，但不能塞进 AzureVisionLLM**——后者与 Azure 协议硬耦合：
  - URL：`{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=...`（GLM 需要 `{base_url}/chat/completions`）
  - 认证：`api-key: {key}` 请求头（GLM 需要 `Authorization: Bearer {key}`）
  - 请求体格式完全一致（都是 OpenAI Vision 格式），payload 构建可原样复用
- 改动清单：
  1. **新增 `src/libs/llm/openai_vision_llm.py`**（核心，约 40 行）：
     - `OpenAICompatibleVisionLLM(AzureVisionLLM)`，仅覆盖 `_build_url()`（`base_url + /chat/completions`，复用 VisionLLMConfig 已有的 `base_url` 字段）与 `_build_headers()`（Bearer 认证）
     - 图片预处理 / `chat_with_image` / 响应解析全部继承，零改动
     - `@LLMFactory.register_vision("openai")` 注册——注册名用 `openai` 而非 `zai`，任何 OpenAI 兼容视觉端点（智谱 / DashScope 兼容模式 / Ollama OpenAI 路由）都走这一个实现
  2. **`config/settings.yaml`** 切换 vision 配置（key 走 `${ZAI_API_KEY}` 环境变量引用，不落盘）：
     ```yaml
     vision_llm:
       provider: openai
       model: glm-5.3-flash
       base_url: https://open.bigmodel.cn/api/paas/v4
       api_key: ${ZAI_API_KEY}
     ```
  3. **测试**：`tests/unit/test_openai_vision_llm.py`，仿照 `test_azure_vision_llm.py` 的 mock HTTP 手法，覆盖 URL 拼接、Bearer 头、payload 格式、错误分支（约 8-10 用例）
  4. **（可选）开启 captioner**：`ingestion.image_captioner.enabled: true` 后重新摄取，图片描述进正文；摄取时每张图一次 LLM 调用（Flash 价位，成本可控）
- 验收：
  - `pytest -q tests/unit/test_openai_vision_llm.py` 全绿（离线 mock）
  - 全量回归不回退
  - 开启 captioner 后真实重新摄取：含图 chunk 的正文出现图片描述、`has_unprocessed_images` 标记消失
- 关联：G10（其他后端）同类思路；与「占位符被切碎」问题正交——那是 splitter 健壮性问题（占位符原子保护，见 G15），另行立项

### G15. 图片占位符被切分器切碎（图片引用静默丢失）——详细方案

- 状态：`[x]`
- 背景：G1 收口时发现。`[IMAGE: {image_id}]` 占位符是一段 28+ 字符的连续文本，当 chunk_size 较小（如 e2e 测试的 60）时，`RecursiveCharacterTextSplitter` 可能从占位符中间切断。C4 DocumentChunker 靠正则扫描**完整**占位符分发 `image_refs`，占位符残缺则扫描不到，该 chunk 的图片元数据静默消失，E6 多模态返回链路断裂。
- 次生问题：
  - 残缺占位符文本（如 `[IMAGE: c4e62af...`）留在 chunk 正文，对向量检索是噪声；
  - 极端情况下 chunk 只含占位符、无正文，独立成 chunk 无检索价值。
  - e2e 测试未暴露此问题，正是因为 chunk_size=60 恰好把占位符切碎、图片元数据从未到达 upsert（测试照常通过）——属于**测试盲区掩盖真实 bug**。
- 改动清单（**已实现，2026-10-05**；实现时方案从"事后修复"演进为"混合保护"）：
  1. **`src/ingestion/chunking/document_chunker.py`**（修复落在 chunker 而非 splitter——对 recursive/semantic/fixed_length 所有切分后端生效）：
     - **混合策略**：默认原样切分零改动；仅当检测到碎片（chunk 以未闭合 `[` 片段结尾，或占位符独占 chunk）时，退化为**提取式切分**——占位符先提取（切分器看不到它，构造上不可能切断）、切分后按文本段落归属回填（占位符附着前邻文本段最后一段，图随文走；无前邻顺延后邻；全占位符文档保留独立 chunk）
     - 演进原因：初版"事后修复"（未闭合片段合并 + 后缀去重）在重复字符边界（长 id 的 `hhh...`）会误删真实文本；初版"无条件提取式"会把段内多占位符段落无谓切开。混合策略两者兼顾
  2. **测试**：`test_document_chunker.py` 新增 8 个用例（纯占位符并入前邻/开头并入后邻/连续占位符/全占位符文档/尾段附着/常规输入零改动/真 RecursiveSplitter 小 chunk_size 端到端 ×2）
- 验收（已达成）：
  - `test_document_chunker.py` 22/22 全绿（含真 RecursiveSplitter chunk_size=30 < 占位符长度的端到端回归）
  - 真实组件复现：chunk_size=60 的带图 PDF 从 5 个碎片 chunk 变为 2 个完整 chunk，`image_refs` 正确写入 chunk metadata（修复前静默丢失）
  - 全量回归 1066 passed / 2 skipped
- 关联：G14 落地后 captioner 会把占位符替换为描述文本，可缓解正文污染，但占位符完整性仍是分发 `image_refs` 的前提，两个都做

---

## P3：真实 Provider / 集成门控

默认 `pytest -q`：**1042 passed, 2 skipped**。跳过的是付费/网络路径，不是失败。

| 环境变量 | 覆盖 | 打开前确认 |
|----------|------|------------|
| `RAG_LLM_INTEGRATION=1` | 真实 LLM Transform / Vision 等 | `settings.yaml` 里的 API Key 可用 |
| `RAG_E2E_INTEGRATION=1` | 真 `python scripts/ingest.py` 子进程 + 真 Embedding | 会产生网络调用和费用；cwd 必须切到临时目录，避免写进仓库 `data/` |

建议单独做一次「带 Key 的手工验收」，不要默认开在 CI。

---

## 推荐后续顺序

1. **G1** 放入可摄取 PDF  
2. **G2** 有数据 query 命中  
3. **G3** 回填 golden set，evaluate 非零  
4. **G4** Dashboard 对着真实 trace 点一遍  
5. **G5 / G6 / G7** 文档对齐  
6. 有余力再从 G8–G11 里挑一项扩 scope

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
