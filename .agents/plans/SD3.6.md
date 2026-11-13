# Plan: [SD3.6] agent-evals.yml + 基线校准（3b）

Status: planned

## Files
1. `.github/workflows/agent-evals.yml` — 路径触发（`.factory/skills/**`、`.claude/skills/**`、`.github/skills/**`、`AGENTS.md`、`.factory/hooks.json`、`.factory/hooks/**`）+ `workflow_dispatch`（输入 `cases` / `repeat`）。三个 job：`gate`（算用例列表；`synchronize` 时只看本次推送的提交是否碰了 steering 路径，避免文档回填推送再花一次 15 会话）→ `evals`（按用例矩阵 5 路并行，每路 3 次会话）→ `summary`（汇总 3 取 2、比对基线、写 job summary）。
2. `evals/ci/factory-settings.json` — CI 用的 `~/.factory/settings.json`：只含一个 BYOK 条目（智谱 Anthropic 兼容端点、`glm-5.3-Flash`），`apiKey` 写 `${ZHIPU_API_KEY}` 由 droid 运行时展开；模型 ID 按「显示名 + 下标」为 `custom:glm-5.3-Flash-0`。
3. `evals/run.py` — 增 `--summarize DIR...`（汇总各矩阵 job 的 `*.report.json`）、`--baseline`（默认 `evals/baseline.json`：之前通过的用例不得变失败 + 通过率门槛）、每条用例的判据失败计数；`droid exec` 非 0 退出也判该次失败（防止什么都没做的会话被判据②③⑤放过）。
4. `evals/baseline.json` — CI 基线跑完后写入（通过用例列表 + 校准后的门槛）。
5. `tests/unit/test_eval_judges.py` — 汇总 / 基线比对 / workflow 与 CI settings 一致性（触发路径逐项对齐规格、不含 `.github/**`、模型 ID 与 settings 下标一致、settings 不含明文 key）。
6. `.factory/skills/auto-coder/SKILL.md` — PR 流程补一句 agent-evals 会被触发（真实的 skill 文本改动，兼作「改一处 skill 文本触发 evals」的验收）；`sync_skills.py` 生成镜像。
7. 规格 §3.3 SD3.6 落地记录 + 任务表；`evals/README.md`；AGENTS / MOC / README 状态同步。

## Order
离线部分先行（run.py 汇总与单测、workflow、settings），本地 CI 等价验证后推送 `feat/sd-sd3.6`（叠在 SD3.4 / SD3.5 上，一个 PR）。PR 打开即触发基线（15 会话）。再从该分支拉临时分支删掉 auto-coder Implement 第 1 步「Read relevant spec」，用例限定 `autocoder-mdown-extension`，开 draft PR（base 为 `feat/sd-sd3.6`）跑 3 次，验证判据①失败后关闭不合入。最后按基线写 `baseline.json`、回填 §3.3。

## Risks
- 花费：PR 每次推送都会被 `paths` 触发；`gate` 只在本次推送碰了 steering 路径时放行，回填提交不碰 AGENTS.md / skill / hooks。预算：基线 15 + 删规则 3 = 18 次会话（使用者已批准）。
- 密钥：`FACTORY_API_KEY`、`ZHIPU_API_KEY` 由使用者加到 fork 的 Actions secrets；fork 来的 PR 拿不到 secrets。agent 会话环境里有 key，上传 artifact 前把 key 值替换成 `***`。
- 5 路并行可能撞智谱限流：限流表现为 droid 非 0 退出，按失败计入并在 summary 里可见，必要时改 `max-parallel` 重跑。
- CI 没有本机 gitignore 的 auto-coder `references/`：用例的 any-of 已把 01 / 04 冻结规格列为等价项。
- 不改 `src/` 业务行为；真实 `droid exec` 不进 `pytest tests`。

## Verification
- 离线：`test_eval_judges.py` 新增用例通过；CI 等价禁网 pytest 全绿、`check_redlines.py` 0 违规、`sync_skills.py --check` 一致；workflow 用 actionlint 校验。
- CI：PR 上 `Agent evals` 被触发并跑完 15 次会话，summary 给出每用例 3 取 2 结果与判据失败计数；删规则 draft PR 上 `autocoder-mdown-extension` 的判据① 3 次中 ≥2 次失败。
- 门槛按基线校准写入 `evals/baseline.json` 与 §3.3。

## Deviations
- Fixed an SD3.5 overlay gap found in the offline rehearsal (fake `droid` on PATH, no model calls): `run.py` overlaid `.factory/skills/` but not its `.claude/` / `.github/` mirrors, so any skill change made `test_skill_mirrors` fail inside every fixture and judge 5 fail regardless of the agent. `OVERLAY_PATHS` now matches the workflow trigger paths, locked by `test_overlay_covers_every_trigger_path`.
- `run.py` clamps `--need` to `--repeat`, so a single-run local check (`--repeat 1`) can pass.
