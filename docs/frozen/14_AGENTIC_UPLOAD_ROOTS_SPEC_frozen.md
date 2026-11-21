<!-- 规格定位：让 agentic 检索覆盖 Web UI 上传目录，使 Hybrid / Agentic (fast) / Agentic (deep) 三种模式对同一批上传文档结果一致。基线：S / SX / WC / KE 均已收口冻结。与既有规格的关系：既有链路的配置与入口扩展，不改 PathGuard 契约（S1.3），不改 sidecar。 -->

# Agentic 检索覆盖上传目录规格 (AGENTIC_UPLOAD_ROOTS_SPEC)

> **📌 冻结记录**
>
> - 冻结时间：2026-11-18
> - 完成概要：AR0–AR4 全部收口——`allowed_roots` 默认追加 `./data/uploads`（D2：默认加入 + 文档外发提示）；进程入口预建 `data/` 下白名单根（D1-a：`ensure_local_roots`，`start_api.py` / `mcp_server.server` / `query.py --agentic` 三入口接入，PathGuard 契约不变）；删除文档联动清理上传源文件（`source_file_removed` 响应字段，三重门禁防误删）；USAGE_GUIDE / README 同步隐私与启动提示；AR4 真实 sidecar 门控冒烟通过（上传 → Hybrid / Agentic (fast) 双模式命中 → 删除后 `source_file_removed: true` 且双模式不再命中）。相关离线测试全量 2803 passed / 11 skipped，红线 0 违规。
> - 遗留：原派生台账 `agentic-roots-followups_backlog.md` 候选 #1 已拆分移交 [16_ROOT_ANCHOR_SPEC_frozen.md](16_ROOT_ANCHOR_SPEC_frozen.md)（已收口）；候选 #2 已单文件晋升为 [18_AGENTIC_COLLECTION_ISOLATION_SPEC_frozen.md](18_AGENTIC_COLLECTION_ISOLATION_SPEC_frozen.md)（AC 系列，已收口冻结，台账原文见 git 历史）

> 版本：0.1 — 由 backlog 提案立项为可执行规格，采用提案方案 A
>
> 起源与决策依据：由 backlog/agentic-upload-roots_backlog.md 单文件晋升，原文见 git 历史；提案起源为会话内配置边界核查（2026-11-18，非文件来源）。依据 [03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md](../frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md) S1.3（PathGuard 双层防线）、[06_WEBUI_CHAT_SPEC_frozen.md](../frozen/06_WEBUI_CHAT_SPEC_frozen.md) D4（Chat agentic 模式复用 `AgenticSearchTool`）
>
> 基线：`c28b50f`（main），S / SX / WC / KE 系列均已收口冻结；本仓库 `config/settings.yaml` 中 `agentic_search.enabled: true`
>
> 日期：2026-11-18

红线不变：主 venv 永不 `import sirchmunk`；白名单仍由本项目调用方执行，PathGuard fail-closed 语义不放宽。

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
| 要解决什么问题？ | Web UI 上传的文件能进 RAG 索引，但 agentic 检索搜不到；同一份文档 Hybrid 模式能答、Agentic 模式答不出 |
| 定位是什么？ | 既有链路的配置与入口扩展：`allowed_roots` 追加 `./data/uploads`，进程入口保证该目录存在，删除文档时同步清理上传源文件 |
| 实施范围（已决策） | AR1–AR3 为承诺任务；AR4 为门控任务（需 sidecar 与 `RAG_AGENTIC_INTEGRATION=1`） |
| 主要代价 | 上传文件进入 agentic 外发范围（见 §8）；三个进程入口各多一次建目录调用；删除接口多一步文件操作 |

### 1.2 背景与现状

证据（基线 `c28b50f`）：

- `config/settings.yaml:104-105`：`agentic_search.allowed_roots` 只有 `./data/documents`。
- `src/observability/dashboard/services/ingestion_service.py:36`：上传落盘目录 `DEFAULT_UPLOAD_DIR = Path("data/uploads")`；`src/api/deps.py:128` 构造 `IngestionService` 时不传 `upload_dir`，即恒为该目录。
- `src/mcp_server/tools/agentic_search.py:225-233`：不传 `paths` 时默认只搜 `allowed_roots`；显式传 `data/uploads/...` 被 PathGuard 拒绝，返回 `-32602`。
- `src/libs/agentic_search/path_guard.py:102-130`：调用后过滤剔除白名单外的证据。

只追加配置会引入两个缺陷，本规格一并处理：

1. **根目录不存在即整体失败**：`PathGuard.__init__` 对不存在的根抛 `allowed root does not exist`（`path_guard.py:56-58`，由 `tests/unit/test_agentic_path_guard.py:34` 锁定）。`data/uploads/` 只在第一次上传时由 `save_uploads` 创建；未上传过的环境里整个 agentic 工具不可用。
2. **删除后源文件仍在**：`DELETE /documents/{id}`（`src/api/routes/ingestion.py:164-182`）经 `DocumentManager.delete_document` 只清 Chroma / BM25 / 图片 / 摄取历史四个存储，不删上传源文件。上传目录进白名单后，Web UI 上已删除的文档仍会被 agentic 检索命中。

### 1.3 决策依据与终裁

提案三问的答案：

1. **已观察到的问题**：见 1.2。
2. **更简单的方案**：对比见 §3 选型理由。方案 A（追加白名单根）改动最小，且不改 PathGuard 契约。
3. **如何确认有效**：见 §4 与 §6，核心是「上传 → 三模式均可答 → 删除后三模式均不再命中」。

| 编号 | 决策 | 终裁（2026-11-18，用户拍板） |
|------|------|------------------------------|
| D1 | 上传目录不存在时如何避免整体失败 | **D1-a 入口建目录**：进程入口对位于项目 `data/` 下的白名单根执行 `mkdir(parents=True, exist_ok=True)`；PathGuard 与配置模型不改。否决 D1-b（PathGuard 新增 `optional_roots` 参数与配置项）：多一个配置概念 |
| D2 | `settings.yaml` 是否默认加入 `./data/uploads` | **默认加入 + 文档提示**：USAGE_GUIDE 与 README 写明上传文件可能被 agentic 模式发给外部 LLM |

## 2. 核心目标与范围

### 2.1 In Scope

| 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|--------|-----------|
| 配置 | 白名单追加上传目录 | 承诺（AR1） | `config/settings.yaml` 的 `allowed_roots` 加入 `./data/uploads` |
| 入口 | 白名单根目录预建 | 承诺（AR1） | `scripts/start_api.py`、`src/mcp_server/server.py`、`scripts/query.py --agentic` 启动时预建 `data/` 下的白名单根 |
| 删除 | 上传源文件同步删除 | 承诺（AR2） | 删除文档时，若源文件在上传目录内且不再被任何 collection 引用，同时删除该文件 |
| 文档 | 使用说明与隐私提示 | 承诺（AR3） | USAGE_GUIDE、README 5.1 的 `allowed_roots` 示例与隐私提示同步 |
| 冒烟 | 真实 sidecar 端到端验证 | 门控（AR4，AR1–AR3 通过后） | 真实 sidecar 下上传 → 三模式可答 → 删除后不再命中 |

### 2.2 Out of Scope（明确不做）

- **修改 PathGuard 契约**（根不存在即报错、fail-closed）：S1.3 已冻结并有测试锁定；D1-b 已否决。
- **相对 `allowed_roots` 改为按项目根解析**：现有根与新根都按进程 CWD 解析，从仓库根以外启动仍会失败。根治会影响所有现有根和 sidecar 路径约定，另立提案；本规格只要求预建逻辑与 PathGuard 用同一解析基准，并在文档中写明须从仓库根启动。
- **agentic 检索按 collection 隔离**：sidecar 按文件系统路径检索，不认识 collection；上传目录扁平，不同 collection 的上传文件在 agentic 模式下互相可见。需要先改上传目录布局，另立提案。
- **上传同名文件跨 collection 互相覆盖**（`save_uploads` 按文件名落盘）：既有行为，与本规格无关。
- **把上传目录移进 `data/documents/`**（提案方案 B）：`scripts/ingest.py --path data/documents/` 会把其他 collection 的上传文件再摄取进 `default`，且已有文档的 `source_path` 全部变化。
- **修改 sidecar 侧配置**（`SIRCHMUNK_*`）：本项目恒向 sidecar 发送经 PathGuard 校验的绝对路径（`src/libs/agentic_search/http_backend.py:232-234`），sidecar 无需改动。
- **扩展到任意用户目录**（如 Obsidian vault）：属于 [OBSIDIAN_RAG_SPEC_backlog.md](../backlog/OBSIDIAN_RAG_SPEC_backlog.md) 的范围。

## 3. 技术选型与模块设计

```
进程入口（start_api.py / mcp_server.server / query.py --agentic）
   │  load_settings()
   │  ensure_local_roots(agentic_search)   ← AR1 新增：仅预建 <CWD>/data/ 下的白名单根（RA2 起预建基准为项目根，见 7.1）
   ▼
AgenticSearchTool / HybridSearch 第三路
   │  PathGuard(allowed_roots=[./data/documents, ./data/uploads])   ← 契约不变
   │  调用前 validate_paths → HTTP → sidecar → 调用后 filter_evidence_paths
   ▼
citations（上传目录内的证据保留）

Web UI 删除：DELETE /documents/{id}
   │  DocumentManager.delete_document（四存储，既有）
   │  源文件在 upload_dir 内且无其他 collection 引用 → 删除文件   ← AR2 新增
   ▼
响应新增 source_file_removed 字段（只增不改）
```

模块设计要点：

- **`ensure_local_roots`**（AR1）：输入 `AgenticSearchConfig`；`enabled` 为 false 时不做任何事。对每个根按 PathGuard 相同方式解析（`Path(raw).expanduser().resolve()`），只有解析结果位于 `<CWD>/data/` 之下、且不命中 `denied_substrings` 时才建目录；其他根不碰，仍由 PathGuard 按原契约报错。放在 `src/libs/agentic_search/` 下，与 PathGuard 同包，不 import sirchmunk。（RA2 起预建基准为项目根，见 7.1）
- **调用点**：只在进程入口调用，不放进 `create_app`（`src/api/app.py` 保持零 IO）与 PathGuard 构造函数（守卫不产生副作用）。
- **删除联动**（AR2）：在 API 删除路由或其服务层完成四存储删除后执行；判断「是否仍被引用」以 Chroma 中同 `source_path` 的其他 collection 记录为准。文件删除失败只记 warning 并在响应中如实返回，不回滚已完成的存储删除。

选型理由（相对被否掉的方案）：

1. **方案 A 对比 B（上传目录移进 `data/documents/`）**：B 只改一行，但会让常用摄取命令跨 collection 串数据，并改变已有文档的 doc_id。
2. **方案 A 对比 C（新配置项让上传目录自动并入白名单）**：C 让 agentic 配置依赖 API 配置，三个 PathGuard 构造点都要感知新字段；收益只是少写一行配置。
3. **D1-a 对比 D（PathGuard 跳过不存在的根）与 D1-b（可选根参数）**：D 改变已冻结契约；D1-b 多一个配置概念。D1-a 只在入口加副作用，守卫保持纯函数式校验。

## 4. 测试方案

- **离线确定性测试**（零网络，fake sidecar 后端 / `httpx.MockTransport`）：
  - `ensure_local_roots`：`enabled=false` 不建目录；`data/` 下缺失的根被创建；`data/` 外缺失的根不创建；命中 `denied_substrings` 的根不创建；已存在的根不变。
  - PathGuard 双根：`allowed_roots` 含 `data/documents` 与 `data/uploads` 时，上传目录内的路径通过调用前校验、证据通过调用后过滤；根外路径与符号链接逃逸仍被拒绝。
  - 入口：在没有 `data/uploads/` 的临时项目根下运行 MCP server 与 `start_api.py` 入口逻辑，agentic 工具可构造 PathGuard，不报 `does not exist`。
  - API 集成：`POST /uploads` → `POST /ingestions` → Chat `agentic_fast`（fake 后端返回上传目录内的证据）→ citations 保留该证据。
  - 删除一致性：上传并摄取后 `DELETE /documents/{id}`，源文件被删除、`source_file_removed` 为 true；同一 `source_path` 仍属于另一个 collection 时文件保留；源文件不在上传目录内（如 CLI 摄取的 `data/documents/` 文件）时不删除。
- **真实依赖冒烟**：`RAG_AGENTIC_INTEGRATION=1` 门控，需 sidecar 已启动，默认跳过（AR4）。
- **既有测试语义**（不得破坏）：
  - `tests/unit/test_agentic_path_guard.py`：根不存在即抛 `PathGuardError("... does not exist")`。
  - `tests/unit/test_agentic_tool.py::test_default_paths_from_allowed_roots`：不传 `paths` 时默认用 `allowed_roots`。
  - `tests/unit/test_ingestion_service.py::test_default_upload_dir`：上传目录默认仍为 `data/uploads`。
  - `tests/integration/test_api_write.py` 的删除用例：既有响应字段与计数口径不变，新字段只增不改。
  - `src/api/app.py` 的 `create_app` 零 IO。
  - MCP server stdout 只输出 JSON-RPC：建目录失败的日志走 stderr。
  - 全量 `pytest tests -q` 与 CI 等价禁网命令保持全绿；`python scripts/check_redlines.py` 通过。

## 5. 任务排期与进度

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|----------|------|
| AR0 | 立项与决策终裁 | D1 / D2 终裁记入 §1.3；MOC、backlog/README、README 导航同步；`test_docs_links.py` 与 `test_spec_structure.py` 通过 | 已完成（2026-11-18） |
| AR1 | 白名单追加 `./data/uploads` + `ensure_local_roots` 入口预建 | §4 中 `ensure_local_roots`、PathGuard 双根、入口三组离线测试通过；既有 PathGuard 用例不变；`create_app` 仍零 IO | 已完成（2026-11-18）：`src/libs/agentic_search/local_roots.py`（`ensure_local_roots`，`enabled=false` / 白名单外 / `denied_substrings` 命中一律跳过，OSError 记 warning 不抛）+ 三入口接入（`start_api.py` / `mcp_server.server` / `query.py --agentic`）+ `settings.yaml` 追加；相关离线测试 140 例全绿（新增 `tests/unit/test_agentic_local_roots.py` 12 例），红线 0 违规，Fix-Rounds: 1 |
| AR2 | 删除文档时同步删除上传源文件 | §4 删除一致性三个场景通过；既有删除用例不变；响应新增 `source_file_removed` | 已完成（2026-11-18）：`DeleteResult.source_file_removed` + `DocumentManager.source_referenced`（兼容 legacy `source` 键）+ `DataService` 删除联动（三重门禁：有实际删除 / 无其他 collection 引用 / 路径在上传目录内；引用检查异常与 unlink 失败均保守保留文件并如实返回 False）；`DeleteDocumentResponse` 只增不改；全量 unit+integration 2803 passed，红线 0 违规；既有精确断言用例经用户确认补新字段；Fix-Rounds: 1 |
| AR3 | 文档同步与隐私提示 | USAGE_GUIDE agentic 配置节与第 10 章、README 5.1 的 `allowed_roots` 示例含 `./data/uploads`，并写明外发提示与「须从仓库根启动」；`test_docs_links.py` 通过 | 已完成（2026-11-18）：USAGE_GUIDE §4.4 新增白名单说明四条（隐私外发 / 入口预建 / 从仓库根启动 / 删除联动）、§10.1 注意新增三条、§10.5 DELETE 路由描述补联动清理；README 5.1 示例含双根并补「白名单与隐私」段；`test_docs_links.py` / `test_spec_structure.py` 16 例通过；Fix-Rounds: 0 |
| AR4 | 门控真实冒烟 | `RAG_AGENTIC_INTEGRATION=1` 下经 Web UI 上传一份含唯一标记词的文档，Hybrid 与 Agentic (fast) 均给出带出处的回答；删除后 Agentic (fast) 不再命中；结果记入本表 | 已完成（2026-11-18，真实 sidecar + 真实 LLM，经 Web UI 同款 API 链路执行）：标记文档 `ar4_marker_doc.md`（标记词 AR4-MARKER-9Q3K）上传 201 → 摄取成功（1 chunk）；Agentic (fast) 命中 `data/uploads/ar4_marker_doc.md`（agentic_answer 正确描述标记用途，2449 token / 2 次 LLM 调用 / 7.5s）；Hybrid (normal) 首条 citation 即该文件（score 0.0328 / 765ms）；`DELETE /documents/{id}` 返回四存储计数 + `source_file_removed: true` 且源文件已从磁盘消失；删除后 fast 不再引用该文件（sidecar 明确回答 SHOULD_ANSWER=false）、normal 无该文件 citation。环境排障备注：主项目 embedding 端点 open.bigmodel.cn 被 Clash TUN fake-IP 劫持致 TLS 失败，经临时 /etc/hosts 指向真实 IP 后恢复（属用户本机网络环境问题，与本项目代码无关） |

> 进度备注：交付后在各任务下回填状态与关键实测数据；全部收口后本规格移入 `docs/frozen/` 并更新 MOC.md。

## 6. 验收清单

- [x] 未上传过任何文件的干净环境中，MCP `agentic_search` 与 Chat agentic 模式可用，不报根目录不存在（离线入口测试 + 真实启动验证）
- [x] 经 Web UI 上传并摄取的文档，Hybrid 与 Agentic (fast) 均能给出带出处的回答（AR4 实测）
- [x] Web UI 删除该文档后，agentic 检索不再命中它；被其他 collection 引用的同名源文件保留（AR4 实测 + 离线单测）
- [x] 显式传白名单外的 `paths` 仍返回 `-32602`（既有 PathGuard 用例不变）
- [x] `pytest tests -q`、CI 等价禁网命令、`python scripts/check_redlines.py` 全绿（AR2 收尾时全量 2803 passed / 11 skipped；红线 0 违规）
- [x] 回写完成（§7：USAGE_GUIDE §4.4/§10、README 5.1、MOC、README 导航）

## 7. 实现合入后的回写清单

- `docs/reference/USAGE_GUIDE.md`：agentic 配置节的 `allowed_roots` 示例、第 10 章 Web UI 上传说明（外发提示、须从仓库根启动）
- `README.md` 5.1：`allowed_roots` 示例与说明
- `docs/MOC.md` 与根目录 README 导航表：本规格状态
- 本规格收口冻结时：按 docs-governance §2.4 分配编号；遗留事项（如 CWD 解析根治、collection 隔离）若要推进，建派生台账

### 7.1 实现偏差（RA2 修订）

- **`ensure_local_roots` 预建基准改为项目根**（RA2，依据 ROOT_ANCHOR_SPEC（16 号），2026-11-20）：冻结时本文档写的是「仅预建 `<CWD>/data/` 下的白名单根」（按进程 CWD 解析）；RA2 已把默认基准改为 `<项目根>/data`（`RAG_PROJECT_ROOT` 优先、否则由代码位置推导），相对 `allowed_roots` 亦在 `load_settings` 锚定为项目根下的绝对路径（绝对路径原样）。原文 §3 的 `<CWD>/data/` 表述为冻结时旧契约，以本条为准；PathGuard 收到的是锚定后的绝对根，契约不变。本文档 §2.2「相对 `allowed_roots` 改为按项目根解析……另立提案」一项即由该提案（ROOT_ANCHOR_SPEC）交付收口。

## 8. 隐私注记

- **暴露面**：不新增路由。`DELETE /documents/{id}` 新增文件删除副作用，只作用于上传目录内、且已无 collection 引用的文件。
- **外发边界**：agentic 检索会把命中文件的内容经 sidecar 发给 sidecar 配置的 LLM（第三方服务）。上传目录进白名单后，所有经 Web UI 上传的文件都进入这个外发范围。
- **开关策略**：按 D2 终裁默认加入。`agentic_search.enabled: false` 时不外发；只想让上传文件留在本地索引的用户，可从 `allowed_roots` 中移除 `./data/uploads`。USAGE_GUIDE 与 README 写明这一点。
