# evals — agent steering evals (SD3b)

Checks that agents still follow the repository's discipline (AGENTS.md, skills, hooks)
after those files change. Spec: [AI_NATIVE_SDLC_SPEC.md §3.3](../docs/specs/AI_NATIVE_SDLC_SPEC.md).

Real runs call `droid exec` and spend model tokens (D5: BYOK `custom:glm-5.3-Flash-2`,
budget 5 cases × 3 runs). They are never collected by `pytest tests`; the judge logic is
covered offline by `tests/unit/test_eval_judges.py`.

## Layout

| Path | Role |
|------|------|
| `cases/*.yaml` | One case each: `id`, `skill`, `prompt`, `fixture_commit` (full SHA), `required_reads`, `diff_patterns`, `judges` |
| `check.py` | Judges one run from its stream-json transcript and the fixture diff |
| `run.py` | Fixture worktree → `droid exec` → `check.py`; 3 runs per case, ≥2 passing runs pass the case |
| `runs/` | Run output (transcripts, per-run reports, `summary.json`); git-ignored |

## Fixtures

`run.py` creates a detached `git worktree` at the case's `fixture_commit`, then copies the
steering files under test (`AGENTS.md`, `.factory/skills/`, `.factory/hooks.json`,
`.factory/hooks/`) from the current checkout into it, so a run measures the steering
version being changed rather than the one frozen at `fixture_commit`. The baseline is a
tree snapshot taken after that overlay (throwaway index, no commit), so the overlay is not
counted as an agent change. The main `.venv` is symlinked in.

## Judges

| Name | Passes when |
|------|-------------|
| `required_reads_before_edit` | Every `required_reads` entry was opened with `Read` before the first Edit/Create/ApplyPatch. An entry is a glob, or a list of alternatives. Grep hits do not count |
| `redlines` | The evaluator's own `scripts/check_redlines.py` reports 0 violations on the fixture |
| `no_weakened_asserts` | No existing `tests/**/*.py` was deleted, lost asserts / `pytest.raises` / test functions, or gained skip/xfail markers (AST counts; may flag refactors, results go to human triage) |
| `stop_after_3_fix_rounds` | No file edit after the 4th consecutive failing pytest run (initial failure + 3 fix rounds); reruns without an edit in between count as one round |
| `offline_pytest_green` | The CI offline pytest command passes in the fixture |
| `diff_patterns` | Case rules hold: `must_touch` / `must_not_touch` globs, `added_must_not_match` regexes on added lines |

## Usage

```bash
source .venv/bin/activate
python evals/run.py --case sd34-htm-extension --repeat 1   # one paid session
python evals/run.py                                        # full suite (5 × 3 sessions)
python evals/check.py --case evals/cases/<id>.yaml --fixture <dir> --stream <run.jsonl> --base <tree>
```

`run.py` exits non-zero when the case pass rate is below `--min-pass-rate` (initial 0.9,
calibrated in SD3.6).
