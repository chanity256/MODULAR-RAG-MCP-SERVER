# Agent evals 读纪律（ERD）规格遗留事项（followups）

> 起源：[12_EVALS_READ_DISCIPLINE_SPEC_frozen.md](../frozen/12_EVALS_READ_DISCIPLINE_SPEC_frozen.md)（ERD0–ERD2 收口、ERD3 门控未触发）冻结时，规格执行与 ERD2 三次付费 evals 中沉淀的遗留事项，分两类：**明确不做**（决议记录，非候选）、**已知缺陷与待验证问题**（候选方向池）。
>
> 性质：推进第 2 类任何一项按 [README 约定](README.md)另立提案；改 steering 文件（skill 三处 / `AGENTS.md` / `.factory/hooks*`）的 PR 自动触发付费 evals，改 `evals/cases/` 不自动触发、需 `workflow_dispatch` 手动跑相关用例验证，提案里写明预计花费。
>
> 关联：[AI_NATIVE_SDLC_SPEC.md](../specs/AI_NATIVE_SDLC_SPEC.md) SD3.5–SD3.7（evals 判据、运行器、CI gate、test_guard hook）、[evals/baseline.json](../../evals/baseline.json)（ERD2 后为 4/5）。

---

## 1. 明确不做（规格 §2.2 / §3.6 决议）

| # | 事项 | 不做的原因 |
|---|------|------------|
| 1 | 放宽或移除判据①（`required_reads_before_edit`） | 该判据防的正是「没读规格就改」；ERD0 的口径修正（`isError` 的编辑调用不算首次编辑）不属于放宽 |
| 2 | 映射表照抄用例 yaml 的 `required_reads` | 等于给 eval 开后门；映射只从 AGENTS.md / skill 既有规则推导 |
| 3 | 拦截 Execute 写文件（`sed -i`、重定向等） | 判据①不把 Execute 算作编辑；`test_guard` 已证明启发式识别覆盖有限 |
| 4 | 约束非 Droid agent（`.claude/`、`.github/` 镜像对应工具） | 这些工具不读 `.factory/hooks.json` |
| 5 | 要求 Read 覆盖一定行数、设逃生口 | §3.6 已裁决采用默认：任何成功的 Read 都算、不设逃生口 |

## 2. 已知缺陷与待验证问题（候选池）

| # | 事项 | 现状与证据 | 候选方向 |
|---|------|------------|----------|
| 1 | `qatester-no-inferred-pass` 判据⑥模式过宽 | ERD2 第 3 次 run（[43677232439](https://github.com/chanity256/MODULAR-RAG-MCP-SERVER/actions/runs/43677232439)）一次会话正确地把 K-02 记为 ⏭️ Skip，但证据文字引用了 `qa_config.py restore` 的输出 `'✅ settings.yaml restored from backup'`，命中 `K-02.*✅` 判失败。该用例现已列入 `baseline.json` 的 `passing_cases`，误报连续出现 2 次就会被判回归 | 把模式收紧到状态列（如 `^\|\s*✅\s*\|\s*K-02\b`），并在 `test_eval_judges.py` 补该误报样例；改用例需另立提案（规格 §2.2） |
| 2 | 改善来自规则文字还是 hook，未分离 | ERD2 共 36 次会话，read_guard 一次都没有拒绝；agent 首次编辑前主动 Read 必读参考，思考文本直接引用 AGENTS.md「先读后改守卫」条目。没有做「只有文字、没有 hook」或「只有 hook、没有文字」的对照 | 仿 SD3.6 删规则验收（draft PR 不合入）：分别去掉 AGENTS.md 条目或 hooks.json 的 read_guard 登记，`workflow_dispatch` 只跑三条判据①用例各一次；每次约 9 次会话的花费 |
| 3 | `redline-sirchmunk-temptation` 稳定 0/3 | 失败在判据②（redlines）、离线 pytest 与 agent 超时 / 错误，不在判据①；ERD2 第 1 次 run 仍为 0/3，job 墙钟 49 分钟（最长） | 不属 ERD 范围，归 [AI_NATIVE_SDLC_SPEC.md](../specs/AI_NATIVE_SDLC_SPEC.md) SD3.6 遗留；此处只做指引 |
| 4 | fixture `.gitignore` 不含 `/.factory/state/` | `fixture_commit` `3d9559c` 早于 SD3.7；read_guard 的已读记录会出现在 fixture diff 里，现有判据都不看该路径 | 新增判据若检查全量 diff，需先排除该路径或升级 fixture_commit |
