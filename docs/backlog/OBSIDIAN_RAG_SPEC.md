<!-- 扩展规格：本地笔记库（Obsidian Vault）RAG 能力。格式与架构分层对齐 DEV_SPEC_frozen.md；基线为 clean-start 分支「阶段 A–I 已完成」状态。 -->
# Obsidian Vault RAG 扩展规格 (OBSIDIAN_RAG_SPEC)

> 版本：0.2 — M1/M2（MarkdownLoader + 路由/排除）已移交 [INGESTION_ROBUSTNESS_SPEC](../specs/INGESTION_ROBUSTNESS_SPEC.md) M6 / 阶段 IR7 排期执行，本规格保留 M3/M4
>
> 0.1 — 对标 Analogy 插件（liaocaoxuezhe/obsidian-extension）核心能力的增量开发规格
>
> 基线：DEV_SPEC_frozen.md 阶段 A–I 全部完成（PDF Ingestion / Hybrid Search / MCP Server / Dashboard / Evaluation / E2E 均已落地）

## 目录

- 项目概述
- 核心目标与范围
- 技术选型与模块设计
- 测试方案
- 配置变更汇总
- 项目排期
- 可扩展性与未来展望

---

## 1. 项目概述

### 1.1 背景与对标

> **🔗 对标项目仓库**：<https://github.com/liaocaoxuezhe/obsidian-extension>
>
> - 插件名称：Analogy — RAG in your vault（Apache-2.0）
> - 核心定位：为 Obsidian vault 构建本地语义检索（本地 Chroma + 本地 Embedding 模型），并通过配套 MCP server 暴露给 Claude Code / Cursor 等客户端

对标插件 **Analogy** 的核心链路为：

> 读取本地笔记（Markdown vault）→ 文本清理、切分、向量化 → 写入本机 Chroma 向量库 → 查询时在本机转向量做相似性匹配。

**差距分析结论**（基于当前代码盘点）：

| 插件能力 | 当前项目状态 | 缺口归属 |
|---------|-------------|---------|
| 读取本地 Markdown 笔记 | ❌ 仅支持 `.pdf` + markitdown 系列（`.md` 被静默忽略） | 已移交 **IR7**（IR7.1–IR7.3） |
| Obsidian 语法（frontmatter / wikilink / tag / embed） | ❌ 无对应解析器 | 已移交 **IR7**（MarkdownLoader，设计见 IR 规格 3.8.1） |
| 排除指定目录/文件不索引 | ❌ `collect_files` 仅按扩展名过滤，无 include/exclude 规则 | 已移交 **IR7**（source_filters，IR7.4） |
| 清理、切分、向量化 | ✅ 已有（ChunkRefiner / RecursiveSplitter / Dense+Sparse 双路编码） | 无 |
| 写入本机 Chroma | ✅ 已有（`chromadb` 嵌入式，`data/db/chroma/`） | 无 |
| 查询在本机转向量、相似匹配 | ✅ 已有（`embedding.provider: ollama` 即全离线；进程内零后台服务方案缺） | **M3（可选增强）** |
| 增量索引（只处理变更文件） | ✅ 已有（SHA256 `FileIntegrityChecker` + `--force`） | 无 |
| 暴露给 MCP 客户端检索 | ✅ 已有且更强（Hybrid + Rerank + 引用透明） | 无 |
| Semantic Walk（chunk 级探索画布） | ❌ Dashboard 无图浏览页 | **M4（可选）** |
| 在 Obsidian 侧边栏内搜索 | ❌ 架构不同（本项目入口是 MCP 客户端 + Streamlit） | Out of Scope |

### 1.2 设计原则

- **只做增量，不动基线**：全部任务落在既有抽象（`BaseLoader` / `BaseEmbedding` / 各工厂 / Dashboard services）之上，不改变阶段 A–I 已验收的行为契约与既有测试语义。
- **双登记不脱钩**：`src/ingestion/pipeline.py` 与 `scripts/ingest.py` 两处扩展名登记继续由既有 drift guard 单测锁死，新增 `.md` 必须两处同步。
- **离线确定性纪律延续**：单元测试零网络、零真实模型（组件注入 fake）；真实模型与真实子进程冒烟沿用 `RAG_LLM_INTEGRATION` / `RAG_E2E_INTEGRATION` 环境变量门控先例。
- **Loader 职责边界不变**：Loader 只做「格式统一 + 结构抽取 + 引用收集」，不切分、不向量化（对齐 DEV_SPEC 3.1.1）。

---

## 2. 核心目标与范围

### 2.1 In Scope（v0.2 起保留 2 个可选模块）

| 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|--------|-----------|
| ~~M1~~ | ~~MarkdownLoader（Vault 感知解析器）~~ | ~~P0~~ | **已移交** [INGESTION_ROBUSTNESS_SPEC](../specs/INGESTION_ROBUSTNESS_SPEC.md) M6 / IR7.1–IR7.2 |
| ~~M2~~ | ~~摄取路由 + 目录扫描/排除规则~~ | ~~P0~~ | **已移交** 同上（IR7.3–IR7.4） |
| M3 | 进程内本地 Embedding provider | P1（可选） | 新增 `sentence_transformers` backend，零后台服务完成摄取+查询向量化 |
| M4 | Semantic Walk 探索页 | P2（可选） | Dashboard 第 7 页，chunk 级相似度图探索 |

**最小可用路径（原 = M1 + M2）已随 M1/M2 移交 IR 规格 IR7**：IR7 完成后即可 `python scripts/ingest.py --path <vault目录> --collection vault` 把整个 Obsidian 库索引进本机 Chroma，并经 MCP / Dashboard / `scripts/query.py` 检索。本规格剩余的 M3/M4 为相互独立的可选增强。

### 2.2 Out of Scope（明确不做）

- **不做 Obsidian TS 插件壳**：侧边栏 UI、ribbon、Obsidian API 属于独立 TypeScript 工程；本项目检索入口保持 MCP 客户端（Copilot / Claude）+ Streamlit Dashboard。
- **不做 vault 实时监听**（file watcher / 自动重索引调度）：沿用 CLI / Dashboard 手动触发 + SHA256 增量跳过，已满足「Continue index」语义。
- **不引入 CLIP 多模态向量**：图片检索沿用既有 Image-to-Text（Captioning）策略。
- **不新建向量库/索引结构**：继续复用 Chroma（All-in-One 存储）+ BM25 JSON 索引 + 图片 SQLite 索引的既有三路存储。

---

## 3. 技术选型与模块设计

### 3.1 M1：MarkdownLoader（Vault 感知解析器）〔已移交〕

> **v0.2 移交**：本节设计全文已迁移至 [INGESTION_ROBUSTNESS_SPEC](../specs/INGESTION_ROBUSTNESS_SPEC.md) **3.8.1**（M6），后续维护以该节为准。移交原因：Markdown 摄取是摄取管线格式能力的自然延伸，与 IR 系列的双登记 / 同步模式 / 重试链路直接耦合，合并排期（IR7）可复用 IR0–IR6 已交付的版本替换、filtered 计数与 drift guard 基础设施。

### 3.2 M2：摄取路由 + 目录扫描/排除规则〔已移交〕

> **v0.2 移交**：本节设计全文已迁移至 [INGESTION_ROBUSTNESS_SPEC](../specs/INGESTION_ROBUSTNESS_SPEC.md) **3.8.2**（路由登记 + source_filters），对应任务 IR7.3–IR7.4。

### 3.3 M3：进程内本地 Embedding（sentence-transformers）

**动机**：现有本地方案 `embedding.provider: ollama` 需要独立后台服务；本模块提供「零后台服务、进程内完成摄取+查询向量化」的选项，进一步对齐插件的本地化形态。

**新增文件**：`src/libs/embedding/sentence_transformers_embedding.py`（命名对齐现有 ollama / openai 实现）

**接口契约**（对齐 `BaseEmbedding`）：

```python
class SentenceTransformersEmbedding(BaseEmbedding):
    def __init__(self, *, model: str = "BAAI/bge-small-zh-v1.5",
                 encode_fn: Callable[..., list[list[float]]] | None = None) -> None: ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    @property
    def dimension(self) -> int: ...
```

- **惰性加载**：构造零 IO，首次 `embed` 才加载模型（对齐 MCP tools 懒初始化先例）。
- **可选依赖降级**：`sentence-transformers` 未安装时构造成功，`embed` 抛带 `pip install sentence-transformers` 提示的 `ImportError`（对齐 RagasEvaluator 先例）。
- **注入点**：`encode_fn` 可注入，单测离线确定性验证（fake 返回稳定向量）。
- **归一化**：`normalize_embeddings=True`（L2），与 cosine 相似度语义一致。
- **工厂注册**：`EmbeddingFactory` 注册 `sentence_transformers` backend，`settings.yaml` 切 `embedding.provider: sentence_transformers` 即生效，摄取与查询共用（零代码切换）。
- **推荐模型**：中文库 `BAAI/bge-small-zh-v1.5`；英文库 `sentence-transformers/all-MiniLM-L6-v2`。首次运行自动从 HF（或 `HF_ENDPOINT` 镜像）下载并缓存本地，之后完全离线。
- **requirements**：`sentence-transformers` 加入可选依赖组（不强制安装，未装时工厂路由才报错）。

### 3.4 M4：Semantic Walk 探索页（Dashboard 第 7 页）

**新增文件**：

```
src/observability/dashboard/
├── pages/
│   └── semantic_walk.py        # 页面 7：Semantic Walk
└── services/
    └── data_service.py         # 扩展：get_similar_chunks()
```

**服务层**：`DataService.get_similar_chunks(chunk_id: str, top_k: int = 8) -> list[dict]`

- 流程：`chroma collection.get(ids=[chunk_id], include=["embeddings"])` 取锚点向量 → `collection.query(query_embeddings=...)` 取 Top-N 相似 → 返回 `{chunk_id, score, title, summary, source_path, text_preview}`。
- 锚点不存在返回空列表（友好提示）；embedding 缺失（理论上不会发生）降级返回空并告警。

**页面交互**（复刻插件核心体验，Streamlit 形态）：

1. **起点选择**：三种入口——随机 chunk（`collection.get(limit=1, offset=randint)`）/ 输入 chunk_id / 按关键词搜索选 chunk。
2. **画布渲染**：`st.graphviz_chart`，锚点居中、候选向外辐射；节点显示 title（截断）+ source；边标签为相对当前批次的相似度分（非绝对百分比，对齐插件语义）。
3. **按需展开**：点选候选（selectbox）→ 该 chunk 成为新锚点继续查询；同一 Markdown 文档的多个 chunk 可同时留在画布上跟随另一分支；「展开历史」纵向记录已走路径。
4. **New batch**：重置画布、重新随机起根。
5. 页面注册进 `app.py` 的 `st.navigation`（六页 → 七页），Overview 数据资产统计自动包含新集合，无需改动。

**离线可测**：页面逻辑抽成可导入纯函数（起点解析 / 图数据组装 / 边标签格式化），`run_walk` 可注入 fake 服务；页面级用 Streamlit AppTest 冒烟（对齐 I2 先例）。

---

## 4. 测试方案

延续 DEV_SPEC 第 4 章分层纪律：**Unit 大量离线确定性 → Integration 真组件+fake 外部依赖 → E2E 少量关键流程**。

### 4.1 测试夹具〔已移交〕

- **v0.2 移交**：`tests/fixtures/vault/` 最小 vault 夹具定义随 M1/M2 迁移至 [INGESTION_ROBUSTNESS_SPEC](../specs/INGESTION_ROBUSTNESS_SPEC.md) 第 5 章「M6 测试夹具」，IR7.1 落地时创建。

### 4.2 单元测试

| 模块 | 测试重点 | 典型用例 |
|------|---------|---------|
| ~~MarkdownLoader~~ | ~~frontmatter 三级 title 兜底；tags 合并去重；wikilink 替换与出链收集；embed 图片存在/缺失两路；坏 frontmatter 降级不阻塞；doc_type/page 契约；确定性（同输入两次 load 相等）；空文件/纯空白文件行为~~ | **已移交** IR 规格 5 章（IR7.1–IR7.2） |
| ~~source_filters~~ | ~~glob 匹配语义（目录段参与）；include 空全受理；exclude 优先；默认排除生效（.obsidian/templates）；显式单文件不过滤；非法配置 fail-fast；filtered 计数聚合~~ | **已移交** IR 规格 5 章（IR7.4） |
| ST Embedding | encode_fn 注入确定性向量；惰性加载（构造零 IO）；未装依赖 ImportError 可读；工厂路由与 settings 切换；dimension 契约；批量顺序一致 | 对齐 B2/B7.3 风格 |
| Semantic Walk 服务 | 锚点缺失空结果；top_k 边界；字段映射；图数据组装确定性；边标签格式化 | fake chroma collection |

### 4.3 集成测试

- **`.md` 全链路与幂等**（v0.2 移交）：随 M1/M2 迁移至 [INGESTION_ROBUSTNESS_SPEC](../specs/INGESTION_ROBUSTNESS_SPEC.md) 第 5 章，对应 IR7.5 验收。

### 4.4 E2E 测试

- **vault 摄取 → 检索 → MCP**（v0.2 移交）：随 M1/M2 迁移至 IR 规格，对应 IR7.5 验收。
- **Dashboard 冒烟**：AppTest 加载第 7 页 Semantic Walk 无异常（fake 服务，对齐 I2 手法）。
- **门控**：真 `sentence-transformers` 模型冒烟用 `RAG_E2E_INTEGRATION=1` 门控，默认跳过（离线全绿）。

---

## 5. 配置变更汇总

`config/settings.yaml` 新增/扩展字段（全部可选，缺省保持现有行为）：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| ~~`ingestion.source_filters.include_patterns`~~ | ~~`[]`~~ | **已移交** IR 规格第 6 章（IR7.4） |
| ~~`ingestion.source_filters.exclude_patterns`~~ | ~~`[".obsidian/**", ".trash/**", "templates/**"]`~~ | **已移交** 同上 |
| `embedding.provider` | （不变） | 新增可选值 `sentence_transformers` |
| `embedding.model` | （不变） | ST 场景推荐 `BAAI/bge-small-zh-v1.5` |

不新增存储路径、不新增数据库表；图片沿用 `data/images/` 与 `image_index.db`，索引沿用 `data/db/chroma|bm25/`。

---

## 6. 项目排期

> **排期原则**（延续 DEV_SPEC 第 6 章）：1 小时一个可验收增量；每步在文件系统产生可见变化；TDD 优先；外部依赖可 Mock。
>
> **任务前缀 V（Vault）**，避免与既有 A–I 编号冲突。

### 阶段总览

1. ~~**模块 M1（V1–V2）**：MarkdownLoader 基础解析 → Obsidian 语法进阶（wikilink/tag/embed）。~~ **已移交 IR7.1–IR7.2**。
2. ~~**模块 M2（V3–V4）**：pipeline 路由登记 `.md` → 目录扫描 include/exclude 规则。~~ **已移交 IR7.3–IR7.4**。
3. **模块 M3（V5）**：sentence-transformers Embedding provider + 工厂注册。
4. **模块 M4（V6）**：Semantic Walk Dashboard 页。
5. **验收（V7）**：Dashboard 冒烟收口 + README 增补（vault 摄取 E2E 与 source_filters 样例已随 M1/M2 移交 IR7.5）。

> 依赖关系（v0.2 更新）：V5、V6 相互独立可并行，且均不依赖已移交的 IR7；V7 依赖 V6（页面冒烟），其 vault 摄取相关验收依赖 IR7 完成（在 IR 规格收口，不阻塞 V7 的 Dashboard 部分）。

### 📊 进度跟踪表 (Progress Tracking)

> **状态说明**：`[ ]` 未开始 | `[~]` 进行中 | `[x]` 已完成

#### 模块 M1：MarkdownLoader〔已移交 INGESTION_ROBUSTNESS_SPEC 阶段 IR7〕

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 |
|---------|---------|------|---------|---------|
| ~~V1~~ | ~~MarkdownLoader 基础（读取/frontmatter/title/outline）~~ | **已移交** | | → IR7.1（IR 规格第 7 章） |
| ~~V2~~ | ~~MarkdownLoader 进阶（tags/wikilinks/embeds/图片）~~ | **已移交** | | → IR7.2 |

#### 模块 M2：路由与扫描排除〔已移交 INGESTION_ROBUSTNESS_SPEC 阶段 IR7〕

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 |
|---------|---------|------|---------|---------|
| ~~V3~~ | ~~Pipeline 路由注册 `.md`（双登记）~~ | **已移交** | | → IR7.3 |
| ~~V4~~ | ~~目录扫描 include/exclude 规则~~ | **已移交** | | → IR7.4 |

#### 模块 M3：本地进程内 Embedding

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 |
|---------|---------|------|---------|---------|
| V5 | sentence-transformers provider | [ ] | | BaseEmbedding 实现 + EmbeddingFactory 注册 `sentence_transformers`；惰性加载（构造零 IO）；未装依赖 embed 抛可读 ImportError；encode_fn 注入离线单测；settings.yaml 切换后摄取与查询同源（真模型冒烟走 RAG_E2E_INTEGRATION 门控）；可选依赖组登记 |

#### 模块 M4：Semantic Walk

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 |
|---------|---------|------|---------|---------|
| V6 | Semantic Walk 页（Dashboard 第 7 页） | [ ] | | `DataService.get_similar_chunks`（锚点缺失友好空）；页面三入口（随机/chunk_id/搜索）+ graphviz 画布 + 按需展开 + New batch 重置；app.py 导航注册七页；纯函数单测 + AppTest 冒烟通过 |

#### 验收收口

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 |
|---------|---------|------|---------|---------|
| V7 | Dashboard 冒烟收口 + 文档 | [ ] | | Dashboard 七页冒烟（含 Semantic Walk）；README 运行手册增补「摄取 Obsidian 库」小节（vault 摄取 E2E 与 source_filters 配置样例已随 M1/M2 移交 IR7.5 验收）；全套件离线全绿 |

---

## 7. 可扩展性与未来展望

- **更多 Loader**：IR7（MarkdownLoader）落地后，Word / HTML / DOCX 只需按同一模式新增 `BaseLoader` 实现并在两处登记扩展名（README FAQ #4 的既定路径）。
- **vault 监听增量**：在 IR7 的 source_filters 过滤层之上叠加 watchdog 文件监听 + 防抖调度，即可实现插件式「Continue index」自动体验；SHA256 增量机制无需改动。
- **双链图谱检索**：IR7 落地后 `wikilinks` 会写入 metadata，未来可做 Graph RAG（沿双链扩展召回上下文）或 Dashboard 关系图页，与 Semantic Walk 共用图渲染。
- **wikilink 解析增强**：当前保留文本语义；后续可解析 vault 内实际文件路径建立 `chunk_id ↔ wikilink` 映射，支持「引用该笔记的笔记」反向检索。
- **Qwen3-Embedding 等本地模型**：M3 的 provider 模式可直接复用于任何 sentence-transformers 兼容模型（BGE 系列 / Qwen 系列本地权重），仅改 `embedding.model`。
