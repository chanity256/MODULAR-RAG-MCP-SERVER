# Sirchmunk 能力覆盖差距分析

> 版本：0.3 — v0.3 按 [SIRCHMUNK_EXTENSION_SPEC（SX 系列）](../frozen/05_SIRCHMUNK_EXTENSION_SPEC_frozen.md) 收口结果（SX0–SX5 于 2026-10-14 全部冻结）更新各能力的现状，并补充第 4 节编译实验结论；v0.2 经源码复核修正第 3/4/5 节的判断（复用成本、演化可达性、telemetry 形态、树索引门槛与编译覆盖面）
>
> 基线：sirchmunk 源码锁定在 commit `b314e11`（Merge pull request #184 from modelscope/feat/search_enhance）；v0.1/v0.2 的分析起点是 SIRCHMUNK_INTEGRATION_SPEC S0–S4 收口状态（commit `690a205`），v0.3 现状以 SX 系列冻结后的分支 `feat/sirchmunk-agentic-search` 为准。
>
> 性质：与 [MICROSERVICES_ANALYSIS.md](MICROSERVICES_ANALYSIS.md) 同类，属架构/能力判断文档，不承诺交付。遗留缺陷与候选方向统一记在 [backlog/sirchmunk-extension-followups_backlog.md](../backlog/sirchmunk-extension-followups_backlog.md)；推进其中任何一项，走 [docs/backlog/](../backlog/) 提案流程。

---

## 目录

- [1. 前提澄清：没有"迁移"，只有 HTTP 接入](#1-前提澄清没有迁移只有-http-接入)
- [2. 已接入的能力](#2-已接入的能力)
- [3. 能力覆盖对照](#3-能力覆盖对照)
- [4. 对 G0 结论的影响](#4-对-g0-结论的影响)
- [5. 文档与代码不一致：telemetry（已修正）](#5-文档与代码不一致telemetry已修正)
- [6. 后续方向](#6-后续方向)
- [7. 一句话总结](#7-一句话总结)

---

## 1. 前提澄清：没有"迁移"，只有 HTTP 接入

本项目按设计**没有迁移任何 sirchmunk 代码**。主 venv 从不 `import sirchmunk`（进程隔离红线，见 SIRCHMUNK_INTEGRATION_SPEC 第 4 章），只通过 `src/libs/agentic_search/http_backend.py` 以 HTTP 调用独立 sidecar 进程。v0.3 起用到两个端点，另有一个 CLI 包装：

```
POST /api/v1/search           （同步；Bearer 鉴权 + response_format=context；MCP tool 与默认路径）
POST /api/v1/search/stream    （SSE；SX5 新用，仅 Dashboard DEEP 进度与可选的 Chat 进度流）
scripts/sirchmunk/compile.sh  （SX3；sidecar 停机时调上游 CLI `sirchmunk compile`，无 HTTP 端点）
```

因此，准确的问题是：**sirchmunk 的哪些能力能通过这些入口用到？哪些用不到或被关闭了？**

sirchmunk 上游约 4.9 万行 Python，核心由两部分组成：

1. **无索引检索**（`search.py`，1.3 万行：FAST/DEEP 模式、多路召回、ReAct 迭代）；
2. **知识编译与学习**（`learnings/` 模块，编译期建索引 + 运行期知识演化，上游自称融合 PageIndex / LLM Wiki / NotebookLM 三家思路）。

---

## 2. 已接入的能力

### 2.1 调用侧（消费 sirchmunk）

`src/libs/agentic_search/http_backend.py` + `converter.py`：

- **模式**：FAST（贪心早停，0–2 次 LLM 调用）、DEEP（多路并行召回 + ReAct 精炼，4–6 次 LLM 调用）。DEEP 内部的多路召回、零命中救援、置信度融合等对我们是黑盒，但只要走该端点就会执行。
- **透传的请求参数**：`query`、`mode`、`paths`、`top_k_files`、`max_loops`、`max_token_budget`、`include_patterns`、`exclude_patterns`，恒定 `response_format=context`；**SX2 新增** `max_depth`（为 None 时不发送）、`enable_dir_scan`（恒发送），`llm_fallback` 恒发 `false` 显式锁定。
- **解析的响应字段**：`answer`、`cluster.evidences[]`（`file_or_url` + `snippets`，无 snippets 时降级用 `summary`）、`cluster.search_results`（引用文件列表）、`cluster.confidence`、`total_llm_tokens`；**SX1 新增** `data` 顶层 telemetry 键（白名单收集 `loop_count`/`budget_remaining`/布尔机制标记等，`retrieval_logs` 降维为计数；`llm_call_count` 优先读顶层固定键，缺失才回退 `len(llm_usages)`）。
- **流式（SX5）**：`search_stream` 消费 SSE，log 帧按行合并后回调，result 帧复用 `parse_search_context`，error 帧或无 result 断流映射为后端错误，另设 `timeout_s` 总时限。

### 2.2 本项目自实现的部分（sirchmunk 无对应代码）

- PathGuard 双层路径防护（`src/libs/agentic_search/path_guard.py`）；
- 触发策略（`src/core/query_engine/agentic_trigger.py`）与结果去重/上下文扩展（`agentic_dedup.py`）；
- 证据 → `RetrievalResult` 转换，供 ResponseBuilder/Citation 复用与 N 路 RRF 融合；
- `query.py --agentic`、评估 A/B 对照、Dashboard 与 Web UI 三模式查询页面、Web UI Chat 的 `agentic_fast`/`agentic_deep` 检索模式；
- 启动器托管 sidecar（`scripts/start_dashboard.py` 自动拉起/回收）；
- **SX 系列新增**：telemetry 进 trace（`details.agentic_telemetry`）与 Dashboard「Sidecar telemetry」面板；`compile.sh` 停机护栏与路径护栏；preflight 复用模型预下载；评估工具链（`baseline_round.sh`、`aggregate_rounds.py`、`reuse_log_markers.py`、`compare.py --json-out`）。

---

## 3. 能力覆盖对照

v0.2 本节标题为「未接入的能力」。v0.3 按 SX 收口结果把「现状」列更新为当前状态，并新增「结论」列：**已接入**、**已接入·默认关闭**（能力可用，实验裁决不推荐默认开）、**不做**。

| # | sirchmunk 能力 | 上游源码位置 | 现状（v0.3） | 结论 |
|---|----------------|--------------|--------------|------|
| 1 | **知识编译**（`sirchmunk compile`）：文档类文件建树索引（tree_indexer，门槛 ≥3,000 字符）、文件级摘要索引（summary_index）、跨文档主题图（corpus_topic_map）、增量清单与健康检查（lint） | `learnings/compiler.py`、`tree_indexer.py`、`summary_index.py`、`corpus_topic_map.py`、`lint.py`；入口 `search.py:2153` | **SX3**：`scripts/sirchmunk/compile.sh` 包装上游 CLI（status/lint/shallow/full 模式；sidecar 存活即拒写入，拒 `.`/`.venv*`/`.git` 及与 work_path 互含的路径）。编译实验 GX **不通过**：编译可覆盖子集 DEEP 14/18 → 7/18，`.py` 题两种模式均回退；唯一正面信号是 FAST 文档题 token 约 -68%。USAGE_GUIDE 7.3 写明「当前不推荐」，编译产物已移出 work_path | 已接入·不推荐 |
| 2 | **知识簇复用**：相似查询（embedding 相似度 ≥0.85）直接复用历史簇结果 | `storage/knowledge_storage.py`；FAST 第 0 步 / DEEP 第 0 阶段 | **SX4**：env 模板保留生效的 `SIRCHMUNK_ENABLE_CLUSTER_REUSE=false`（上游对未设置按 `true` 处理），开启须先跑 preflight 预下载模型。实测只有 FAST 原题逐字重问命中（3/3，约 30 ms、0 token），改写 0/3，DEEP 不存簇；复用无过期检查，改文件后仍答旧值。USAGE_GUIDE 4.3 写明开启须知 | 已接入·默认关闭 |
| 3 | **知识演化**：搜索后在后台连接、合并、刷新知识簇 | `scheduler/evolver.py` | HTTP 与 CLI 均不可达：`AgenticSearch(enable_knowledge_evolution=False)` 为默认，`api/search.py` 与 CLI 均不传 `True`，开启必须改上游源码 | 不做 |
| 4 | **流式检索** `POST /api/v1/search/stream`（SSE 实时日志） | `api/search.py` | **SX5**：`SirchmunkHttpBackend.search_stream` 已实现。接入面：Streamlit Dashboard DEEP 用 `st.status` 展示进度；Web UI Chat 经 `chat.agentic_progress_stream` 可选开启（默认 `false`）；Web UI 搜索页（`/api/search`）与 MCP tool 仍走同步端点。SX5.3 实证上游流式端点每请求泄漏约 10 个线程，这是接入面保持收窄的原因 | 已接入（部分入口） |
| 5 | **请求参数**：`max_depth`、`enable_dir_scan`、`llm_fallback` | `api/search.py` 的 `SearchRequest` | **SX2**：`max_depth`/`enable_dir_scan` 已透传（配置键 + 请求级覆盖，非法值 fail-fast）；`llm_fallback` 恒发 `false`（与「引用必须可追溯」冲突） | 已接入（`llm_fallback` 锁定为关） |
| 6 | FILENAME_ONLY 模式（不调 LLM，按文件名找文件） | `search.py` | 规格 2.2 明确不对外暴露 | 不做 |
| 7 | **响应字段**：telemetry（合并于 `data` 顶层，不存在 `data.telemetry` 嵌套键）、snippet 的 `reasoning`、有 snippets 时的证据 `summary`、`cluster.content` 等 | `schema/search_context.py:163-189`、`schema/knowledge.py` | **SX1**：telemetry 白名单解析，进 trace 与 Dashboard；上游 ad-hoc 字符串字段（可能含答案片段）有意排除。`reasoning`、有 snippets 时的 `summary`、`cluster.content` 仍未读取，目前没有消费方 | telemetry 已接入；其余字段不读 |
| 8 | 多轮对话（WebSocket `/chat`）、知识簇浏览（`/knowledge/*`）、会话历史、监控、文件上传、WebUI | `api/chat.py`、`knowledge.py`、`history.py`、`monitor.py`、`files.py` | 未使用。本项目 Web UI（WC 系列）自建了多轮 Chat 与 Dashboard 职能 | 不做 |

**MCP 工具层没有遗漏**：上游官方 MCP server（`sirchmunk_mcp/server.py`）实际只注册 `sirchmunk_search` 一个工具；`sirchmunk_get_cluster` / `sirchmunk_list_clusters` 在上游已被注释掉（仅供内部 service 层使用）。

### 3.1 仍存在的缺口（v0.3 新增）

能力层面已没有「能接却没接」的项。剩下的缺口都受上游缺陷或红线约束，详情与推进路径见 [followups 台账](../backlog/sirchmunk-extension-followups_backlog.md) 第 3 节：

1. **流式端点线程泄漏**（上游缺陷）：每次流式请求泄漏约 10 个线程且不回收。规避方式是重启 sidecar；根治只能向上游提 PR。
2. **Web UI 搜索页没有 DEEP 进度流**：`src/api/routes/search.py` 只调同步 `SearchService.run`，`run_stream` 只接到 Streamlit 页面。受第 1 条约束，目前不接。
3. **复用命中不可观测、复用无过期机制、存簇质量闸门可能拒存正确答案**：均为上游行为。
4. **DocQA 短路异常**：DEEP 的 `_try_direct_doc_analysis` 记 0 token，证据为仓库根目录，与真实「零 LLM」路径混淆，触发条件未定位。
5. **样本小**：每题 ×3 协议下跑间方差约 ±5/30，SX3/SX4 的结论只能定性。

---

## 4. 对 G0 结论的影响

G0 三方对照（裸 rg 6/6，FAST 2/6，DEEP 多跳题 1/2）的测量配置是：**无编译、无知识簇复用、无知识演化，每次搜索都是冷启动**。

也就是说，G0 只验证了 sirchmunk 两大核心中的前一半（无索引检索），后一半（编译与学习）完全没有参与。因此更精确的结论是：

> **"冷启动、未编译的 sirchmunk，在本仓库小语料上，不如裸 rg。"**

这不能推广为"sirchmunk 整体没有价值"。但也要注意限制编译收益的条件（**v0.2 修正**，v0.1 误写为「树索引只对 ≥50KB 大文档生效」）：

- 编译**完全不处理源码**：`learnings/compiler.py` 的 `_ELIGIBLE_EXTENSIONS` 只含 pdf/docx/doc/md/markdown/html/htm/rst/tex/txt/pptx/xlsx，不含 `.py`。G0 的 6 题中 4 题（含 DEEP 多跳题 Q5/Q6）答案在 `.py` 里，编译对它们没有直接帮助；
- 对文档类文件，树索引门槛是 **3,000 字符**（`tree_indexer.py` `_TREE_MIN_CHARS`），本仓库多数 Markdown 规格会建树，所以编译对文档类问题仍有机会；
- 编译需对每个文件调用 LLM，是一笔前置成本。

**v0.3 实测结论**（SX0.4 基线 + SX3.2 编译实验，逐题数据见 `scripts/sirchmunk/sx04_baseline_results.md`、`sx32_compiled_results.md`）：

- 新口径基线（复用关闭、每轮冷启动、每题 ×3）：FAST 13/30，DEEP 23/30。DEEP 命中了 FAST 未命中的题（Q2/Q5、D1），说明 DEEP 本身有使用价值，这也是 SX5 的启动依据。泄题排除口径在 SX0.4 有变更，新数字不能与 G0 旧数字直接对比。
- 在编译可覆盖子集（Q3/Q4/D1–D4）上编译**没有带来稳定改善**：FAST 11/18 → 13/18（逐题升 3 降 2），DEEP 14/18 → 7/18；`.py` 题也回退。门控 GX 判为不通过。
- 补出的「后一半」中，编译与复用都已实测，均不值得默认开启；演化不可达。所以 G0 的悲观结论目前扩展为：**在本仓库小语料上，开启编译或复用也没有让 sirchmunk 稳定超过冷启动配置。**更大语料或不同场景需新证据才能重开。

---

## 5. 文档与代码不一致：telemetry（已修正）

v0.1 曾按冻结规格 3.2 认为存在 `data.telemetry` 字段未被消费。**v0.2 源码复核**：上游根本没有 `data.telemetry` 键——`SearchContext.to_dict()`（`schema/search_context.py:163-189`）把 pipeline 各阶段挂到 `context.telemetry` 上的动态键**逐键 `setdefault` 合并进 `to_dict()` 的顶层**（固定字段同名时固定字段胜出）。

**v0.3 状态**：两处偏差均已修正。

1. 冻结规格 3.2 的 `data.telemetry` 表述已加勘误注记（SX0.2）；
2. SX1 在 `converter._parse_telemetry` 中按白名单读取顶层键，写入 `AgenticSearchResponse.telemetry`、tool trace `details.agentic_telemetry` 与 `structuredContent.agentic.telemetry`。SX0.4 门控校验 60/60 响应 telemetry 非空。

注意：`zero_llm_path=true` 不等于复用命中，DocQA 短路和闲聊短路同样为真（见 3.1 第 3、4 条）。

---

## 6. 后续方向

v0.2 列出的四个候选方向已于 2026-10-13 立项为 SX 系列（决策依据见 [frozen/sirchmunk-capability-completion_backlog_frozen.md](../frozen/sirchmunk-capability-completion_backlog_frozen.md)），并于 2026-10-14 全部收口：

| 原候选方向 | 对应任务 | 结果 |
|-----------|----------|------|
| sidecar 侧跑 `sirchmunk compile` 并评估 | SX3 | 工具已交付；GX 不通过，记负结果，不推荐编译 |
| 补齐 telemetry 解析 | SX1 | 已交付 |
| 评估开启知识簇复用的代价 | SX4 | 开关与 preflight 已交付；收益面过窄，默认保持关闭 |
| 流式检索 | SX5 | 已交付（Dashboard DEEP、可选 Chat 进度流）；发现上游线程泄漏 |

当前唯一可行动的候选项是**向上游提 PR 修复流式端点线程泄漏**，仅在 Dashboard/Web UI 的 DEEP 成为日常路径时才值得做，决策三问见 [followups 台账](../backlog/sirchmunk-extension-followups_backlog.md) 第 4 节。其余缺口保留在候选池，不进入排期。

---

## 7. 一句话总结

经 HTTP 能用到的 sirchmunk 能力已基本接齐：无索引检索、参数透传、telemetry、SSE 流式都已接入，编译与知识簇复用也已可用，但实验裁决不推荐默认开启；知识演化在架构上不可达。剩下的缺口来自上游缺陷（流式线程泄漏、复用无过期等），按不改上游的红线只做规避。
