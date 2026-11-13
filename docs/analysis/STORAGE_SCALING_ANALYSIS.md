# 存储层扩容分析与演进方向（STORAGE_SCALING_ANALYSIS）

> 版本：0.1 — 基于当前代码与实测数据的架构判断（2026-10-16）
>
> 基线：WC 系列（WebUI + Chat）冻结收口状态。性质：**分析文档，非可执行排期**；文中的「换后端」动作落地前需另走 `docs/backlog/` 提案立项。
>
> 背景问题：资料数量级将持续攀升（批量 PDF / Markdown），是否需要引入 PostgreSQL / DuckDB 等专用数据库系统。

---

## 目录

- [1. 现状盘点：存储在哪、什么格式](#1-现状盘点存储在哪什么格式)
- [2. 各组件的扩容极限与瓶颈顺序](#2-各组件的扩容极限与瓶颈顺序)
- [3. 对 PostgreSQL / DuckDB 的判断](#3-对-postgresql--duckdb-的判断)
- [4. 分阶段演进路线](#4-分阶段演进路线)
- [5. 一句话总结](#5-一句话总结)

---

## 1. 现状盘点：存储在哪、什么格式

全链路自包含，无外部数据库服务；所有衍生物在 `data/` 下，原始文件不动。

| 位置 | 内容 | 格式 | 写入方 |
|------|------|------|--------|
| `data/documents/` | 原始文件 | 用户原文件 | 上传/摄取脚本 |
| `data/db/chroma/` | **主库**：chunk 稠密向量 + 原文 + metadata（`chroma.sqlite3`） | Chroma 内嵌 | `src/ingestion/` upsert |
| `data/db/bm25/index.json` | BM25 稀疏检索索引（倒排） | 单个 JSON 文件 | `src/ingestion/storage/bm25_indexer.py` |
| `data/db/ingestion_history.db` | 摄取历史：SHA256、状态、`parser/chunk/embedding` 三版本号 | SQLite | DocumentManager |
| `data/db/image_index.db` | PDF 抽出图片 + 描述 | SQLite | 摄取管线 |
| `data/db/tasks.db` | Web 后台任务状态 | SQLite | `src/api/tasks.py` |
| `data/db/chat_sessions.db` | Chat 会话/消息/引用 | SQLite | SessionStore（WC6） |
| `data/db/sirchmunk/` | sidecar 知识簇（agentic 模式） | Parquet | sidecar 进程 |
| `logs/traces.jsonl` | 查询/摄取 trace | JSON Lines | TraceContext |

实测基准（2026-10-16，本机当前索引）：**716 个向量**，`bm25/index.json` **17 MB**。BM25 索引体积约按 chunk 数线性增长，是后续推算内存占用的最直观标尺。

## 2. 各组件的扩容极限与瓶颈顺序

| 存储 | 技术 | 约在什么量级开始疼 | 疼的原因 |
|------|------|--------------------|----------|
| BM25 `index.json` | 全量 JSON | **最先**（数十万 chunks 即可感知） | `json.loads` 整文件进内存（`bm25_indexer.py:396`），保存为全量重写；进程常驻内存随语料线性涨 |
| 向量库 Chroma | 内嵌 | 约 50万–100万 chunks | 单进程内嵌：无独立服务、并发写入弱、运维态（备份/迁移）依赖停写 |
| 元数据 SQLite ×4 | SQLite | 千万行级都不疼 | 单实例应用的 OLTP 读写模式与 SQLite 完全匹配 |
| 原始文件 / Parquet | 文件系统 | 不构成瓶颈 | 不是数据库 |

体感标尺：100 万 chunks ≈ 1024 维 float32 下 4–5 GB 磁盘，检索本身无压力；**先到的是嵌入费用与摄取耗时，其次 BM25 内存，数据库最后才疼**。

结论：瓶颈到来顺序为 **BM25 → 向量库 → 元数据库**。当前量级（716 向量）离第一个瓶颈尚有约三个数量级余量。

## 3. 对 PostgreSQL / DuckDB 的判断

- **DuckDB（OLAP）不对症**：擅长扫描聚合，不擅长会话/任务这类频繁小读写（OLTP）。拿它替换 SQLite 管元数据是降级。合理用武之地仅一处：对 `logs/traces.jsonl` / 评估结果做**离线分析**（按需引入为工具，非架构改动）。
- **PostgreSQL 的价值在多写入者**（多服务实例共写一库）。当前整条链路单实例，现在引入只有运维成本没有收益。
- **向量检索若换，正解是专用向量库或 pgvector**，不是拿 Postgres 管一切。注意：`config/settings.yaml` 中 `backend: chroma # chroma | qdrant | pinecone` 注释只是愿景，`src/libs/vector_store/` 目前**只实现了 chroma**；`sparse_backend: bm25 # bm25 | elasticsearch` 同样只有 bm25 实现。注释里的选项都需先立项开发。

## 4. 分阶段演进路线

本项目已在 `src/libs/` 预留可插拔扩展轴（`BaseVectorStore` + factory，切换后端 = 改配置）。据此按触发条件分阶段，**每个动作落地前走 backlog 提案**：

| 阶段 | 触发条件 | 动作 | 性质 |
|------|----------|------|------|
| 0（现在） | 数十万 chunks 内 | 什么都不换：保持文件型，备份 = 停写后拷目录 | — |
| 1 | BM25 索引使进程内存明显上涨 / 索引保存耗时不可接受 | 稀疏检索独立化：Elasticsearch/OpenSearch 后端（或 BM25 索引改 SQLite 分片存取，改动更小、优先评估） | backlog 提案 |
| 2 | 向量数达数十万–百万，或需要独立扩缩/在线备份 | 实现 Qdrant 后端（`BaseVectorStore` 子类 + factory 注册），Docker 自托管 | backlog 提案 |
| 3 | 拆微服务 / 多实例共写（对齐 `MICROSERVICES_ANALYSIS.md` 路线） | 引入 PostgreSQL 统一元数据；向量可顺带评估 pgvector | 届时再议 |

阶段 1 有一个**改动更小的中间形态**值得优先评估：仅把 BM25 索引的持久化从「单 JSON 全量重写」换成 SQLite 存取（分片读写、增量保存），复用既有 SQLite 运维经验，避免为第一个瓶颈引入一个常驻服务。

## 5. 一句话总结

**存储保持文件型，不动**；瓶颈顺序是 BM25 → 向量库 → 元数据库，当前距第一个瓶颈尚有约三个数量级余量；DuckDB 只适合做 traces 离线分析，PostgreSQL 等到多实例那天再说；每个换后端动作落地前按治理流程走 backlog 提案。
