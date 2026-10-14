# Sirchmunk SX 规格遗留事项（followups）

> 起源：[SIRCHMUNK_EXTENSION_SPEC_frozen](../frozen/SIRCHMUNK_EXTENSION_SPEC_frozen.md)（SX0–SX5）任务全部收口冻结后，规格执行过程中沉淀的遗留事项汇总，分三类：**明确不做**、**实现但裁决保持关闭/不推荐**、**已知缺陷与 gap**。
>
> 性质：第 1 类是决议记录（非候选）；第 2 类是实验负结果的存档（重开需新证据）；第 3 类中可行动的项是**候选方向池**，推进任何一项按 [README 约定](README.md)另立提案。
>
> 关联：[sirchmunk-capability-completion.md](sirchmunk-capability-completion.md)（前序提案）、[SIRCHMUNK_CAPABILITY_GAP_ANALYSIS](../analysis/SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md)（能力对照）、[SIRCHMUNK_INTEGRATION_SPEC_frozen](../frozen/SIRCHMUNK_INTEGRATION_SPEC_frozen.md)（S 系列前置规格）。

<!-- Note: 为何冻结入 frozen、遗留为何建台账而非写回规格 — 见 .agents/notes/implemented/process/2026-10-14-sx-spec-freeze-followups-ledger.md -->

---

## 1. 明确不做（Out of Scope 决议，规格 2.2 收口）

| # | 事项 | 不做的确定原因 |
|---|------|----------------|
| 1 | **知识演化（evolver）** | 唯一"确认不能实现"项：源码实证（规格 4.2）`AgenticSearch(enable_knowledge_evolution=False)` 为上游默认，`api/search.py` 与 CLI 均不传 `True`——HTTP/CLI 均不可达，开启必须改上游源码，同时违反本项目与上游双方纪律 |
| 2 | **`llm_fallback`** | 无文档时让 LLM 凭自身知识回答，与"引用必须可溯源"原则冲突；SX2 已恒发 `false` 显式锁定 |
| 3 | **FILENAME_ONLY 模式** | 沿用冻结规格 2.2 决定，不对外暴露 |
| 4 | **chat/knowledge/history/monitor/files 路由与 WebUI** | sirchmunk 自身产品层，Dashboard 已有对应职能，重复建设 |
| 5 | **compile 的 HTTP/SDK 集成** | 上游无编译端点，只做 CLI 包装（`compile.sh`）已够用，为此改上游不划算 |
| 6 | **任何上游源码改动** | 总红线（第 3 类的线程泄漏不修，根源在此） |

## 2. 已实现但实验裁决保持关闭/不推荐

代码能力已接入，实验数据判了"不值得默认开"。重开的前提是新证据（更大语料、不同场景），而非重跑同一实验。

### 2.1 知识编译：GX 不通过（SX3.4，负结果）

- 编译可覆盖子集（Q3/Q4/D1–D4）未"多数题稳定改善"：FAST 11/18 → 13/18（逐题升 3 降 2），**DEEP 14/18 → 7/18 显著回退**；`.py` 题 FAST 2/12 → 0/12、DEEP 9/12 → 7/12，两种模式均回退。
- 唯一正面信号：FAST 文档类题（非 DocQA）token 35.7k → 11.4k（约 -68%），不足以抵消回退。
- 落地：USAGE_GUIDE 7.3「知识编译：当前不推荐」；编译产物已移出 work_path 恢复未编译状态。
- 未解疑问：DEEP 的 DocQA 短路（0 token、证据为仓库根目录）次数随编译变化，触发条件未定位（见 3.5）。

### 2.2 知识簇复用：默认保持关闭（SX4.2，收益面过窄）

- 实测只有 **FAST 原题逐字重问**命中（3/3，相似度 1.0、约 30 ms、0 token）；改写查询 0/3、干扰题 0/3（无误复用）；DEEP 不存簇，任何探针 0 命中。
- 回归风险：复用开启下非命中查询命中数 FAST 13/30 → 8/30、DEEP 23/30 → 18/30（0 错误，但硬/软复用 0 次触发，差值归为 n=3 的 LLM 跑间方差 ±5/30）。
- 适用场景已写入 USAGE_GUIDE 4.3：语料稳定 + FAST 高频原题重问才有收益；开启须知含"改写不命中、DEEP 只复用 FAST 簇、改文件须手动清 `.cache/knowledge/`"。

## 3. 已知缺陷与 gap（候选方向池）

按影响排序；标注了可能的推进路径，均未立项。

| # | 缺陷 / gap | 现状与原因 | 可能路径 |
|---|-----------|-----------|----------|
| 1 | **流式端点线程泄漏**（SX5.3 实证） | 每次流式请求稳定泄漏约 10 线程（每请求新建 `KnowledgeStorage`：DuckDB 连接 9 线程 + parquet 同步 1 线程；端点不调 `close()`，atexit 钩子持引用致 GC 不回收），空闲不回落。根因在上游 `_run_search`，按"不改上游"红线不修。当前规避：大量使用 Dashboard DEEP 后重启 sidecar；MCP tool 走同步端点不受影响 | 向上游提 PR（含复现数据 `scripts/sirchmunk/sx53_stream_smoke_results.json`）是唯一根治路径 |
| 2 | **复用命中不可观测** | 上游命中路径不写任何 telemetry 标记，`zero_llm_path=true` 也不等于复用（DocQA 短路、闲聊短路同样为真），判定只能靠解析 sidecar 日志 | 属上游可观测性缺口；若上游愿意加标记，主项目只需 converter 白名单加一键 |
| 3 | **复用无过期机制** | `_try_reuse_cluster` 只校验内容非空，无 mtime/hash 失效检查；实测文件修改后仍答旧值，须手动清 `.cache/knowledge/`（已在 USAGE_GUIDE 声明） | 同上，属上游缺陷；本项目侧至多做 preflight 检查提示 |
| 4 | **FAST 保存簇的 LLM 质量闸门可能拒存正确答案**（SX4.2 实测：首次 seed 正确答案被拒，第二次才入库） | 复用行为不可预测的另一个来源；上游行为，不修 | 记录性；评估复用时须知 |
| 5 | **DocQA 短路异常** | 短路答案证据为仓库根目录、token 记 0，`llm_call_count=0` 与真实"零 LLM"混淆；触发条件未定位（规格 3.4 SX3.2） | 若后续要依赖 telemetry 做自动判定，需先定位该路径 |
| 6 | **小样本跑间方差 ±5/30** | 每题 ×3 协议下所有小幅差值（含 SX3.2）都不可靠，结论只能定性 | 扩样本才有定量结论，成本高，暂不做 |
| 7 | **G0 历史数据泄题口径**（已勘误） | `rg_baseline_results.json` 曾在检索范围内，G0 DEEP 单跑那轮命中可能被抬高（"不通过"结论不变）；新口径已修，新旧数字不可直接对比 | 已收口，纯记录 |

## 4. 唯一可行动候选项的决策三问（#1 线程泄漏 → 上游 PR）

1. **解决了什么已观察到的问题？**——是：SX5.3 实测线程数随请求数线性增长且不回收（58 → 199+）。
2. **有无更简单方案？**——本项目侧规避（重启 sidecar）已存在且成本为零；提 PR 是改上游，仅当 Dashboard DEEP 进入日常使用才值得。
3. **如何确认有效？**——连续 ≥10 次流式请求后线程数回落至基线（同一协议可直接复用 SX5.3 的测量脚本）。

在 Dashboard DEEP 成为日常路径之前，本项与 3.x 各条均**保留在候选池，不进入排期**。
