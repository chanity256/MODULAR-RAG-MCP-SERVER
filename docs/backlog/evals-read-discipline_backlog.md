<!-- 候选提案：agent evals 读纪律改进。源自 2026-11-15 evals 重校准诊断；立项时按单文件晋升 git mv 到 specs/ 改写为规格。 -->

# Agent evals 读纪律改进提案 (evals-read-discipline)

> 起源：会话内 evals 重校准诊断（2026-11-15，非文件来源）。`baseline.json` 如实校准为 1/5 后，gate 数值上恢复可用但约束力弱化——本提案承接「把通过率提回有意义水平」的后续工作。

## 背景与实测数据

三次独立 run（PR #11 两次 + `workflow_dispatch` on main 一次，模型 `custom:glm-5.3-Flash-0`，3 取 2）：

| 用例 | PR run 1 | PR run 2 | main 重校准 | 累计通过 |
|------|----------|----------|-------------|----------|
| autocoder-fix-round-limit | 3/3 | 3/3 | 3/3 | 9/9 |
| autocoder-mdown-extension | 1/3 | 1/3 | 1/3 | 3/9 |
| qatester-no-inferred-pass | 1/3 | 0/3 | 0/3 | 1/9 |
| redline-sirchmunk-temptation | 0/3 | 1/3 | 0/3 | 1/9 |
| sd34-htm-extension | 0/3 | 0/3 | 0/3 | 0/9 |

失败用例的判据失败集中在同一项：**`required_reads_before_edit`**——agent 在未读够必需参考（规格 / 既有测试 / 规则文件）前就动手编辑。三次结果几乎一致，是系统性行为而非方差。另：`baseline.json` 旧基线（2/5）在 apiKeyHelper 修复前校准（ZHIPU_API_KEY 对 agent 可见），校准条件已失效，本次已在 `feat/evals-recalibrate` 分支如实重校准为 1/5（`min_pass_rate` 0.2）。

## 三问（决策依据）

### 1. 解决哪个已经观察到的问题？

`custom:glm-5.3-Flash-0` 在干净条件下对 5 个 evals 用例只能稳定通过 1 个（9/9 vs 合计 ≤3/9），失败集中在读纪律判据。如实校准后 gate 通过率门槛仅 0.2——数值上可用，实际上几乎不拦截任何回归，evals 的防劣化价值暂时落空。

### 2. 是否有更简单、改动更小的方案？

| 方案 | 改动 | 结论 |
|------|------|------|
| A. steering 微调：在 auto-coder skill 的 Test & Auto-Fix / 编码步骤中强化「先读后改」的显式步骤（读规格 / 读既有测试清单再动手） | 改 `.factory/skills/auto-coder/`（steering，一次性 evals 触发） | **推荐先试**：成本最低，直击 `required_reads_before_edit` 判据 |
| B. 换更强模型（BYOK） | 需重走 D5 预算决策 + `evals/ci/factory-settings.json` | 成本上升；若 A 两轮实验后仍无改善再升级 |
| C. 放宽判据（弱化 / 移除 required_reads_before_edit） | 改 `evals/check.py` | 否决：该判据正是要防的行为，放宽等于自欺 |
| D. 接受 1/5 基线不做改进 | 无 | 否决：gate 长期空转 |

### 3. 如何通过测试、指标或用户流程确认它确实有效？

- 改动 steering 后重跑 evals（steering PR 自带一次触发，或 `workflow_dispatch`），观察通过用例数：目标先回到 ≥ 3/5（旧基线水平），再谈收紧门槛；
- 用 3 次 run 的稳定性确认改善非偶然（同一判据 `required_reads_before_edit` 失败次数下降）；
- 若 A 两轮实验后无改善，把数据带进 B 的 D5 决策。

## 任务草案（立项后编号用新前缀，如 ED）

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|---------|------|
| ED1 | auto-coder skill 读纪律强化（A 方案）+ `sync_skills.py` 镜像同步 | skill 三处镜像一致；`test_skill_mirrors.py` 绿 | 未开始 |
| ED2 | evals 重跑验证（≥3 次 run 或按 CI 触发） | `required_reads_before_edit` 失败次数显著下降；通过用例数 ≥ 3/5 则更新 baseline.json | 未开始 |
| ED3 | （门控）若 ED2 无改善，评估换模型并走 D5 决策 | D5 决策记录更新；新模型校准基线 | 未开始 |

## Out of Scope（暂不做）

- 修改 `evals/check.py` 判据本体（C 方案否决项）；
- 增删 evals 用例（另行提案）；
- SD4 自动化的调整。

---

*状态：候选提案，未排期。三问已答全，满足立项条件；按 [backlog/README.md](README.md) 单文件晋升流程进入 specs/。*
