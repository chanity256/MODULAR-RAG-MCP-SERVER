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
