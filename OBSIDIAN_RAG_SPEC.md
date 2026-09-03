<!-- 扩展规格：本地笔记库（Obsidian Vault）RAG 能力。格式与架构分层对齐 DEV_SPEC.md；基线为 clean-start 分支「阶段 A–I 已完成」状态。 -->
# Obsidian Vault RAG 扩展规格 (OBSIDIAN_RAG_SPEC)

> 版本：0.1 — 对标 Analogy 插件（liaocaoxuezhe/obsidian-extension）核心能力的增量开发规格
>
> 基线：DEV_SPEC.md 阶段 A–I 全部完成（PDF Ingestion / Hybrid Search / MCP Server / Dashboard / Evaluation / E2E 均已落地）

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
| 读取本地 Markdown 笔记 | ❌ 仅支持 `.pdf`（`pipeline._SUPPORTED_EXTENSIONS = {".pdf"}`，`scripts/ingest.py` 同步登记，`.md` 被静默忽略） | **M1 + M2** |
| Obsidian 语法（frontmatter / wikilink / tag / embed） | ❌ 无对应解析器 | **M1** |
| 排除指定目录/文件不索引 | ❌ `collect_files` 仅按扩展名过滤，无 include/exclude 规则 | **M2** |
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

### 2.1 In Scope（4 个模块，7 个任务）

| 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|--------|-----------|
| M1 | MarkdownLoader（Vault 感知解析器） | P0（必须） | `.md` → 标准 `Document`，吃下 frontmatter / wikilink / tag / embed |
| M2 | 摄取路由 + 目录扫描/排除规则 | P0（必须） | `.md` 进入 pipeline 路由；目录递归扫描支持 include/exclude glob |
| M3 | 进程内本地 Embedding provider | P1（可选） | 新增 `sentence_transformers` backend，零后台服务完成摄取+查询向量化 |
| M4 | Semantic Walk 探索页 | P2（可选） | Dashboard 第 7 页，chunk 级相似度图探索 |

**最小可用路径 = M1 + M2**。完成后即可：`python scripts/ingest.py --path <vault目录> --collection vault` 把整个 Obsidian 库索引进本机 Chroma，并经 MCP / Dashboard / `scripts/query.py` 检索。

### 2.2 Out of Scope（明确不做）

- **不做 Obsidian TS 插件壳**：侧边栏 UI、ribbon、Obsidian API 属于独立 TypeScript 工程；本项目检索入口保持 MCP 客户端（Copilot / Claude）+ Streamlit Dashboard。
- **不做 vault 实时监听**（file watcher / 自动重索引调度）：沿用 CLI / Dashboard 手动触发 + SHA256 增量跳过，已满足「Continue index」语义。
- **不引入 CLIP 多模态向量**：图片检索沿用既有 Image-to-Text（Captioning）策略。
- **不新建向量库/索引结构**：继续复用 Chroma（All-in-One 存储）+ BM25 JSON 索引 + 图片 SQLite 索引的既有三路存储。

---

## 3. 技术选型与模块设计

### 3.1 M1：MarkdownLoader（Vault 感知解析器）

**新增文件**：`src/libs/loader/markdown_loader.py`（参考 `pdf_loader.py` 的实现模式与降级风格）

**接口契约**（对齐 `BaseLoader`）：

```python
class MarkdownLoader(BaseLoader):
    def __init__(self, *, vault_root: str | None = None,
                 images_root: str | None = None) -> None: ...
    def load(self, source: str) -> Document: ...
```

- `vault_root`：解析 `![[image.png]]` / `![](relative.png)` 相对引用的根目录；缺省按 `source` 所在目录解析。
- `images_root`：图片落盘根目录，对齐 C3/C13 的 `ImageStorage.images_root` 约定。

**解析管线（有序，逐段降级不阻塞）**：

1. **读取**：UTF-8 读全文；编码错误抛可读 `LoaderError`（不静默替换）。
2. **YAML frontmatter**：文件以 `---` 行开头时提取至下一个 `---` 行；用简单行级解析（`key: value`、`key: [a, b]`、多行 `- item` 列表）优先零新依赖；环境内已有 PyYAML（chromadb 传递依赖）时可用其解析。**解析失败降级**：frontmatter 原样拼回正文，`metadata["frontmatter_parse_error"]=true`，不阻塞摄取。
3. **title**：`frontmatter.title` > 正文首个 `# H1` > 文件名 stem，三级兜底保证非空。
4. **heading_outline**：收集全部 ATX 标题行（`#`~`######`），供定位与回溯。
5. **行内 tags**：匹配 `(^|\s)#([\w\u4e00-\u9fff][\w\u4e00-\u9fff/-]*)`（排除标题行与代码块内），与 frontmatter `tags` 合并 → `metadata["tags"]`（去重保序）。
6. **wikilinks**：`[[target]]` / `[[target|alias]]` → 正文替换为 `alias 或 target`（去掉双方括号，保留可检索语义、消除 token 噪音），`target` 记入 `metadata["wikilinks"]`（去重保序）。
7. **嵌入引用 `![[target]]` 与 `![alt](path)`**：
   - 目标为图片扩展名（png/jpg/jpeg/gif/webp/svg/bmp）→ 按 `vault_root` 解析路径；**存在**：生成 `image_id`（沿用 `make_image_id` 语义）+ `[IMAGE: {image_id}]` 占位符 + `ImageRef` 落盘 `images_root`（对齐 C3 约定）；**不存在**：保留原文并记入 `metadata["missing_image_refs"]`，告警不阻塞。
   - 目标为笔记（`![[note.md]]`）：**不递归展开**，保留文本引用并记入 `metadata["embeds"]`（防止索引体积失控与环引用）。
8. **doc_type**：`metadata["doc_type"] = "markdown"`；无 `page` 字段（不适用）。

**输出 metadata 契约**（在 PDF 基础字段之上扩展）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `source_path` | str | 绝对路径 |
| `doc_type` | str | 固定 `"markdown"` |
| `sha256` | str | 文件指纹（与 C2 同算法） |
| `title` | str | 三级兜底非空 |
| `heading_outline` | list[str] | 标题行列表 |
| `tags` | list[str] | frontmatter + 行内合并 |
| `wikilinks` | list[str] | 出链目标（文件名，不含扩展名） |
| `embeds` | list[str] | 笔记嵌入引用（未展开） |
| `images` | list[ImageRef] | 图片引用（对齐既有六字段规范） |

**确定性**：同输入同输出（无随机性、无时间戳入 metadata）；单图失败降级不阻塞整体（对齐 C3 语义）。

### 3.2 M2：摄取路由 + 目录扫描/排除规则

**路由登记（两处同步，drift guard 锁死）**：

- `src/ingestion/pipeline.py`：`_SUPPORTED_EXTENSIONS = {".pdf", ".md", ".markdown"}`；`_resolve_loader()` 新增分支 `suffix in {".md", ".markdown"}` → `MarkdownLoader(vault_root=..., images_root=str(self._image_storage.images_root))`。
- `scripts/ingest.py`：`SUPPORTED_EXTENSIONS = frozenset({".pdf", ".md", ".markdown"})`（既有 drift guard 单测自动约束两处一致）。

**目录扫描与排除（`collect_files` 扩展）**：

- 新增配置节 `ingestion.source_filters`（见第 5 节）：
  - `include_patterns: list[str]`（glob，空列表 = 全部受理）
  - `exclude_patterns: list[str]`（glob，默认 `[".obsidian/**", ".trash/**", "templates/**"]`）
- **匹配语义**：对「扫描根目录」的 POSIX 相对路径做 `fnmatch` 匹配（目录段参与匹配，如 `.obsidian/attachments/x.png` 命中 `.obsidian/**`）；include 与 exclude 同时命中时 **exclude 优先**。
- **作用范围**：过滤仅作用于**目录递归扫描**；`--path` 显式指向单个文件时视为用户明确意图，**不过滤**（仅当扩展名不支持时维持现有 usage 报错）。
- **CLI 汇总**：`IngestSummary` 新增 `filtered` 计数；汇总块输出 `filtered: N (excluded by source_filters)`；被过滤文件不出现在逐文件行中。
- **配置校验**：`include_patterns` / `exclude_patterns` 必须为 `list[str]`（元素非空字符串），否则 `ConfigError` 可读报错；空 glob 串拒绝。

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

### 4.1 测试夹具

新增 `tests/fixtures/vault/` 最小 vault：

```
tests/fixtures/vault/
├── .obsidian/app.json              # 应被默认 exclude 过滤
├── templates/daily.md              # 应被默认 exclude 过滤
├── notes/
│   ├── frontmatter.md              # 完整 YAML frontmatter（title/tags/aliases）
│   ├── wikilinks.md                # [[note]] / [[note|alias]] / ![[note.md]]
│   ├── inline-tags.md              # 行内 #tag 中文英文混合
│   ├── embeds-image.md             # ![[assets/pic.png]] + ![](rel.png)（含存在与缺失两种）
│   ├── bad-frontmatter.md          # 非法 YAML → 降级路径
│   └── 中文笔记.md                  # CJK 内容 + CJK 标签
└── assets/pic.png                  # 1x1 PNG
```

### 4.2 单元测试

| 模块 | 测试重点 | 典型用例 |
|------|---------|---------|
| MarkdownLoader | frontmatter 三级 title 兜底；tags 合并去重；wikilink 替换与出链收集；embed 图片存在/缺失两路；坏 frontmatter 降级不阻塞；doc_type/page 契约；确定性（同输入两次 load 相等）；空文件/纯空白文件行为 | 每类 fixture 至少 1 正例 + 1 边界 |
| source_filters | glob 匹配语义（目录段参与）；include 空全受理；exclude 优先；默认排除生效（.obsidian/templates）；显式单文件不过滤；非法配置 fail-fast；filtered 计数聚合 | 参数化 pattern×path 矩阵 |
| ST Embedding | encode_fn 注入确定性向量；惰性加载（构造零 IO）；未装依赖 ImportError 可读；工厂路由与 settings 切换；dimension 契约；批量顺序一致 | 对齐 B2/B7.3 风格 |
| Semantic Walk 服务 | 锚点缺失空结果；top_k 边界；字段映射；图数据组装确定性；边标签格式化 | fake chroma collection |

### 4.3 集成测试

- **`.md` 全链路**（对齐 C14/C15 模式）：真 MarkdownLoader + 真 DocumentChunker / 规则 transform / BM25Indexer / ChromaStore / SQLite 全部落 `tmp_path`，唯一注入确定性 fake embedding；摄取 fixtures/vault 后断言：向量数、BM25 `num_docs`、chunk `metadata.doc_type=="markdown"`、`tags`/`wikilinks` 进入 metadata、`collection` 过滤可命中、图片 SQLite 登记与字节文件存在、`.obsidian/` 与 `templates/` 未被摄取且 `filtered==2`。
- **幂等**：重跑全 skipped；`--force` 幂等替换；修改一个笔记后仅该文件重摄取。

### 4.4 E2E 测试

- **vault 摄取 → 检索 → MCP**：CLI 摄取 mini vault（fake embedding，离线）→ `scripts/query.py` 命中预期笔记 → 子进程 MCP server `tools/call query_knowledge_hub` 返回引用 `source` 为笔记路径（对齐 I1 手法）。
- **Dashboard 冒烟**：AppTest 加载第 7 页 Semantic Walk 无异常（fake 服务，对齐 I2 手法）。
- **门控**：真 `sentence-transformers` 模型冒烟用 `RAG_E2E_INTEGRATION=1` 门控，默认跳过（离线全绿）。

---

## 5. 配置变更汇总

`config/settings.yaml` 新增/扩展字段（全部可选，缺省保持现有行为）：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `ingestion.source_filters.include_patterns` | `[]` | glob 列表；空 = 全部受理 |
| `ingestion.source_filters.exclude_patterns` | `[".obsidian/**", ".trash/**", "templates/**"]` | glob 列表；优先于 include |
| `embedding.provider` | （不变） | 新增可选值 `sentence_transformers` |
| `embedding.model` | （不变） | ST 场景推荐 `BAAI/bge-small-zh-v1.5` |

不新增存储路径、不新增数据库表；图片沿用 `data/images/` 与 `image_index.db`，索引沿用 `data/db/chroma|bm25/`。

---

## 6. 项目排期

> **排期原则**（延续 DEV_SPEC 第 6 章）：1 小时一个可验收增量；每步在文件系统产生可见变化；TDD 优先；外部依赖可 Mock。
>
> **任务前缀 V（Vault）**，避免与既有 A–I 编号冲突。

### 阶段总览

1. **模块 M1（V1–V2）**：MarkdownLoader 基础解析 → Obsidian 语法进阶（wikilink/tag/embed）。
2. **模块 M2（V3–V4）**：pipeline 路由登记 `.md` → 目录扫描 include/exclude 规则。
3. **模块 M3（V5）**：sentence-transformers Embedding provider + 工厂注册。
4. **模块 M4（V6）**：Semantic Walk Dashboard 页。
5. **验收（V7）**：E2E 收口 + 配置样例 + README 增补。

> 依赖关系：V3 依赖 V1/V2；V4 依赖 V3；V5、V6 相互独立可并行；V7 依赖 V1–V4（V5/V6 完成度纳入但不阻塞主验收）。

### 📊 进度跟踪表 (Progress Tracking)

> **状态说明**：`[ ]` 未开始 | `[~]` 进行中 | `[x]` 已完成

#### 模块 M1：MarkdownLoader

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 |
|---------|---------|------|---------|---------|
| V1 | MarkdownLoader 基础（读取/frontmatter/title/outline） | [ ] | | `load()` 返回标准 Document：frontmatter 解析（含坏 YAML 降级）、title 三级兜底非空、heading_outline 收集、doc_type=markdown、sha256 一致；单测覆盖全部 fixtures 基础场景 |
| V2 | MarkdownLoader 进阶（tags/wikilinks/embeds/图片） | [ ] | | 行内 tag + frontmatter tags 合并去重；wikilink 替换为 alias/target 且出链入 metadata；`![[img]]`/`![](rel)` 存在两路（落盘+占位符）/缺失（保留原文+missing_image_refs）；`![[note.md]]` 不展开仅记录；确定性单测通过 |

#### 模块 M2：路由与扫描排除

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 |
|---------|---------|------|---------|---------|
| V3 | Pipeline 路由注册 `.md`（双登记） | [ ] | | `pipeline._SUPPORTED_EXTENSIONS` 与 `scripts/ingest.py` 同步含 `.md`/`.markdown`（drift guard 绿）；`_resolve_loader` 路由 MarkdownLoader；单文件 `--path x.md` 可摄取；未知扩展报错信息更新 |
| V4 | 目录扫描 include/exclude 规则 | [ ] | | `ingestion.source_filters` 配置解析+校验（非法 fail-fast）；collect_files 按 glob 过滤（exclude 优先、目录段参与）；默认排除 `.obsidian/.trash/templates`；显式单文件不过滤；CLI 汇总输出 filtered 计数；集成测试断言 fixtures/vault 过滤后 filtered==2 |

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
| V7 | E2E 收口 + 文档 | [ ] | | e2e：vault 摄取 → query 命中 → MCP tools/call 引用笔记路径；Dashboard 七页冒烟；config/settings.yaml 补 source_filters 示例；README 运行手册增补「摄取 Obsidian 库」小节；全套件离线全绿 |

---

## 7. 可扩展性与未来展望

- **更多 Loader**：M1 落地后，Word / HTML / DOCX 只需按同一模式新增 `BaseLoader` 实现并在两处登记扩展名（README FAQ #4 的既定路径）。
- **vault 监听增量**：在 M2 的过滤层之上叠加 watchdog 文件监听 + 防抖调度，即可实现插件式「Continue index」自动体验；SHA256 增量机制无需改动。
- **双链图谱检索**：M1 已把 `wikilinks` 写入 metadata，未来可做 Graph RAG（沿双链扩展召回上下文）或 Dashboard 关系图页，与 Semantic Walk 共用图渲染。
- **wikilink 解析增强**：当前保留文本语义；后续可解析 vault 内实际文件路径建立 `chunk_id ↔ wikilink` 映射，支持「引用该笔记的笔记」反向检索。
- **Qwen3-Embedding 等本地模型**：M3 的 provider 模式可直接复用于任何 sentence-transformers 兼容模型（BGE 系列 / Qwen 系列本地权重），仅改 `embedding.model`。
