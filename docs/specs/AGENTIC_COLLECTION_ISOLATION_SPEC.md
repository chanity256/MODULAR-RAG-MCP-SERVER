<!-- 规格定位：让 agentic 检索（MCP agentic_search 工具、Chat / Playground 的 Agentic fast/deep、Hybrid 第三路）按 collection 隔离可见范围，并顺带修复第三路绕开调用方白名单的缺口与同名上传跨 collection 覆盖。基线：S / SX / WC / AR / RA 均已收口冻结。与既有规格的关系：调用方定界扩展，不改 PathGuard 契约（S1.3）、不改 sidecar、不动 Hybrid 主路。 -->

# Agentic 检索按 collection 隔离规格 (AGENTIC_COLLECTION_ISOLATION_SPEC)

> 版本：0.1 — 由 AR 遗留台账候选 #2 立项为可执行规格，采用定界方案 A（历史表定界 paths）
>
> 起源与决策依据：由 `backlog/agentic-roots-followups_backlog.md` 单文件晋升（该台账候选 #1 已先行拆分移交 RA 规格，候选 #2 为本规格全部承接内容，台账原文见 git 历史）；台账源自 [14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md](../frozen/14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md) §2.2 Out of Scope。依据 [03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md](../frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md) S1.3（PathGuard 双层防线）与 S2.3（第三路）、[04_INGESTION_ROBUSTNESS_SPEC_frozen.md](../frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md) M1（历史表 v2 `(source_path, collection)` 主键）
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
| 要解决什么问题？ | agentic 检索按文件系统路径工作、不认识 collection：用户指定 collection 时，Hybrid 主路按 collection 过滤，而 MCP `agentic_search` 工具、Chat / Playground 的 Agentic (fast/deep)、Hybrid 第三路（S2.3）都把全部白名单根（含其他 collection 的上传文件与 `data/documents/`）纳入检索与 LLM 外发范围；同名上传文件跨 collection 互相覆盖使磁盘内容进一步与索引脱节 |
| 定位是什么？ | 既有链路的调用方定界扩展：collection → 摄取历史 v2 success 记录 → 绝对路径集 ∩ PathGuard 白名单 → 作为 `paths` 发给 sidecar + 证据后置过滤；不改目录布局、不改 PathGuard 契约、不改 sidecar |
| 实施范围（已决策） | AC1–AC6 为承诺任务；AC7 为门控任务（需 sidecar 与 `RAG_AGENTIC_INTEGRATION=1`） |
| 主要代价 | 带 collection 的 agentic 检索多一次历史表查询；空定界集直接返回零证据、不调用 sidecar；同名上传防护使「覆盖式更新」改为「先删除文档再重传」（见 D4）；第三路修复后恒发显式 `paths`（行为收紧，不再回退 sidecar CWD） |

### 1.2 背景与现状

sidecar 按文件系统路径检索，不认识 collection（03 规格）；上传目录扁平（`save_uploads` 按 `sanitize_filename` 落盘 `data/uploads/`，`src/observability/dashboard/services/ingestion_service.py:103-108`），`data/documents/` 的 CLI 摄取文件同样跨 collection 可见。证据（基线 `a33ab97`）：

- **MCP 工具无 collection 概念**：`src/mcp_server/tools/agentic_search.py:75` 的 schema 只有 `query` / `mode` / `paths` / `top_k`；`:225-227` 不传 `paths` 时默认搜全部 `allowed_roots`。
- **Chat 有意丢弃 collection**：`src/observability/dashboard/services/chat_retrievers.py:108` 注释「agentic 按路径白名单检索，没有 collection 概念，该参数在此被有意忽略」；ChatEngine 传入的 collection 到此为止。
- **Playground 同样丢弃**：`src/observability/dashboard/services/search_service.py:233-237` `_run_agentic` 不传 collection；前端 collection 输入仅 Normal 模式显示（USAGE_GUIDE §2.7），`frontend/lib/search.ts` 对 fast/deep 丢弃 collection。
- **Hybrid 第三路更严重（绕开调用方白名单）**：`src/libs/agentic_search/adapter.py:54` 构造 `AgenticSearchRequest(query=query)` 不带 `paths` → `src/libs/agentic_search/http_backend.py:234` 发送 `paths: None` → sidecar 回退 `SIRCHMUNK_SEARCH_PATHS` / 自身 CWD（03 规格 §4.6 路径解析链），**完全绕开本项目应执行的调用方白名单**；证据也不经 `filter_evidence_paths`（PathGuard 只在 `agentic_dedup` 上下文扩展时使用）。且 `converter.py:263-270` 产出的 metadata 无 `collection` 键，`_apply_metadata_filters` 缺失字段宽松包含（`hybrid_search.py:366-367`）→ collection 过滤对第三路结果无效。第三路当前仅 `scripts/query.py --agentic` 与 `scripts/evaluate.py` 接入、默认 `trigger: tool_only` 不触发，但 `always` / `fallback` 配置下缺口即暴露。
- **Hybrid 主路按 collection 过滤但存在既有缺陷 N-05**：`chunk_id = {doc_hash}_{index}_{content_hash}` 不含 collection，所有 collection 共用一个物理 Chroma collection；同一内容摄取到第二个 collection 会覆盖第一个的 Chroma 记录（04 规格「已知遗留问题」，`tests/e2e/test_qa_offline_gaps.py:240` strict xfail 锁定）。本规格不修（见 2.2），但它是设计定界基准的依据：collection → 文件集的权威来源是**摄取历史表 v2**（主键 `(source_path, collection)`，04 规格 M1），不是 Chroma 反查。
- **同名上传跨 collection 覆盖**（AR 规格 §2.2 登记的既有行为）：同名文件先后上传到 A、B 两个 collection 后，磁盘文件已是 B 的版本；从 A 删除时文件因仍被 B 引用而保留（AR2 删除联动），A 的索引与磁盘文件早已不一致；agentic 按路径检索命中的是 B 的内容。
- **可见范围口径差异在文档中未说明**：USAGE_GUIDE §2.7 与 README 仅写「collection 输入仅在 Normal 模式显示」，未解释 agentic 各入口的可见范围口径。

提案三问的答案：

1. **已观察到的问题**：见上。AR4 冒烟只覆盖单 collection，跨 collection 为设计分析结论（尚无跨 collection 实测，AC7 补齐）。
2. **更简单的方案**：对比见 §3 选型理由。方案 A（历史表定界 paths）改动最小，且同时覆盖 `data/uploads/` 与 `data/documents/`、不改任何既有 doc_id。
3. **如何确认有效**：见 §4 与 §6，核心是「同一文件在两个 collection 中，带 collection 的 agentic 检索只见本 collection；不带时口径与现状一致」。

### 1.3 决策依据与终裁

| 编号 | 决策 | 终裁（2026-11-21，用户拍板） |
|------|------|------------------------------|
| D1 | collection 隔离方案 | **方案 A 历史表定界 paths**：collection → 摄取历史 v2 success 记录的 `source_path` 集 → 解析为绝对路径 ∩ PathGuard 白名单 → 作为 `paths` 发送 + 证据后置过滤。否决方案 B（上传目录按 collection 分子目录）：改 `source_path` / doc_id、需迁移既有上传、且不覆盖 `data/documents/`；否决方案 C（仅文档说明口径差异）：可见范围与外发面问题仍在 |
| D2 | 顺带范围 | 一并承接：① Hybrid 第三路白名单修复（恒发显式 `paths` + 证据后置过滤）；② 同名上传 409 防护；③ MCP `agentic_search` 增加可选 `collection` 参数。**不承接** N-05（chunk_id 含 collection + 数据迁移），留作 QA xfail 与 04 规格已知遗留，另立提案 |
| D3 | 空定界集语义 | collection 给定但定界集为空（该 collection 无 success 记录，或记录全部落在白名单外）时，**不调用 sidecar**，直接返回零证据结构化结果（`source_files: []`）；fail-closed，绝不静默退化为全量 `allowed_roots`（「该集合无文件」不能变成「全库外发」） |
| D4 | 同名上传 409 语义 | `POST /uploads` 落盘前检查：目标路径已存在**且**被任一 collection 的摄取历史 success 记录引用（`source_path` 匹配）**且**新内容 sha256 ≠ 磁盘现有文件 sha256 → 409（`upload_name_in_use`），消息指引先删除对应文档（删除经 AR2 联动清理源文件后即可重传）；同哈希 → 幂等放行（重写相同字节）；无历史引用（孤儿文件）→ 现状覆盖放行 |

## 2. 核心目标与范围

### 2.1 In Scope

| 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|--------|-----------|
| `libs/agentic_search` | collection 路径集推导组件 | 承诺（AC1） | collection → 历史表 success 记录 → 绝对路径集 ∩ PathGuard 白名单（逐路径过滤语义） |
| MCP | `agentic_search` collection 参数 | 承诺（AC2） | 可选 `collection`：给定 → 定界集检索 + 证据后置过滤；空集按 D3 返回零证据；不给 → 现状 |
| Chat / Playground | collection 透传 | 承诺（AC3） | `AgenticChatRetriever` 与 `SearchService._run_agentic` 透传 collection；Playground 前端 agentic 模式开放 collection 输入 |
| `core/query_engine` | Hybrid 第三路白名单修复 + collection 定界 | 承诺（AC4） | 第三路恒发显式 `paths`（默认白名单根；filters 含 collection 时发定界集）+ 证据经 `filter_evidence_paths` 后置过滤 |
| API | 同名上传 409 防护 | 承诺（AC5） | 按 D4 语义在 `POST /uploads` 拦截跨 collection 静默覆盖 |
| 文档 | 使用说明同步 | 承诺（AC6） | USAGE_GUIDE 与 README 写明 collection 口径、409 行为与第三路 paths 语义 |
| 冒烟 | 真实 sidecar 跨 collection 验证 | 门控（AC7，AC1–AC5 通过后） | 两 collection 标记文档实测可见性隔离与 409 防护 |

### 2.2 Out of Scope（明确不做）

- **N-05 修复**（chunk_id 纳入 collection + 数据迁移）：04 规格「已知遗留问题」，`tests/e2e/test_qa_offline_gaps.py:240` strict xfail 锁定；改动面与迁移成本独立成篇，另立提案。本规格的定界基准特意选历史表 v2 而非 Chroma，故不受 N-05 影响。
- **上传目录按 collection 分子目录**（方案 B）：D1 已否决；会改变 `source_path` / doc_id 并需迁移既有上传，且不覆盖 `data/documents/`。
- **修改 PathGuard 契约**（根不存在即报错、fail-closed）：S1.3 已冻结并有测试锁定。AC1 组件复用的是 `filter_evidence_paths` 的逐路径过滤语义，不改 `validate_paths`。
- **修改 sidecar 侧配置**（`SIRCHMUNK_*`）：本项目恒向 sidecar 发送经 PathGuard 校验的绝对路径，sidecar 无需改动。
- **Chat 前端 collection 选择器 UI**：Web UI Chat 本就不传 collection（Hybrid 路同样不传）；本规格只让 wiring 支持 collection，Chat UI 另议。
- **Hybrid 主路（dense + BM25）行为变更**：collection 过滤语义现状不动。
- **扩展 agentic 白名单到任意用户目录**（如 Obsidian vault）：归 [OBSIDIAN_RAG_SPEC_backlog.md](../backlog/OBSIDIAN_RAG_SPEC_backlog.md)。

## 3. 技术选型与模块设计

```
collection（可选，来自 MCP 参数 / Chat wiring / Playground 表单 / query.py --collection）
   │
   ▼  AC1：collection_paths 组件（src/libs/agentic_search/，与 PathGuard 同包，不 import sirchmunk）
摄取历史表 v2：status=success AND collection=? → source_path 集
   │  resolve 绝对路径 → 逐路径过 PathGuard（filter_evidence_paths 语义：白名单外/不存在剔除，不整体抛错）
   ▼
定界路径集 scoped_paths（可能为空）
   │  空 → D3：不调用 sidecar，直接返回零证据
   ▼
各入口：
  ├─ MCP agentic_search 工具（AC2）：collection 给定 → paths=scoped_paths；证据再按 scoped 集过滤
  ├─ AgenticChatRetriever / SearchService._run_agentic（AC3）：透传 collection → 工具 arguments
  └─ Hybrid 第三路 AgenticRouteAdapter（AC4）：
        collection（HybridSearch 从 filters 取）给定 → paths=scoped_paths；空集 → 返回 [] 不调用 sidecar
        未给 → paths=allowed_roots 经 PathGuard 校验的绝对路径（修复 paths: None 回退 sidecar CWD）
        证据恒经 filter_evidence_paths 后置过滤
   ▼
sidecar（HTTP，零改动）

POST /uploads（AC5）：落盘前按 D4 三重判定（被引用 + 内容哈希不同 → 409；同哈希幂等；孤儿放行）
```

模块设计要点：

- **AC1 `collection_paths` 组件**：输入 collection 名与（可注入的）历史表访问句柄、PathGuard；输出有序绝对路径列表。历史表 v2 的 `(source_path, collection)` success 记录是「该 collection 应可见文件」的唯一权威（04 规格 M1 纪律；N-05 只影响 Chroma 记录，不影响历史表）。路径过滤复用 `filter_evidence_paths` 语义：逐路径剔除白名单外 / 不存在 / 命中 `denied_substrings` 的项，不因单条坏记录整体失败；`validate_paths` 的整体抛错契约不动。
- **AC2 MCP 工具**：schema 增加可选 `collection`（非空字符串校验，非法 → `-32602`）。给定 → `paths = scoped_paths`（D3 空集不调用后端，直接返回零证据结构化结果）；响应证据再按 scoped 集过滤（防御 sidecar 返回 `paths` 外证据）。未给定 → 行为与基线逐字节一致。
- **AC3 透传**：`AgenticChatRetriever.retrieve` 不再忽略 collection，写入 tool arguments；`SearchService._run_agentic` 透传；Playground 前端 agentic 模式显示 collection 输入、`buildSearchRequest` 对 fast/deep 保留 collection（既有 vitest 断言「drops collection outside normal mode」为**有意变更**，随实现更新）。ChatEngine 已把 collection 传给 retriever，引擎不改。
- **AC4 第三路修复**：`AgenticRouteAdapter.retrieve` 鸭子类型扩展为 `retrieve(query, top_k, trace=..., collection=None)`；内部按上表定 paths，证据恒经 `filter_evidence_paths` 后置过滤。`HybridSearch.search` 从 `processed.filters` 取 `collection` 传给第三路（filters 无 collection → 现状语义）。「未触发第三路时主链路与基线逐字节一致」的 S2.3 回归红线不变。
- **AC5 同名上传防护**：检查在 `save_uploads` 落盘前执行，判定依据为历史表 success 记录（与 AR2 删除联动的引用检查同源）；409 经 `ApiError`，`code="upload_name_in_use"`，消息列出占用该文件名的 collection 并指引先删除。`POST /uploads` 响应模型只增不改。

选型理由（相对被否掉的方案）：

1. **方案 A 对比 B（按 collection 分子目录）**：B 才能从路径上根治，但改变所有新上传的 `source_path` / doc_id、既有上传需迁移、删除联动与白名单结构都要改，且管不到 `data/documents/` 的 CLI 摄取文件；A 不改任何落盘布局与 doc_id，历史表即权威，覆盖面更全。
2. **方案 A 对比 C（仅文档说明口径差异）**：C 只消除「不知道」，不消除「串数据 + 外发面」；A 的增量只是各入口多一次历史表查询与一次集合过滤。
3. **定界基准选历史表而非 Chroma 反查**：N-05 使 Chroma 记录可能被后摄取的 collection 覆盖，按 Chroma 反查会把文件错判给别的 collection；历史表 v2 的 `(source_path, collection)` 主键不受 N-05 影响（04 规格 M1 单一权威纪律）。
4. **空集 fail-closed（D3）而非退化为全量**：静默退化会把「该 collection 无可检索文件」变成「全白名单外发」，既串数据又扩大外发面；显式零证据结果可解释、零 token 消耗。
5. **同名防护放上传时而非摄取时（D4）**：覆盖发生在上传落盘瞬间，摄取时拦截已无法挽回被覆盖的源文件；上传时无目标 collection 上下文，故按「被任一 collection 引用」拦截，合法更新走「删除文档（AR2 联动清源文件）→ 重传」。

## 4. 测试方案

- **离线确定性测试**（零网络，fake 后端 / `httpx.MockTransport` / fake 历史表）：
  - AC1：fake 历史表 + fake PathGuard——success 记录入选、failed/其他 collection 记录剔除、白名单外与不存在的路径逐条剔除、空集原样返回、结果有序确定。
  - AC2：fake 后端——带 collection 时发送的 `paths` 恰为定界集；空定界集不调用后端、返回零证据结构化结果；证据后置过滤剔除定界集外项；`collection` 非法（空串 / 非字符串）→ `-32602`；不传 collection 时行为与基线逐字节一致。
  - AC3：fake tool 断言 `AgenticChatRetriever` 与 `SearchService._run_agentic` 透传 collection；前端 vitest 更新（agentic 模式保留 collection）。
  - AC4：fake 后端断言第三路 `paths` 恒非 `None` 且为白名单内绝对路径；filters 含 collection → 定界集；空定界集 → 返回 `[]` 且不调用后端；证据后置过滤生效；未触发第三路时主链路（trace details、`last_*` 属性）与基线逐字节一致的回归红线不变。
  - AC5：`POST /uploads` 三场景——被引用且内容不同 → 409 且磁盘文件不变；同哈希 → 201 幂等；孤儿文件（无历史引用）→ 现状覆盖放行。
- **真实依赖冒烟**：`RAG_AGENTIC_INTEGRATION=1` 门控，需 sidecar 已启动，默认跳过（AC7）：两 collection 各传一份含唯一标记词的文档，带 collection 的 Agentic (fast) 只命中本 collection 标记、不带时两者皆可见（现状口径保留）；同名跨 collection 上传 409 实证；`query.py --agentic --collection` 触发第三路时证据限于定界集。
- **既有测试语义**（不得破坏）：
  - `tests/unit/test_agentic_path_guard.py`：PathGuard 契约全部既有用例不变。
  - `tests/unit/test_agentic_tool.py::test_default_paths_from_allowed_roots`：不传 `paths`（且不传 collection）时默认用 `allowed_roots`。
  - `tests/unit/test_upload_source_cleanup.py`：AR2 删除联动三场景不变（409 防护为新增场景，只增不改）；`DeleteDocumentResponse` 只增不改。
  - Hybrid 第三路「未注入 / `enabled=false` / 未触发时主链路与基线逐字节一致」（S2.3 回归红线）。
  - `tests/e2e/test_qa_offline_gaps.py` N-05 strict xfail 保持（本规格不修 N-05）。
  - MCP server stdout 只输出 JSON-RPC；`src/api/app.py` 的 `create_app` 零 IO。
  - 全量 `pytest tests -q` 与 CI 等价禁网命令保持全绿；`python scripts/check_redlines.py` 通过。
- **既有测试语义有意变更点**（随实现同步更新断言，在此登记）：`frontend/lib/search.test.ts`「omits empty top_k and drops collection outside normal mode」——AC3 起 fast/deep 保留 collection，断言改为「agentic 模式透传 collection」。

## 5. 任务排期与进度

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|----------|------|
| AC0 | 立项与决策终裁 | D1–D4 终裁记入 §1.3；MOC、backlog/README、README 导航与冻结规格引用（14 / 16 号）同步；`test_docs_links.py` 与 `test_spec_structure.py` 通过 | 已完成（2026-11-21） |
| AC1 | collection 路径集推导组件 | §4 AC1 五组离线测试通过；组件位于 `src/libs/agentic_search/` 且不 import sirchmunk（红线脚本过） | 未开始 |
| AC2 | MCP `agentic_search` collection 参数 | §4 AC2 六组离线测试通过；不传 collection 时行为与基线逐字节一致 | 未开始 |
| AC3 | Chat / Playground collection 透传 | §4 AC3 测试通过（含前端 vitest 更新）；ChatEngine 零改动 | 未开始 |
| AC4 | Hybrid 第三路白名单修复 + collection 定界 | §4 AC4 五组离线测试通过；S2.3 未触发回归红线不变 | 未开始 |
| AC5 | 同名上传 409 防护 | §4 AC5 三场景通过；AR2 删除联动既有用例不变 | 未开始 |
| AC6 | 文档同步 | USAGE_GUIDE 与 README 写明 collection 口径、409 行为、第三路 paths 语义；`test_docs_links.py` 通过 | 未开始 |
| AC7 | 门控真实冒烟 | `RAG_AGENTIC_INTEGRATION=1` 下完成 §4 冒烟三场景，结果记入本表 | 未开始 |

> 进度备注：交付后在各任务下回填状态与关键实测数据；全部收口后本规格移入 `docs/frozen/` 并更新 MOC.md。

## 6. 验收清单

- [ ] 同一上传文件摄取到两个 collection 后，带 collection 的 agentic 检索（MCP / Chat wiring / Playground）只命中本 collection 的文件；不带 collection 时口径与现状一致（离线 + AC7 实测）
- [ ] collection 给定但定界集为空时返回零证据结构化结果，不调用 sidecar、不产生 token 消耗（D3）
- [ ] Hybrid 第三路触发时发送的 `paths` 恒为白名单内绝对路径，不再回退 sidecar CWD；证据经后置过滤（AC4 离线断言）
- [ ] 同名文件跨 collection 上传被 409 拦截且磁盘文件不变；同哈希重传幂等；孤儿文件覆盖放行（D4）
- [ ] PathGuard 契约、AR2 删除联动、S2.3 未触发回归红线、N-05 xfail 全部保持
- [ ] `pytest tests -q`、CI 等价禁网命令、`python scripts/check_redlines.py` 全绿
- [ ] 回写完成（§7：USAGE_GUIDE、README、MOC、README 导航）

## 7. 实现合入后的回写清单

- `docs/reference/USAGE_GUIDE.md`：§4.4 agentic 节补 collection 口径与定界语义；§2.7 Playground collection 输入改为全模式；§10.5 路由表补 `POST /uploads` 409 与 `agentic_search` collection 参数；第 10 章 Chat collection 说明
- `README.md`：MCP 工具节与「白名单与隐私（AR 系列）」段补 collection 隔离口径
- `docs/MOC.md` 与根目录 README 导航表：本规格状态
- 本规格收口冻结时：按 docs-governance §2.4 分配编号；冻结后本规格成为改动 agentic collection 语义前的必读契约，届时新增 AGENTS.md 条目；N-05 修复若要推进，建派生台账另立提案

## 8. 隐私注记

- **暴露面**：不新增路由。MCP `agentic_search` 工具新增可选参数；`POST /uploads` 新增 409 拒绝场景（信息仅为占用文件名的 collection 名）。
- **外发边界**：agentic 检索把命中文件内容经 sidecar 发给其配置的 LLM（第三方服务）。本规格使带 collection 的检索外发范围从「全白名单」收窄到「该 collection 定界集」；不带 collection 时外发面与现状一致（AR 规格 §8 口径不变）。
- **开关策略**：`agentic_search.enabled: false` 时零加载、不外发（现状不变）；`allowed_roots` 移除 `./data/uploads` 后上传文件不进定界集（AC1 经 PathGuard 过滤自然生效）。
