# Sirchmunk 能力补齐规格 (SIRCHMUNK_EXTENSION_SPEC)

> 状态：**已冻结**（SX 系列，SX0–SX5 全部收口于 2026-10-14；遗留事项见 [backlog/sirchmunk-extension-followups.md](../backlog/sirchmunk-extension-followups.md)）｜上游基线：`b314e11`｜决策依据：[backlog/sirchmunk-capability-completion.md](../backlog/sirchmunk-capability-completion.md)
>
> 前置规格：[SIRCHMUNK_INTEGRATION_SPEC_frozen](SIRCHMUNK_INTEGRATION_SPEC_frozen.md)（S 系列，已收口）。本规格只做**增量**，不改 S 系列任何契约；进程隔离红线不变：主 venv 永不 `import sirchmunk`，不改上游源码。

---

## 目录

- [1. 概述](#1-概述)
- [2. 范围](#2-范围)
- [3. 模块设计](#3-模块设计)
- [4. 源码实证注意事项](#4-源码实证注意事项)
- [5. 测试方案](#5-测试方案)
- [6. 配置变更汇总](#6-配置变更汇总)
- [7. 排期与进度](#7-排期与进度)

---

## 1. 概述

### 1.1 背景与目标

G0 三方对照的结论只在「无编译、无复用、冷启动」配置下成立。本规格补齐 sirchmunk 其余可经 sidecar 使用的能力，并修复规格与实现的三处偏差：

1. **telemetry 消费**：`SearchContext.to_dict()` 将 `context.telemetry` 合并到 `data` 顶层（`schema/search_context.py:163-189`），`converter.py` 未读取，trace/Dashboard 无展示；
2. **知识编译运维入口**：HTTP API 无编译端点，需脚本化 sidecar venv CLI 并完成「编译能否翻盘」实验；
3. **知识簇复用**：上游 core 依赖已含 torch/sentence-transformers，关闭理由（依赖安装）不再成立，需受控开启并评估。

外加两枚小增量：请求参数透传（`max_depth`/`enable_dir_scan`）与 SSE 流式（仅 Dashboard DEEP）。

### 1.2 修正后的能力可达性（源码复核）

| 能力 | 上一版判断 | 复核结论（`b314e11`） |
|------|-----------|----------------------|
| telemetry | 「`data.telemetry` 未消费」 | **无 `data.telemetry` 键**——`to_dict()` 把动态 telemetry `setdefault` 合并进顶层；应读 `loop_count`/`budget_remaining`/`llm_call_count`/`retrieval_logs`/`read_file_ids`/`search_history` 及机制标记（`self_correct_entered`/`fallback_best_candidate`/`answer_withheld_by_policy` 等，见 `search.py` 20+ 处 `_record_context_telemetry` 调用） |
| 知识簇复用 | 「需安装 torch + embedding 模型」 | `.venv-sirchmunk` 实测已含 torch 2.14.1 / sentence-transformers 6.1.0（上游 requirements/core.txt）；真实代价仅 MiniLM 下载（`EMBEDDING_MODEL_ID`，默认 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`）+ 常驻内存 |
| 知识演化 | 「随复用失效」 | **HTTP 不可达**：`AgenticSearch(enable_knowledge_evolution=False)` 为默认，`api/search.py`/CLI 均不传 `True`；开启须改上游 → out of scope |
| 编译覆盖面 | 「树索引只对 ≥50KB 大文档生效」 | **门槛是 3,000 字符**（`learnings/tree_indexer.py:26` `_TREE_MIN_CHARS`；50,000 是启发式窗口上限）。但编译**完全不处理 `.py`**：`compiler._ELIGIBLE_EXTENSIONS` 只含 pdf/docx/doc/md/markdown/html/htm/rst/tex/txt/pptx/xlsx，`_TREE_EXTENSIONS` 再去掉 pptx/xlsx。现有 golden 集 6 题中 Q1/Q2/Q5/Q6 的 `expected_sources` 全是 `.py`，编译最多影响 Q3/Q4 |

---

## 2. 范围

### 2.1 In Scope

| 期 | 模块 | 优先级 | 一句话目标 |
|----|------|--------|-----------|
| SX0 | 规格勘误与基线冻结 | 承诺 | gap analysis v0.2、冻结规格勘误、golden 集补编译可覆盖题、基线固化 |
| SX1 | telemetry 解析 → trace + Dashboard | 承诺 | 白名单键解析，`record_stage` 打点，agentic 元数据面板 |
| SX2 | 请求参数透传 | 承诺 | `max_depth`/`enable_dir_scan`；`llm_fallback` 恒 `false` 显式锁定 |
| SX3 | 编译工具 + 编译收益实验 | 承诺 | `scripts/sirchmunk/compile.sh`；门控真实 LLM 实验（文档类题子集），正负结果均记录 |
| SX4 | 知识簇复用开启与评估 | 承诺 | env 模板 + preflight 预下载；重复/改写查询评估 + 非命中查询回归检查 |
| SX5 | SSE 流式（Dashboard DEEP） | 可选（门控：SX0.4 基线显示 DEEP 有实际使用价值；**与 GX 无关**） | `search_stream` 消费 `/api/v1/search/stream`，MCP 保持同步 |

### 2.2 Out of Scope（明确不做）

- **知识演化（evolver）**：HTTP/CLI 均不可达，开启须改上游源码（违反本项目与上游双方纪律）。
- **`llm_fallback`**：无文档时让 LLM 凭自身知识回答，与「引用必须可溯源」冲突；SX2 中恒发 `false`。
- **FILENAME_ONLY 模式**：沿用冻结规格 2.2 决定。
- **chat/knowledge/history/monitor/files 路由与 WebUI**：sirchmunk 自身产品层，Dashboard 已有对应职能。
- **compile 的 HTTP/SDK 集成**：只做 CLI 包装。
- **任何上游源码改动**。

---

## 3. 模块设计

### 3.1 运行架构（增量）

```
┌──────────────────── MCP Client ────────────────────┐
└──────────────┬──────────────────────────────────────┘
               │ stdio JSON-RPC（同步，不变）
               ▼
┌─────────────────────────────────────────────────────┐
│ 主项目（主 venv）                                     │
│  AgenticSearchTool                                  │
│   ├ SirchmunkHttpBackend.search()      （SX1/SX2）  │
│   └ SirchmunkHttpBackend.search_stream()（SX5，新）  │
│  trace：agentic 阶段新增 telemetry 键   （SX1）      │
│  Dashboard query_playground：DEEP 进度  （SX5）      │
└───────────┬─────────────────────────────────────────┘
            │ POST /api/v1/search（不变）
            │ POST /api/v1/search/stream（SX5 新用）
            ▼
┌─────────────────────────────────────────────────────┐
│ sirchmunk serve（独立 venv，不变）                     │
│   └ work_path/.cache/ ← compile.sh 离线写入（SX3）   │
└─────────────────────────────────────────────────────┘
     ▲
     │ sidecar 停机期间执行（SX3）
   scripts/sirchmunk/compile.sh → sirchmunk compile CLI
```

### 3.2 SX1：telemetry 解析

| 项 | 设计 |
|----|------|
| 契约 | `AgenticSearchResponse` 新增 `telemetry: dict[str, Any] | None`（缺省 `None`，容忍键缺失） |
| 白名单 | `loop_count`/`max_loops`/`total_llm_tokens`（已单独解析，去重）/`budget_remaining`/`llm_call_count`/`read_file_count`（由 `read_file_ids` 长度派生）/`search_history_len`（由 `search_history` 长度派生）/机制标记布尔键（`self_correct_entered`/`fallback_best_candidate`/`answer_withheld_by_policy`/`loop_hygiene_rescue_used`/`typed_comparison_checked` 等，遇即收） |
| 降维 | `retrieval_logs` 不透传原文，只按 `tool_name` 计数 → `retrieval_tool_counts: {tool: n}`，避免 trace 膨胀 |
| 派生 | `zero_llm_path`：`llm_call_count == 0 and loop_count == 0`，任一键缺失则为 `None`。只陈述事实，**不叫「复用命中」**：复用命中、DocQA 直析（其 LLM 调用记在实例级 `self.llm_usages`，不进 context，冻结规格第 7 章 Q6 已观察到 0 调用/0 token）、闲聊短路都会产生这个组合 |
| 打点 | `AgenticSearchTool._record_trace` 的 `record_stage` metadata 增加 `agentic_telemetry`（白名单 dict） |
| Dashboard | `query_playground.py` 的 `_render_agentic_meta` 增加 telemetry 区块（键值表） |
| fixture | `tests/fixtures/agentic/` 增加含顶层 telemetry 键的契约 fixture（现 fixture 为裁剪子集，无这些键） |

### 3.3 SX2：请求参数透传

| 项 | 设计 |
|----|------|
| 配置 | `AgenticSearchConfig` 新增 `max_depth: int | None`、`enable_dir_scan: bool = True`（可选节纪律：缺失走默认、非法值 fail-fast） |
| 请求 | `_build_payload` 透传（请求级覆盖配置级，同既有 `top_k_files` 模式）；恒发 `"llm_fallback": false` 显式锁定 |
| 校验 | `max_depth` 为正整数或 `None`；`enable_dir_scan` 严格 bool |

### 3.4 SX3：编译工具与实验

`scripts/sirchmunk/compile.sh`：

- 用法：`bash scripts/sirchmunk/compile.sh [--full|--shallow|--status|--lint [--fix]] [--max-files N] [--concurrency N] -- <paths>...`
- 读取 `.env-sirchmunk` 的 `SIRCHMUNK_WORK_PATH`（与 sidecar 同一 work_path）；`env.example` 新增 `SIRCHMUNK_COMPILE_PATHS` 缺省候选（`README.md docs`，相对仓库根）。
- **停机护栏**：执行前探活 `/api/v1/search/status`，sidecar 存活即拒绝运行（work_path 无文件锁，spec 4.7 last-writer-wins）；`--status`/`--lint` 只读模式例外。
- **缓存护栏**：编译完成后提示必须重启 sidecar（`AgenticSearch._tree_paths_cache` 按实例缓存，singleton 不感知新编译产物）。
- **路径护栏**：上游 `_discover_files` 用 `rglob("*")` + 扩展名过滤，不读 `.gitignore`，CLI 也没有 exclude 参数。`compile.sh` 必须拿到显式路径，拒绝仓库根（`.`）或任何包含 `.venv*`/`.git`/`build`/`logs`/`cache` 的目录。`data/documents/` 不进缺省路径：其中是与 golden 集无关的用户文档，编译会把全文发给 LLM。

实验（门控 `RAG_AGENTIC_INTEGRATION=1`，真实 LLM）：

**前提**：编译只覆盖文档类扩展名（§1.2），不处理 `.py`。现有 golden 集里 `.py` 题（Q1/Q2/Q5/Q6）只作回归观察：编译后的文档目录可能把路由拉向文档、远离源码，需确认没有变差。编译收益只用文档类题评估。

1. 补题（SX0.3）：**新建独立 fixture** `tests/fixtures/agentic/golden_questions_docs.json`（与原文件同 schema，ID 用 `D1`–`D4`，category `doc_navigation`），放 3–4 道答案只在 `README.md`/`docs/**/*.md` 中、需要跨章节定位的题；它和原文件的 Q3/Q4 一起构成「编译可覆盖子集」。**不修改 `golden_questions.json`**：它是 G0 的考卷，冻结规格第 7 章的 6/6、2/6 都以它为分母。出题约束：
   - 答案最好落在超过 3,000 字符的文档里（能触发树索引）；
   - 答案不能来自讨论 golden 集本身的文档（冻结规格第 7 章、gap analysis、本规格、backlog 提案），新题题面和答案也不写进 docs；
   - 题目按问题语义编写，不针对编译机制设计措辞，不用打招呼式措辞（冻结规格 4.6）；
   - 先跑 `rg_baseline.py --fixture <新文件>` 得出词法下限。若裸 rg 已全中，编译只能在成本/精度上赢，如实记录。
2. 基线（SX0.4）：复用关闭、知识簇存储为空，两个 fixture 分别跑 FAST/DEEP，每题 3 次，记录逐题命中、耗时、token，两个 fixture 分开报告。
3. 编译：先 `compile.sh --full -- README.md docs`（测收益上限，本仓库 md 多数超过 3,000 字符门槛，会建树），记录编译 token/耗时。
4. 重跑：重启 sidecar，重复第 2 步的同一协议（同题、同次数、复用关闭）。
5. 对照（可选，仅当第 4 步有正收益）：`--shallow` 重编译后重跑，量化树索引贡献与成本差。
6. **门控 GX**（逐题定性裁决，不只看总比例，样本太小）：编译可覆盖子集多数题稳定改善、`.py` 题无回退、编译成本可接受 → USAGE_GUIDE 增加编译推荐配置；否则记录负结果。GX 只决定是否推荐编译，**不影响 SX4/SX5 是否启动**。

### 3.5 SX4：知识簇复用

- `env.example`：`SIRCHMUNK_ENABLE_CLUSTER_REUSE=true`（默认注释保持 false，开启为显式动作）+ `CLUSTER_SIM_THRESHOLD`/`CLUSTER_SIM_TOP_K`/`EMBEDDING_MODEL_ID`/`EMBEDDING_CACHE_DIR` 说明。
- `preflight.sh` 增加可选步骤：复用开启时预触发模型下载并报告就绪状态（避免首次搜索卡模型加载）。
- **实验隔离**：SX3 实验数据采集完成后再开启复用，或使用独立 work_path。评估前清空 `work_path/.cache/knowledge/`。复用关闭时保存的簇没有 embedding（`search.py:1019-1049` 仅在 embedding 客户端就绪时写入），混入会让评估失真。
- 评估（门控）：
  - 重复查询与改写查询的延迟/LLM 调用对比、误复用率抽查、文件修改后陈旧结果检查；
  - **非命中查询回归**：embedding 客户端存在时，软复用 `_try_soft_reuse`（`search.py:2596`、`3798`）会给未命中的查询注入历史簇提示，改变正常搜索行为。需用 SX0.4 同一协议对比复用开/关下的 golden 集命中率。
- **复用命中无法从响应判定**：命中路径（`search.py:2590-2592`、`3792-3795`）直接返回，不写任何 telemetry 标记。响应里也没有可靠的替代信号：
  - `zero_llm_path` 为真不等于复用（DocQA、闲聊短路同样为真）；
  - 返回簇的 `queries` 长度也不行：`_add_query_to_cluster`（`search.py:939-955`）对完全相同的查询去重，重复查询复用后长度仍为 1。
  - 因此 SX4 评估以 sidecar 日志为准：`Found similar cluster`（含相似度）与 `Reused cached knowledge cluster`（FAST）逐条对齐查询；`zero_llm_path` 只用作初筛。
- 复用检索按 `search_paths` 限定范围（`search_similar_clusters(search_paths=paths)`），PathGuard 调用后过滤照常生效。

### 3.6 SX5：SSE 流式（可选期）

- **启动门控**：SX0.4 基线显示 DEEP 有实际使用价值（DEEP 命中了 rg/FAST 未命中的题），或 Dashboard 确有 DEEP 使用需求。SSE 解决的是 DEEP 等待期无反馈和断连取消，与编译是否有收益（GX）无关。
- **决策变更记录**：冻结规格 2.2「不引入 SSE 流」在本规格按需推翻——理由：DEEP 数十秒无进度反馈的体验问题 + SSE 端点断连时上游会 `task.cancel()`（`api/search.py:419-420`），顺带修复冻结规格 4.7 的「客户端超时后成本仍在消耗」问题。MCP tool 保持同步不动。
- `BaseAgenticSearch` 新增可选 `search_stream(request, on_event)`（默认实现抛 `NotImplementedError` → 等价于不支持）；`SirchmunkHttpBackend` 用 `httpx.stream` 消费 SSE 帧：
  - `event: log` → 透传给 `on_event(level, message, timestamp)`；
  - `event: result` → `parse_search_context(data)` 复用（SX1 telemetry 一并生效）；
  - `event: error` → 映射 `AgenticSearchBackendError`；
  - `: heartbeat` 注释行 → 忽略。
- Dashboard `query_playground.py` DEEP 模式经 `SearchService.run_stream` 展示进度（`st.status`），Fast/Normal 不变。
- **注意事项（源码实证）**：
  - 流式端点每请求新建 `AgenticSearch` 实例（`_create_search_instance`），与同步端点的 singleton 不同——每个请求都含组件构造开销；preflight 时须实测该差异。
  - `KnowledgeStorage` 不是单例：每个实例启动自己的 parquet 同步守护线程并注册 atexit 钩子（`storage/knowledge_storage.py:94-95`、`364-388`），而流式端点（`api/search.py:394`）用完实例后不调用 `close()`。每请求新实例意味着线程与写入方可能随请求数累积；SX5.3 须连续发起 ≥10 次流式请求，观察 sidecar 线程数与 parquet 写入是否稳定。**SX5.3 已证实**：每次流式请求泄漏约 10 个线程（DuckDB 工作线程 + parquet 同步线程），见任务表 SX5.3 备注。

---

## 4. 源码实证注意事项

> 全部经 2026-10-13 对 `b314e11` 源码复核；file:line 相对 `sirchmunk/src/sirchmunk/`。

1. **telemetry 是顶层合并非嵌套**：`schema/search_context.py:181-189` 把 `context.telemetry` 逐键 `setdefault` 进 `to_dict()` 顶层；冻结规格 3.2 的 `data.telemetry` 表述错误（已在其 3.2 勘误）。
2. **evolver HTTP 不可达**：`search.py:569-583` 要求 `enable_knowledge_evolution=True` 且 embedding 客户端非空；`api/search.py` 两处实例构造均不传该参。
3. **compile 与 serve 不能并发**：work_path 无文件锁（`search.py:262-263` 按 mtime 判 staleness，Parquet last-writer-wins，冻结规格 4.7）。
4. **singleton 缓存**：`_tree_paths_cache`、`_search_instance`（同步端点）按实例缓存，编译产物对运行中的 sidecar 不可见，须重启。
5. **torch 已在 sidecar venv**：`requirements/core.txt` 含 `sentence-transformers`/`modelscope`/`sentencepiece`；复用的真实代价是模型下载与内存。
6. **SSE 每请求新实例**：`api/search.py` `_create_search_instance`（流式）vs `_get_search_instance`（同步 singleton）——构造副作用与缓存行为不同。
7. **`include_patterns`/`exclude_patterns` 字段名陷阱**（沿冻结规格 3.2，SX2/SX5 透传新参数时同样适用）：FastAPI 对未知字段静默丢弃。
8. **编译覆盖面**：`learnings/compiler.py:381-384` `_ELIGIBLE_EXTENSIONS` 不含 `.py`/`.yaml`；`learnings/tree_indexer.py:26` 树门槛 3,000 字符，`:56-59` `_TREE_EXTENSIONS` 只有文档类。源码问题不受编译影响。
9. **编译文件发现不排除目录**：`compiler.py` `_discover_files` 为 `rglob("*")` + 扩展名过滤，不读 `.gitignore`；CLI 无 exclude 参数（`cli/cli.py:1736-1761`）。范围只能靠显式路径控制。
10. **复用命中无 telemetry 标记，且有软复用**：`search.py:2590-2592`/`3792-3795` 命中即返回，不写标记；`_try_soft_reuse`（`2596`/`3798`）在未命中时仍会改变搜索行为。
11. **`KnowledgeStorage` 非单例**：每实例一个 parquet 同步守护线程 + atexit 钩子（`storage/knowledge_storage.py:364-388`）；外部写入经 mtime 检查重载（`_check_and_reload`），所以单例 sidecar 能看到其他进程写入的 parquet。

---

## 5. 测试方案

### 5.1 离线（默认必须全绿）

| 层 | 覆盖 |
|----|------|
| 单元（SX1） | `parse_search_context`：顶层 telemetry 键白名单收集、`retrieval_logs` 降维计数、键缺失/畸形容忍（恒不抛）、fixture 锁 shape |
| 单元（SX1） | tool trace metadata 含 `agentic_telemetry`；fake backend 注入 |
| 单元（SX2） | `_build_payload` 透传 `max_depth`/`enable_dir_scan`、恒发 `llm_fallback=false`、配置校验 fail-fast |
| 单元（SX3） | compile.sh 停机护栏（探活存活即拒）、路径护栏（`.`/`.venv*`/`.git` 等被拒、未传路径时取 `SIRCHMUNK_COMPILE_PATHS`）、参数拼装（`tmp_path` mock sidecar venv 可执行文件） |
| 单元（SX1） | `zero_llm_path` 派生规则：`llm_call_count == 0` 且 `loop_count == 0` 才为真，键缺失时为 `None` 不猜测 |
| 单元（SX5） | 罐头 SSE 帧解析：log/result/error/heartbeat/断流；`parse_search_context` 复用正确 |
| Dashboard | `search_service` 流式路径 fake 覆盖；页面冒烟沿用 `AppTest.from_file` |

### 5.2 门控真实测试（仅显式开启）

| 门控 | 内容 |
|------|------|
| `RAG_AGENTIC_INTEGRATION=1` | 真实 sidecar：SX1 telemetry 字段实测非空校验；SX3 编译前后 golden 集对照（每题 ×3，复用关闭）；SX4 复用延迟/命中实测 + 复用开/关非命中回归；SX5 SSE 真实流冒烟 + 连续请求线程数观察 |

---

## 6. 配置变更汇总

### 6.1 主项目 `config/settings.yaml`（SX2，可选键）

```yaml
agentic_search:
  # 既有键不变；新增：
  max_depth: null            # 目录扫描深度上限（int 或 null=上游默认）
  enable_dir_scan: true      # DEEP 目录扫描开关
```

### 6.2 Sidecar `.env-sirchmunk`（SX3/SX4，env.example 模板增补）

```bash
# SX3：编译默认路径（compile.sh 未显式传 -- <paths> 时使用；相对仓库根，空格分隔）
# 不要放 "." 或 data/documents（前者会扫进 .venv/build，后者是无关的用户文档）
SIRCHMUNK_COMPILE_PATHS="README.md docs"

# SX4：知识簇复用（默认注释关闭；开启前先跑 preflight 预下载模型）
# SIRCHMUNK_ENABLE_CLUSTER_REUSE=true
# CLUSTER_SIM_THRESHOLD=0.85
# CLUSTER_SIM_TOP_K=3
# EMBEDDING_MODEL_ID=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
# EMBEDDING_CACHE_DIR=${SIRCHMUNK_WORK_PATH}/.cache/models
```

---

## 7. 排期与进度

### 阶段总览

```
SX0（勘误/补题/基线） ──► SX1（telemetry）──┬──► SX2（参数透传，独立）
                                            ├──► SX3（compile 实验，复用关闭）──► GX：是否推荐编译
                                            └──► SX4（复用评估；SX3 采集完成后或独立 work_path）

SX5（SSE，可选期）：门控 = SX0.4 基线显示 DEEP 有实际价值；不依赖 GX
```

SX1 是 SX3/SX4 的前置，因为两者的成本与复用判定都依赖 telemetry。SX4 排在 SX3 之后只是为了实验隔离，不是功能依赖。

### 进度跟踪表 (Progress Tracking)

| 任务 | 内容 | 状态 | 完成日期 | 备注 |
|------|------|------|----------|------|
| SX0.1 | gap analysis 升 v0.2（telemetry/复用成本/演化不可达三处修正；补树门槛与编译覆盖面修正） | [x] | 2026-10-13 | |
| SX0.2 | 冻结规格 3.2/4.1 勘误注记 | [x] | 2026-10-13 | |
| SX0.3 | 新建 `golden_questions_docs.json`（3–4 道文档类题，答案只在 `README.md`/`docs/**/*.md`），与 Q3/Q4 合成编译可覆盖子集；原 `golden_questions.json` 不动 | [x] | 2026-10-13 | D1–D4（来源：MICROSERVICES_ANALYSIS、INGESTION_ROBUSTNESS×2、README，均 >3,000 字符，答案唯一性经 rg 核实）。裸 rg 词法下限 4/4，但每题 rg 命中 184–229/243 个文件，无区分度：编译在本子集只能比成本/精度，不能比命中率。结果存 `scripts/sirchmunk/rg_baseline_docs_results.json`（已在泄题排除 glob 内） |
| SX0.4 | 基线固化：复用关闭、知识簇存储为空，两个 fixture 各跑 FAST/DEEP，每题 ×3，逐题命中/耗时/token 入档 | [x] | 2026-10-13 | 门控 `RAG_AGENTIC_INTEGRATION=1`；泄题排除口径已变更，见下方备注。上游 `b314e11`，`deepseek-v4-flash`，`LENS_HARD_TOKEN_BUDGET` 128k。**协议细化**：源码确认复用关闭时上游仍保存知识簇（原记为 FAST/DEEP Phase 5；**SX4.2 更正**：只有 FAST 保存，DEEP 在 agentic loop 后把 `cluster` 置空，Phase 5 不执行，DEEP 轮次的冷启动因此并非必要，但不影响数据有效性），DEEP Phase 1 `_probe_knowledge_cache` 无 embedding 时以 SQL LIKE 读取，同题 ×3 共用存储会污染后两次；因此每个 (mode, 轮次) 冷启动：清空 `work_path/.cache/{knowledge,spec}` → 启动 sidecar → 10 题 → 停机（`scripts/sirchmunk/baseline_round.sh` + `aggregate_rounds.py`，SX3.2 复用；旧缓存备份于 `data/db/sirchmunk_backup_pre_sx04/`）。compare.py 增 `--json-out`、经 converter 收 telemetry、显式发 `enable_dir_scan`/`llm_fallback=false`。**结果**（次数口径，逐题见 `scripts/sirchmunk/sx04_baseline_results.md`）：FAST 13/30（identifier 2/6、format 4/6、multihop 0/6、doc 7/12），中位耗时 3.5–11.5 s，约 2.5–4.1 万 token/题；DEEP 23/30（identifier 6/6、format 5/6、multihop 3/6、doc 9/12），中位耗时 5.7–19.2 s，1.2–9.5 万 token/题。编译可覆盖子集（Q3/Q4/D1–D4）：FAST 11/18、DEEP 14/18。**SX1 门控校验通过**：60/60 响应 telemetry 非空。观察：①D4 DEEP 0/3 及 Q6 DEEP 第 3 轮均 `hard_budget_exhausted=true` 后返回「No results found」，FAST 反而 D4 2/3；②Q6 DEEP 前两轮返回长答案但 `llm_call_count=0`/`total_llm_tokens=0`/`zero_llm_path=true`、唯一证据为仓库根目录——上游该路径未计 LLM 用量，`zero_llm_path` 不能当作「未调用 LLM」的证据；③跑实验时 sidecar 写 `data/db/sirchmunk`，会让 `test_ingest_cli_subprocess.py` 的 `data/` 不变守卫误报，离线套件须在 sidecar 停机后跑（停机复跑 1674 通过 / 3 跳过） |
| SX1.1 | `AgenticSearchResponse.telemetry` 契约 + converter 白名单解析 + retrieval_logs 降维 | [x] | 2026-10-13 | 上游复核仍为 `b314e11`。`converter._parse_telemetry`：固定键之外只收布尔机制标记（上游 ad-hoc 字段含答案片段等字符串，不进 trace）；`llm_call_count` 改为优先读顶层同名固定键，缺失才回退 `len(llm_usages)`；无任何 telemetry 信号键时为 `None`（只有 `llm_usages` 不算） |
| SX1.2 | tool trace `record_stage` metadata + Dashboard `_render_agentic_meta` 增块 | [x] | 2026-10-13 | `record_stage` 的 `details.agentic_telemetry`；`structuredContent.agentic.telemetry` 同步透出（Dashboard 经此读取）；在线检索页新增「Sidecar telemetry」折叠键值表，附「零 LLM 路径≠复用命中」说明 |
| SX1.3 | 新契约 fixture + 离线单测（键缺失容忍） | [x] | 2026-10-13 | `tests/fixtures/agentic/contract_response_telemetry.json`（按 `to_dict()` 字面量构造，含一个字符串 ad-hoc 键验证排除）；converter/backend/tool/Dashboard 冒烟共 +23 用例，全量离线 1593 通过 / 3 跳过。真实 sidecar 非空校验归 5.2 门控，随 SX0.4 一并执行 |
| SX2.1 | `AgenticSearchConfig` 新键 + settings 解析 + `_build_payload` 透传 + `llm_fallback=false` | [x] | 2026-10-13 | 字段名经上游 `api/search.py` `SearchRequest`（`b314e11`）核对。`AgenticSearchRequest` 增同名请求级覆盖（None = 沿用配置）；`enable_dir_scan` 恒发送，`max_depth` 为 None 时不发送；`config/settings.yaml` 补两键 |
| SX2.2 | 配置校验与 payload 单测 | [x] | 2026-10-13 | 配置非法值 fail-fast（`max_depth` 拒 0/负/字符串/bool/浮点，`enable_dir_scan` 拒非 bool）；显式 `null` 按 `_opt` 既有语义视同缺失走默认；payload 透传/覆盖/字段名/`llm_fallback` 锁定单测 +19，全量离线 1612 通过 / 3 跳过 |
| SX3.1 | `scripts/sirchmunk/compile.sh`（停机护栏 + 路径护栏 + status/lint/shallow/full 模式） | [x] | 2026-10-13 | 参数对照上游 `cli/cli.py` compile 子命令（`b314e11`）：上游 `--paths` 恒必填，`--status`/`--lint` 也透传路径；`--work-path` 显式传入（相对值按仓库根解析）。实现细化：①探活收到任何 HTTP 响应（含 401）即判存活；缺 `curl` 时写入模式 fail-closed；②`--lint --fix` 会写 work_path，按写入模式走停机护栏，只读例外仅 `--status` 与不带 `--fix` 的 `--lint`；③路径护栏另拒与 work_path 互相包含的路径；④`--full` 与 `--shallow` 可组合（供 SX3.3 全量浅编译）。离线单测 42 个（fake venv + PATH 上的 fake curl，系统 bash 3.2 下运行），全量 1654 通过 / 3 跳过 |
| SX3.2 | 编译实验：`--full -- README.md docs` → 重启 sidecar → 按 SX0.4 协议重跑 → 编译成本/逐题结果入档 | [x] | 2026-10-14 | 门控 `RAG_AGENTIC_INTEGRATION=1`。**环境缺陷先修**：首次编译 15/15 文件失败（`OutputFormat has no attribute 'PLAIN'`）——sidecar venv 经 `--pre` 回退装到 kreuzberg 5.0.0rc3，5.x 枚举成员改小写，上游 `document_extractor.py:559` 仍用 `OutputFormat.PLAIN`；降到 4.10.4 并在 `setup_venv.sh` 锁 `kreuzberg>=4.0.0,<5`（SX0.4 检索路径未触发该代码，日志 0 次，基线不受影响）。**编译成本**：15 文件 / 约 580 KB，36.4 s，trees=13、clusters created=13/merged=2、errors=0；上游编译报告不含 token 用量，未计量。编译另会下载 embedding 模型（约 470 MB，首次）。**协议细化**：编译把知识簇写入 knowledge 存储（`learnings/compiler.py:1386`），故编译后每轮改为从编译完成时的 knowledge 快照恢复（`baseline_round.sh` 的 `BASELINE_KNOWLEDGE_SNAPSHOT`），而非清空。DEEP r2/r3 首跑遇机器睡眠（墙钟 ≫ perf_counter、`APITimeoutError`）整轮作废，`caffeinate -dims` 重跑。**结果**（`scripts/sirchmunk/sx32_compiled_results.md`）：编译可覆盖子集 FAST 11/18 → 13/18（Q4/D1/D3 升、Q3/D2 降，非 DocQA 平均 token 35.7k → 11.4k）、DEEP 14/18 → 7/18（去掉 DocQA 短路仍 7/13）；`.py` 题 FAST 2/12 → 0/12、DEEP 9/12 → 7/12。DEEP 的上游 DocQA 短路（`_try_direct_doc_analysis`：LLM 意图分类后只读仓库根 4 个文件作答，证据为根目录、token 记 0）由基线 2/30 次增至 5/30 次（编译可覆盖子集 0 → 5），触发条件只看查询本身，与编译的关系未定位。耗时不参与对照：两次实验跑在不同时段，FAST token 降的同时耗时升 2–3 倍，主要是 API 负载差异。编译产物已移出 work_path（备份 `data/db/sirchmunk_backup_sx32_compiled/`），sidecar 恢复未编译状态 |
| SX3.3 | `--shallow` 对照（仅当 SX3.2 有正收益）：量化树索引贡献与成本差 | [x] | 2026-10-14 | **不执行（按前置条件关闭）**：SX3.2 无正收益（GX 不通过），按本行前置条件跳过 |
| SX3.4 | 门控 GX 裁决：USAGE_GUIDE 编译推荐 or 负结果记录（含 `.py` 题回归结论） | [x] | 2026-10-14 | **GX 不通过，记负结果**：编译可覆盖子集未「多数题稳定改善」（FAST 逐题升 3 降 2，DEEP 显著回退），`.py` 题两种模式均回退。唯一正面信号是 FAST 文档题 token 降约 68%，不足以抵消回退。USAGE_GUIDE 新增 7.3「知识编译：当前不推荐」（含恢复未编译状态的方法）与 kreuzberg 5.x 排障行。样本小（每题 ×3），结论为定性裁决 |
| SX4.1 | env.example 复用段 + preflight 预下载步骤 | [x] | 2026-10-13 | 源码复核（`b314e11`）：`api/search.py:155/198` 对未设置的 `SIRCHMUNK_ENABLE_CLUSTER_REUSE` 按 `"true"` 处理，所以模板保留生效的 `=false` 行，复用旋钮以注释形式补在其后（与 6.2 示意的「整段注释」不同：注释掉 `=false` 等于开启）。preflight 新增第 4 步：按 `start_sidecar.sh` 同口径 source env 文件，复用开启（含未设置）时经 sidecar venv 调上游 `EmbeddingUtil.preload_model`，缓存目录与 `AgenticSearch` 解析一致（`EMBEDDING_CACHE_DIR` 优先，否则 `work_path/.cache/models`）；`SIRCHMUNK_SKIP_MODEL_PRELOAD=1` 可跳过。顺带修复 preflight 既有缺陷：未配置 token 时 bash 3.2 下 `set -u` 展开空数组直接报错退出。USAGE_GUIDE 4.3 同步改正「跳过 torch 下载」旧表述。离线单测 16 个，全量 1670 通过 / 3 跳过 |
| SX4.2 | 复用评估：清空知识簇存储后，重复/改写查询延迟与命中、误复用与陈旧结果检查、复用开/关非命中回归 | [x] | 2026-10-14 | 门控，上游 `b314e11`，`deepseek-v4-flash`，阈值 0.85 / top-3（上游默认）。逐请求复用判定靠 sidecar 日志：新增 `scripts/sirchmunk/reuse_log_markers.py`（按 `POST /api/v1/search` 访问行切分日志，提取 `Found similar cluster`/`[FAST] Reused`/`No similar clusters found`/DocQA 标记，与 compare.py 结果按 id 对齐）；`baseline_round.sh` 增 `BASELINE_EXPECT_REUSE`（env 守卫翻转为要求开启）与 `BASELINE_FIXTURES`；探针题 `tests/fixtures/agentic/reuse_probe_questions.json`（3 组 × seed/repeat/reword/distractor）。**源码复核**：`_try_reuse_cluster` 只校验内容非空、非拒答，无 mtime/hash 过期检查；**只有 FAST 保存簇**（DEEP agentic loop 后 `cluster = None`，Phase 5 不执行；据此更正 SX0.4 备注）；FAST 保存前有 LLM 质量闸门，正确答案也可能被拒存。**结果**（逐题见 `scripts/sirchmunk/sx42_reuse_results.md`）：①FAST 原题重问 3/3 命中（相似度 1.0、约 30 ms、0 token、答案正确）；改写 0/3、干扰题 0/3（未误复用）；DEEP 任何探针 0 命中（不存簇）。②陈旧检查：文件端口 7311 改 7422 后，FAST（51 ms、0 token）与 DEEP（1.3 s、0 token，复用 FAST 的簇）都答旧值 7311；首次 seed 正确答案被质量闸门拒存，第二次才入库。③非命中回归（SX0.4 协议 6 轮）：FAST 13/30 → 8/30，DEEP 23/30 → 18/30，0 错误；但 60 次请求硬复用 0 次、软复用 0 次、DocQA 短路次数与基线同量级，排查的其他 embedding 路径均未触发，DEEP D4 0/3→2/3 与 Q4 2/3→0/3 方向相反——差值归为 n=3 下的 LLM 跑间方差（±5/30），SX3.2 的小幅差值同样应按此打折。**结论**：复用默认保持关闭；只在语料稳定、FAST 高频原题重问的场景有收益，USAGE_GUIDE 4.3 已补开启须知（改写不命中、DEEP 只复用 FAST 簇、改文件须手动清 `.cache/knowledge/`） |
| SX5.1 | `search_stream` 基类可选方法 + HTTP 后端 SSE 解析 | [x] | 2026-10-14 | 启动门控满足：SX0.4 中 DEEP 命中 FAST 未命中的题（Q2/Q5 3/3 vs 0/3、D1 3/3 vs 0/3）。`BaseAgenticSearch.search_stream` 可选方法（默认抛 `NotImplementedError`）；`SirchmunkHttpBackend.search_stream` 消费 `/api/v1/search/stream`（帧格式按 `b314e11` `api/search.py:255-258,380-437` 实证）：log→`on_event`（上游 LLM 输出逐 token `flush=True` 发帧，按行合并后再回调）、result→复用 `parse_search_context`（SX1 telemetry 透传）、error 帧或无 result 断流→`AgenticSearchBackendError`、heartbeat 忽略；payload 复用 `_build_payload`，鉴权/错误映射同同步路径；另加 `timeout_s` 总时限（每 0.5 s 一次 heartbeat 使 httpx 读超时失效，断连后上游 `task.cancel()`）。Fake 后端回放罐头日志。`test_agentic_backend_stream.py` 23 例 |
| SX5.2 | Dashboard DEEP 进度（`st.status`）+ 罐头 SSE 离线测试 | [x] | 2026-10-14 | `SearchService.run_stream` 仅 DEEP 走流式：另起一次性 `AgenticSearchTool`，后端包成流式适配器，tool 的参数校验/PathGuard 双层防线/trace/isError 原样复用（MCP tool 未改动，代价是服务层调用 tool 的三个私有 getter）；后端 `NotImplementedError` 或注入的不是真实 tool 时回退 `run`。`query_playground` DEEP 用 `st.status` 实时追加进度日志（尾部 30 行、单行截断 300 字，DEEP 日志含 LLM 全文），Normal/Fast 不变。服务层 +6 例、Dashboard 冒烟 +3 例（含回退）；USAGE_GUIDE 2.7 同步 |
| SX5.3 | 真实流冒烟 + 每请求新实例构造开销 + 连续 ≥10 次请求线程数观察 | [x] | 2026-10-14 | 门控，上游 `b314e11`，复用关闭，单文件临时语料（控制 token）。经主项目 `SirchmunkHttpBackend.search_stream` 真实调用：3 次同步 FAST → 2 次流式 DEEP → **12 次连续流式 FAST** → 3 次同步 FAST，逐请求记录耗时、首事件时延、事件数、sidecar 线程数（`ps -M`）与打开文件数（`lsof`），结束后空闲 15 s 复查（数据见 `scripts/sirchmunk/sx53_stream_smoke_results.json`）。**结果**：20/20 成功、答案全对、telemetry 全部非空；流式首事件中位 14.5 ms，每请求 26–39 条 log 事件；流式 FAST 中位 3341 ms vs 同步 3250 ms，每请求新建实例的构造开销约 90 ms，在跑间波动内。**线程泄漏（证实 3.6 风险）**：同步请求线程数不变（58→59→59，尾段 199→199→199），每次流式请求稳定 +10（DEEP/FAST 一样，89→199），空闲 15 s 后仍 197，不回收；打开文件数稳定在 153。根因：每个流式请求新建 `KnowledgeStorage`，各开一个 DuckDB 连接（本机 `threads=10`，离线实测每连接 9 个 OS 线程，`close()` 后释放）+ 1 个 parquet 同步线程；流式端点不调 `close()`，atexit 钩子又持有实例引用，GC 无法回收。线程数随 CPU 核数和请求数线性增长，属上游缺陷，按进程隔离红线不改上游；USAGE_GUIDE 已注明大量使用 Dashboard Deep 后重启 sidecar，MCP tool 走同步端点不受影响。上游修复方向：`_run_search` 结束时关闭 searcher 的 `KnowledgeStorage` |

> **泄题排除口径变更（2026-10-13）**：`compare.py` 原先只排除 `tests/fixtures/**`，但入库的 `scripts/sirchmunk/rg_baseline_results.json` 含 6 道题原文与 `expected_sources`，仍在检索范围内（实测 `rg -l "IDF formula implemented" -g '!tests/fixtures/**'` 命中该文件）。现 `compare.py` 的 `EXCLUDE_PATTERNS` 与 `rg_baseline.py` 的 `EXCLUDE_GLOBS` 都新增了 `scripts/sirchmunk/*results*`。该文件提交于 G0 的 DEEP 单跑之前，那一轮可能受泄题影响（只会抬高 sidecar 命中，G0 不通过的结论不变，见冻结规格第 7 章勘误）；SX0.4 起的结果与 G0 旧数字口径不同，对比时须注明。裸 rg 基线在新口径下复跑仍为 6/6（这个文件只影响题面检索，不改变期望来源是否在 rg 文件列表中）。
>
> 纪律：任务全收口后本规格冻结入 `docs/frozen/`；执行中发现的新需求另走 backlog 提案，不在本表新增后改前缀编号。
