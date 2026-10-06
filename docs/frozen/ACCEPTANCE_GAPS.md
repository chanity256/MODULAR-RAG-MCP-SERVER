# 验收缺口清单

> 对照 `DEV_SPEC.md` 第 6 章排期：A1–I5 共 68/68 已完成。  
> 本文件只记录「主工程已落地，但验收/文档/真实数据仍不完整」的后续事项。  
> 更新时间：2026-08-18

## 怎么用

- 每项独立，做完把状态改成 `[x]`，并在「完成记录」补一行。
- 不改排期表。这些不是未完成的阶段任务，是收口后的补强。
- 需要 API Key / 费用的项单独标了，默认离线套件不要打开。

---

## P0：让 I5 变成「有数据」的全链路

### G1. 样例目录没有可摄取 PDF

- 状态：`[x]`（2026-10-06 完成）
- 规格：I5 要求  
  `python scripts/ingest.py --path tests/fixtures/sample_documents/ --collection test` **成功**
- 现状：~~目录里只有 `tests/fixtures/sample_documents/sample.md`。  
  Loader / CLI 当前只认 `.pdf`，命令退出码 0，但输出  
  `no supported files found under tests/fixtures/sample_documents/ (supported: ['.pdf'])`~~  
  7 个测试 PDF（simple / with_images / complex_technical_doc / chinese 三件套 / blogger_intro）已从 `origin/main` 恢复到本分支（clean-start 重置时被删，删除提交 `b56c5f2`）。
- 影响：~~后续 query / evaluate / Dashboard 追踪都没有真实摄取产物。~~已消除（见 G2/G3）。
- 建议：
  1. ~~把 `tests/e2e/test_data_ingestion.py` 里的极简 PDF 生成器抽成公共 fixture helper，或直接落盘 2 个文件~~ 恢复自 origin/main，无需再生成。
  2. `sample.md` 注释已说明它只是说明文件，不是 ingest 输入（保持原样）。
  3. 验收命令改为对 PDF 成功写入 Chroma / BM25 / ImageStorage。✅
- 验收（已通过，collection 用 `eval`）：
  ```bash
  python scripts/ingest.py --path tests/fixtures/sample_documents/ --collection eval
  # 实测：7 files ingested, 60 chunks, 9 images, vectors written 60，退出码 0
  # 注意：含图片 PDF 曾因 Chroma metadata 不接受 dict 列表而 store 失败，
  # 已在 chroma_store 写入侧净化 + parse_image_refs 读取侧兼容 JSON 字符串修复
  ```

### G2. query 在空库上只返回友好提示

- 状态：`[x]`（2026-10-06 完成，G1 后）
- 规格：I5 要求  
  `python scripts/query.py --query "测试查询" --verbose` **返回结果**
- 现状：~~无索引时退出码 0，输出「未找到相关文档」。这是正确的空库行为，不是检索成功。~~  
  空库行为保留（正确）；G1 摄取后实测 Top-3 全部命中正确文档。
- 依赖：~~先做 G1，并配置可用的 Embedding（或确认 Dense 降级 Sparse 后 BM25 能命中）。~~ Embedding 已配置为智谱兼容端点（`config/settings.yaml`，`api_key: ${ZAI_API_KEY}` 环境变量注入，dim=2048）。
- 建议验收查询（摄取完成后）：
  ```bash
  python scripts/query.py --query "RAG" --collection test --verbose --no-rerank
  # 期望：Top-K 至少 1 条，verbose 打出 dense/sparse/fusion 段 ✅
  ```
- 备注：无 API Key 时 Dense 会 401 并降级 Sparse。只要 PDF 文本被 BM25 索引，`--no-rerank` 仍应能命中。

### G3. evaluate 指标全是 0，golden set 未回填

- 状态：`[x]`（2026-10-06 完成）
- 规格：H3 / H5 / I5。`tests/fixtures/golden_test_set.json` 应在真实摄取后回填 `expected_chunk_ids`。
- 现状：~~5 条用例的 `expected_chunk_ids` 都是 `[]`，`expected_sources` 指向仓库里不存在的  
  `azure_setup.pdf` / `bm25_guide.pdf` / `config_guide.pdf` / `ingest_guide.pdf` / `retrieval_guide.pdf`。  
  `python scripts/evaluate.py --evaluator custom` 输出 5/5 evaluated，指标全 `0.0000`。~~  
  5 条用例已重写为指向恢复后的真实文档（`chinese_table_chart_doc` / `chinese_technical_doc` / `chinese_long_doc` / `complex_technical_doc` / `simple.pdf`），`expected_chunk_ids` 按实际检索命中回填。
- 说明：`tests/e2e/test_recall.py` 自己在 tmp 里造语料，**不依赖**这份 fixture。仓库级评估脚本才依赖它。
- 建议：
  1. ~~用 G1 的真实 PDF（或单独准备 5 篇短 PDF）摄取到固定 collection（例如 `eval`）。~~ ✅
  2. ~~跑一次 query，把实际 `chunk_id` 回填进 `expected_chunk_ids`。~~ ✅
  3. ~~`expected_sources` 改成真实文件名。~~ ✅
  4. 再跑：
     ```bash
     python scripts/evaluate.py --evaluator custom --collection eval
     # 实测：5/5 evaluated，hit_at_k=1.0000 / hit_rate=1.0000 / mrr=1.0000
     #（超过 H5 阈值 hit_at_k@5 ≥ 0.8、mrr ≥ 0.5）
     ```
     注意：golden set 的 chunk_id 是摄取产物的确定性哈希（对同一语料稳定），
     但若重新生成或修改 PDF 语料需重跑摄取并回填。

### G4. Dashboard 追踪页没有真实 ingest/query trace

- 状态：`[ ]`
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

- 状态：`[ ]`
- 规格：I3 验收要求「Dashboard 使用指南：启动命令、各页面功能说明、**截图示例**」。
- 现状：运行手册有启动命令和六页文字说明，没有图片。
- 建议：在 `docs/images/` 放 6 张（或至少 3 张：总览 / 数据浏览 / Query 追踪），README 对应章节嵌入。

### G6. README 写了「PDF / Markdown 多格式」，实现只有 PDF

- 状态：`[ ]`
- 现状：`SUPPORTED_EXTENSIONS = {".pdf"}`，`IngestionPipeline` Loader 路由也只有 `.pdf`。  
  README 简历示例仍写「支持 PDF / Markdown 多格式文档解析」。同一份 README 的 FAQ 又正确写了「目前默认实现了 PDF Loader」。
- 建议：二选一。
  - 文档收口：简历/概述改成「当前默认 PDF，Loader 可插拔扩展 Markdown / Word」。
  - 或补实现：Markdown Loader + 同步 `SUPPORTED_EXTENSIONS` + ingest 单测 drift guard。

### G7. `sample.md` 自身过期

- 状态：`[ ]`
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
| | | |
