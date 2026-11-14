"""Agent eval runner (docs/specs/AI_NATIVE_SDLC_SPEC.md §3.3, SD3.5).

For each case and each repeat: create a detached worktree at the case's
``fixture_commit``, overlay the steering files under test (``AGENTS.md``,
``.factory/skills/`` and its ``.claude/`` / ``.github/`` mirrors, ``.factory/hooks*``) from the current
checkout (the fixture keeps its own ``review-guidelines`` skill), snapshot the baseline tree, run ``droid exec``, then judge the run with
``check.py``. A case passes when at least ``--need`` of ``--repeat`` runs pass.

This spends real model tokens; it is never collected by ``pytest tests``.

Usage::

    python evals/run.py                          # all cases, 3 runs each
    python evals/run.py --case sd34-htm-extension --repeat 1
    python evals/run.py --summarize evals/runs/a evals/runs/b   # merge per-case run dirs (CI matrix)

The gate (SD3.6): case pass rate >= the threshold, and no case listed as passing in
``evals/baseline.json`` may fail.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check  # noqa: E402

REPO_ROOT = check.REPO_ROOT
CASES_DIR = REPO_ROOT / "evals" / "cases"
RUNS_DIR = REPO_ROOT / "evals" / "runs"
BASELINE_PATH = REPO_ROOT / "evals" / "baseline.json"

#: D5 (§1.3): BYOK GLM via the user's Zhipu endpoint; Factory-hosted glm-5.3-flash returned 404.
DEFAULT_MODEL = "custom:glm-5.3-Flash-2"
DEFAULT_REPEAT = 3
DEFAULT_NEED = 2
DEFAULT_TIMEOUT_MIN = 15
DEFAULT_MIN_PASS_RATE = 0.9

# Mirrors are overlaid too: a source-only overlay trips test_skill_mirrors in the fixture.
OVERLAY_PATHS = ("AGENTS.md", ".factory/skills", ".claude/skills", ".github/skills",
                 ".factory/hooks.json", ".factory/hooks")


def _listed_files(source: Path, rel: str) -> list[str] | None:
    """Non-ignored files under ``rel`` (tracked + untracked), or None when source is not a git checkout."""
    proc = subprocess.run(
        ["git", "-C", str(source), "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", rel],
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    return [p for p in proc.stdout.decode("utf-8").split("\0") if p]


# review-guidelines is read only by the CI Droid review, never by eval sessions, and the fixture's own
# test_review_guidelines.py locks it to the fixture's REVIEW.md: overlaying it alone breaks that test.
OVERLAY_KEEP_FIXTURE = (".factory/skills/review-guidelines",)


def overlay_steering(source: Path, fixture: Path) -> None:
    with tempfile.TemporaryDirectory() as stash:
        kept = []
        for rel in OVERLAY_KEEP_FIXTURE:
            if (fixture / rel).exists():
                shutil.move(str(fixture / rel), str(Path(stash) / rel.replace("/", "__")))
                kept.append(rel)
        _overlay_paths(source, fixture)
        for rel in OVERLAY_KEEP_FIXTURE:
            if (fixture / rel).is_dir():
                shutil.rmtree(fixture / rel)
            elif (fixture / rel).exists():
                (fixture / rel).unlink()
            if rel in kept:
                (fixture / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(Path(stash) / rel.replace("/", "__")), str(fixture / rel))


def _overlay_paths(source: Path, fixture: Path) -> None:
    for rel in OVERLAY_PATHS:
        src, dst = source / rel, fixture / rel
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        elif dst.exists() or dst.is_symlink():
            dst.unlink()
        files = _listed_files(source, rel)
        if files is None:
            if src.is_dir():
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
            elif src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            continue
        # Ignored files (e.g. auto-coder's generated references/) must not leak in: CI checkouts lack them.
        for name in files:
            if (source / name).is_file():
                (fixture / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / name, fixture / name)


def prepare_fixture(repo: Path, commit: str, dest: Path, overlay_from: Path | None = None,
                    venv: Path | None = None) -> str:
    """Create the fixture worktree and return its baseline tree hash."""
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(dest), commit],
                   check=True, capture_output=True, text=True)
    if overlay_from is not None:
        overlay_steering(overlay_from, dest)
    if venv is not None and venv.exists() and not (dest / ".venv").exists():
        (dest / ".venv").symlink_to(venv.resolve())
    return check.snapshot_tree(dest)


def remove_fixture(repo: Path, dest: Path) -> None:
    proc = subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(dest)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        subprocess.run(["git", "-C", str(repo), "worktree", "prune"], capture_output=True)


def agent_command(model: str, fixture: Path, prompt_file: Path) -> list[str]:
    return ["droid", "exec", "--auto", "medium", "-o", "stream-json", "-m", model,
            "--cwd", str(fixture), "-f", str(prompt_file)]


def run_agent(cmd: list[str], stream_path: Path, timeout_s: int) -> dict:
    started = time.monotonic()
    with stream_path.open("w", encoding="utf-8") as out, \
            stream_path.with_suffix(".stderr.log").open("w", encoding="utf-8") as err:
        try:
            proc = subprocess.run(cmd, stdout=out, stderr=err, timeout=timeout_s)
            code, timed_out = proc.returncode, False
        except subprocess.TimeoutExpired:
            code, timed_out = None, True
    return {"exit_code": code, "timed_out": timed_out, "seconds": round(time.monotonic() - started, 1)}


def aggregate(case_id: str, runs: list[dict], need: int = DEFAULT_NEED) -> dict:
    passes = sum(1 for r in runs if r.get("passed"))
    judge_failures: dict[str, int] = {}
    for r in runs:
        for name, result in (r.get("judges") or {}).items():
            if not result.get("passed"):
                judge_failures[name] = judge_failures.get(name, 0) + 1
    agent_errors = sum(1 for r in runs if _agent_failed(r.get("agent") or {}))
    return {"case": case_id, "passed": passes >= need, "passes": passes, "runs": len(runs),
            "judge_failures": judge_failures, "agent_errors": agent_errors}


def summarize(cases: list[dict], min_pass_rate: float, baseline: dict | None = None) -> dict:
    passed = sum(1 for c in cases if c["passed"])
    rate = passed / len(cases) if cases else 0.0
    was_passing = set((baseline or {}).get("passing_cases", []))
    regressions = sorted(c["case"] for c in cases if c.get("case") in was_passing and not c["passed"])
    return {"cases": len(cases), "passed": passed, "pass_rate": round(rate, 3),
            "min_pass_rate": min_pass_rate, "regressions": regressions,
            "ok": bool(cases) and rate >= min_pass_rate and not regressions}


def _agent_failed(agent: dict) -> bool:
    return bool(agent) and (agent.get("timed_out") or agent.get("exit_code") != 0)


def load_baseline(path: Path | None) -> dict | None:
    if path is None or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def collect_reports(dirs: list[Path]) -> dict[str, list[dict]]:
    """Per-run reports grouped by case id, in run order."""
    by_case: dict[str, list[dict]] = {}
    for d in dirs:
        for path in sorted(Path(d).rglob("*.report.json")):
            report = json.loads(path.read_text(encoding="utf-8"))
            by_case.setdefault(report["case"], []).append(report)
    return dict(sorted(by_case.items()))


def usage_totals(by_case: dict[str, list[dict]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for runs in by_case.values():
        for r in runs:
            for key, value in (r.get("usage") or {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    totals[key] = totals.get(key, 0) + int(value)
    return totals


def render_markdown(summary: dict) -> str:
    verdict = "PASS" if summary["ok"] else "FAIL"
    lines = [
        f"## Agent evals: {verdict}",
        "",
        f"Model `{summary['model']}`; cases passed {summary['passed']}/{summary['cases']} "
        f"(rate {summary['pass_rate']}, threshold {summary['min_pass_rate']}); "
        f"regressions vs baseline: {', '.join(summary['regressions']) or 'none'}.",
        "",
        "| Case | Passing runs | Result | Judge failures (runs) | Agent errors |",
        "|------|--------------|--------|-----------------------|--------------|",
    ]
    for c in summary["per_case"]:
        failures = ", ".join(f"{k} ×{v}" for k, v in sorted(c["judge_failures"].items())) or "-"
        lines.append(f"| `{c['case']}` | {c['passes']}/{c['runs']} | {'PASS' if c['passed'] else 'FAIL'} "
                     f"| {failures} | {c['agent_errors']} |")
    if summary.get("usage"):
        lines += ["", "Token usage (all runs): "
                  + ", ".join(f"{k} {v:,}" for k, v in sorted(summary["usage"].items()))]
    return "\n".join(lines) + "\n"


def write_summary(out: Path, summary: dict, md_out: Path | None) -> None:
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown = render_markdown(summary)
    if md_out is not None:
        with md_out.open("a", encoding="utf-8") as fh:
            fh.write(markdown)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _select_cases(ids: list[str]) -> list[tuple[Path, dict]]:
    selected = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        case = check.load_case(path)
        if not ids or case["id"] in ids:
            selected.append((path, case))
    unknown = set(ids) - {c["id"] for _, c in selected}
    if unknown:
        raise SystemExit(f"unknown case id(s): {sorted(unknown)}")
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run agent steering evals (SD3.5).")
    parser.add_argument("--case", action="append", default=[], help="case id (repeatable; default: all)")
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT)
    parser.add_argument("--need", type=int, default=DEFAULT_NEED, help="passing runs needed per case")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout-min", type=int, default=DEFAULT_TIMEOUT_MIN, help="per agent session")
    parser.add_argument("--min-pass-rate", type=float,
                        help=f"default: the baseline's min_pass_rate, else {DEFAULT_MIN_PASS_RATE}")
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH,
                        help="cases listed as passing here must not fail")
    parser.add_argument("--summarize", nargs="+", type=Path, metavar="DIR",
                        help="only merge *.report.json under DIRs and apply the gate (no agent runs)")
    parser.add_argument("--md-out", type=Path, help="append a Markdown summary (e.g. $GITHUB_STEP_SUMMARY)")
    parser.add_argument("--venv", type=Path, default=REPO_ROOT / ".venv", help="linked into each fixture")
    parser.add_argument("--python", default=sys.executable, help="interpreter for the offline pytest judge")
    parser.add_argument("--out", type=Path, help="output dir (default: evals/runs/<UTC timestamp>)")
    parser.add_argument("--keep-fixtures", action="store_true")
    args = parser.parse_args(argv)
    baseline = load_baseline(args.baseline)
    min_pass_rate = args.min_pass_rate
    if min_pass_rate is None:
        min_pass_rate = (baseline or {}).get("min_pass_rate", DEFAULT_MIN_PASS_RATE)

    out = args.out or RUNS_DIR / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)

    if args.summarize:
        by_case = collect_reports(args.summarize)
        case_results = [aggregate(cid, runs, args.need) for cid, runs in by_case.items()]
        summary = {"model": args.model, **summarize(case_results, min_pass_rate, baseline),
                   "per_case": case_results, "usage": usage_totals(by_case)}
        write_summary(out, summary, args.md_out)
        return 0 if summary["ok"] else 1

    case_results = []
    by_case: dict[str, list[dict]] = {}
    for _, case in _select_cases(args.case):
        runs = []
        for i in range(1, args.repeat + 1):
            tag = f"{case['id']}-{i}"
            fixture = Path(tempfile.mkdtemp(prefix=f"rag-eval-{tag}-"))
            try:
                base = prepare_fixture(REPO_ROOT, str(case["fixture_commit"]), fixture,
                                       overlay_from=REPO_ROOT, venv=args.venv)
                prompt_file = out / f"{tag}.prompt.txt"
                prompt_file.write_text(case["prompt"], encoding="utf-8")
                stream = out / f"{tag}.jsonl"
                agent = run_agent(agent_command(args.model, fixture, prompt_file), stream, args.timeout_min * 60)
                report = check.evaluate_case(case, fixture, stream, base=base, python=args.python)
                # A crashed or rate-limited session changes nothing, which judges 2/3/5 alone would pass.
                if _agent_failed(agent):
                    report["passed"] = False
                report.update(agent=agent, fixture=str(fixture) if args.keep_fixtures else None)
            finally:
                if not args.keep_fixtures:
                    remove_fixture(REPO_ROOT, fixture)
            (out / f"{tag}.report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                                    encoding="utf-8")
            print(f"{tag}: {'PASS' if report['passed'] else 'FAIL'}", file=sys.stderr)
            runs.append(report)
            by_case.setdefault(case["id"], []).append(report)
        case_results.append(aggregate(case["id"], runs, min(args.need, args.repeat)))
    summary = {"model": args.model, **summarize(case_results, min_pass_rate, baseline),
               "per_case": case_results, "usage": usage_totals(by_case)}
    write_summary(out, summary, args.md_out)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
