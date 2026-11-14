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
| `baseline.json` | Calibrated gate: `min_pass_rate` and the cases that passed the baseline run |
| `ci/factory-settings.json` | BYOK model entry used by the CI workflow (no key, env reference only) |

## Fixtures

`run.py` creates a detached `git worktree` at the case's `fixture_commit`, then copies the
steering files under test (`AGENTS.md`, `.factory/skills/` and its `.claude/skills/` /
`.github/skills/` mirrors, `.factory/hooks.json`, `.factory/hooks/`) from the current
checkout into it, so a run measures the steering version being changed rather than the
one frozen at `fixture_commit`. Mirrors are overlaid as well; otherwise the skill-mirror
drift guard fails inside the fixture. The baseline is a
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
python evals/run.py --summarize evals/runs/<dir> [...]     # merge run dirs and apply the gate only
python evals/check.py --case evals/cases/<id>.yaml --fixture <dir> --stream <run.jsonl> --base <tree>
```

`run.py` exits non-zero when the gate fails: the case pass rate is below `--min-pass-rate`
(default: `min_pass_rate` from `baseline.json`), or a case listed in `baseline.json`'s
`passing_cases` fails. A run whose `droid exec` exits non-zero or times out counts as failed.

## CI (`.github/workflows/agent-evals.yml`, SD3.6)

- Triggers: a pull request touching `.factory/skills/**`, `.claude/skills/**`, `.github/skills/**`,
  `AGENTS.md`, `.factory/hooks.json` or `.factory/hooks/**`, or `workflow_dispatch` (inputs
  `cases`, `repeat`). Not `.github/**`: editing any workflow would start a paid run.
- On later pushes to an open PR, the `gate` job runs the evals only if that push itself
  touched a steering path, so doc-only follow-up commits cost nothing.
- One matrix job per case (5 in parallel, 3 sessions each); the `summary` job merges the
  per-case artifacts with `run.py --summarize` and writes the gate result to the job summary.
- Model: `custom:glm-5.3-Flash-0`, the only entry of `ci/factory-settings.json` (copied to
  `~/.factory/settings.json`; same model and endpoint as the local `custom:glm-5.3-Flash-2`).
  Secrets: `FACTORY_API_KEY` (droid auth) and `ZHIPU_API_KEY`. The Zhipu key is written to a
  file outside the fixture and read by droid through `apiKeyHelper`, so it is not in the agent
  session's environment (a baseline qa-tester session probed for it). `FACTORY_API_KEY` must
  stay in droid's environment. Key values are replaced with `***` in run output before upload.
