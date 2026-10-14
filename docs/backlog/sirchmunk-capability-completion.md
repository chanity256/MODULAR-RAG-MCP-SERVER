# Sirchmunk 剩余能力补齐提案（SX 系列）

> 状态：已立项（2026-10-13）为 [frozen/SIRCHMUNK_EXTENSION_SPEC_frozen.md](../frozen/SIRCHMUNK_EXTENSION_SPEC_frozen.md) SX 系列，SX0–SX5 已收口冻结（2026-10-14）；本页保留作决策依据，细节以规格为准
> 依据：[SIRCHMUNK_CAPABILITY_GAP_ANALYSIS](../analysis/SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md) v0.2 + 上游源码（`b314e11`）复核
> 红线不变：主 venv 永不 `import sirchmunk`，不改上游源码，只经 HTTP / sidecar venv CLI 接入。

## 决策三问

### 1. 它解决了哪个已经观察到的问题？

G0 三方对照（裸 rg 6/6，FAST 2/6，DEEP 1/2）是在**无编译、无复用、冷启动**配置下测得的，只覆盖了 sirchmunk 两大核心中的「无索引检索」半边。另一半（知识编译 → DEEP 自动消费树索引/主题图/摘要索引）完全缺席。同时有三个已确认的接入缺口：

- **telemetry 未消费**：`SearchContext.to_dict()` 把 `context.telemetry` 合并进 `data` 顶层（`loop_count`/`budget_remaining`/`llm_call_count`/`retrieval_logs` 及机制标记如 `self_correct_entered`），`converter.py` 一个都没读，trace 与 Dashboard 无从展示——冻结规格 3.2 的 `data.telemetry` 描述与代码不符（规格勘误见下）。
- **编译无运维入口**：HTTP API 无编译端点，编译只能走 sidecar venv 的 `sirchmunk compile` CLI，目前没有脚本化（且 work_path 无文件锁、`_tree_paths_cache` 按实例缓存 → 编译须在 sidecar 停机时进行、完成后重启 sidecar）。
- **复用/演化被一刀切关闭**：`SIRCHMUNK_ENABLE_CLUSTER_REUSE=false` 的理由是「避免安装 torch + embedding 模型」，但实测 `.venv-sirchmunk` 已含 torch 2.14.1 / sentence-transformers 6.1.0（上游 core 依赖），真实代价只剩 MiniLM 模型下载（约 470 MB）与常驻内存。

另有一个**上游不可达能力**（提案明确排除的）：知识演化（evolver）。`AgenticSearch(enable_knowledge_evolution=False)` 为默认，`api/search.py` 与 CLI 均不传 `True`，经 `sirchmunk serve` 无法开启，除非改上游——违反双方纪律。记为 out of scope，不做。

### 2. 是否有更简单、改动更小的方案？

- 编译：不改 `src/`，只加 `scripts/sirchmunk/compile.sh` 包装 sidecar venv CLI（比 SDK/HTTP 集成小一个数量级），且是检验「编译能否翻盘」的最小成本实验。
- telemetry：只做**白名单键解析**（`retrieval_logs` 降维为按 tool 计数），不做全量透传——全量 `retrieval_logs`/`read_file_ids` 会膨胀 trace 体积，诊断价值低。
- 参数透传：只加 `max_depth` / `enable_dir_scan` 两个；`llm_fallback` 恒发 `false` 显式锁定（与「引用必须可溯源」冲突）。
- 流式：SSE 只接 Dashboard DEEP 进度展示，MCP tool 保持同步——复用上游既有端点，不自建轮询。

### 3. 如何通过测试、指标或用户流程确认它确实有效？

- 编译实验：编译只处理文档类扩展名（md/txt/html/pdf/docx 等），**不处理 `.py`**，而现有 golden 集 6 题中 4 题（含 DEEP Q5/Q6）的答案在 `.py` 里。所以先补几道答案在 `README.md`/`docs/` 的文档类题，用它们评估编译收益。新题放进独立 fixture，不修改 G0 的 `golden_questions.json`，以免改变冻结规格里 6/6、2/6 的分母；`.py` 题只看有没有回退。编译前后按同一协议重跑（复用关闭、每题 ×3），对照逐题命中、耗时、LLM token；**编译自身的 token/时间成本入账**。正收益才进 USAGE_GUIDE 推荐配置，负结果也如实记录（G0 门控纪律的延续）。
- 复用评估：重复查询/改写查询的延迟与 LLM 调用对比、误复用率、文件修改后陈旧结果检查；另需对比复用开/关下未命中查询的准确率（上游软复用会改变未命中查询的搜索行为）。上游复用命中**不写 telemetry 标记**，响应里也没有可靠的替代信号（0 次 LLM 调用的情况还包括 DocQA 直析和闲聊短路），判定以 sidecar 日志为准。
- telemetry：trace JSONL 与 Dashboard agentic 元数据面板可见 `loop_count`/`llm_call_count` 等键；离线 MockTransport 单测锁解析（键缺失容忍）。
- 流式：Dashboard DEEP 执行期呈现阶段化进度；离线用罐头 SSE 帧测试；断连时上游会取消搜索任务，顺带修复冻结规格 4.7 的「客户端超时后成本仍在消耗」问题。

## 建议分期（立项后在规格中细化）

| 期 | 内容 | 性质 |
|----|------|------|
| SX0 | 规格勘误、golden 集补文档类题、基线冻结（gap analysis v0.2、冻结规格 3.2 勘误） | 文档 + 门控实测 |
| SX1 | telemetry 解析 → trace + Dashboard | 小改 `src/` |
| SX2 | `max_depth` / `enable_dir_scan` 透传 | 小改 `src/` |
| SX3 | `compile.sh` + 编译收益实验（文档类题子集） | 脚本 + 门控实验 |
| SX4 | 知识簇复用开启与评估 | sidecar 配置 + 门控实验 |
| SX5 | SSE 流式接 Dashboard DEEP | 中改，可选期（推翻冻结规格 2.2「不做 SSE」，需记录理由；是否启动看 DEEP 本身有无使用价值，与编译实验结果无关） |

## 后续

已立项：规格见 [frozen/SIRCHMUNK_EXTENSION_SPEC_frozen.md](../frozen/SIRCHMUNK_EXTENSION_SPEC_frozen.md)（新前缀 `SX`，已核查与 S/IR/V/G/P/A–I 无冲突），引用本提案作为决策依据。
