# Plan: [SD3.5] evals 用例 / fixture / 判据

Status: deviated

## Files
1. `evals/check.py` — 判据实现：读 `droid exec -o stream-json` 记录 + fixture 前后树的 diff，按用例 `judges` 逐项判定，输出 JSON 报告。
2. `evals/run.py` — 运行器：`git worktree add --detach` 到 `fixture_commit`，把被测的 steering 文件（`AGENTS.md`、`.factory/skills/`、`.factory/hooks.json`、`.factory/hooks/`）从当前检出覆盖进 fixture，快照基线树，跑 `droid exec`，调 `check.py`，每条用例 3 次、≥2 次通过算通过。
3. `evals/cases/*.yaml` — 5 条用例（预算：5 × 3，§3.3 SD3.4 测量记录）。
4. `evals/README.md` — 用法与判据口径。
5. `tests/unit/test_eval_judges.py` + `tests/fixtures/evals/sd34_htm_run.jsonl`（SD3.4 真实运行的脱敏录制样本）。
6. `.gitignore` 加 `evals/runs/`；规格 §3.3 落地记录 + 任务表；AGENTS / MOC / README 状态同步。

## Order
判据先行（可离线单测），运行器依赖判据的 `snapshot_tree`，用例最后按判据能力写。

## Risks
- 基线用提交 vs. 用树：fixture 覆盖了 steering 文件，若以 `fixture_commit` 为基线，覆盖本身会被当成 agent 改动。用临时 index（`GIT_INDEX_FILE`）`add -A` + `write-tree` 快照基线树，不产生提交、不需要 git 身份。
- 判据①只认 `Read` 工具（规格原文「被 Read」）；Grep 命中不算读过，这是 SD3.4 暴露出的口径问题，在此定死。
- 判据③是 AST 计数启发式（assert / `pytest.raises` 数不减、测试函数不删、skip/xfail 不增），可能误报重构；产出先经人 triage（§2 Out of Scope）。
- 判据②⑤调用评估方自己仓库里的 `check_redlines.py` 和固定的 CI pytest 命令，不用 fixture 内可能被 agent 改过的副本。
- 不改 `src/` 业务行为；真实 `droid exec` 不进 `pytest tests`。

## Verification
- `tests/unit/test_eval_judges.py`：录制样本上判据①失败（与 SD3.4 人工复核一致）、④通过；合成样本覆盖①通过 / any-of / glob、④第 4 次失败后继续改文件判失败、ApplyPatch 路径解析；tmp git 仓库上覆盖③⑤⑥与 `prepare_fixture` 覆盖逻辑。
- CI 等价离线 pytest 全绿、`check_redlines.py` 0 违规、`sync_skills.py --check` 一致。
- 本任务不跑付费运行；基线与门槛校准属 SD3.6。

## Deviations
- Added judge 6 `diff_patterns` (must_touch / must_not_touch / added_must_not_match): dual registration and the qa-tester no-inferred-pass rule cannot be expressed by judges 1–5.
- No `evals/fixtures/` directory: none of the 5 cases needs a planted patch; the fixture is the worktree + steering overlay built by `run.py`.
- Overlay copies only non-ignored files (`git ls-files --cached --others --exclude-standard`): the local checkout has gitignored auto-coder `references/` that CI lacks.
- Offline pytest judge adds `-o addopts=`: pyproject `-q` plus CI `-q` suppresses the summary line the report shows.
