---
name: auto-coder
description: Autonomous spec-driven development agent. Syncs 01_DEV_SPEC_frozen.md and active specs (docs/specs/*.md) into chapter-based reference files, identifies the next pending task from the schedules, implements code following spec architecture and patterns, runs tests with up to 3 auto-fix rounds, and persists progress with atomic commits. Use when user says "auto code", "自动开发", "自动写代码", "auto dev", "一键开发", "autopilot", or wants fully automated spec-to-code workflow.
---

# Auto Coder

One trigger completes **read spec → find task → code → test → persist progress**.

Optional modifiers: append a task ID (e.g. `auto code B2`) to target a specific task, or `--no-commit` to skip git commit.

---

## Pipeline

```
Sync Spec → Find Task → Implement → Test (≤3 fix rounds) → Persist
```

Pause ONLY at these points, run everything else autonomously:
1. **Gated tasks** — tasks marked `门控` in the schedule (real LLM / real sidecar, costs tokens) need explicit user confirmation before gated verification runs.
2. **Commit confirmation** — at the end of each task.

> **⚠️ CRITICAL: Activate `.venv` before ANY `python`/`pytest` command (idempotent, re-run if unsure).**
> - **Windows**: `.\.venv\Scripts\Activate.ps1`
> - **macOS/Linux**: `source .venv/bin/activate`

## Reference Map

All files under `.github/skills/auto-coder/references/`. The **main spec** (DEV_SPEC) produces unprefixed files; each **incremental spec** (docs/specs/*.md) produces files prefixed with its own prefix (e.g. `sx-`), so they never clobber each other:

| File | Content | When to Read |
|------|---------|-------------|
| `01-overview.md` | Project overview & goals (main spec) | First task or when needing project context |
| `02-features.md` | Feature specifications (main spec) | When implementing feature-related tasks |
| `03-tech-stack.md` | Tech stack & dependencies | When choosing libraries or patterns |
| `04-testing.md` | Testing conventions | When writing tests (a task's own spec test plan overrides it, see Implement step) |
| `05-architecture.md` | Architecture & module design | When creating/modifying modules |
| `06-schedule.md` | Main spec task schedule & status | Every cycle (Sync Spec step) |
| `07-future.md` | Future roadmap | When planning or assessing scope |
| `sx-*.md` | Incremental spec chapters (e.g. SIRCHMUNK_EXTENSION_SPEC) | When working on its task series |

---

### 1. Sync Spec

```powershell
# Main spec (01_DEV_SPEC_frozen.md)
python .github/skills/auto-coder/scripts/sync_spec.py

# Each active incremental spec under docs/specs/ (prefix = its own series)
python .github/skills/auto-coder/scripts/sync_spec.py --spec docs/specs/SIRCHMUNK_EXTENSION_SPEC.md --prefix sx
```

Then read **every schedule file** to get task statuses (glob `references/*schedule*.md` — e.g. `06-schedule.md`, `sx-07-schedule.md`).

Task markers:

| Marker | Status |
|--------|--------|
| `[ ]` / `⬜` | Not started |
| `[~]` / `🔶` / `(进行中)` | In progress |
| `[x]` / `✅` / `(已完成)` | Completed |

Additional task flags (from the schedule's 备注/notes column):

| Flag | Meaning |
|------|---------|
| `门控` | Gated task — requires real LLM/sidecar via env var (e.g. `RAG_AGENTIC_INTEGRATION=1`); NOT auto-runnable |
| gate verdict reference (e.g. `门控 GX`) | Task is blocked until an upstream gate verdict is recorded in the spec |

---

### 2. Find Task

Search **all schedule files** (all synced specs). Pick the first `IN_PROGRESS` task, then the first `NOT_STARTED`. If user specified a task ID, use that directly.

Task-selection constraints:

- **Gated tasks are not auto-selectable.** Skip any task flagged `门控`, and any task blocked by an unrecorded gate verdict. If the only remaining tasks are gated/blocked → STOP and hand the list to the user.
- **A gated task CAN be partially executed**: its offline deliverables (code + offline tests) may be implemented, but completion requires the gated verification (see Test step) — pause there.
- **Doc tasks** (deliverable lives under `docs/`, e.g. gap-analysis updates, spec errata, baseline archival): use doc mode — write per the spec section, self-review against the acceptance points, skip pytest entirely.

Quick-check predecessor artifacts exist (file-level only). On mismatch, log a warning and continue — only stop if the target task itself is blocked.

---

### 3. Implement

2. **Extract** from spec: inputs/outputs, design principles (Pluggable? Config-driven? Factory?), file list, acceptance criteria.

3. **Test-plan precedence**: if the owning spec has its own test plan section (e.g. its §5 测试方案), it **overrides** the generic `04-testing.md` conventions. `04-testing.md` is a fallback for tasks without a spec-level plan.

4. **Plan** files to create/modify before writing any code. A task is **complex** if ANY of these holds:
   - it touches 2+ top-level packages under `src/` (e.g. `core/` + `ingestion/`);
   - it changes a storage structure (Chroma metadata fields, SQLite tables, BM25 index format);
   - it changes a pipeline stage contract, an MCP tool schema, or the structure of `config/settings.yaml`.

   For a complex task, write `.agents/plans/<task-id>.md` BEFORE coding (format: `.agents/plans/README.md`): files to change, order, risks, and how correctness will be proven. If the implementation deviates, update the plan in the same commit; the plan is committed with the task as an audit record. Simple tasks skip the file and use the schedule's acceptance-criteria column.

5. **Code** — project-specific rules:
   - Treat spec as single source of truth
   - Use `config/settings.yaml` values, never hardcode
   - Match existing codebase patterns and style
   - Check the spec's **Out of Scope / 不做** table before writing anything adjacent to it

6. **Write tests** alongside code (skip for doc tasks):
   - Place in `tests/unit/` or `tests/integration/` per the governing test plan
   - Mock external deps in unit tests (zero network, zero real models)

7. **Self-review** before running tests: verify all planned files exist and tests import correctly.

8. **Red-line checklist** — verify NONE of these are violated by the planned change (a violation means STOP and re-plan, never commit):

   - Main venv never `import sirchmunk`; sirchmunk is reached only via HTTP sidecar (`src/libs/agentic_search/http_backend.py`)
   - Never modify sirchmunk upstream source code
   - `llm_fallback` stays hard-locked `false` unless a spec explicitly says otherwise
   - MCP server stdout outputs only legal JSON-RPC; all logs go to stderr
   - Offline determinism: unit/integration tests zero-network, external deps injected fakes
   - Pipeline-supported extensions registered in BOTH `src/ingestion/pipeline.py` and `scripts/ingest.py`
   - If the spec pins an upstream baseline commit and sirchmunk has since changed version → source-evidence items must be re-verified before coding against them

---

### 4. Test & Auto-Fix

```

Round 0..2:
  Run pytest on relevant test file (default offline run — must be all green, gated tests skip by default)
  If pass → go to step 5
  If fail → analyze error, apply fix, re-run

Round 3 still failing → STOP, show failure report to user
```

**Fix-round count** (recorded in the commit trailer, see Persist): `Fix-Rounds: N`, where N is the number of fix-and-rerun rounds needed before the run went green (0 = green on the first run). Doc tasks that skip pytest record `Fix-Rounds: 0`.

**Completion semantics — two distinct states:**

- **Code complete**: offline pytest all green. For a gated task this is NOT "done" — it means implementation finished, gated verification pending.
- **Verified**: the task's gated checks passed (real sidecar / real LLM). Gated verification (`RAG_LLM_INTEGRATION` / `RAG_E2E_INTEGRATION` / `RAG_AGENTIC_INTEGRATION` = 1) is **never auto-run** — it costs real tokens and may need the sidecar started. Pause and ask the user whether to run it now, defer, or mark code-complete-only in the schedule.

---

### 5. Persist

1. **Update the OWNING spec** — the spec file the task's schedule came from (main: `docs/frozen/01_DEV_SPEC_frozen.md`; incremental: e.g. `docs/specs/SIRCHMUNK_EXTENSION_SPEC.md`): change task marker `[ ]` → `[x]`, fill in 完成日期/备注 as the table provides. Never edit the generated reference files by hand.
2. **Spec freeze discipline**: when the LAST task of an incremental spec closes, follow its own closing note — move the spec into `docs/frozen/` (`*_frozen.md`), and update `docs/MOC.md` plus the README 开发文档 nav table. Do not add new tasks to a frozen spec's table.
3. **Re-sync**: `python .github/skills/auto-coder/scripts/sync_spec.py --force` (plus `--spec ... --prefix ... --force` for the owning incremental spec)
4. **Show summary & ask**:

```
✅ [SX1.1] telemetry 契约 + 白名单解析 — done (code complete)
   Files: src/libs/agentic_search/converter.py, tests/unit/test_agentic_converter.py
   Tests: 12/12 passed (offline)
   Commit: feat(agentic): [SX1.1] parse top-level telemetry whitelist
           Fix-Rounds: 1

   "commit" → git add + commit
   "skip"   → end
   "next"   → commit + start next task
```

Commit message format: `<type>(<scope>): [<SERIES-N>] <summary>` — scope and type follow the change (e.g. `feat(agentic)`, `docs(spec)`, `chore(scripts)`), series prefix matches the task ID. Every task commit carries the trailer `Fix-Rounds: N` (see Test & Auto-Fix) on its own line in the trailer block.

**Branch & PR flow** (AI_NATIVE_SDLC_SPEC D1 / SD6.5). Check the D1 row of `docs/specs/AI_NATIVE_SDLC_SPEC.md` §1.3 (or its frozen successor):
- **D1 not yet in effect** (no branch protection on `main`): commit on the current branch and **do not push**.
- **D1 in effect**: start each series (or each large task) from `main` on a branch named `feat/<series>-<task>`. Persist = commit → push to the remote chosen in D2 → open a PR whose description cites the task ID, its acceptance criteria and the plan path (if any) → wait for CI and review. Important review findings must be fixed or answered in a PR comment before merge. On "next", continue only with tasks that do **not** depend on an unmerged PR; dependent tasks wait for the merge.
- PRs that touch steering files (`.github/skills/` or its mirrors, `AGENTS.md`, `.factory/hooks*`) also trigger `agent-evals.yml`, a paid real-model run (~20 min). Batch steering edits into as few pushes as possible, and read its summary before merge: a case listed as passing in `evals/baseline.json` that now fails must be fixed or explained in the PR.

On "next", loop back to step 1 and start the next task.
