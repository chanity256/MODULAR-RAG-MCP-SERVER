## SX4.2 知识簇复用评估（2026-10-14）

上游 `b314e11`，`deepseek-v4-flash`，`SIRCHMUNK_ENABLE_CLUSTER_REUSE=true`，`CLUSTER_SIM_THRESHOLD=0.85`、`CLUSTER_SIM_TOP_K=3`（上游默认）。原始数据见 `sx42_reuse_results.json`。复用命中没有 telemetry 字段，逐请求判定靠 sidecar 日志（`reuse_log_markers.py`：`Found similar cluster` / `[FAST] Reused cached knowledge cluster` / `No similar clusters found`）。

### 上游逻辑（源码复核）

- 硬复用：embedding 相似度 ≥ 0.85，取 top-3；软复用阈值 0.65（`[SoftReuse]`）。
- `_try_reuse_cluster` 只检查簇内容非空、非拒答，**没有 mtime/hash 过期校验**。
- **只有 FAST 保存知识簇**。DEEP 在 agentic loop 结束后把 `cluster` 置为 `None`，Phase 5 的 `if cluster and should_save` 永不成立（DEEP 日志 0 条 `Saved knowledge cluster`，FAST 7 条）。SX0.4 备注里「FAST/DEEP Phase 5 都保存」的说法据此更正。
- FAST 保存前还有一道 LLM 质量闸门（`[FAST] Quality gate: low-quality answer, skipping cluster save`），正确答案也可能被拒存，复用是否可用有不确定性。
- DEEP 的 Phase 0a DocQA 在复用检查之前执行；DEEP 会复用 FAST 存下的簇。

### 探针（`tests/fixtures/agentic/reuse_probe_questions.json`，每组 seed → repeat → reword → distractor，单轮冷启动）

| 角色 | FAST 复用命中 | FAST 答案正确 | DEEP 复用命中 |
|------|---------------|---------------|---------------|
| repeat（原题重问） | 3/3（相似度 1.0，约 30 ms，0 token） | 3/3 | 0/3（DEEP 不存簇） |
| reword（换说法） | 0/3（未过 0.85） | — | 0/3 |
| distractor（同主题不同问） | 0/3（未误复用） | 3/3 | 0/3 |

### 过期检查（临时语料，单文件 runbook）

| 步骤 | 文件内端口 | mode | 耗时 ms | token | 答案 |
|------|-----------|------|---------|-------|------|
| seed | 7311 | FAST | 5475 | 2710 | 7311（质量闸门拒存） |
| repeat | 7311 | FAST | 4901 | 2679 | 7311（完整搜索，入库） |
| 改文件后 | 7422 | FAST | 51 | 0 | **7311（过期）** |
| 改文件后 | 7422 | DEEP | 1303 | 0 | **7311（复用 FAST 的过期簇）** |

### 非命中回归（SX0.4 协议：每 (mode, 轮次) 冷启动，10 题 × 3 次）

| id | mode | SX0.4 基线 | 复用开启 |
|----|------|-----------|----------|
| Q1 | FAST | 2/3 | 2/3 |
| Q2 | FAST | 0/3 | 0/3 |
| Q3 | FAST | 3/3 | 3/3 |
| Q4 | FAST | 1/3 | 2/3 |
| Q5 | FAST | 0/3 | 0/3 |
| Q6 | FAST | 0/3 | 0/3 |
| D1 | FAST | 0/3 | 0/3 |
| D2 | FAST | 3/3 | 1/3 |
| D3 | FAST | 2/3 | 0/3 |
| D4 | FAST | 2/3 | 0/3 |
| Q1 | DEEP | 3/3 | 3/3 |
| Q2 | DEEP | 3/3 | 3/3 |
| Q3 | DEEP | 3/3 | 2/3 |
| Q4 | DEEP | 2/3 | 0/3 |
| Q5 | DEEP | 3/3 | 3/3 |
| Q6 | DEEP | 0/3 | 0/3 |
| D1 | DEEP | 3/3 | 2/3 |
| D2 | DEEP | 3/3 | 2/3 |
| D3 | DEEP | 3/3 | 1/3 |
| D4 | DEEP | 0/3 | 2/3 |

合计：FAST 13/30 → 8/30，DEEP 23/30 → 18/30；token 合计 FAST 1.01M → 0.94M，DEEP 1.37M → 1.27M；0 错误，0 睡眠疑似。

**这组差值不能归因于复用**：60 次请求里硬复用 0 次、软复用 0 次（题目各不相同，单轮内不重复），DocQA 短路 DEEP 各轮 2/2/1 次，与基线同量级。排查过的其他 embedding 路径（`_fast_find_best_file` 的 embedding 回退需要编译产物，此处没有；DEEP self-correct 的簇证据路径，两种配置日志都 0 次）都没有触发。DEEP 的 D4 0/3 → 2/3 与 Q4 2/3 → 0/3 方向相反，也符合跑间方差。结论：n=3 下 LLM 跑间方差足以造成 ±5/30 的波动，SX3.2 的小幅差值同样应按此打折。
