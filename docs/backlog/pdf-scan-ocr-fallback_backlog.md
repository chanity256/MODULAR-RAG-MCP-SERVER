# 扫描件 PDF OCR 兜底提案（pdf-scan-ocr-fallback）

> 起源：会话调研（2026-11-27，非文件来源）。用户询问「项目里有没有 OCR 识别模块」，经三个子代理全面调研（src/ 代码、依赖与配置、文档与测试）确认：**本仓库无任何 OCR 能力**（tesseract / paddleocr / easyocr 等零命中，无 OCR 依赖、配置与外部工具调用），且 [04_INGESTION_ROBUSTNESS_SPEC_frozen](../frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md) 明确「不做 OCR」为 Out of Scope。本提案为用户主动提出的新方向（PaddleOCR 提取扫描件 PDF 文字后进入既有文档处理管线），**不推翻 IR 决议的调研结论本身，而是承接其「仅在展望中提及」的展望**；立项时按「全新功能走独立规格」纪律处理，并在新规格中显式取代 IR 对扫描件「pypdf 抽取为空文本、行为保持不变」的冻结口径。
>
> **状态：草案（2026-11-27，用户已裁决第一期范围：仅扫描 PDF 兜底，不含独立图片文件加载；集成形态两案并陈，立项时裁决）**。
>
> 关联：[ingestion-empty-content-guard_backlog](ingestion-empty-content-guard_backlog.md)（同一缺口的不同应对：防护是拒收/告警，本提案是补全，两案可组合）、[01_DEV_SPEC_frozen](../frozen/01_DEV_SPEC_frozen.md)（OCR 曾列于可插拔 Transform 展望，无实现）、[19_PAGEINDEX_TREE_RETRIEVAL_SPEC_frozen](../frozen/19_PAGEINDEX_TREE_RETRIEVAL_SPEC_frozen.md)（sidecar 集成先例）。

---

## 1. 已观察到的现象（2026-11-27 调研实证）

- **扫描件摄取产出空 chunk 且「成功」**：`PdfLoader._extract_text`（`src/libs/loader/pdf_loader.py:137` 起）对无文本层的 PDF，pypdf 路径 `page.extract_text() or ""` 返回空字符串，MarkItDown 路径同样提取不到文字；管线与 loader 均**无空文本检测与兜底**，扫描件会走完全摄取流程，产出空/近空 chunk 进入索引，检索时无内容可召回。
- **IR 冻结口径**：IR 规格明文「不做 OCR：扫描版 PDF / 图片内文字识别不在本范围（现 PdfLoader 的 pypdf 路径对扫描件抽取为空文本，行为保持不变）」。即这是**已知且当时有意接受**的缺口，非缺陷回归。
- **图片系统现状**：PDF 内嵌图片已提取落盘（`data/images/`）并在 `data/db/image_index.db` 登记（`src/ingestion/storage/image_storage.py`，仅 `image_id → file_path` 元数据）；可选 `ImageCaptioner`（`src/ingestion/transform/image_captioner.py`）经 Vision LLM 生成图片描述，**默认 `ingestion.image_captioner.enabled: false`**。整页扫描件即使被当作「图片」提取，在默认配置下对检索近乎不可见。
- **独立图片文件（png/jpg）无 loader**：`src/ingestion/pipeline.py:111` 支持扩展名不含图片格式，独立图片文件直接被拒。**第一期不做**（用户裁决），登记为边界。

## 2. 想法（方案草案）

核心思路：**OCR 只作为「文本层为空时的逐页兜底」，不是全量替换**。正常文本层 PDF（绝大多数）继续走 pypdf / MarkItDown（快、准、零额外成本）；仅对提取文本为空（或近空，阈值待定）的页，栅格化后交 PaddleOCR 识别，文本段回填该页后照常进入既有 pipeline（chunk → embed → index，下游零改动）。这正是「先提取文字、再进文档管线」合理性的来源：OCR 的产出就是普通文本 Document，管线无需感知它的存在。

与 Vision LLM captioner 的关系是**互补而非替代**：PaddleOCR 管页内文字（本地、确定性、免费），captioner 管图片语义描述（多模态、计费），两者口径不同、可并存。

### 集成形态两案并陈（立项时裁决）

| | 方案 A：独立 venv HTTP sidecar（PageIndex 模式） | 方案 B：可选依赖组 + 惰性 import（rerank 模式） |
|---|---|---|
| 形态 | `scripts/ocr_sidecar/` 独立 venv 装 paddleocr + 本机 HTTP 服务；主进程对空文本页经 HTTP 调用（对齐 sirchmunk「主 venv 永不 import」红线） | `pip install -e ".[ocr]"` 进主 venv 可选组，使用点惰性 `import paddleocr`（对齐 CER 的 `rerank` 组先例） |
| 优点 | 主 venv / CI 完全不碰 paddle，依赖污染为零；sidecar 未启动时行为退化为现状（空文本），离线确定性测试零改动；与 PageIndex 先例同构 | 部署简单（无第二个进程），CER 已趟过可选依赖组 + 门控冒烟的路 |
| 缺点 | 多一个部署组件与 venv 维护 | paddlepaddle 体积大（数百 MB + 模型权重）、平台 wheel 风险（macOS arm64 可用性需实测）；进主 venv 后 `pip check` / 依赖冲突面变大 |
| 门控冒烟 | `RAG_OCR_INTEGRATION=1` 风格，对齐 PI / CER 先例 | 同左 |

初步倾向方案 A（paddle 的体积与平台问题比 torch 更重），但留待立项时基于实测（Q3）裁决。

## 3. 决策三问（初答）

1. **解决了哪个已经观察到的问题？**——是：扫描件 PDF 摄取产出空 chunk 且无任何检测/告警，检索无内容可召回（IR 冻结时接受的缺口，用户现判断需要补齐）。PI 台账候选池 #1（空文本防护）与之同源，互证成立。
2. **是否有更简单、改动更小的方案？**——有：只做空文本防护（拒收或告警，见 [ingestion-empty-content-guard_backlog](ingestion-empty-content-guard_backlog.md)），零新依赖、改动极小，但**放弃扫描件内容**，只解决「不知道摄取了个空文档」的可见性问题。OCR 兜底成本高（paddle 依赖 / 独立进程 / 识别耗时）但真正补全内容。两案不互斥：防护先行立即可做，OCR 兜底按需立项。
3. **如何通过测试、指标或用户流程确认它确实有效？**——离线单测用 fake OCR 后端（HTTP fake / 注入）验证逐页空文本检测与回填逻辑，零网络全绿不回归；门控真实集成测试（`RAG_OCR_INTEGRATION=1`）用真实扫描件 fixture PDF 摄取后，查询命中 OCR 补全的正文文本；指标：扫描件摄取的 chunk 非空率从 0% 提升，OCR 识别质量抽样（中文语料 CER 目视核验）。

## 4. 边界与不做（第一期）

- **不做独立图片文件（png/jpg）加载**（用户裁决 2026-11-27；若未来做，注意 pipeline.py 与 scripts/ingest.py 的双登记 drift guard）。
- **不做全量 OCR**：文本层正常的页永不走 OCR。
- **不做表格结构化还原**（PP-Structure 等）：第一期只出纯文本段，结构还原列待研究。
- **不动 captioner 的定位与默认配置**：两者互补，captioner 是否默认开启属另一议题。
- 不改 IR 冻结文档正文：取代口径的工作发生在新规格中（冻结规格不回写）。

## 5. 待研究问题（立项前需回答）

| # | 问题 | 说明 |
|---|------|------|
| Q1 | 空文本判定口径 | 逐页判定阈值（空串？字符数 < N？全空白字符？）与近空页（如页眉页脚残留几个字符）的归类；误判成本不对称：把文本层页误送 OCR 浪费时间，把扫描页漏判则缺口仍在 |
| Q2 | 栅格化来源与方式 | 优先复用 PdfLoader 已提取的整页图片（pypdf XObject，扫描件通常是单张整页图），提取不到再经 pypdfium2 渲染页面；DPI / 尺寸口径影响识别质量与耗时 |
| Q3 | PaddleOCR 实测 | 版本选择、macOS arm64 CPU 推理实测（本机环境）、中文识别质量抽样、单页耗时（决定整篇扫描件摄取时长是否可接受） |
| Q4 | OCR 文本的元数据与溯源 | OCR 段是否标记 `metadata` 来源（如 `ocr: true` / 引擎与置信度），供检索展示与后续质量分析；与既有 `ImageRef` / `image_captions` 的关联方式 |
| Q5 | IR 契约的取代方式 | 新规格如何声明取代 IR「扫描件行为保持不变」口径；摄取历史 / `failed_stage` 语义是否受影响（扫描件从「空文本成功」变为「OCR 成功或显式失败」） |
| Q6 | 失败语义 | sidecar 未启动 / OCR 失败时的行为：退化为现状（空文本）还是显式失败？与 ingestion-empty-content-guard 提案的防护口径需对齐，避免两提案对同一页给出矛盾语义 |

## 6. 流转记录

- 2026-11-27：草案创建（会话调研沉淀：三子代理全面调研「有无 OCR 模块」→ 无；用户提出 PaddleOCR 方向并裁决第一期范围仅扫描 PDF 兜底、集成形态两案并陈）。
