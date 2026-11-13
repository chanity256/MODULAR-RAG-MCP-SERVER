"""Agent eval runner (docs/specs/AI_NATIVE_SDLC_SPEC.md §3.3, SD3.5).

For each case and each repeat: create a detached worktree at the case's
``fixture_commit``, overlay the steering files under test (``AGENTS.md``,
``.factory/skills/``, ``.factory/hooks.json``, ``.factory/hooks/``) from the current
checkout, snapshot the baseline tree, run ``droid exec``, then judge the run with
``check.py``. A case passes when at least ``--need`` of ``--repeat`` runs pass.

This spends real model tokens; it is never collected by ``pytest tests``.

Usage::

    python evals/run.py                          # all cases, 3 runs each
    python evals/run.py --case sd34-htm-extension --repeat 1
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

#: D5 (§1.3): BYOK GLM via the user's Zhipu endpoint; Factory-hosted glm-5.3-flash returned 404.
DEFAULT_MODEL = "custom:glm-5.3-Flash-2"
DEFAULT_REPEAT = 3
DEFAULT_NEED = 2
DEFAULT_TIMEOUT_MIN = 15
DEFAULT_MIN_PASS_RATE = 0.9

OVERLAY_PATHS = ("AGENTS.md", ".factory/skills", ".factory/hooks.json", ".factory/hooks")


def _listed_files(source: Path, rel: str) -> list[str] | None:
    """Non-ignored files under ``rel`` (tracked + untracked), or None when source is not a git checkout."""
    proc = subprocess.run(
        ["git", "-C", str(source), "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", rel],
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    return [p for p in proc.stdout.decode("utf-8").split("\0") if p]


def overlay_steering(source: Path, fixture: Path) -> None:
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
    return {"case": case_id, "passed": passes >= need, "passes": passes, "runs": len(runs)}


def summarize(cases: list[dict], min_pass_rate: float) -> dict:
    passed = sum(1 for c in cases if c["passed"])
    rate = passed / len(cases) if cases else 0.0
    return {"cases": len(cases), "passed": passed, "pass_rate": round(rate, 3),
            "min_pass_rate": min_pass_rate, "ok": bool(cases) and rate >= min_pass_rate}


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
    parser.add_argument("--min-pass-rate", type=float, default=DEFAULT_MIN_PASS_RATE)
    parser.add_argument("--venv", type=Path, default=REPO_ROOT / ".venv", help="linked into each fixture")
    parser.add_argument("--python", default=sys.executable, help="interpreter for the offline pytest judge")
    parser.add_argument("--out", type=Path, help="output dir (default: evals/runs/<UTC timestamp>)")
    parser.add_argument("--keep-fixtures", action="store_true")
    args = parser.parse_args(argv)

    out = args.out or RUNS_DIR / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    case_results = []
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
                if agent["timed_out"]:
                    report["passed"] = False
                report.update(agent=agent, fixture=str(fixture) if args.keep_fixtures else None)
            finally:
                if not args.keep_fixtures:
                    remove_fixture(REPO_ROOT, fixture)
            (out / f"{tag}.report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                                    encoding="utf-8")
            print(f"{tag}: {'PASS' if report['passed'] else 'FAIL'}", file=sys.stderr)
            runs.append(report)
        case_results.append(aggregate(case["id"], runs, args.need))
    summary = {"model": args.model, **summarize(case_results, args.min_pass_rate), "per_case": case_results}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
