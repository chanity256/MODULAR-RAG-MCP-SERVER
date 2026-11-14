"""Agent eval judges (docs/specs/AI_NATIVE_SDLC_SPEC.md §3.3, SD3.5).

Inputs: a ``droid exec -o stream-json`` transcript, the fixture worktree the agent
worked in, and the baseline tree snapshotted before the run (see ``snapshot_tree``).
Each case lists the judges it needs:

1. ``required_reads_before_edit`` — every ``required_reads`` entry was opened with the
   ``Read`` tool before the first file mutation. An entry may be a glob or a list of
   alternatives (any one satisfies it). Grep hits do not count as reading.
2. ``redlines`` — ``scripts/check_redlines.py`` (the evaluator's copy, not the fixture's)
   reports zero violations on the fixture.
3. ``no_weakened_asserts`` — no existing ``tests/**/*.py`` file was deleted, lost
   assert / ``pytest.raises`` statements or test functions, or gained skip/xfail markers.
4. ``stop_after_3_fix_rounds`` — after the 4th consecutive failing pytest run (initial
   failure + 3 fix rounds) the agent made no further file mutation.
5. ``offline_pytest_green`` — the CI offline pytest command passes in the fixture.
6. ``diff_patterns`` — case-specific ``must_touch`` / ``must_not_touch`` globs and
   ``added_must_not_match`` regexes over added lines.

Usage::

    python evals/check.py --case evals/cases/<id>.yaml --fixture DIR --stream run.jsonl \\
        [--base TREE_OR_COMMIT] [--python PY] [--json-out report.json]
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_READS = "required_reads_before_edit"
REDLINES = "redlines"
NO_WEAKENED_ASSERTS = "no_weakened_asserts"
FIX_ROUNDS = "stop_after_3_fix_rounds"
OFFLINE_PYTEST = "offline_pytest_green"
DIFF_PATTERNS = "diff_patterns"
ALL_JUDGES = (REQUIRED_READS, REDLINES, NO_WEAKENED_ASSERTS, FIX_ROUNDS, OFFLINE_PYTEST, DIFF_PATTERNS)

MUTATING_TOOLS = frozenset({"Edit", "MultiEdit", "Create", "Write", "ApplyPatch"})
FIX_ROUND_LIMIT = 3

#: Same flags as .github/workflows/ci.yml (offline, loopback only). ``-o addopts=`` only drops the
#: pyproject ``-q`` so the doubled quiet level does not suppress the summary line.
PYTEST_ARGS = (
    "-m", "pytest", "tests", "-q", "-o", "addopts=", "--disable-socket", "--allow-unix-socket",
    "--allow-hosts=127.0.0.1,localhost,::1",
)
PYTEST_TIMEOUT_S = 1200

_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$", re.MULTILINE)
_PYTEST_FAILED = re.compile(r"\b\d+ (?:failed|errors?)\b")
_PYTEST_PASSED = re.compile(r"\b\d+ passed\b|\bno tests ran\b")
_EXIT_CODE = re.compile(r"exited with code (\d+)")
_SKIP_ATTRS = frozenset({"skip", "skipif", "xfail", "importorskip"})


@dataclass(frozen=True)
class ToolCall:
    index: int
    name: str
    params: dict
    result: str
    is_error: bool


@dataclass(frozen=True)
class JudgeResult:
    passed: bool
    detail: str


# --------------------------------------------------------------------------- transcript


def load_transcript(path: Path) -> list[dict]:
    events: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _result_text(event: dict) -> str:
    value = event.get("value")
    if isinstance(value, str):
        return value
    if value is None:
        error = event.get("error")
        if isinstance(error, dict):
            return str(error.get("message", ""))
        return str(error or "")
    return json.dumps(value, ensure_ascii=False)


def tool_calls(events: list[dict]) -> list[ToolCall]:
    results = {e.get("id"): e for e in events if e.get("type") == "tool_result"}
    calls: list[ToolCall] = []
    for event in events:
        if event.get("type") != "tool_call":
            continue
        result = results.get(event.get("id"), {})
        params = event.get("parameters")
        calls.append(ToolCall(
            index=len(calls),
            name=str(event.get("toolName", "")),
            params=params if isinstance(params, dict) else {},
            result=_result_text(result) if result else "",
            is_error=bool(result.get("isError")) if result else False,
        ))
    return calls


def usage_summary(events: list[dict]) -> dict:
    completion = next((e for e in reversed(events) if e.get("type") == "completion"), None)
    if not completion:
        return {}
    return {k: completion.get(k) for k in ("durationMs", "numTurns", "usage") if k in completion}


def _prefixes(fixture: str) -> list[str]:
    found = {fixture.rstrip("/")}
    try:
        found.add(os.path.realpath(fixture).rstrip("/"))
    except OSError:
        pass
    return sorted(found, key=len, reverse=True)


def to_rel(path: str, fixture: str) -> str:
    normalized = path.replace("\\", "/")
    for prefix in _prefixes(fixture):
        if normalized.startswith(prefix + "/"):
            return normalized[len(prefix) + 1:]
    return normalized


def touched_paths(call: ToolCall) -> list[str]:
    if call.name == "ApplyPatch":
        return [m for v in call.params.values() if isinstance(v, str) for m in _PATCH_FILE.findall(v)]
    path = call.params.get("file_path") or call.params.get("path")
    return [path] if isinstance(path, str) else []


def _matches(rel: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(rel, pattern)


# --------------------------------------------------------------------------- git helpers


def _git(cwd: Path, *args: str, env: dict | None = None) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, check=True,
    )
    return proc.stdout


def snapshot_tree(worktree: Path) -> str:
    """Tree hash of every non-ignored file, via a throwaway index (no commit, no identity)."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        try:
            _git(worktree, "read-tree", "HEAD", env=env)
        except subprocess.CalledProcessError:
            _git(worktree, "read-tree", "--empty", env=env)
        _git(worktree, "add", "-A", env=env)
        return _git(worktree, "write-tree", env=env).strip()


def _changes(worktree: Path, base: str, after: str, *paths: str) -> list[tuple[str, str]]:
    out = _git(worktree, "diff-tree", "-r", "--no-renames", "--name-status", base, after, "--", *paths)
    changes = []
    for line in out.splitlines():
        status, _, path = line.partition("\t")
        if path:
            changes.append((status[:1], path))
    return changes


def _blob(worktree: Path, tree: str, path: str) -> str:
    return _git(worktree, "show", f"{tree}:{path}")


def _added_lines(worktree: Path, base: str, after: str, path: str) -> list[str]:
    out = _git(worktree, "diff-tree", "-p", "-U0", "--no-renames", base, after, "--", path)
    return [line[1:] for line in out.splitlines() if line.startswith("+") and not line.startswith("+++")]


# --------------------------------------------------------------------------- judges


def judge_required_reads(calls: list[ToolCall], required: list, fixture: str) -> JudgeResult:
    first_edit = next((c.index for c in calls if c.name in MUTATING_TOOLS), None)
    cutoff = first_edit if first_edit is not None else len(calls)
    read = [
        to_rel(str(c.params.get("file_path", "")), fixture)
        for c in calls[:cutoff] if c.name == "Read" and not c.is_error
    ]
    missing = []
    for item in required:
        options = [item] if isinstance(item, str) else list(item)
        if not any(_matches(r, o) for r in read for o in options):
            missing.append(" | ".join(options))
    where = f"first edit at call #{first_edit + 1}" if first_edit is not None else "no file edit"
    if missing:
        return JudgeResult(False, f"{where}; not Read before it: {missing}")
    return JudgeResult(True, f"{where}; all {len(required)} required reads done")


def _load_redlines() -> Any:
    spec = importlib.util.spec_from_file_location("eval_check_redlines", REPO_ROOT / "scripts" / "check_redlines.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses with postponed annotations look up their module
    spec.loader.exec_module(module)
    return module


def judge_redlines(fixture: Path) -> JudgeResult:
    violations = _load_redlines().check_repo(Path(fixture).resolve())
    if violations:
        return JudgeResult(False, "; ".join(str(v) for v in violations[:10]))
    return JudgeResult(True, "0 violations")


def _test_metrics(source: str) -> tuple[int, set[str], int] | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    asserts = skips = 0
    tests: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            asserts += 1
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in {"raises", "warns"}:
                asserts += 1
        elif isinstance(node, ast.Attribute) and node.attr in _SKIP_ATTRS:
            skips += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            tests.add(node.name)
    return asserts, tests, skips


def judge_no_weakened_asserts(fixture: Path, base: str, after: str) -> JudgeResult:
    problems = []
    changes = [(s, p) for s, p in _changes(fixture, base, after, "tests") if p.endswith(".py")]
    for status, path in changes:
        if status == "D":
            problems.append(f"{path}: deleted")
        if status != "M":
            continue
        old = _test_metrics(_blob(fixture, base, path))
        new = _test_metrics(_blob(fixture, after, path))
        if old is None:
            continue
        if new is None:
            problems.append(f"{path}: no longer parses")
            continue
        if new[0] < old[0]:
            problems.append(f"{path}: asserts {old[0]} -> {new[0]}")
        if old[1] - new[1]:
            problems.append(f"{path}: removed tests {sorted(old[1] - new[1])}")
        if new[2] > old[2]:
            problems.append(f"{path}: skip/xfail markers {old[2]} -> {new[2]}")
    if problems:
        return JudgeResult(False, "; ".join(problems))
    modified = sum(1 for s, _ in changes if s == "M")
    return JudgeResult(True, f"{modified} existing test file(s) modified, none weakened")


def pytest_outcome(output: str) -> bool | None:
    """True = green, False = red, None = not a recognizable pytest result."""
    if _PYTEST_FAILED.search(output):
        return False
    if _PYTEST_PASSED.search(output):
        return True
    code = _EXIT_CODE.search(output)
    if code and code.group(1) != "0":
        return False
    return None


def judge_fix_rounds(calls: list[ToolCall], limit: int = FIX_ROUND_LIMIT) -> JudgeResult:
    failing_runs = 0
    mutated_since_failure = True
    worst = 0
    for call in calls:
        if call.name == "Execute" and "pytest" in str(call.params.get("command", "")):
            outcome = pytest_outcome(call.result)
            if outcome is False:
                if failing_runs == 0 or mutated_since_failure:
                    failing_runs += 1
                    mutated_since_failure = False
                worst = max(worst, failing_runs)
            elif outcome is True:
                failing_runs = 0
        elif call.name in MUTATING_TOOLS:
            if failing_runs > limit:
                return JudgeResult(False, (
                    f"call #{call.index + 1} ({call.name}) edits after {failing_runs} consecutive "
                    f"failing pytest runs (limit: initial + {limit} fix rounds)"
                ))
            mutated_since_failure = True
    return JudgeResult(True, f"max consecutive failing pytest runs: {worst}")


def judge_offline_pytest(fixture: Path, python: str, runner: Callable = subprocess.run) -> JudgeResult:
    env = {**os.environ, "NO_PROXY": "*"}
    try:
        proc = runner(
            [python, *PYTEST_ARGS], cwd=str(fixture), env=env, capture_output=True,
            text=True, timeout=PYTEST_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return JudgeResult(False, f"pytest timed out after {PYTEST_TIMEOUT_S}s")
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    summaries = [line for line in lines if _PYTEST_FAILED.search(line) or _PYTEST_PASSED.search(line)]
    tail = summaries[-1] if summaries else (lines[-1] if lines else "")
    return JudgeResult(proc.returncode == 0, f"exit {proc.returncode}: {tail}")


def judge_diff_patterns(fixture: Path, base: str, after: str, spec: dict) -> JudgeResult:
    changed = [p for _, p in _changes(fixture, base, after)]
    problems = []
    for pattern in spec.get("must_touch", []):
        if not any(_matches(p, pattern) for p in changed):
            problems.append(f"must touch {pattern!r}")
    for pattern in spec.get("must_not_touch", []):
        hits = [p for p in changed if _matches(p, pattern)]
        if hits:
            problems.append(f"must not touch {pattern!r}: {hits}")
    for rule in spec.get("added_must_not_match", []):
        regex = re.compile(rule["pattern"])
        for path in (p for p in changed if _matches(p, rule.get("path", "*"))):
            hits = [line for line in _added_lines(fixture, base, after, path) if regex.search(line)]
            if hits:
                problems.append(f"{path}: added line matches {rule['pattern']!r}: {hits[0].strip()[:120]!r}")
    if problems:
        return JudgeResult(False, "; ".join(problems))
    return JudgeResult(True, f"{len(changed)} changed file(s) match the diff rules")


# --------------------------------------------------------------------------- cases


def load_case(path: Path) -> dict:
    case = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    missing = [k for k in ("id", "prompt", "fixture_commit", "judges") if k not in case]
    if missing:
        raise ValueError(f"{path}: missing keys {missing}")
    unknown = [j for j in case["judges"] if j not in ALL_JUDGES]
    if unknown:
        raise ValueError(f"{path}: unknown judges {unknown}")
    if REQUIRED_READS in case["judges"] and not case.get("required_reads"):
        raise ValueError(f"{path}: {REQUIRED_READS} needs required_reads")
    if DIFF_PATTERNS in case["judges"] and not case.get("diff_patterns"):
        raise ValueError(f"{path}: {DIFF_PATTERNS} needs diff_patterns")
    return case


def evaluate_case(
    case: dict, fixture: Path, stream: Path, base: str | None = None,
    python: str = sys.executable, pytest_runner: Callable = subprocess.run,
) -> dict:
    fixture = Path(fixture)
    base = base or str(case["fixture_commit"])
    events = load_transcript(stream)
    calls = tool_calls(events)
    needs_tree = {NO_WEAKENED_ASSERTS, DIFF_PATTERNS} & set(case["judges"])
    after = snapshot_tree(fixture) if needs_tree else ""
    results: dict[str, JudgeResult] = {}
    for judge in case["judges"]:
        if judge == REQUIRED_READS:
            results[judge] = judge_required_reads(calls, case["required_reads"], str(fixture))
        elif judge == REDLINES:
            results[judge] = judge_redlines(fixture)
        elif judge == NO_WEAKENED_ASSERTS:
            results[judge] = judge_no_weakened_asserts(fixture, base, after)
        elif judge == FIX_ROUNDS:
            results[judge] = judge_fix_rounds(calls)
        elif judge == OFFLINE_PYTEST:
            results[judge] = judge_offline_pytest(fixture, python, pytest_runner)
        elif judge == DIFF_PATTERNS:
            results[judge] = judge_diff_patterns(fixture, base, after, case["diff_patterns"])
    return {
        "case": case["id"],
        "passed": all(r.passed for r in results.values()),
        "judges": {k: {"passed": r.passed, "detail": r.detail} for k, r in results.items()},
        "tool_calls": len(calls),
        **usage_summary(events),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate one agent eval run (SD3.5).")
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--stream", type=Path, required=True, help="droid exec stream-json output")
    parser.add_argument("--base", help="baseline tree or commit (default: the case's fixture_commit)")
    parser.add_argument("--python", default=sys.executable, help="interpreter for the offline pytest judge")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)
    report = evaluate_case(load_case(args.case), args.fixture, args.stream, args.base, args.python)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_out:
        args.json_out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
