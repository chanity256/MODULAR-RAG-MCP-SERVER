<!-- 规格定位：让 agentic 检索（MCP agentic_search 工具、Chat / Playground 的 Agentic fast/deep、Hybrid 第三路）按 collection 隔离可见范围，并顺带修复第三路绕开调用方白名单的缺口与同名上传跨 collection 覆盖。基线：S / SX / WC / AR / RA 均已收口冻结。与既有规格的关系：调用方定界扩展，不改 PathGuard 契约（S1.3）、不改 sidecar、不动 Hybrid 主路。 -->

# Agentic 检索按 collection 隔离规格 (AGENTIC_COLLECTION_ISOLATION_SPEC)

> 版本：0.2 — 由 AR 遗留台账候选 #2 立项为可执行规格，采用定界方案 A（历史表定界 paths）
>
> 修订记录：v0.1（2026-11-21）立项，D1–D4 终裁；v0.2（2026-11-21）对照代码评审修订：补相对 `source_path` 解析基准、定界集成员后置过滤、`paths` / `collection` 同时给出的语义（D5 终裁：互斥）、`run_stream` 透传、第三路鸭子类型兼容、批量上传与并发语义；纠正「复用 `filter_evidence_paths`」「与 AR2 引用检查同源」「历史表不受 N-05 影响」「外发范围收窄到定界集」四处表述；D1–D4 终裁不变，新增 D5 终裁
>
> 起源与决策依据：由 `backlog/agentic-roots-followups_backlog.md` 单文件晋升（该台账候选 #1 已先行拆分移交 RA 规格，候选 #2 为本规格全部承接内容，台账原文见 git 历史）；台账源自 [14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md](../frozen/14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md) §2.2 Out of Scope。依据 [03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md](../frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md) S1.3（PathGuard 双层防线）与 S2.3（第三路）、[04_INGESTION_ROBUSTNESS_SPEC_frozen.md](../frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md) M1（历史表 v2 `(source_path, collection)` 主键）、[16_ROOT_ANCHOR_SPEC_frozen.md](../frozen/16_ROOT_ANCHOR_SPEC_frozen.md)（项目根锚定）
>
> 基线：`a33ab97`（main），AR / RA 系列均已收口冻结；本仓库 `config/settings.yaml` 中 `agentic_search.enabled: true`、`allowed_roots` 含 `./data/documents` 与 `./data/uploads`
>
> 日期：2026-11-21

红线不变：主 venv 永不 `import sirchmunk`；白名单仍由本项目调用方执行，PathGuard fail-closed 语义不放宽；sidecar 侧零改动。

## 目录

- 1 项目概述（1.1 结论速览、1.2 背景与现状、1.3 决策依据与终裁）
- 2 核心目标与范围（2.1 In Scope、2.2 Out of Scope）
- 3 技术选型与模块设计
- 4 测试方案
- 5 任务排期与进度
- 6 验收清单
- 7 实现合入后的回写清单
- 8 隐私注记

---

## 1. 项目概述

### 1.1 结论速览

| 问题 | 结论 |
|------|------|
| 要解决什么问题？ | agentic 检索按文件系统路径工作、不认识 collection：用户指定 collection 时，Hybrid 主路按 collection 过滤，而 MCP `agentic_search` 工具、Chat / Playground 的 Agentic (fast/deep)、Hybrid 第三路（S2.3）都把全部白名单根（含其他 collection 的上传文件与 `data/documents/`）作为检索起点；同名上传文件跨 collection 互相覆盖使磁盘内容进一步与索引脱节 |
| 定位是什么？ | 既有链路的调用方定界扩展：collection → 摄取历史 v2 success 记录 → 按项目根归一的绝对路径集 ∩ PathGuard 白名单 → 作为 `paths` 发给 sidecar + 证据按定界集后置过滤；不改目录布局、不改 PathGuard 契约、不改 sidecar |
| 实施范围（已决策） | AC1–AC6 为承诺任务；AC7 为门控任务（需 sidecar 与 `RAG_AGENTIC_INTEGRATION=1`） |
| 主要代价 | 带 collection 的 agentic 检索多一次历史表查询；空定界集直接返回零证据、不调用 sidecar；同名上传防护使「覆盖式更新」改为「先删除文档再重传」（见 D4）；第三路修复后恒发显式 `paths`（行为收紧，不再回退 sidecar CWD，`evaluate.py` 的 agentic 指标可能随之变化） |
| 不能做到什么？ | 定界收窄的是检索起点与引用证据，不是安全边界：sidecar 的 `file_read` 仍可读 `paths` 之外的文件，agentic 答案文本不经路径过滤（见 §8） |

### 1.2 背景与现状

sidecar 按文件系统路径检索，不认识 collection（03 规格）；上传目录扁平（`save_uploads` 按 `sanitize_filename` 落盘 `data/uploads/`，`src/observability/dashboard/services/ingestion_service.py:103-108`），`data/documents/` 的 CLI 摄取文件同样跨 collection 可见。证据（基线 `a33ab97`）：

- **MCP 工具无 collection 概念**：`src/mcp_server/tools/agentic_search.py:75` 的 schema 只有 `query` / `mode` / `paths` / `top_k`；`:225-227` 不传 `paths` 时默认搜全部 `allowed_roots`。
- **Chat 有意丢弃 collection**：`src/observability/dashboard/services/chat_retrievers.py:108` 注释「agentic 按路径白名单检索，没有 collection 概念，该参数在此被有意忽略」；ChatEngine 传入的 collection 到此为止。
- **Playground 同样丢弃**：`src/observability/dashboard/services/search_service.py:233-237` `_run_agentic` 不传 collection，Deep 流式入口 `run_stream`（`:207`，WS `/api/v1/search/ws` 调用）另行组装 arguments、同样不传；前端 collection 输入仅 Normal 模式显示（USAGE_GUIDE §2.7），`frontend/lib/search.ts:109` 的 `buildSearchRequest` 对 fast/deep 丢弃 collection（HTTP 与 WS 两条路径共用）。
- **Hybrid 第三路更严重（绕开调用方白名单）**：`src/libs/agentic_search/adapter.py:54` 构造 `AgenticSearchRequest(query=query)` 不带 `paths` → `src/libs/agentic_search/http_backend.py:234` 发送 `paths: None` → sidecar 回退 `SIRCHMUNK_SEARCH_PATHS` / 自身 CWD（03 规格 §4.6 路径解析链），**完全绕开本项目应执行的调用方白名单**；证据也不经 `filter_evidence_paths`（第三路只在 `agentic_dedup` 上下文扩展时用 PathGuard 校验宿主文件读取）。且 `converter.py:263-270` 产出的 metadata 无 `collection` 键，`_apply_metadata_filters` 缺失字段宽松包含（`hybrid_search.py:366-367`）→ collection 过滤对第三路结果无效。第三路当前仅 `scripts/query.py --agentic` 与 `scripts/evaluate.py` 接入、默认 `trigger: tool_only` 不触发，但 `always` / `fallback` 配置下缺口即暴露。
- **Hybrid 主路按 collection 过滤但存在既有缺陷 N-05**：`chunk_id = {doc_hash}_{index}_{content_hash}` 不含 collection，所有 collection 共用一个物理 Chroma collection；同一内容摄取到第二个 collection 会覆盖第一个的 Chroma 记录（04 规格「已知遗留问题」，`tests/e2e/test_qa_offline_gaps.py:240` strict xfail 锁定）。本规格不修（见 2.2），但它是设计定界基准的依据：collection → 文件集的权威来源是**摄取历史表 v2**（主键 `(source_path, collection)`，04 规格 M1），不是 Chroma 反查。限定：历史表在摄取路径上不受 N-05 影响，但删除路径会联动——`DocumentManager._remove_history`（`src/ingestion/document_manager.py:422-455`）删除时会顺带清掉「同路径、同哈希、Chroma 已无数据」的其他 collection 记录，即 N-05 覆盖后从 B 删除，A 的同文件历史记录随之消失（AR2 也会因 Chroma 无引用而删上传源文件）。该情形下 A 的定界集失去该文件，与 A 的 Hybrid 主路（Chroma 记录早已被覆盖）口径一致，属 fail-closed，不扩大可见范围。
- **历史表 `source_path` 形态混合**：历史表原样存储 `source_path`、不做归一（`src/libs/loader/file_integrity.py:192-193`，「归一化由调用方负责」）。CLI 摄取记录取决于调用时传入的 `--path`，本地实库中为 `data/documents/...` 相对形式；RA3（`4026931`）之前上传目录为 `Path("data/uploads")`，旧上传记录为 `data/uploads/<name>` 相对形式；RA3 之后上传记录为绝对路径。另有 v1 迁移遗留的占位记录（`source_path` 对外为 `None`）。而 `PathGuard.filter_evidence_paths` 对相对路径先按当前 CWD 解析（`src/libs/agentic_search/path_guard.py:132-141`），对绝对路径不检查存在性、对 URL 原样放行——直接复用会使定界结果依赖启动目录（违背 RA「可从任意目录启动」），且放过不存在的路径。
- **同名上传跨 collection 覆盖**（AR 规格 §2.2 登记的既有行为）：不同内容的同名文件先后上传到 A、B 两个 collection 后，磁盘文件已是 B 的版本；从 A 删除时文件因仍被 B 引用而保留（AR2 删除联动），A 的索引与磁盘文件早已不一致；agentic 按路径检索命中的是 B 的内容。
- **可见范围口径差异在文档中未说明**：USAGE_GUIDE §2.7 与 README 仅写「collection 输入仅在 Normal 模式显示」，未解释 agentic 各入口的可见范围口径。

提案三问的答案：

1. **已观察到的问题**：见上。AR4 冒烟只覆盖单 collection，跨 collection 为设计分析结论（尚无跨 collection 实测，AC7 补齐）。
2. **更简单的方案**：对比见 §3 选型理由。方案 A（历史表定界 paths）改动最小，且同时覆盖 `data/uploads/` 与 `data/documents/`、不改任何既有 doc_id。
3. **如何确认有效**：见 §4 与 §6，核心是「A、B 两个 collection 各有一份独有文件时，带 collection=A 的 agentic 检索只命中 A 的文件、带 collection=B 只命中 B 的；不带 collection 时两者皆可见（口径与现状一致）」。

### 1.3 决策依据与终裁

| 编号 | 决策 | 终裁（2026-11-21，用户拍板） |
|------|------|------------------------------|
| D1 | collection 隔离方案 | **方案 A 历史表定界 paths**：collection → 摄取历史 v2 success 记录的 `source_path` 集 → 解析为绝对路径 ∩ PathGuard 白名单 → 作为 `paths` 发送 + 证据后置过滤。否决方案 B（上传目录按 collection 分子目录）：改 `source_path` / doc_id、需迁移既有上传、且不覆盖 `data/documents/`；否决方案 C（仅文档说明口径差异）：可见范围与外发面问题仍在 |
| D2 | 顺带范围 | 一并承接：① Hybrid 第三路白名单修复（恒发显式 `paths` + 证据后置过滤）；② 同名上传 409 防护；③ MCP `agentic_search` 增加可选 `collection` 参数。**不承接** N-05（chunk_id 含 collection + 数据迁移），留作 QA xfail 与 04 规格已知遗留，另立提案 |
| D3 | 空定界集语义 | collection 给定但定界集为空（该 collection 无 success 记录，或记录全部落在白名单外）时，**不调用 sidecar**，直接返回零证据结构化结果（`source_files: []`）；fail-closed，绝不静默退化为全量 `allowed_roots`（「该集合无文件」不能变成「全库外发」） |
| D4 | 同名上传 409 语义 | `POST /uploads` 落盘前检查：目标路径已存在**且**被任一 collection 的摄取历史 success 记录引用（`source_path` 匹配）**且**新内容 sha256 ≠ 磁盘现有文件 sha256 → 409（`upload_name_in_use`），消息指引先删除对应文档（删除经 AR2 联动清理源文件后即可重传）；同哈希 → 幂等放行（重写相同字节）；无历史引用（孤儿文件）→ 现状覆盖放行 |
| D5 | MCP 工具 `paths` 与 `collection` 同时给出 | **二者互斥**：同时给出 → `-32602`、不调用后端（最简单且 fail-closed）。否决交集语义：需要「collection 内某子目录」时另立提案 |

## 2. 核心目标与范围

### 2.1 In Scope

| 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|--------|-----------|
| `libs/agentic_search` | collection 路径集推导组件 | 承诺（AC1） | collection → 历史表 success 记录 → 相对路径按项目根解析 → 逐条经 PathGuard 校验（不合格剔除）→ 有序绝对路径集 |
| MCP | `agentic_search` collection 参数 | 承诺（AC2） | 可选 `collection`：给定 → 定界集检索 + 证据按定界集后置过滤；空集按 D3 返回零证据；与 `paths` 互斥（D5）；不给 → 现状 |
| Chat / Playground | collection 透传 | 承诺（AC3） | `AgenticChatRetriever`、`SearchService._run_agentic` 与 `SearchService.run_stream` 透传 collection；Playground 前端 agentic 模式开放 collection 输入 |
| `core/query_engine` | Hybrid 第三路白名单修复 + collection 定界 | 承诺（AC4） | 第三路恒发显式 `paths`（默认白名单根；filters 含 collection 时发定界集）+ 证据经 `filter_evidence_paths` 白名单过滤、带 collection 时再按定界集过滤 |
| API | 同名上传 409 防护 | 承诺（AC5） | 按 D4 语义在 `POST /uploads` 拦截跨 collection 静默覆盖；批量上传整批预检、整批拒绝 |
| 文档 | 使用说明同步 | 承诺（AC6） | USAGE_GUIDE 与 README 写明 collection 口径、409 行为、第三路 paths 语义与定界的安全边界 |
| 冒烟 | 真实 sidecar 跨 collection 验证 | 门控（AC7，AC1–AC5 通过后） | 两 collection 各一份标记文档实测可见性隔离与 409 防护 |

### 2.2 Out of Scope（明确不做）

- **N-05 修复**（chunk_id 纳入 collection + 数据迁移）：04 规格「已知遗留问题」，`tests/e2e/test_qa_offline_gaps.py:240` strict xfail 锁定；改动面与迁移成本独立成篇，另立提案。本规格的定界基准特意选历史表 v2 而非 Chroma，摄取路径不受 N-05 影响；删除路径的联动清理见 §1.2，本规格不改 `_remove_history`。
- **上传目录按 collection 分子目录**（方案 B）：D1 已否决；会改变 `source_path` / doc_id 并需迁移既有上传，且不覆盖 `data/documents/`。
- **修改 PathGuard 契约**（根不存在即报错、fail-closed）：S1.3 已冻结并有测试锁定。AC1 逐条调用既有 `validate_paths([path])`、捕获 `PathGuardError` 即剔除该条，不改 `validate_paths` / `filter_evidence_paths` 任一方法。
- **修改 sidecar 侧配置**（`SIRCHMUNK_*`）：本项目恒向 sidecar 发送经 PathGuard 校验的绝对路径，sidecar 无需改动。
- **agentic 答案文本过滤**：sidecar 的 `file_read` 可读 `paths` 之外的文件（USAGE_GUIDE §9），`agentic_answer` 文本可能包含定界集外内容；本规格只过滤证据与引用文件，答案文本按不可信输入处理（现状口径）。
- **历史表 `source_path` 数据迁移 / 归一化回写**：混合形态由 AC1 / AC5 读时归一，不改存量记录。按非项目根 CWD 摄取的相对记录读时解析不到会被剔除（fail-closed，记 warning），不做修复。
- **CLI 摄取文件的同名覆盖防护**：`data/documents/` 下文件由用户就地管理，409 防护只覆盖 `POST /uploads`。
- **Chat 前端 collection 选择器 UI**：Web UI Chat 前端本就不传 collection（Chat WS 帧已支持 `collection` 字段，`src/api/routes/chat.py:296`）；本规格只让 wiring 支持 collection，Chat UI 另议。
- **Hybrid 主路（dense + BM25）行为变更**：collection 过滤语义现状不动。
- **扩展 agentic 白名单到任意用户目录**（如 Obsidian vault）：归 [OBSIDIAN_RAG_SPEC_backlog.md](../backlog/OBSIDIAN_RAG_SPEC_backlog.md)。

## 3. 技术选型与模块设计

```
collection（可选，来自 MCP 参数 / Chat wiring / Playground 表单 / query.py --collection）
   │
   ▼  AC1：collection_paths 组件（src/libs/agentic_search/，与 PathGuard 同包，不 import sirchmunk）
摄取历史表 v2：status=success AND collection=? → source_path 集（跳过 None 占位记录）
   │  相对路径按 resolve_project_root() 解析为绝对路径（不按 CWD）
   │  逐条 PathGuard.validate_paths([p])：白名单外 / 不存在 / 命中 denied / 符号链接逃逸 / URL → 剔除
   ▼
定界路径集 scoped_paths（realpath 去重、按路径字符串排序；可能为空）
   │  空 → D3：不调用 sidecar，直接返回零证据
   ▼
各入口：
  ├─ MCP agentic_search 工具（AC2）：collection 给定 → paths=scoped_paths（与 paths 同给 → -32602，D5）；
  │     证据先经 filter_evidence_paths 归一，再只保留 ∈ scoped_paths 的项
  ├─ AgenticChatRetriever / SearchService._run_agentic / run_stream（AC3）：透传 collection → 工具 arguments
  └─ Hybrid 第三路 AgenticRouteAdapter（AC4）：
        collection（HybridSearch 从 filters 取，仅存在时才作为关键字传入）给定 → paths=scoped_paths；空集 → 返回 [] 不调用 sidecar
        未给 → paths=allowed_roots 经 PathGuard 校验的绝对路径（修复 paths: None 回退 sidecar CWD）
        证据恒经 filter_evidence_paths；带 collection 时再按 scoped_paths 成员过滤
   ▼
sidecar（HTTP，零改动）

POST /uploads（AC5）：加锁 → 整批按 D4 三重判定预检（任一冲突 → 整批 409，不写任何文件）→ 落盘
```

模块设计要点：

- **AC1 `collection_paths` 组件**：输入 collection 名与（可注入的）历史表访问句柄、PathGuard、项目根；输出有序绝对路径列表。
  - 记录来源：历史表 v2 中 `status = 'success'` 且 `collection` 匹配的记录；`source_path` 为 `None`（v1 迁移占位）的记录跳过。历史表的 `(source_path, collection)` 是「该 collection 应可见文件」的权威（04 规格 M1 纪律；限定见 §1.2）。
  - 路径归一：绝对路径原样；相对路径按 `resolve_project_root()` 解析（与 RA 规格的数据路径锚定一致），**不按 CWD**，不借用 `filter_evidence_paths` 的相对路径解析。
  - 逐条校验：对每条归一后路径单独调用 `PathGuard.validate_paths([path])`，捕获 `PathGuardError` 即剔除该条，从而获得 realpath 归一、白名单根、`denied_substrings`、符号链接逃逸、存在性五项检查，且单条坏记录不使整体失败；URL 形态因不存在于本地文件系统而被剔除。`validate_paths` / `filter_evidence_paths` 均不改。
  - 输出：realpath 去重后按路径字符串升序排列（确定性）；剔除条数大于 0 时记一条 warning（含 collection 与剔除数，不含路径内容）。
  - 规模：本规格不设路径数上限；AC7 记录实测定界集大小与耗时，超大 collection 的分批 / 目录折叠留待实测后另议。
- **AC2 MCP 工具**：schema 增加可选 `collection`（非空、非纯空白字符串，非法 → `-32602`）。
  - 与 `paths` 同时给出 → `-32602`，不调用后端（D5）。
  - 给定 → 先算 `scoped_paths`；为空时按 D3 在 `validate_paths` 之前短路（`validate_paths` 对空列表会抛错），不调用后端，返回零证据结构化结果（`structuredContent.agentic.evidence_files: []`），并在 trace 记一条 `agentic_search` 阶段（`method="scoped_empty"`，details 含 `collection` 与 `scoped_paths: 0`），保证 Query Traces 可解释。
  - 非空 → `paths = scoped_paths`；响应证据与 `source_files` 先经既有 `filter_evidence_paths` 归一与白名单过滤，再只保留 realpath ∈ `scoped_paths` 的项（防御 sidecar 经 `file_read` 返回定界集外、但仍在白名单内的其他 collection 文件）。
  - 未给定 → 行为与基线逐字节一致。
- **AC3 透传**：`AgenticChatRetriever.retrieve` 不再忽略 collection，非空时写入 tool arguments（流式 `stream_tool_factory` 路径共用同一 arguments）；`SearchService._run_agentic` 与 `SearchService.run_stream`（Deep 流式，`search_service.py:207`）均透传；Playground 前端 agentic 模式显示 collection 输入、`buildSearchRequest` 对 fast/deep 保留 collection（HTTP 与 WS 共用；既有 vitest 断言「drops collection outside normal mode」为**有意变更**，随实现更新）。ChatEngine 已把 collection 传给 retriever（含 auto 模式定档后的 retriever），引擎不改。
- **AC4 第三路修复**：
  - 调用兼容：`AgenticRouteAdapter.retrieve` 签名扩展为 `retrieve(query, top_k, trace=..., collection=None)`；`HybridSearch.search` 仅在 `processed.filters` 含非空 `collection` 时才以关键字传入 `collection`，否则调用形态与基线完全相同——既有鸭子类型 fake（如 `tests/unit/test_hybrid_agentic_route.py:121`、`tests/unit/test_agentic_dedup.py:392` 的 `retrieve(query, top_k, trace=None)`）无需修改。否则传入未知关键字会抛 `TypeError`，被第三路 `except` 吞掉并静默降级为两路。
  - 依赖装配：`build_agentic_retriever(settings)` 为适配器装配 PathGuard（按 `allowed_roots` / `denied_substrings`）与历史表句柄（均懒构造）。PathGuard 构造失败（如根不存在）→ 适配器抛错、不调用 sidecar，由 `HybridSearch` 既有异常分支降级为两路（fail-closed，记入 `failures["agentic"]`）。
  - paths：未给 collection → `PathGuard.allowed_roots`（已归一的绝对路径）；给定 → `scoped_paths`，空集返回 `[]` 不调用 sidecar。
  - 证据过滤在 `evidence_to_retrieval_results` 之前完成：恒经 `filter_evidence_paths`（白名单外剔除、相对路径归一）；带 collection 时再按 `scoped_paths` 成员过滤。下游 `dedup_and_expand` 只看到过滤后的证据。
  - 「未触发第三路时主链路与基线逐字节一致」的 S2.3 回归红线不变；`last_response` / `last_elapsed_ms` 语义不变。
- **AC5 同名上传防护**：
  - 判定：目标 `dest = upload_dir / sanitize_filename(name)`；「被引用」= 存在任一 success 记录，其 `source_path` 按 AC1 同一规则归一（相对按项目根、realpath）后等于 `dest` 的 realpath——从而同时识别 RA3 前的相对记录 `data/uploads/<name>` 与 RA3 后的绝对记录；再按 D4 比较新内容与磁盘文件 sha256。
  - 判定依据选历史表（与 AC1 定界同源：409 拦下的正是会被 agentic 定界读取的文件）。注意这与 AR2 删除联动的引用检查**不同源**——AR2 的 `source_referenced` 查 Chroma metadata（`src/ingestion/document_manager.py:393-407`）。两者分工：AR2 决定「删除后源文件能否清理」，AC5 决定「上传能否覆盖」；历史表引用存在而 Chroma 已无记录时（N-05 覆盖），AC5 偏保守地拦截，不会静默覆盖。
  - 批量：`POST /uploads` 支持多文件；先对整批全部文件预检，任一冲突 → 整批 409、**不写入任何文件**；`details.conflicts` 列出 `{name, collections}`。
  - 并发：「预检 + 落盘」在进程内锁内串行执行（沿用 `src/api/routes/ingestion.py` `_submit_lock` 的先例），防止并发上传同名文件同时通过检查；与并发摄取 / 删除的竞态不在本规格范围。
  - 响应：409 经 `ApiError`，`code="upload_name_in_use"`，消息列出占用该文件名的 collection 并指引先删除；路由 `responses` 声明补 409；`UploadResponse` 只增不改；同哈希幂等放行仍返回 201。

选型理由（相对被否掉的方案）：

1. **方案 A 对比 B（按 collection 分子目录）**：B 才能从路径上根治，但改变所有新上传的 `source_path` / doc_id、既有上传需迁移、删除联动与白名单结构都要改，且管不到 `data/documents/` 的 CLI 摄取文件；A 不改任何落盘布局与 doc_id，历史表即权威，覆盖面更全。
2. **方案 A 对比 C（仅文档说明口径差异）**：C 只消除「不知道」，不消除「串数据 + 外发面」；A 的增量只是各入口多一次历史表查询与一次集合过滤。
3. **定界基准选历史表而非 Chroma 反查**：N-05 使 Chroma 记录可能被后摄取的 collection 覆盖，按 Chroma 反查会把文件错判给别的 collection；历史表 v2 的 `(source_path, collection)` 主键在摄取路径上不受 N-05 影响（04 规格 M1 单一权威纪律）；删除路径的联动清理只会让定界集变小（§1.2），不会串入其他 collection 的文件。
4. **空集 fail-closed（D3）而非退化为全量**：静默退化会把「该 collection 无可检索文件」变成「全白名单外发」，既串数据又扩大外发面；显式零证据结果可解释、零 token 消耗。
5. **同名防护放上传时而非摄取时（D4）**：覆盖发生在上传落盘瞬间，摄取时拦截已无法挽回被覆盖的源文件；上传时无目标 collection 上下文，故按「被任一 collection 引用」拦截，合法更新走「删除文档（AR2 联动清源文件）→ 重传」。
6. **逐条 `validate_paths` 而非复用 `filter_evidence_paths`**：后者为调用后过滤设计，绝对路径不查存在、URL 放行、相对路径按 CWD 优先解析；前者的五项检查正是「可发给 sidecar 的路径」的定义，逐条调用即得「剔除而非整体失败」，且不改任何 PathGuard 方法。

## 4. 测试方案

- **离线确定性测试**（零网络，fake 后端 / `httpx.MockTransport` / fake 历史表）：
  - AC1（五组）：
    1. 只取 `status=success` 且 `collection` 匹配的记录；failed 记录、其他 collection 记录、`source_path` 为 `None` 的占位记录均不入选。
    2. 相对 `source_path` 按项目根解析：`monkeypatch.chdir` 到项目根以外的临时目录后，结果与在项目根下一致。
    3. 逐条剔除且互不影响：白名单外、不存在（含绝对路径）、命中 `denied_substrings`、符号链接逃逸、URL 形态各一条，与合格记录混合时只剩合格项。
    4. 无合格记录时返回空列表、不抛错。
    5. realpath 去重（同一文件的相对与绝对两种记录只出现一次）、按路径字符串升序，重复调用结果一致。
  - AC2（六组）：
    1. 带 collection 时发送的 `paths` 恰为定界集。
    2. 空定界集不调用后端，返回零证据结构化结果，trace 记一条 `method="scoped_empty"` 的 `agentic_search` 阶段。
    3. 证据后置过滤：白名单内但不在定界集的证据与 `source_files` 被剔除，定界集内的保留。
    4. `collection` 非法（空串 / 纯空白 / 非字符串）→ `-32602`。
    5. `paths` 与 `collection` 同时给出 → `-32602` 且不调用后端（D5）。
    6. 不传 collection 时既有用例原样通过（行为与基线逐字节一致）。
  - AC3（三组）：
    1. `AgenticChatRetriever` 透传 collection（同步与流式两条路径），未给时 arguments 不含 `collection`。
    2. `SearchService._run_agentic` 与 `SearchService.run_stream` 透传 collection。
    3. 前端 vitest 更新：agentic 模式保留 collection。
  - AC4（六组）：
    1. 未给 collection 时第三路 `paths` 恒非 `None`，且为 `allowed_roots` 的绝对路径。
    2. filters 含 collection → `paths` 为定界集；空定界集 → 返回 `[]` 且不调用后端。
    3. 证据后置过滤：白名单外证据剔除；带 collection 时白名单内但定界集外的证据也剔除。
    4. PathGuard 构造失败 → 不调用后端，`HybridSearch` 降级为两路，`failures` 记录 agentic。
    5. 兼容：filters 不含 collection 时调用不带 `collection` 关键字，既有签名为 `retrieve(query, top_k, trace=None)` 的 fake 不改即通过。
    6. 未触发第三路时主链路（trace details、`last_*` 属性）与基线逐字节一致的回归红线不变。
  - AC5（五组）：
    1. 被引用且内容不同 → 409（`upload_name_in_use`），磁盘文件不变。
    2. 同哈希 → 201 幂等。
    3. 孤儿文件（无历史引用）→ 现状覆盖放行。
    4. 批量上传中一个冲突 → 整批 409，批内所有文件均未写入（含不冲突的新文件）。
    5. 历史记录为 RA3 前相对形式 `data/uploads/<name>` 时同样识别为引用。
- **真实依赖冒烟**：`RAG_AGENTIC_INTEGRATION=1` 门控，需 sidecar 已启动，默认跳过（AC7）：A、B 两 collection 各上传一份含唯一标记词的**不同文件**，带 collection 的 Agentic (fast) 只命中本 collection 标记、不带时两者皆可见（现状口径保留）；同名不同内容跨 collection 上传 409 实证；`query.py --agentic --collection` 触发第三路时证据限于定界集；记录定界集大小与端到端耗时。
- **既有测试语义**（不得破坏）：
  - `tests/unit/test_agentic_path_guard.py`：PathGuard 契约全部既有用例不变。
  - `tests/unit/test_agentic_tool.py::test_default_paths_from_allowed_roots`：不传 `paths`（且不传 collection）时默认用 `allowed_roots`。
  - `tests/unit/test_hybrid_agentic_route.py`、`tests/unit/test_agentic_dedup.py`、`tests/unit/test_query_agentic.py`：既有第三路 fake 与断言不改即通过。
  - `tests/unit/test_upload_source_cleanup.py`：AR2 删除联动三场景不变（409 防护为新增场景，只增不改）；`DeleteDocumentResponse` 只增不改。
  - Hybrid 第三路「未注入 / `enabled=false` / 未触发时主链路与基线逐字节一致」（S2.3 回归红线）。
  - `tests/e2e/test_qa_offline_gaps.py` N-05 strict xfail 保持（本规格不修 N-05）。
  - MCP server stdout 只输出 JSON-RPC；`src/api/app.py` 的 `create_app` 零 IO。
  - 全量 `pytest tests -q` 与 CI 等价禁网命令保持全绿；`python scripts/check_redlines.py` 通过。
- **既有测试语义有意变更点**（随实现同步更新断言，在此登记）：`frontend/lib/search.test.ts`「omits empty top_k and drops collection outside normal mode」——AC3 起 fast/deep 保留 collection，断言改为「agentic 模式透传 collection」。`tests/` 下既有文件不在变更清单内；如实现中确需修改，先在此登记再改（auto-coder 修复模式下 `.factory/hooks/test_guard.py` 会拦截对既有测试文件的编辑）。

## 5. 任务排期与进度

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|----------|------|
| AC0 | 立项与决策终裁 | D1–D4 终裁记入 §1.3；MOC、backlog/README、README 导航与冻结规格引用（14 / 16 号）同步；`test_docs_links.py` 与 `test_spec_structure.py` 通过；v0.2 评审修订补 AGENTS.md 14 号条目的旧台账指针；D5 终裁记入 §1.3 | 已完成（2026-11-21） |
| AC1 | collection 路径集推导组件 | §4 AC1 五组离线测试通过；组件位于 `src/libs/agentic_search/` 且不 import sirchmunk（红线脚本过）；PathGuard 方法零改动 | 已完成（2026-11-21，`CollectionPathResolver`，测试 `tests/unit/test_collection_paths.py` 9/9） |
| AC2 | MCP `agentic_search` collection 参数 | §4 AC2 六组离线测试通过；不传 collection 时行为与基线逐字节一致 | 未开始 |
| AC3 | Chat / Playground collection 透传 | §4 AC3 三组测试通过（含前端 vitest 更新）；ChatEngine 零改动 | 未开始 |
| AC4 | Hybrid 第三路白名单修复 + collection 定界 | §4 AC4 六组离线测试通过；既有第三路测试文件零改动；S2.3 未触发回归红线不变 | 未开始 |
| AC5 | 同名上传 409 防护 | §4 AC5 五组测试通过；AR2 删除联动既有用例不变 | 未开始 |
| AC6 | 文档同步 | §7 回写清单完成；`test_docs_links.py` 通过 | 未开始 |
| AC7 | 门控真实冒烟 | `RAG_AGENTIC_INTEGRATION=1` 下完成 §4 冒烟场景，结果（含定界集大小与耗时）记入本表 | 未开始 |

> 进度备注：交付后在各任务下回填状态与关键实测数据；全部收口后本规格移入 `docs/frozen/` 并更新 MOC.md。

## 6. 验收清单

- [ ] A、B 两 collection 各有独有文件时，带 collection 的 agentic 检索（MCP / Chat wiring / Playground 含 Deep 流式）只命中本 collection 的文件；不带 collection 时口径与现状一致（离线 + AC7 实测）
- [ ] 定界集与启动目录无关：相对 `source_path` 按项目根解析，从非项目根目录启动时结果不变
- [ ] collection 给定但定界集为空时返回零证据结构化结果，不调用 sidecar、不产生 token 消耗，trace 可见（D3）
- [ ] 白名单内但定界集外的证据在 MCP 工具与第三路均被后置过滤剔除
- [ ] MCP 工具 `paths` 与 `collection` 同时给出时返回 `-32602` 且不调用后端（D5）
- [ ] Hybrid 第三路触发时发送的 `paths` 恒为白名单内绝对路径，不再回退 sidecar CWD；既有第三路 fake 不改即通过（AC4 离线断言）
- [ ] 同名不同内容文件跨 collection 上传被 409 拦截且磁盘文件不变；批量上传整批拒绝；同哈希重传幂等；孤儿文件覆盖放行（D4）
- [ ] PathGuard 契约、AR2 删除联动、S2.3 未触发回归红线、N-05 xfail 全部保持
- [ ] `pytest tests -q`、CI 等价禁网命令、`python scripts/check_redlines.py` 全绿
- [ ] 回写完成（§7）

## 7. 实现合入后的回写清单

- `docs/reference/USAGE_GUIDE.md`：
  - §6 `agentic_search` 参数表新增 `collection` 行（可选；定界语义、空集零证据、与 `paths` 互斥（D5））
  - §4.4 主项目配置的隐私提示与删除联动段补 collection 定界口径与 409 行为
  - §2.7 Playground collection 输入改为全模式
  - §9 安全边界补「collection 定界不是安全边界：只收窄检索起点与引用，答案文本仍按不可信处理」
  - §10.4 Chat 使用补 collection 口径（wiring 支持、前端暂不提供选择器）；§10.5 路由表补 `POST /uploads` 409（`upload_name_in_use`）
- `README.md`：MCP 工具节与「白名单与隐私（AR 系列）」段补 collection 隔离口径
- `docs/MOC.md` 与根目录 README 导航表：本规格状态
- 本规格收口冻结时：按 docs-governance §2.4 分配编号；冻结后本规格成为改动 agentic collection 语义前的必读契约，届时新增 AGENTS.md 条目（改 AGENTS.md 会触发 `agent-evals.yml` 付费 evals）；N-05 修复若要推进，建派生台账另立提案

## 8. 隐私注记

- **暴露面**：不新增路由。MCP `agentic_search` 工具新增可选参数；`POST /uploads` 新增 409 拒绝场景（信息仅为占用文件名的 collection 名）。
- **外发边界**：agentic 检索把命中文件内容经 sidecar 发给其配置的 LLM（第三方服务）。本规格使带 collection 的检索**起点**从「全白名单」收窄到「该 collection 定界集」，返回的证据与引用文件也限定在定界集内。但这不是硬边界：sidecar 的 `file_read` 可按 LLM 请求读取 `paths` 之外的文件（DEEP 模式尤甚，见 USAGE_GUIDE §9），被读内容可能外发给 LLM 并出现在 agentic 答案文本中；答案文本不经路径过滤。不带 collection 时外发面与现状一致（AR 规格 §8 口径不变）。
- **日志**：AC1 剔除记录只记 collection 名与剔除条数，不记路径；409 消息只含文件名与 collection 名。
- **开关策略**：`agentic_search.enabled: false` 时零加载、不外发（现状不变）；`allowed_roots` 移除 `./data/uploads` 后上传文件不进定界集（AC1 经 PathGuard 校验自然生效）。
