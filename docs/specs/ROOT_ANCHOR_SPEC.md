<!-- 规格定位：让主项目的相对数据路径按项目根解析而非按启动进程 CWD 解析，使任意目录启动的入口行为一致。基线：AR 系列已收口冻结（14 号规格），本规格承接其派生台账候选 #1。与既有规格的关系：既有链路的路径解析调整，不改 PathGuard 契约（S1.3），不改 sidecar。RA0 已终裁：D1-a + D2-b + D3-a。 -->

# 项目根锚定规格 (ROOT_ANCHOR_SPEC)

> 版本：0.2 — RA0 立项与 D1–D3 终裁已完成（D1-a + D2-b + D3-a，2026-11-20）
>
> 起源与决策依据：由 [backlog/agentic-roots-followups_backlog.md](../backlog/agentic-roots-followups_backlog.md) 候选 #1「相对 `allowed_roots` 改为按项目根解析」拆分移交（该台账留在 backlog/，#1 细节已移入本规格，原文见 git 历史）；台账源自 [14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md](../frozen/14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md) §2.2 Out of Scope。立项依据为会话内入口复现（2026-11-20，非文件来源，结果见 §1.2）
>
> 基线：`5337024`（fork/main），AR / MR 系列均已收口冻结；本仓库 `config/settings.yaml` 中 `agentic_search.enabled: true`
>
> 日期：2026-11-20

红线不变：主 venv 永不 `import sirchmunk`；白名单仍由本项目调用方执行，PathGuard fail-closed 语义不放宽；MCP server stdout 只输出 JSON-RPC。

## 目录

- 1 项目概述（1.1 结论速览、1.2 背景与现状、1.3 决策依据与待终裁事项）
- 2 核心目标与范围（2.1 In Scope、2.2 Out of Scope）
- 3 技术选型与模块设计
- 4 测试方案
- 5 任务排期与进度
- 6 验收清单
- 7 实现合入后的回写清单

---

## 1. 项目概述

### 1.1 结论速览

| 问题 | 结论 |
|------|------|
| 要解决什么问题？ | 从仓库根以外的目录启动时，入口不报错，但 agentic 检索静默失效：`start_api.py` / `query.py --agentic` 在启动目录下新建空的 `data/` 并检索它；MCP server 读不到配置，`agentic_search` 工具直接消失 |
| 定位是什么？ | 既有链路的路径解析调整：引入「项目根」锚点，配置中的相对数据路径按它解析；不改 PathGuard 契约，不改 sidecar |
| 实施范围（已终裁） | RA0 立项与终裁（已完成，2026-11-20）；RA1–RA4 为承诺任务（RA3 按 D2-b 覆盖全部相对数据路径）；RA5 为门控任务（需 sidecar 与 `RAG_AGENTIC_INTEGRATION=1`） |
| 主要代价 | 相对路径语义从「按 CWD」变为「按项目根」，约 11 处既有测试断言需改写（已获用户批准，见 §4「RA0 核查结果」）；文档里「须从仓库根启动」的约定随之撤销 |

### 1.2 背景与现状

证据（基线 `5337024`）：

- 配置读取路径有两种口径：`scripts/start_api.py:30` 与 `scripts/query.py:87` 用 `__file__` 推导出仓库根下 `config/settings.yaml` 的绝对路径；`src/mcp_server/server.py:182` 调 `load_settings()`，默认参数是相对路径 `"config/settings.yaml"`（`src/core/settings.py:1165`），找不到配置时按禁用处理（`server.py:183-184`、`162-164`）。
- 白名单预建按 CWD 推导基准：`src/libs/agentic_search/local_roots.py:54` 取 `Path.cwd() / "data"`，由 `tests/unit/test_agentic_local_roots.py::test_default_base_is_cwd_data` 锁定。
- PathGuard 把相对根按 CWD 解析（`path_guard.py:56`），相对证据路径的解析候选含 `Path.cwd()`（`path_guard.py:137`）。
- 数据层的相对路径都按 CWD 解析：上传目录 `DEFAULT_UPLOAD_DIR = Path("data/uploads")`（`ingestion_service.py:36`）、`vector_store.persist_path`、BM25 索引、摄取历史、图片存储、`api.tasks_db`、`chat.session_db`、`api.static_dir`（`src/api/static.py:67` 显式拼 `Path.cwd()`）。
- 已有的锚定先例：`src/core/chat/prompts.py:13` 用 `__file__` 锚定仓库根；`src/observability/log_files.py:26-38` 的 `RAG_LOG_ROOT` 环境变量在设置时作为相对日志路径的解析根，未设置时按 CWD；`evaluation_service.py:97` 的测试集路径先按 CWD、再按仓库根解析。

会话内复现（2026-11-20，主项目 venv，在 `mktemp -d` 建的临时目录里执行，仓库根执行作对照）：

| 入口 | 在仓库外的目录启动 | 在仓库根启动 |
|------|------------------|-------------|
| `start_api.py` 启动逻辑（`load_settings(DEFAULT_CONFIG_PATH)` → `ensure_local_roots` → `PathGuard`） | 配置正常读取；`ensure_local_roots` 新建 `<TMP>/data/documents` 与 `<TMP>/data/uploads`；PathGuard 构造成功，白名单指向这两个空目录；上传目录与 Chroma 也解析到 `<TMP>/data/` 下 | 白名单指向仓库内 `data/` |
| `python -m mcp_server.server`（`tools/list`） | 返回 3 个工具，无 `agentic_search`；无报错，不建目录 | 返回 4 个工具，含 `agentic_search` |

`query.py --agentic` 的读配置与预建链路和 `start_api.py` 相同（代码阅读确认，未单独执行）。

对台账原描述的勘误：台账写的是「从仓库根以外启动时根不存在，PathGuard fail-closed 拒绝，整个 agentic 工具不可用」。这是 AR1 交付前的行为。AR1 的 `ensure_local_roots` 让 `start_api.py` / `query.py` 改为静默检索空目录；MCP server 则是读不到配置、工具缺失。两种表现都不报错，比 fail-closed 更难察觉。台账中的「AR0 核查时实证」在 14 号规格里没有记录，以本节复现为准；台账写的「spec 4.6」指 [03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md](../frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md) §4.6（sidecar 的 `paths` 缺省解析链含 sidecar CWD；本项目恒发绝对路径，不受影响）。

现有规避方式是文档约定（USAGE_GUIDE §4.4 / §10.1、README 5.1 写明「须从仓库根启动」，README §9 MCP 客户端示例设置 `cwd`），依赖使用者记住。

### 1.3 决策依据与待终裁事项

提案三问：

1. **已观察到的问题**：见 1.2 的复现，任意目录启动时 agentic 检索静默失效，且会在启动目录留下 `data/`。
2. **更简单的方案**：仅文档提示已经做了，但没有消除静默失效。改动最小的根治是入口 `chdir` 到仓库根（D1-d），代价见下表。
3. **如何确认有效**：在临时 CWD 下以子进程运行三个入口的离线测试（§4），断言白名单与数据路径落在项目根、启动目录不新建 `data/`、MCP `tools/list` 含 `agentic_search`；门控冒烟 RA5 在仓库外的目录启动，走一遍真实的上传 → agentic 命中。

| 编号 | 决策 | 候选方案 | 推荐（待 RA0 用户终裁） |
|------|------|----------|------------------------|
| D1 | 项目根锚点机制 | **D1-a** 代码位置推导（`Path(__file__).parents[N]`，同 `prompts.py` / `start_api.py` 先例），可选环境变量 `RAG_PROJECT_ROOT` 覆盖；**D1-b** 由配置文件位置推导（`settings.yaml` 的上上级目录），跟随 `--config`，但配置放在仓库外时锚点随之漂移；**D1-c** 仅环境变量，未设置时沿用 CWD（仿 `RAG_LOG_ROOT`，完全向后兼容，但不修复默认场景）；**D1-d** 三个入口 `os.chdir(项目根)`，改动最小，但会改变 CLI 参数中相对路径（如 `ingest.py --path`）的含义，且是进程级副作用，库内直接调用（测试、被 import 时）不受保护 | D1-a：与现有先例一致，默认场景即修复，环境变量给打包 / 非标准布局留出口 |
| D2 | 锚定覆盖范围 | **D2-a** 只覆盖 agentic 链路（`allowed_roots`、`ensure_local_roots` 基准、MCP 默认配置路径）；**D2-b** 覆盖配置中全部相对数据路径（D2-a + 上传目录、Chroma、BM25、摄取历史、图片存储、`tasks_db`、`session_db`、`static_dir`、日志目录） | D2-b：只做 D2-a 会让上传目录落在 CWD、白名单落在项目根，AR 系列建立的「上传即可被 agentic 检索」一致性被拆开 |
| D3 | 在哪一层解析 | **D3-a** `load_settings` 时把相对路径规范化为绝对路径（一处改动，但 `Settings` 字段值改变，可能影响断言原始字符串的测试）；**D3-b** 在各使用点解析（逐点改动，`Settings` 保持原样） | RA0 核查测试断言后再定；倾向 D3-a，前提是断言原始字符串的测试很少 |

日志目录的特殊约束（适用于任何 D1 方案）：`RAG_LOG_ROOT` 已设置时优先级不变，测试依赖它隔离仓库 `logs/`（LG 规格）。

> **终裁记录（2026-11-20，RA0，用户会话确认）**：D1-a + D2-b + D3-a；同时批准 §4 核查出的既有测试断言改写（清单见 §4「RA0 核查结果」）。§3 即按终裁方案绘制，无需改写。

## 2. 核心目标与范围

### 2.1 In Scope

| 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|--------|-----------|
| 决策 | 立项与终裁 | 承诺（RA0） | D1–D3 终裁；核查 §4 列出的 CWD 相关测试，分类为「不受影响 / 需改写语义」 |
| 锚点 | 项目根解析 + MCP 默认配置路径 | 承诺（RA1） | 提供单一的项目根解析函数；MCP server 从任意 CWD 都能读到仓库配置 |
| agentic | 白名单与预建按项目根 | 承诺（RA2） | 相对 `allowed_roots` 与 `ensure_local_roots` 基准按项目根解析，PathGuard 收到的是绝对根 |
| 数据层 | 相对数据路径按项目根 | 承诺（RA3，范围按 D2） | 上传目录、存储、会话库、静态目录、日志目录与白名单使用同一基准 |
| 文档 | 撤销「须从仓库根启动」约定 | 承诺（RA4） | USAGE_GUIDE §4.4 / §10.1、README 5.1 与 §9 示例同步新语义 |
| 冒烟 | 真实 sidecar 任意 CWD 验证 | 门控（RA5，RA1–RA4 通过后） | 从仓库外的目录启动，上传 → Agentic (fast) 命中 → 删除后不再命中 |

### 2.2 Out of Scope（明确不做）

- **修改 PathGuard 契约**（根不存在即报错、fail-closed）：S1.3 已冻结并有测试锁定。锚定在调用方完成，PathGuard 只收到绝对根。
- **修改 sidecar 侧配置**（`SIRCHMUNK_*`）：本项目恒发绝对路径（`http_backend.py:232-234`），sidecar 不受 CWD 影响。
- **CLI / API 中由用户输入的相对路径**（`ingest.py --path`、`--config`、`POST /evaluations` 的 `test_set_path`）：保持按 CWD 解析，与命令行常规预期一致；`evaluation_service` 现有的「先 CWD 再仓库根」规则不变。
- **`source_path` / doc_id 口径与已有数据迁移**：锚定只影响存储与白名单的落盘位置，不改写已摄取文档的 `source_path`，不迁移已有数据。
- **agentic 检索按 collection 隔离**：留在台账候选 #2。
- **把上传目录移进 `data/documents/`**：继承 AR 规格 §2.2。

## 3. 技术选型与模块设计

以下按终裁方案（D1-a + D2-b + D3-a，2026-11-20）绘制。

```
任意 CWD 启动入口（start_api.py / mcp_server.server / query.py --agentic）
   │  load_settings()   ← RA1：默认配置路径 = <项目根>/config/settings.yaml
   │     └─ 相对数据路径 → <项目根>/...（RAG_LOG_ROOT 已设置时日志目录仍按它）   ← RA2 / RA3
   │  ensure_local_roots(agentic_search)   ← RA2：基准 = <项目根>/data
   ▼
PathGuard(allowed_roots=[<项目根>/data/documents, <项目根>/data/uploads])   ← 契约不变，收到绝对根
   │
IngestionService(upload_dir=<项目根>/data/uploads) / Chroma / BM25 / SQLite   ← RA3
```

模块设计要点：

- **项目根解析**（RA1）：一个函数，放在 `src/core/` 下，供 settings 与入口共用。`RAG_PROJECT_ROOT` 已设置时取其值（D1-a 的覆盖出口），否则由代码位置推导。不做 IO，不建目录。
- **MCP 默认配置路径**（RA1）：`load_settings()` 的默认值改为锚定路径；显式传入的路径语义不变。读不到配置时按禁用处理的既有契约（`server.py:162-164`）不变。
- **`ensure_local_roots`**（RA2）：默认基准改为 `<项目根>/data`；`base` 参数与「基准外不碰 / denied 不建 / OSError 只记 warning」的语义不变。
- **日志目录**（RA3）：`resolve_log_path` 保持「绝对路径原样、`RAG_LOG_ROOT` 优先」，只把未设置时的回退基准从 CWD 改为项目根。
- **`static_dir`**（RA3）：`resolve_static_dir` 去掉显式 `Path.cwd()` 拼接，改用同一基准。

选型理由（相对被否掉的方案）：

1. **D1-a 对比 D1-d（入口 chdir）**：D1-d 改动最小，但会悄悄改变用户 CLI 相对路径参数的含义，而且只保护经入口启动的进程；D1-a 在配置层解析，库内调用与入口行为一致。
2. **D1-a 对比 D1-c（仅环境变量）**：D1-c 零风险，但默认场景（不设变量、从别处启动）仍然静默失效，等于没修。
3. **D2-b 对比 D2-a**：见 §1.3 D2 一行。

## 4. 测试方案

- **离线确定性测试**（零网络，fake 后端 / `httpx.MockTransport`）：
  - 项目根解析：默认推导结果等于仓库根；`RAG_PROJECT_ROOT` 覆盖生效；不做 IO。
  - 配置规范化：相对路径解析到项目根下；绝对路径原样保留；`RAG_LOG_ROOT` 已设置时日志目录按它解析。
  - 入口子进程测试（仿 §1.2 复现）：在 `tmp_path` 作 CWD、配置指向 fixture 项目根的条件下，MCP server `tools/list` 含 `agentic_search`（`enabled: true` 时）；`start_api.py` 启动逻辑（不起 uvicorn）得到的白名单、上传目录、Chroma 路径都在项目根下；`tmp_path` 下不新建 `data/`。
  - `ensure_local_roots`：默认基准为 `<项目根>/data`；其余既有用例不变。
  - stdout 纪律：MCP 子进程测试逐行断言 stdout 只有 JSON-RPC。
- **真实依赖冒烟**：`RAG_AGENTIC_INTEGRATION=1` 门控，需 sidecar 已启动，默认跳过（RA5）。
- **既有测试语义**（不得破坏）：
  - `tests/unit/test_agentic_path_guard.py`：根不存在即抛 `PathGuardError("... does not exist")`；根外路径与符号链接逃逸被拒。
  - `tests/unit/test_agentic_tool.py::test_default_paths_from_allowed_roots`：不传 `paths` 时默认用 `allowed_roots`。
  - `tests/unit/test_ingestion_service.py::test_default_upload_dir`：上传目录默认仍为项目下的 `data/uploads`（RA0 已核查：D3-a 绝对化后按批准改写为断言锚定后的绝对路径，见下文「RA0 核查结果」）。
  - LG 规格：`RAG_LOG_ROOT` 隔离测试日志的语义不变。
  - `src/api/app.py` 的 `create_app` 零 IO。
  - MCP server 读不到配置时按禁用处理，`tools/list` 维持 3 个工具。
  - 全量 `pytest tests -q` 与 CI 等价禁网命令保持全绿；`python scripts/check_redlines.py` 通过。
- **RA0 核查清单（已完成，以 2026-11-20 核查结果为准）**：原待核查的 13 处 CWD 相关测试（使用 `monkeypatch.chdir`，可能编码「按 CWD 解析」语义；改写既有断言已经用户批准）已逐一核查完毕，分类与处置见下文「RA0 核查结果」表，该表为本节权威口径，此处不再重复罗列。

**RA0 核查结果（2026-11-20，逐行读源码核验；改写已获用户批准）**：

| 类别 | 判定 | 测试 | 处置 |
|------|------|------|------|
| 锁定 CWD 语义 | 需改写为项目根语义（不放松断言强度） | `test_agentic_local_roots.py:123 / 188 / 210 / 223`（默认基准 = CWD/data，4 处入口预建）、`test_sparse_retriever.py:255`（BM25 默认索引路径按 CWD）、`test_upload_source_cleanup.py:143`（默认上传目录按 CWD） | RA2 / RA3 中改写 |
| 断言原始相对字符串 | 需改写为断言锚定后的绝对路径（D3-a 影响） | `test_ingestion_service.py:80`（`upload_dir == Path("data/uploads")`）、`test_api_security.py:141`（`tasks_db` 原始串）、`test_chat_settings.py:49`（`session_db` 原始串）、`test_file_integrity.py:155`（`DEFAULT_DB_PATH`）、`test_image_storage.py:337-338`（两个 DEFAULT 常量） | RA3 中改写 |
| chdir 仅隔离数据目录 | 锚定后隔离失效但断言仍通过，暂不动 | `test_config_loading.py:241 / 262`、`test_evaluation_service.py:26`、`test_sparse_retriever.py:248`、`test_query_service.py:181`、`test_composite_evaluator.py:60`、`test_evaluate_entry.py:178 / 190` | 不改写；其中落在真实仓库数据路径上的写行为由 RA3 的入口子进程测试接管兜底 |
| 不受影响 | 断言的是 Out of Scope 契约 | `test_agentic_path_guard.py:69`（PathGuard 用户输入相对路径按 CWD，契约不变）、`test_evaluation_service.py:141`（测试集路径先 CWD 再仓库根，规则不变）、`test_answer_generator.py:171`（prompt 加载与 CWD 无关，正是目标语义）、`tests/integration/test_agentic_http.py:87`（sidecar 侧证据解析，03 号规格 §4.6 契约） | 不改写 |

> **RA3 更正（2026-11-20）**：原表把 `tests/integration/test_api_static.py:156` 归入「不受影响」有误——`static_dir` 不是 Out of Scope 的 CLI 用户输入路径，D2-b 明确把它纳入锚定范围，该用例锁定 CWD 语义、已在 RA3 改写（`test_relative_static_dir_anchored_to_project_root`）；`test_file_integrity.py:155` / `test_image_storage.py:337-338` 断言模块常量本身，常量保持原样（使用点锚定），实际无需改写；`test_api_security.py:141` / `test_chat_settings.py:49` 因 `tasks_db` / `session_db` 改为使用点锚定（Settings 字段保持原样）也无需改写。

补充发现：`tests/e2e/test_chat_real_integration.py:100` 与 `tests/e2e/test_chat_agentic_real_integration.py:55`（均为门控用例）已主动 `chdir(PROJECT_ROOT)` 编码目标语义，锚定落地后可简化；`test_evaluation_service.py:141` 的断言原归类于待核查清单，核查确认其锁定的是 Out of Scope 的既有规则，不改写。

## 5. 任务排期与进度

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|----------|------|
| RA0 | 立项与决策终裁 | D1–D3 终裁记入 §1.3；§4 的 CWD 相关测试逐一分类并记入本行；MOC、backlog/README、README 导航同步；`test_docs_links.py` 与 `test_spec_structure.py` 通过 | ✅ 已完成（2026-11-20）：终裁 D1-a + D2-b + D3-a + 批准改写（见 §1.3 终裁记录与 §4「RA0 核查结果」）；MOC / backlog README / README 导航随立项提交（`7630b86`）同步，本轮随终裁更新状态描述；两 drift guard 测试 18/18 通过 |
| RA1 | 项目根解析 + MCP 默认配置路径锚定 | §4 中项目根解析用例与 MCP 入口子进程用例通过；读不到配置时仍按禁用处理 | ✅ 已完成（2026-11-20）：`src/core/project_root.py` 提供 `resolve_project_root()`（`RAG_PROJECT_ROOT` 优先、空串视为未设置、纯计算不建目录）；`load_settings()` 默认路径改惰性锚定（`path=None` 哨兵，显式传入路径仍按 CWD）；新增单测 10 个（`test_project_root.py` 7 个 + `test_config_loading.py` 3 个）；3 个 e2e MCP 子进程 fixture 注入 `RAG_PROJECT_ROOT`（原依赖 CWD 相对配置）；全量禁网 2952 passed / 23 skipped / 1 xfailed，`check_redlines.py` OK。计划见 `.agents/plans/RA1.md` |
| RA2 | agentic 白名单与预建按项目根 | `ensure_local_roots` 默认基准用例通过；临时 CWD 下 PathGuard 根落在项目根、CWD 不新建 `data/`；既有 PathGuard 用例不变 | ✅ 已完成（2026-11-20）：`load_settings` 把相对 `allowed_roots` 锚定为 `<项目根>/...` 绝对路径（绝对原样、`~` 展开，D3-a 口径）；`ensure_local_roots` 默认基准改 `resolve_project_root()/data`；改写 `test_agentic_local_roots` 4 处（默认基准 2 例含「CWD 不新建 data/」+ 3 入口用例注入 `RAG_PROJECT_ROOT`，start_api 改显式 `--config`）、新增 `test_agentic_config` 3 例（相对锚定 / 绝对原样 / `~` 展开）；PathGuard 契约用例不变；全量禁网 2956 passed / 23 skipped / 1 xfailed，`check_redlines.py` OK。计划见 `.agents/plans/RA2.md` |
| RA3 | 数据层相对路径按项目根（范围按 D2） | 配置规范化用例与 `start_api.py` 入口子进程用例通过；`RAG_LOG_ROOT` 优先级不变；经确认改写的既有测试在本行列明 | ✅ 已完成（2026-11-20）：`core.project_root.resolve_data_path()`（幂等；sqlite `:memory:` 哨兵原样返回）；`load_settings` 锚定 `vector_store.persist_path`；`tasks_db` / `session_db` / `static_dir` 改为**使用点锚定**（偏离 D3-a 字面：两字段各仅 1 个消费者，且 `test_api_security:153` / `test_chat_settings` 共 5 处 dataclass 整体等值断言会因字段绝对化破裂；落地行为等价，计划见 `.agents/plans/RA3.md`）；模块常量默认值（上传目录 / BM25 索引 / 摄取历史库 / 图片库）在构造函数默认处锚定，常量原样；`resolve_log_path` 回退基准 CWD → 项目根（`RAG_LOG_ROOT` 优先级不变）；改写 5 处既有测试（上传目录 ×2、BM25 默认路径、`static_dir`、log 回退 ×2）+ 2 个 e2e CLI 子进程文件注入 `RAG_PROJECT_ROOT`（原依赖 CWD 相对缺省存储）；新增 `resolve_data_path` 4 例 + `persist_path` 规范化 2 例 + 入口「数据路径落项目根、CWD 不新建 data/」1 例；全量禁网 2963 passed / 23 skipped / 1 xfailed，`check_redlines.py` OK |
| RA4 | 文档同步 | USAGE_GUIDE §4.4 / §10.1、README 5.1 撤销「须从仓库根启动」并说明新语义与 `RAG_PROJECT_ROOT`；README §9 示例的 `cwd` 改为可选；`test_docs_links.py` 通过 | ✅ 已完成（2026-11-20）：USAGE_GUIDE §4.4「从仓库根启动」改写为「项目根锚定（RA 系列）」（相对白名单根按项目根解析、`RAG_PROJECT_ROOT` 优先否则按代码位置推导、启动目录不新建 `data/`、绝对路径与 `~` 原样生效）；§10.1 同款改写为「任意目录启动（RA 系列）」；README 5.1 白名单段落同步新语义；README §9 两处 MCP Client 示例移除 `cwd` 并补「`cwd` 可选 + `RAG_PROJECT_ROOT`」说明，FAQ「MCP 连接问题」同步；USAGE_GUIDE §2.5 补同款可选说明。`test_docs_links.py` / `test_spec_structure.py` 通过 |
| RA5 | 门控真实冒烟 | `RAG_AGENTIC_INTEGRATION=1` 下从仓库外的目录启动 `start_api.py`，上传一份含唯一标记词的文档，Agentic (fast) 命中；删除后不再命中；启动目录无 `data/`；结果记入本表 | 未开始 |

> 进度备注：交付后在各任务下回填状态与关键实测数据；全部收口后本规格移入 `docs/frozen/` 并更新 MOC.md。

## 6. 验收清单

- [ ] 从仓库外的任意目录启动 `start_api.py` / `query.py --agentic`，白名单与数据路径都落在项目根下，启动目录不新建 `data/`
- [ ] 从仓库外的任意目录启动 MCP server，`agentic_search.enabled: true` 时 `tools/list` 含 `agentic_search`
- [ ] 绝对路径配置与 `RAG_LOG_ROOT` 的行为不变；用户输入的 CLI / API 相对路径仍按 CWD 解析
- [ ] 显式传白名单外的 `paths` 仍返回 `-32602`（既有 PathGuard 用例不变）
- [ ] `pytest tests -q`、CI 等价禁网命令、`python scripts/check_redlines.py` 全绿
- [ ] 回写完成（§7）

## 7. 实现合入后的回写清单

- `docs/reference/USAGE_GUIDE.md`：§4.4 白名单说明的「从仓库根启动」条、§10.1 对应注意事项
- `README.md`：5.1「白名单与隐私」段、§9 MCP 客户端示例
- `docs/backlog/agentic-roots-followups_backlog.md`：候选 #1 的移交记录补「已交付」
- `docs/MOC.md` 与根目录 README 导航表：本规格状态
- 本规格收口冻结时：按 docs-governance §2.4 分配编号；遗留事项若需推进，建派生台账
