# Sirchmunk 能力覆盖差距分析

> 版本：0.1 — 基于源码对照的能力覆盖判断（非排期文档）
>
> 基线：sirchmunk 源码锁定在 commit `b314e11`（Merge pull request #184 from modelscope/feat/search_enhance）；本项目处于 SIRCHMUNK_INTEGRATION_SPEC S0–S4 已交付收口状态（分支 `feat/sirchmunk-agentic-search`，commit `690a205`）。
>
> 性质：与 [MICROSERVICES_ANALYSIS.md](MICROSERVICES_ANALYSIS.md) 同类，属架构/能力判断文档，不承诺交付。若要推进其中任何一项，走 [docs/backlog/](../backlog/) 提案流程。

---

## 目录

- [1. 前提澄清：没有"迁移"，只有 HTTP 接入](#1-前提澄清没有迁移只有-http-接入)
- [2. 已接入的能力](#2-已接入的能力)
- [3. 未接入的能力](#3-未接入的能力)
- [4. 对 G0 结论的影响](#4-对-g0-结论的影响)
- [5. 文档与代码不一致：telemetry](#5-文档与代码不一致telemetry)
- [6. 后续候选方向（按性价比排序）](#6-后续候选方向按性价比排序)
- [7. 一句话总结](#7-一句话总结)

---

## 1. 前提澄清：没有"迁移"，只有 HTTP 接入

本项目按设计**没有迁移任何 sirchmunk 代码**。主 venv 从不 `import sirchmunk`（进程隔离红线，见 SIRCHMUNK_INTEGRATION_SPEC 第 4 章），只通过 `src/libs/agentic_search/http_backend.py` 以 HTTP 调用独立 sidecar 进程的单一端点：

```
POST /api/v1/search    （Bearer 鉴权 + response_format=context）
```

因此，准确的问题是：**sirchmunk 的哪些能力能通过这一个端点用到？哪些用不到或被关闭了？**

sirchmunk 上游约 4.9 万行 Python，核心由两部分组成：

1. **无索引检索**（`search.py`，1.3 万行：FAST/DEEP 模式、多路召回、ReAct 迭代）；
2. **知识编译与学习**（`learnings/` 模块，编译期建索引 + 运行期知识演化，上游自称融合 PageIndex / LLM Wiki / NotebookLM 三家思路）。

---

## 2. 已接入的能力

### 2.1 调用侧（消费 sirchmunk）

`src/libs/agentic_search/http_backend.py` + `converter.py`：

- **模式**：FAST（贪心早停，0–2 次 LLM 调用）、DEEP（多路并行召回 + ReAct 精炼，4–6 次 LLM 调用）。DEEP 内部的多路召回、零命中救援、置信度融合等对我们是黑盒，但只要走该端点就会执行。
- **透传的请求参数**：`query`、`mode`、`paths`、`top_k_files`、`max_loops`、`max_token_budget`、`include_patterns`、`exclude_patterns`，恒定 `response_format=context`。
- **解析的响应字段**：`answer`、`cluster.evidences[]`（`file_or_url` + `snippets`，无 snippets 时降级用 `summary`）、`cluster.search_results`（引用文件列表）、`cluster.confidence`、`llm_usages` 条数、`total_llm_tokens`。

### 2.2 本项目自实现的部分（sirchmunk 无对应代码）

- PathGuard 双层路径防护（`src/libs/agentic_search/path_guard.py`）；
- 触发策略（`src/core/query_engine/agentic_trigger.py`）与结果去重/上下文扩展（`agentic_dedup.py`）；
- 证据 → `RetrievalResult` 转换，供 ResponseBuilder/Citation 复用与 N 路 RRF 融合；
- `query.py --agentic`、评估 A/B 对照、Dashboard 三模式查询页面；
- 启动器托管 sidecar（`scripts/start_dashboard.py` 自动拉起/回收）。

---

## 3. 未接入的能力

按重要性排序：

| # | sirchmunk 能力 | 上游源码位置 | 现状 | 重要性 |
|---|----------------|--------------|------|--------|
| 1 | **知识编译**（`sirchmunk compile`）：大文档（≥50KB）建树索引（tree_indexer）、文件级摘要索引（summary_index，embedding+BM25 兜底）、跨文档主题图（corpus_topic_map）、增量清单与健康检查（lint） | `learnings/compiler.py`（2790 行）、`tree_indexer.py`（1993 行）、`summary_index.py`、`corpus_topic_map.py`、`lint.py`；入口 `search.py:2153` | **完全未用**。本项目 src/scripts 均无调用；HTTP API 也没有编译端点，只能用上游 CLI 或 SDK 执行 | **高**。编译产物会被 DEEP 自动消费：树导航收窄证据区间、主题图发现相关文件、摘要索引在 rga 零命中时兜底。这是 sirchmunk 的另一半核心 |
| 2 | **知识簇复用**：相似查询（embedding 相似度 ≥0.85）直接复用历史簇结果，秒回且零 LLM 成本 | `storage/knowledge_storage.py`；FAST 第 0 步 / DEEP 第 0 阶段 | **被主动关闭**（`scripts/sirchmunk/env.example` 中 `SIRCHMUNK_ENABLE_CLUSTER_REUSE=false`，为避免 sidecar venv 安装 torch + embedding 模型） | 中高 |
| 3 | **知识演化**：搜索后在后台连接、合并、刷新知识簇（"越用越好"） | `scheduler/evolver.py`（876 行） | **随 #2 失效**。`search.py:571` 要求 embedding 客户端非空才启用 evolver | 中 |
| 4 | **流式检索** `POST /api/v1/search/stream`（SSE 实时日志） | `api/search.py` | 规格明确不做 | 中。DEEP 模式数十秒无进度反馈，Dashboard 体验受限 |
| 5 | **未透传的请求参数**：`max_depth`、`enable_dir_scan`、`llm_fallback` | `api/search.py` 的 `SearchRequest` | 均用上游默认值 | 低–中。`llm_fallback`（无文档时让 LLM 凭自身知识回答）与本项目"引用必须可追溯"原则冲突，不接是对的 |
| 6 | FILENAME_ONLY 模式（不调 LLM，按文件名找文件） | `search.py` | 规格 2.2 明确不对外暴露 | 低 |
| 7 | **响应中被丢弃的字段**：`telemetry`（含 `budget_remaining`、各阶段信息）、snippet 的 `reasoning`、有 snippets 时的证据 `summary`、`cluster.content` 等 | `schema/search_context.py`、`schema/knowledge.py` | `converter.py` 未读取 | 低–中。详见第 5 节 |
| 8 | 多轮对话（WebSocket `/chat` + `chat_history` 参数）、知识簇浏览接口（`/knowledge/*`：簇列表/图谱/统计）、会话历史、监控、文件上传、WebUI | `api/chat.py`、`knowledge.py`、`history.py`、`monitor.py`、`files.py` | 未使用 | 低。属 sirchmunk 自身产品层，Dashboard 已有对应职能 |

**MCP 工具层没有遗漏**：上游官方 MCP server（`sirchmunk_mcp/server.py`）实际只注册 `sirchmunk_search` 一个工具；`sirchmunk_get_cluster` / `sirchmunk_list_clusters` 在上游已被注释掉（仅供内部 service 层使用）。

---

## 4. 对 G0 结论的影响

G0 三方对照（裸 rg 6/6，FAST 2/6，DEEP 多跳题 1/2）的测量配置是：**无编译、无知识簇复用、无知识演化，每次搜索都是冷启动**。

也就是说，G0 只验证了 sirchmunk 两大核心中的前一半（无索引检索），后一半（编译与学习）完全没有参与。因此更精确的结论是：

> **"冷启动、未编译的 sirchmunk，在本仓库小语料上，不如裸 rg。"**

这不能推广为"sirchmunk 整体没有价值"。但也要注意两个限制编译收益的条件：

- 树索引只对 **≥50KB** 的大文档生效，本仓库大多数是小体量的代码与 Markdown 文件；
- 编译需对每个文件调用 LLM，是一笔前置成本。

所以在当前语料上开启编译后能否超过 rg 基线，**仍需实测判断**。

---

## 5. 文档与代码不一致：telemetry

SIRCHMUNK_INTEGRATION_SPEC 3.2 写明会消费响应中的 `data.telemetry`，但 `src/libs/agentic_search/converter.py` 的 `parse_search_context()` 实际**没有读取** telemetry 字段，trace 中也没有相应打点。

属规格与实现的偏差，量小（纯解析 + 打点），修正时应同步勘误规格描述或补齐实现（以 backlog 提案定夺）。

---

## 6. 后续候选方向（按性价比排序）

以下仅为候选，**不构成排期**；推进任何一项均走 `docs/backlog/` 提案 → 立项后在 `docs/specs/` 建独立规格（新任务前缀），不在已冻结的 S 系列中追加任务。

1. **sidecar 侧跑 `sirchmunk compile`，重跑 DEEP 的 Q5/Q6**。进程隔离不受影响（编译在 sidecar venv 内执行 CLI，主项目代码零改动），是检验"编译能否翻盘"的最小成本实验。
2. **补齐 `telemetry` 解析**：至少写入 trace，并修正规格 3.2 与代码的不一致。改动很小。
3. **评估开启知识簇复用的代价**：需在 sidecar venv 安装 torch 与 embedding 模型，只影响 sidecar。复用（#2）与演化（#3）能否回归取决于这一步。
4. **流式检索**：等 DEEP 模式真正投入使用时，再考虑接 SSE 到 Dashboard。

---

## 7. 一句话总结

本项目只接入了 sirchmunk 的"无索引检索"半边（且复用/演化被关闭）；"知识编译与学习"半边完全缺席，G0 的悲观结论仅在该冷启动配置下成立——补一个 compile 实验即可验证另一半价值。
