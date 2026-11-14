# Plan: [SD3.7] test-file 保护 hook（3b，可选加固）

Status: deviated

## Files
1. `.factory/hooks/test_guard.py` — 两个子命令：
   - `pre`（PreToolUse，matcher `^(Edit|Create|ApplyPatch|Execute)$`）：标记文件 `.factory/state/fix_mode` 不存在时立即放行；存在时找出本次工具调用要写的路径，命中 `<项目根>/tests/` 下**已有**文件就 `permissionDecision: deny`。Edit / Create 看 `file_path`；ApplyPatch 看补丁里的 `*** Update File:` / `*** Delete File:`（`*** Add File:` 是新建，放行）；Execute 用启发式（使用者决定）：重定向 `>` / `>>` 的目标，`rm` / `unlink` / `truncate` / `mv` / `tee` 的参数，`cp` / `install` / `ln` 的目标，`sed -i` / `perl -i` 的文件参数，`git rm|mv|checkout|restore` 的路径参数，`dd of=`。
   - `reset`（SessionStart，`source` 为 `startup` / `clear` 时）：删除上一个会话崩溃留下的标记，避免它在 Implement 阶段误拦。
   - fail-open：stdin 解析失败、shell 解析失败、未知子命令一律放行，与 `docs_guard.py` 一致。
2. `.factory/hooks.json` — 注册 `PreToolUse` 与 `SessionStart` 两个条目。
3. `.factory/skills/auto-coder/SKILL.md` — Test & Auto-Fix 段：进入时 `mkdir -p .factory/state && touch .factory/state/fix_mode`，离开时（转绿或第 3 轮后 STOP）`rm -f`；被拦时修实现，测试本身有误就停下报告。`sync_skills.py` 生成镜像。
4. `.gitignore` — `/.factory/state/`。
5. `tests/unit/test_test_guard_hook.py` — 离线单测：各工具的拦截 / 放行、标记缺失放行、路径归一（`tests/../src`）、Execute 启发式的正反例、reset 的 source 区分、fail-open、hooks.json 注册与 matcher、skill 与 hook 标记路径一致、`.gitignore`。
6. 规格 §3.3 SD3.7 落地记录 + 任务表；AGENTS.md / MOC / README 状态（顺带修正 AGENTS.md 测试计数）。

## Order
hook 脚本与单测先行 → hooks.json / skill / .gitignore → CI 等价全量离线验证 → 本机一次真实 `droid exec` 短会话（`custom:glm-5.3-Flash-2`，临时 worktree，确认 droid 真的按 deny 拦下）→ 文档回填 → 一次推送到 `feat/sd-sd3.6`（PR #7，使用者决定追加）。推送会触发一次 15 会话的 agent-evals（hook 在 eval 会话里同样加载）。

## Risks
- 误拦：标记残留会拦下 Implement 阶段合法的测试改动 → SessionStart 清残留；拦截理由写明原因与处理方式。同一工作树并发会话时 reset 会清掉别人的标记（方向是放行，可接受）。
- 漏拦：Execute 启发式不覆盖 `python -c` / here-doc 脚本内部写文件、`cd` 后的相对路径等；hook 只在 Droid 会话生效且 agent 可删标记。确定性兜底仍是 SD2「弱化测试断言记 Important」与 SD3b 判据③。
- 每次 Edit / Create / Execute 多一次 python3 启动；标记不存在时第一步就返回。
- evals：fixture 的 `.gitignore` 来自 `fixture_commit`，不含 `/.factory/state/`，agent 若忘删标记会出现在 diff 里；现有判据不看该路径，不影响判定。
- 付费：推送触发 15 次会话（使用者已接受；结果更好则更新 `baseline.json`）。

## Verification
- `pytest tests/unit/test_test_guard_hook.py`；CI 等价禁网全量 pytest、`check_redlines.py`、`sync_skills.py --check`。
- 本机真实会话：标记存在时 Edit / `sed -i` 改已有测试被拒、新建测试文件成功，`git status` 只多出新文件。
- PR #7 的 agent-evals summary：baseline 中 2 条通过用例不得回退。

## Deviations
- The first real-session smoke created the marker before `droid exec` started; the SessionStart `reset` removed it as designed and both writes went through. The rerun had the agent create the marker inside the session (the way auto-coder does), and Edit and `sed -i` on existing tests were denied while Create of a new test file succeeded.
- AGENTS.md test count corrected from the stale 2553 to the measured 2609 (2554 + 55 new hook tests).
