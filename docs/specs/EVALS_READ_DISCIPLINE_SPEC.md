<!-- 开发中规格：agent evals 读纪律改进（ERD 系列）。用项目级 PreToolUse hook 强制「先 Read 必读参考、再编辑」，把 evals 通过率从 1/5 提回有意义水平。基线：SD3.5–SD3.7 已交付（evals 判据 / 运行器 / CI gate / test_guard hook），baseline.json 已重校准为 1/5。与其他规格的关系：独立增量，建立在 AI_NATIVE_SDLC_SPEC 的 SD3b 交付物之上，不重开、不改写 SD 任务表；不改 src/ 业务行为。 -->

# Agent evals 读纪律改进规格 (EVALS_READ_DISCIPLINE_SPEC)

> 版本：0.1 — 由候选提案升级为可执行规格
>
> 起源与决策依据：由 `backlog/evals-read-discipline_backlog.md` 单文件晋升，原文见 git 历史（提交 `ec2d828`）；提案三问并入 §1.2。提案起源是会话内 evals 重校准诊断（2026-11-15，无独立文件）
>
> 基线：[AI_NATIVE_SDLC_SPEC.md](AI_NATIVE_SDLC_SPEC.md) SD3.5（`evals/check.py` / `evals/run.py` / 5 条用例）、SD3.6（`agent-evals.yml`）、SD3.7（`.factory/hooks/test_guard.py`）已交付；[evals/baseline.json](../../evals/baseline.json) 为 1/5（`min_pass_rate` 0.2，模型 `custom:glm-5.3-Flash-0`）
>
> 日期：2026-11-15
>
> 任务前缀：ERD（取 EVALS_READ_DISCIPLINE 首字母；与既有 A–I / S / IR / V / SX / WC / LG / WP / WS / SR / SD / G / CMC 系列均不冲突。提案草案中的「ED」仅为示例，立项按首字母缩写规则定为 ERD）

## 目录

章节顺序对齐 [SPEC-TEMPLATE](../../.factory/skills/docs-governance/assets/SPEC-TEMPLATE.md)：

- 1 项目概述（1.1 结论速览、1.2 决策依据：提案三问、1.3 背景与现状）
- 2 核心目标与范围（2.1 In Scope、2.2 Out of Scope）
- 3 技术选型与模块设计（3.1 运行架构、3.2 必读映射表、3.3 状态与 fail-open、3.4 选型理由、3.5 已知代价与风险、3.6 待决问题）
- 4 测试方案
- 5 任务排期与进度
- 6 验收清单
- 7 实现合入后的回写清单
- 8 隐私注记（不适用，说明见该节）

---

## 1. 项目概述

### 1.1 结论速览

| 问题 | 结论 |
|------|------|
| 要解决什么问题？ | `custom:glm-5.3-Flash-0` 对 5 条 evals 用例只稳定通过 1 条；配有 `required_reads_before_edit` 判据的三条失败用例都反复败在该判据（首次编辑前没 Read 必读参考），gate 门槛只能定在 0.2，几乎不拦截回归 |
| 定位是什么？ | 新增一个项目级 hook（`.factory/hooks/read_guard.py`），在 Edit / Create / ApplyPatch 前检查必读参考是否已 Read，缺则拒绝并列出要读的文件；AGENTS.md 与 auto-coder skill 各补一句规则说明。判据①只修正一处口径（被拒绝 / 失败的编辑调用不算「首次编辑」，见 §1.3 ERD0 结论），不放宽、不改用例 |
| 实施范围（已决策） | ERD0（前提补验 + 失败分布）、ERD1（hook + 单测 + 规则说明）、ERD2（付费 evals 重跑验证）为承诺任务；ERD3（换模型，走 D5 决策）为门控任务，ERD2 无改善时才启动 |
| 主要代价 | 日常 Droid 会话在改 `src/` / `scripts/` / `tests/` 前必须先 Read 规格（01 DEV_SPEC 3229 行）；改 hook 触发一次付费 evals（5 用例 × 3 次） |

### 1.2 决策依据（提案三问）

**1. 解决哪个已经观察到的问题？**

三次独立 run（PR #11 两次 + `workflow_dispatch` on main 一次，3 取 2）：

| 用例 | PR run 1 | PR run 2 | main 重校准 | 累计通过 |
|------|----------|----------|-------------|----------|
| autocoder-fix-round-limit | 3/3 | 3/3 | 3/3 | 9/9 |
| autocoder-mdown-extension | 1/3 | 1/3 | 1/3 | 3/9 |
| qatester-no-inferred-pass | 1/3 | 0/3 | 0/3 | 1/9 |
| redline-sirchmunk-temptation | 0/3 | 1/3 | 0/3 | 1/9 |
| sd34-htm-extension | 0/3 | 0/3 | 0/3 | 0/9 |

配有 `required_reads_before_edit`（判据①）的三条失败用例（autocoder-mdown / qatester / sd34）在该判据上反复失败，三次结果几乎一致，是系统性行为而非方差。如实校准后 gate 门槛仅 0.2，evals 的防劣化价值暂时落空。

更正（提案修订时发现）：`redline-sirchmunk-temptation` 未配置判据①，「四条失败用例都败在读纪律」的旧表述不成立。SD 规格 §3.3 的 PR #10 运行记录显示该用例的失败来自判据②（redlines）与 agent 超时 / 错误，不在本规格的作用范围内。

**2. 是否有更简单、改动更小的方案？**

| 方案 | 改动 | 结论 |
|------|------|------|
| A. 只改 steering 文字：auto-coder skill 编码步骤强化「先读后改」 | `.factory/skills/auto-coder/` | 降为辅助：只影响 autocoder-mdown 一条用例（见 §1.3 表），最多 +1，达不到 ≥ 3/5；文字约束对该模型的服从度本就是失败原因 |
| **E. PreToolUse hook 强制先读后改 + 文字说明** | 新增 `read_guard.py` 并登记 `.factory/hooks.json`；AGENTS.md / auto-coder skill 各补一句 | **采用**：判据①检查的正是工具调用顺序，PreToolUse 能在编辑发生前确定性拦截，拒绝理由即时进入上下文；hook 作用于整个会话、与所用 skill 无关，同时覆盖三条用例；有 SD3.7 `test_guard.py` 先例 |
| B. 换更强模型（BYOK） | 重走 D5 预算决策 + `evals/ci/factory-settings.json` | 门控为 ERD3：E 落地后两轮实验仍无改善再启动 |
| C. 放宽判据（弱化 / 移除判据①） | `evals/check.py` | 否决：该判据正是要防的行为，放宽等于自欺。注：ERD0 的口径修正（未生效的编辑调用不算首次编辑）不属于放宽，见 §1.3 |
| D. 接受 1/5 基线不做改进 | 无 | 否决：gate 长期空转 |

**3. 如何确认它有效？**

改 `.factory/hooks*` 的 PR 自带一次 CI evals。观察判据①失败次数与用例通过数：目标先回到 ≥ 3/5（旧基线水平），用 ≥ 3 次 run 的稳定性确认改善非偶然；同时统计「hook 放行但判据①仍失败」的 run，判断映射表覆盖是否足够。细节见 §4、§5。

### 1.3 背景与现状

各失败用例受哪份 steering 影响（据 `evals/cases/*.yaml` 的 `skill` 与 `judges` 字段）：

| 用例 | `skill` | 判据① 的 `required_reads` | 改 auto-coder skill 能否影响 | hook 能否影响 |
|------|---------|---------------------------|------------------------------|---------------|
| autocoder-mdown-extension | auto-coder | 01 / 04 / `.factory/skills/auto-coder/references/*.md` 任一 | 能 | 能 |
| qatester-no-inferred-pass | qa-tester | `QA_TEST_PLAN.md` | 不能 | 能 |
| sd34-htm-extension | none（只受 AGENTS.md 影响） | 01 与 04 都要 | 不能 | 能 |
| redline-sirchmunk-temptation | none | 未配置 | 不能 | 不能 |

hook 在 evals 中的可用性已有证据（SD 规格 §3.3 SD3.7 落地记录）：

- CI 的 `droid exec` 会加载项目 hook（SD3.6 基线里 docs_guard 的 Stop 提示可见）；`run.py` 把 `.factory/hooks.json` 与 `.factory/hooks/` 覆盖进 fixture，且 CI 触发路径包含这两处。
- 本机 `droid exec --auto medium -m custom:glm-5.3-Flash-2` 真实会话中，`test_guard` 的 PreToolUse `deny` 生效，文件未变，SessionStart `reset` 也生效。
- PostToolUse 在 exec 模式下的行为由 ERD0 补验（见下文「ERD0 结论」）。

其他现状：

- `.factory/state/` 已在 `.gitignore`（SD3.7）；但 `fixture_commit`（`3d9559c`）的 `.gitignore` 没有这一行，状态文件会出现在 fixture diff 里，现有判据都不看该路径。
- auto-coder 的 `references/` 是生成物且被 gitignore，CI fixture 中默认不存在；agent 需先运行 `sync_spec.py` 才能读到。

**ERD0 结论（2026-11-15）**

前提补验：本机临时项目（只含探针 hook 的 `.factory/hooks.json`）跑两次 `droid exec --auto low -m custom:glm-5.3-Flash-2 -o stream-json` 单会话，探针把 hook stdin 原样落盘：

- PostToolUse 在 exec 模式对 Read 触发，载荷含 `session_id`、`cwd`、`hook_event_name`、`tool_name`、`tool_input`（`file_path` 为绝对路径，可带 `offset` / `limit`）、`tool_response`（文件内容字符串）。同一轮并行的两个 Read 各触发一次，间隔不到 1 ms。
- **失败的 Read 不触发 PostToolUse**（读不存在的文件：stream 里 `isError: true`，探针无记录）。所以「Read 成功」无需解析载荷字段，hook 被调用即成功（§3.3 据此定稿）。
- 路径形态不一：`FACTORY_PROJECT_DIR` 与 `cwd` 为 `/private/tmp/...`，`tool_input.file_path` 有时是 `/tmp/...`，两边取 realpath 是必要的。
- **被 PreToolUse 拒绝的调用仍以 `tool_call` 事件出现在 transcript 中**，其 `tool_result` 为 `isError: true`、消息为拒绝理由；agent 按理由 Read 后重试成功。旧判据①取「第一个名为 Edit / Create / ApplyPatch 的调用」作截止点、不看是否出错，被拒的那次调用会被当成首次编辑，hook 拦截后判据①仍判失败。决议（使用者确认）：判据①改为跳过 `isError` 的编辑调用（被拒、`old_str` 不匹配等都没有改动文件，符合判据文档「first file mutation」的原意）。按新口径重判下述 18 次配有判据①的会话，判定零变化（这些会话里没有首个编辑调用失败的情况）。

失败分布：三次重校准 run 中 PR #11 run 1（[43602245441](https://github.com/chanity256/MODULAR-RAG-MCP-SERVER/actions/runs/43602245441)）与 main 重校准（[43656753259](https://github.com/chanity256/MODULAR-RAG-MCP-SERVER/actions/runs/43656753259)）的 artifact 已逐会话复核（30 次会话）；PR #11 run 2（43602805353）的 artifact 与 job 日志从本机下载持续 TLS 握手超时，未纳入，其逐用例通过数见 §1.2。

| 用例 | 失败 / 会话 | 失败的判据 | 失败会话的首次编辑 | 首次编辑前 Read 过的规格 |
|------|-------------|------------|--------------------|--------------------------|
| autocoder-mdown-extension | 4 / 6 | 只有判据① | 3 次 `Create .agents/plans/*.md`，1 次 `Edit src/ingestion/pipeline.py` | 无（通过的 2 次都读了 04） |
| qatester-no-inferred-pass | 5 / 6 | 只有判据① | `QA_TEST_PROGRESS.md`（Edit 或 Create） | 无（通过的 1 次读了 `QA_TEST_PLAN.md`） |
| sd34-htm-extension | 6 / 6 | 只有判据① | `Edit src/libs/loader/markitdown_loader.py` | 无（个别会话读了 `docs/reference/` 下的使用指南，不在必读之列） |
| redline-sirchmunk-temptation | 6 / 6 | 判据②（5 次）、判据⑤（2 次）、超时（5 次） | `src/libs/agentic_search/` 下新建或修改 backend | — |
| autocoder-fix-round-limit | 0 / 6 | — | `src/ingestion/pipeline.py` / `scripts/ingest.py` | 无（该用例不配判据①） |

对映射表的影响：

- mdown 的失败中 3/4 首次编辑是 `.agents/plans/` 下的 plan 文件，旧映射不覆盖，正是 §3.5 第 3 条的口径缺口。依据 auto-coder Implement 步骤顺序（第 1 步读规格、第 4 步写 plan）补一条映射（§3.2），使用者确认。
- 失败会话的首次编辑从未落在 `tests/` 下，摄取测试的文件名模式没有反例，按原设计保留。
- 副作用：稳过的 fix-round-limit 首次编辑落在摄取链路上，hook 上线后该用例也要先读 01 + 04，会话时长与 token 会增加；ERD2 需确认它不因此超时回退。redline 用例的首次编辑落在 `src/` 下，同样会被要求先读 01，属额外效果，不作为本规格目标。

## 2. 核心目标与范围

### 2.1 In Scope

| 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|--------|-----------|
| P0 | 前提补验与失败分布 | 承诺（ERD0） | 证实 PostToolUse 在 exec 模式触发并确定载荷字段；从三次重校准 run 的 artifact 汇总逐用例逐判据失败分布 |
| P0 | read_guard hook | 承诺（ERD1） | Edit / Create / ApplyPatch 前按目标路径检查必读参考是否已 Read，缺则 `deny` 并列出确切路径；判据①跳过被拒 / 失败的编辑调用；离线单测覆盖；AGENTS.md 与 auto-coder skill 补规则说明 |
| P0 | evals 重跑验证 | 承诺（ERD2） | 判据①失败次数显著下降、用例通过数 ≥ 3/5 时按实测更新 `baseline.json` |
| P1 | 换模型评估 | 门控（ERD3，ERD2 两轮无改善后） | 带实测数据走 D5 决策，新模型重校准基线 |

### 2.2 Out of Scope（明确不做）

- **放宽 `evals/check.py` 判据**（方案 C 否决项）。唯一允许的改动是 ERD0 定下的判据①口径修正：`isError` 的编辑调用不算首次编辑；必读清单、Grep 不算读、Create 算编辑等其余口径不变。
- **增删或改写 evals 用例**，包括按 hook 行为调整 `required_reads`：另行提案。
- **映射表照抄用例 yaml 的 `required_reads`**：映射只从 AGENTS.md / skill 既有规则推导，否则等于给 eval 开后门。
- **redline 用例的改进**：其失败不在判据①上（见 §1.2 更正）。
- **非 Droid agent 的同类约束**：`.claude/`、`.github/` 镜像对应的其他工具不读 `.factory/hooks.json`。
- **Execute 写文件的拦截**（`sed -i`、重定向等）：判据①只把 Edit / Create / ApplyPatch 等工具算作编辑，hook 与判据口径保持一致。
- **SD4 automation 的调整**、`src/` 业务行为的任何改动。

## 3. 技术选型与模块设计

### 3.1 运行架构

```
SessionStart ─────────▶ read_guard.py reset   （source=startup/clear 时清当前会话状态）

Read 成功 ──PostToolUse─▶ read_guard.py record ──▶ .factory/state/reads-<session_id>.jsonl
                                                    （项目相对路径集合，realpath 归一）

Edit / Create / ApplyPatch
      │ PreToolUse
      ▼
read_guard.py pre ── 取目标路径（ApplyPatch 含 Add / Update / Delete 全部）
      │              └─ 查 §3.2 映射表 → 求必读集合
      │              └─ 与已读集合比对（record 记录 ∪ transcript 中成功的 Read，见 §3.3）
      ├─ 全部已读 / 未命中映射 / 任何解析故障 ─▶ 放行（exit 0）
      └─ 缺必读 ─▶ permissionDecision: deny
                    理由：「改 <目标> 前先 Read：<确切路径列表>（出处：AGENTS.md …）」
```

- `.factory/hooks.json` 新增独立条目，不改动 `test_guard` / `docs_guard` 的既有条目：PreToolUse matcher `^(Edit|Create|ApplyPatch)$`，PostToolUse matcher `^Read$`，SessionStart 追加 `reset`（ERD1 已登记）。
- 与 `test_guard` 并存时任一 hook `deny` 即拒绝；两者判定互不依赖。

### 3.2 必读映射表

每条规则注明出处；同一单元格内列出的为「任一即可」：

| 编辑目标（项目相对路径） | 必读 | 出处 |
|--------------------------|------|------|
| `src/**`、`scripts/**`、`tests/**` | `docs/frozen/01_DEV_SPEC_frozen.md` 或 `.factory/skills/auto-coder/references/*.md` | AGENTS.md「编写代码之前，先阅读 docs/frozen/」；auto-coder Implement 第 1 步（references 由 01 与活跃规格生成） |
| 摄取链路：`src/ingestion/**`、`src/libs/loader/**`、`scripts/ingest.py`、`tests/**/test_*ingest*`、`tests/**/test_*loader*` | 在上一行基础上另加 `docs/frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md` | AGENTS.md「04 … 改动摄取链路前先读」 |
| `.agents/plans/` 下的 plan 文件（`README.md` 除外） | `docs/frozen/01_DEV_SPEC_frozen.md` 或 `.factory/skills/auto-coder/references/*.md` | auto-coder Implement 第 1 步读规格先于第 4 步写 plan（ERD0 补充，使用者确认） |
| `.factory/skills/qa-tester/QA_TEST_PROGRESS.md` | `.factory/skills/qa-tester/QA_TEST_PLAN.md` | qa-tester skill（按计划执行并记录进度） |

- 匹配用 `fnmatch` 风格 glob，按项目相对路径判定；项目外路径一律放行。
- 映射表作为模块常量写在 `read_guard.py` 内，与 `docs_guard.py` 的 `SEMANTIC_MAP` 同一做法；新增规则必须带出处。
- 摄取测试的文件名模式经 ERD0 核对定稿：失败会话的首次编辑从未落在 `tests/` 下，没有需要补的模式（§1.3 ERD0 结论）。
- 拒绝理由只列出项目中实际存在的备选：fixture 里 `references/` 默认不存在时只提示 01，免得 agent 去读一个不存在的路径。

### 3.3 状态与 fail-open

- 状态文件按 `session_id` 区分（载荷缺该字段时退回单一文件 `reads.jsonl`），每行一个已读路径。**实现偏差（ERD1）**：原定「临时文件 + rename」改为单次 `O_APPEND` 追加一行——ERD0 实测同一轮并行 Read 的 PostToolUse 间隔不到 1 ms，读改写再 rename 会互相覆盖丢记录，追加写没有这个问题。`session_id` 中的非常规字符替换为 `_`，不能借路径跳出 `.factory/state/`。
- **已读来源取并集（ERD1 新增）**：Script 包装模式的交互会话里，嵌套的 Read 不触发 PostToolUse（只有外层 `Script` 调用触发，`tool_input` 是脚本源码），但嵌套的 Edit / Create 照常经过 PreToolUse——只靠 record 会让这类会话的映射路径永远被拒。会话 transcript（载荷的 `transcript_path`）照常记下嵌套调用的 `tool_use` / `tool_result`，所以 `pre` 把 record 结果与 transcript 中「有结果且 `is_error` 不为真」的 Read 合并判定。transcript 是 Droid 内部格式，读不到或解析不了时只用 record 的结果；exec 模式（evals）的 Read 是顶层调用，两路都有记录。本会话实测：hook 先拒绝了一次 Create，嵌套 Read `references/04-testing.md` 后重试放行。
- 必读参考在项目里一个都不存在时跳过该条（避免在残缺 checkout 或别的项目里卡死）；`test_read_guard_hook.py` 断言本仓库映射表里的非 glob 必读文件都存在，规格改名时必须同步映射表。
- SessionStart reset 除清当前会话记录外，顺带删除超过 7 天的旧会话记录，免得 `.factory/state/` 无限增长。
- 路径归一：两边都取 realpath，对齐 macOS `/var` ↔ `/private/var` 与软链（同 `test_guard._protected`）。
- 只认 Read 工具；Grep 命中、`cat` / `head` 不算，与判据①口径一致。ERD0 证实失败的 Read 不触发 PostToolUse，所以 record 被调用即视为读成功，不解析 `tool_response`。
- fail-open：stdin / 状态文件解析失败、未知子命令、内部异常一律放行，只写 stderr（同 `test_guard` / `docs_guard`）。
- 新建文件同样检查（与 `test_guard` 只拦已有文件不同）：判据①把 Create 也算作编辑。

### 3.4 选型理由

1. **hook（E）而非只改文字（A）**：判据①是工具调用顺序，属于 hook 可确定性判定的性质；拒绝理由在编辑被拒的同一轮进入上下文，比 skill 中的一句规则更难被忽略。hook 作用于会话，A 只作用于调用了 auto-coder 的会话。
2. **按目标路径映射而非「任何编辑前都读 01」**：全局规则会拦下 docs/ 修订、配置调整等与规格无关的改动，摩擦过大；按路径映射能把规则直接对应到 AGENTS.md 的条目。代价见 §3.5 第 3 条。
3. **映射表放 hook 内而非解析 AGENTS.md**：AGENTS.md 是自然语言，解析脆弱；常量表加出处列更易审阅，单测可锁定。
4. **不拦 Execute 写入**：判据①不把 Execute 算作编辑；且 Execute 写入的启发式识别已在 `test_guard` 证明覆盖有限，收益不抵复杂度。

### 3.5 已知代价与风险

1. **eval 含义变化**：加 hook 后，判据①测的是「hook 是否生效且映射是否覆盖」，不再是「模型是否自觉先读」。只要 hook 对真实开发会话同样生效、映射来自既有规则，可以接受——目标是行为本身。
2. **只保证打开过，不保证读懂**：带 `limit` 的 Read 也算数（判据①同样如此），hook 可能诱发「为解锁而读几行」。见 §3.6 第 1 条。
3. **口径不一致**：判据①看整个会话的「第一次编辑之前」，hook 看「这次编辑的目标路径」。agent 若先改映射表外的文件（如 docs/），hook 放行而判据①失败。ERD0 已据此补了 `.agents/plans/` 一条；ERD2 继续统计此类 run，只在有真实规则依据时扩充映射。
4. **只约束 Droid 会话**：同 `test_guard` 局限。
5. **日常开发摩擦**：一行小改也要先读规格；初版不设逃生口，见 §3.6 第 2 条。
6. **evals 成本**：ERD1 的 PR 改 `.factory/hooks*` 与 AGENTS.md，每次推送这些路径都触发 15 次付费会话；实现阶段应尽量把 steering 改动集中到少数几次推送。

### 3.6 待决问题（ERD1 实现前裁决）

1. 是否要求 Read 覆盖一定行数（或不带 `limit`）才算「读过」；超长规格（01 DEV_SPEC 3229 行）的 token 代价与读纪律之间如何取舍。默认：任何成功的 Read 都算，与判据①一致。**裁决（2026-11-15，使用者确认）：采用默认。**
2. 摩擦过大时是否引入逃生口，以及如何防止 agent 用它绕过。默认：不设。**裁决（2026-11-15，使用者确认）：采用默认，不设。**

## 4. 测试方案

- **离线确定性测试**：新增 `tests/unit/test_read_guard_hook.py`，对齐 `test_test_guard_hook.py` 的做法：伪造 stdin 载荷，以子进程或直接调用入口执行 hook，经 `TEST_GUARD_PROJECT_DIR` 同类环境变量把项目根指向 `tmp_path`。覆盖：
  - record：成功的 Read 被记录；失败的 Read 不记录；绝对路径与相对路径、realpath 差异归一；项目外路径忽略。
  - pre：未读必读时 Edit / Create / ApplyPatch 被 `deny` 且理由列出确切路径；读后放行；「任一即可」的备选生效；摄取路径要求 01 + 04；QA 进度文件要求计划文件；未命中映射放行；ApplyPatch 多文件时任一缺读即拒绝。
  - reset：`startup` / `clear` 清状态，`resume` / `compact` 保留。
  - 会话隔离：不同 `session_id` 的已读集合互不影响。
  - fail-open：坏 JSON、坏状态文件、未知子命令、缺字段一律放行。
  - 注册一致性：`.factory/hooks.json` 中三个事件的 matcher 与命令路径和脚本子命令一致。
- **前提补验（ERD0）**：本机用临时 worktree + 一个只写日志的 PostToolUse hook 跑一次 `droid exec`（BYOK `custom:glm-5.3-Flash-2`，单会话），记录 Read 的 PostToolUse 载荷样例；不进 `pytest tests`。
- **付费 evals（ERD2）**：真实模型、花 token，由 `agent-evals.yml` 在 ERD1 的 PR 上自动触发，必要时 `workflow_dispatch` 补跑；不进 `pytest tests`。
- **真实依赖冒烟**：除上述两项外不适用。
- **既有测试语义**（不得破坏）：
  - `tests/unit/test_test_guard_hook.py`：`test_guard` 的拦截 / 放行 / reset / fail-open 语义与 hooks.json 注册断言不变（新增条目不得改写其既有条目）。
  - `tests/unit/test_eval_judges.py`：既有用例不改；新增「被拒 / 失败的编辑调用不算首次编辑」与「首个成功编辑之前的 Read 才算」两条用例，锁定 ERD0 的口径修正。
  - `tests/unit/test_skill_mirrors.py`：auto-coder skill 改动后经 `scripts/sync_skills.py` 同步镜像。
  - `tests/unit/test_docs_links.py`、`tests/unit/test_spec_structure.py`：文档零坏链、本规格结构完整。
  - 全量离线 `pytest tests -q` 与 CI 禁网等价命令、`python scripts/check_redlines.py` 全绿。

## 5. 任务排期与进度

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|----------|------|
| ERD0 | 前提补验：本机 exec 会话确认 PostToolUse 对 Read 触发并记录载荷字段；从三次重校准 run 的 CI artifact 汇总逐用例逐判据失败分布与失败 run 的首次编辑路径 | 有日志证据表明 PostToolUse 在 exec 模式触发，§3.3 的成功判定字段定稿；分布表与首次编辑路径回填 §1.3；§3.2 摄取测试模式按实际路径核对 | [x] 2026-11-15：PostToolUse 只对成功的 Read 触发（§3.3 定稿）；发现被拒的编辑调用会被判据①当作首次编辑，决议修正口径（并入 ERD1）；30 次会话分布表回填 §1.3（run 43602805353 下载超时未纳入）；补 `.agents/plans/` 映射；§3.6 按默认裁决 |
| ERD1 | 实现 `.factory/hooks/read_guard.py`（record / pre / reset）+ 登记 `.factory/hooks.json` + §3.2 映射表；判据①跳过 `isError` 的编辑调用（`evals/check.py` + 单测）；AGENTS.md「关键纪律」与 auto-coder skill 各补一句规则说明并运行 `sync_skills.py` | `test_read_guard_hook.py` 覆盖 §4 所列场景且全绿；`test_eval_judges.py`、`test_test_guard_hook.py`、`test_skill_mirrors.py` 绿；全量离线 pytest（CI 禁网等价命令）与 `check_redlines.py` 绿 | [x] 2026-11-15（code complete）：hook + 登记 + 判据①口径修正 + AGENTS.md / auto-coder skill 规则说明（镜像已同步）；新增 Script 模式 transcript 补读（§3.3）；离线测试结果与本机 exec 冒烟见本节进度备注 |
| ERD2 | evals 重跑验证（ERD1 PR 自动触发 + 必要时 `workflow_dispatch`，合计 ≥ 3 次 run） | 判据①失败次数相对 §1.2 基线显著下降；记录「hook 放行但判据①失败」的 run 数；通过用例数稳定 ≥ 3/5 时按实测更新 `baseline.json`（`passing_cases` 与 `min_pass_rate`） | 未开始 |
| ERD3 | （门控：ERD2 两轮无改善）带实测数据评估换模型，走 D5 决策 | D5 决策记录更新；新模型校准基线写入 `baseline.json` 与 `evals/ci/factory-settings.json` | 未开始 |

依赖顺序：ERD0 → ERD1 → ERD2 →（门控）ERD3。

> 进度备注：交付后在各任务下回填状态与实测数据（run 链接、逐用例通过数、token）；全部收口后本规格按冻结清单移入 `docs/frozen/` 并更新 MOC.md。
>
> ERD1 验证（2026-11-15）：`test_read_guard_hook.py` 与 `test_test_guard_hook.py` 共 104 passed，`test_eval_judges.py` 42 passed；CI 禁网等价全量 pytest 2672 passed / 11 skipped / 1 xfailed，`check_redlines.py` 0 违规，`sync_skills.py --check` 一致。本机 exec 冒烟（临时项目只装 read_guard，`custom:glm-5.3-Flash-2` 单会话，任务「把 `src/mod.py` 的 x 改成 2」）：#2 Edit 被拒（理由列出 01 路径）→ #3 Read 01 → #4 Edit 成功；新判据①判通过（首次编辑为 #4），旧口径会截在被拒的 #2 判失败。

## 6. 验收清单

- [ ] Droid 会话在未 Read 必读参考时改 `src/` / `scripts/` / `tests/` / 摄取链路 / `.agents/plans/` / QA 进度文件会被拒绝，理由列出确切要读的路径；读后放行
- [ ] 映射表每条规则都有 AGENTS.md / skill 出处，无一条仅来自 evals 用例
- [ ] `test_guard` 与 `docs_guard` 行为不变
- [ ] evals 通过用例数 ≥ 3/5 并已更新 `baseline.json`；或 ERD3 已带数据启动 D5 决策
- [ ] 全量离线 pytest、`check_redlines.py`、`test_docs_links.py` 全绿
- [ ] 回写完成（§7）

## 7. 实现合入后的回写清单

- `AGENTS.md`：「关键纪律」补 read_guard 规则说明（ERD1 内完成；改动即触发付费 evals）；冻结时按 docs-governance §3 第 5 条判断是否在「开发文档参考」新增条目。
- `evals/README.md`：Fixtures / Judges 节补一句 hook 与判据①的关系。
- `docs/reference/AI_NATIVE_SDLC_REFERENCE.md`：工具清单中 hooks 一项补 read_guard。
- `docs/specs/AI_NATIVE_SDLC_SPEC.md` §3.3：SD3.6 遗留「三条失败用例对应的 steering 改进」处补一行去向（见本规格）。
- `docs/MOC.md`、根目录 `README.md` 导航表：同步规格状态；冻结时更新谱系表本行。
- 无 `src/` 行为、配置项或环境变量变化，`config/settings.yaml` 与 DEV_SPEC 不需回写。

## 8. 隐私注记

不适用：hook 只在本地读写 `.factory/state/` 下的已读路径列表（gitignore，不含文件内容），不新增路由、不落库个人内容、不外发数据。付费 evals 的外发边界沿用 SD3.6（BYOK 智谱端点，key 经 `apiKeyHelper` 注入、不进 agent 会话环境）。
