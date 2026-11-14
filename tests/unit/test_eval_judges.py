"""SD3.5 / SD3.6: evals/check.py 判据、evals/run.py fixture 准备与汇总门、agent-evals.yml 配置（离线：录制样本 + tmp_path git 仓库，不调用 droid exec）。"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE = REPO_ROOT / "tests" / "fixtures" / "evals" / "sd34_htm_run.jsonl"
CASES_DIR = REPO_ROOT / "evals" / "cases"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check = _load("eval_check", REPO_ROOT / "evals" / "check.py")
run = _load("eval_run", REPO_ROOT / "evals" / "run.py")

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
}

TEST_A = """\
import pytest


def test_one():
    assert 1 + 1 == 2
    assert "a" in "abc"


def test_two():
    with pytest.raises(ValueError):
        int("x")
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env=GIT_ENV).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "tests" / "test_a.py").write_text(TEST_A, encoding="utf-8")
    (root / "src" / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("old rules\n", encoding="utf-8")
    (root / ".gitignore").write_text(".venv\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _events(*calls: tuple) -> list[dict]:
    events = []
    for i, (name, params, *rest) in enumerate(calls):
        result = rest[0] if rest else ""
        is_error = rest[1] if len(rest) > 1 else False
        events.append({"type": "tool_call", "id": f"c{i}", "toolName": name, "parameters": params})
        events.append({"type": "tool_result", "id": f"c{i}", "isError": is_error, "value": result})
    return events


def _calls(*calls: tuple) -> list:
    return check.tool_calls(_events(*calls))


def _write_stream(path: Path, events: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
    return path


def _pytest(result: str) -> tuple:
    return ("Execute", {"command": "pytest tests -q"}, result)


def _edit(path: str = "/fx/src/mod.py") -> tuple:
    return ("Edit", {"file_path": path, "old_str": "a", "new_str": "b"}, "ok")


# --------------------------------------------------------------------------- recorded SD3.4 run


def test_recorded_run_required_reads_fails_like_manual_review():
    case = check.load_case(CASES_DIR / "sd34-htm-extension.yaml")
    calls = check.tool_calls(check.load_transcript(SAMPLE))
    result = check.judge_required_reads(calls, case["required_reads"], "/fixture")
    assert not result.passed
    assert "first edit at call #22" in result.detail
    assert "docs/frozen/01_DEV_SPEC_frozen.md" in result.detail
    assert "docs/frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md" in result.detail


def test_recorded_run_grep_on_spec_is_not_a_read():
    calls = check.tool_calls(check.load_transcript(SAMPLE))
    spec = "docs/frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md"
    assert any(c.name == "Grep" and spec in str(c.params.get("path")) for c in calls)
    assert not check.judge_required_reads(calls, [spec], "/fixture").passed


def test_recorded_run_fix_rounds_and_usage():
    events = check.load_transcript(SAMPLE)
    calls = check.tool_calls(events)
    assert len(calls) == 40
    assert check.judge_fix_rounds(calls).passed
    usage = check.usage_summary(events)
    assert usage["durationMs"] == 379559
    assert usage["numTurns"] == 20
    assert usage["usage"]["output_tokens"] == 14653


def test_recorded_run_first_mutation_is_dual_registration_site():
    calls = check.tool_calls(check.load_transcript(SAMPLE))
    edits = [check.to_rel(p, "/fixture") for c in calls if c.name in check.MUTATING_TOOLS
             for p in check.touched_paths(c)]
    assert edits[:2] == ["src/libs/loader/markitdown_loader.py", "scripts/ingest.py"]


# --------------------------------------------------------------------------- judge 1


def test_required_reads_pass_when_read_before_first_edit():
    calls = _calls(("Read", {"file_path": "/fx/docs/a.md"}), _edit(), ("Read", {"file_path": "/fx/docs/b.md"}))
    assert check.judge_required_reads(calls, ["docs/a.md"], "/fx").passed
    late = check.judge_required_reads(calls, ["docs/a.md", "docs/b.md"], "/fx")
    assert not late.passed and "docs/b.md" in late.detail


def test_required_reads_any_of_and_glob():
    calls = _calls(("Read", {"file_path": "/fx/.factory/skills/auto-coder/references/05-architecture.md"}), _edit())
    required = [["docs/frozen/01_DEV_SPEC_frozen.md", ".factory/skills/auto-coder/references/*.md"]]
    assert check.judge_required_reads(calls, required, "/fx").passed


def test_required_reads_ignores_failed_reads_and_counts_create_as_edit():
    calls = _calls(("Read", {"file_path": "/fx/docs/a.md"}, "Error: not found", True),
                   ("Create", {"file_path": "/fx/new.py", "content": ""}, "ok"))
    assert not check.judge_required_reads(calls, ["docs/a.md"], "/fx").passed


def test_required_reads_without_any_edit_uses_whole_run():
    calls = _calls(("Read", {"file_path": "/fx/docs/a.md"}))
    result = check.judge_required_reads(calls, ["docs/a.md"], "/fx")
    assert result.passed and "no file edit" in result.detail


def test_apply_patch_paths_are_parsed():
    call = _calls(("ApplyPatch", {"input": "*** Begin Patch\n*** Update File: /fx/src/a.py\n"
                                           "*** Add File: /fx/tests/test_b.py\n*** End Patch"}))[0]
    assert [check.to_rel(p, "/fx") for p in check.touched_paths(call)] == ["src/a.py", "tests/test_b.py"]


# --------------------------------------------------------------------------- judge 4


@pytest.mark.parametrize(("output", "expected"), [
    ("2 failed, 3 passed in 0.4s", False),
    ("ERROR tests/x.py\n1 error in 0.2s", False),
    ("5 passed, 1 skipped in 1.0s", True),
    ("[Process exited with code 1]", False),
    ("........ [100%]\n\n[Process exited with code 0]", None),
])
def test_pytest_outcome(output, expected):
    assert check.pytest_outcome(output) is expected


def test_fix_rounds_fail_when_editing_after_fourth_failing_run():
    red = _pytest("1 failed in 0.1s")
    calls = _calls(red, _edit(), red, _edit(), red, _edit(), red, _edit())
    result = check.judge_fix_rounds(calls)
    assert not result.passed and "call #8" in result.detail


def test_fix_rounds_pass_when_stopping_after_three_rounds():
    red = _pytest("1 failed in 0.1s")
    assert check.judge_fix_rounds(_calls(red, _edit(), red, _edit(), red, _edit(), red)).passed


def test_fix_rounds_reruns_without_edits_are_one_round_and_green_resets():
    red, green = _pytest("1 failed in 0.1s"), _pytest("3 passed in 0.1s")
    assert check.judge_fix_rounds(_calls(red, red, red, red, _edit())).passed
    calls = _calls(red, _edit(), red, _edit(), red, _edit(), green, _edit(), red, _edit())
    assert check.judge_fix_rounds(calls).passed


# --------------------------------------------------------------------------- judges 2/3/5/6 on a git repo


def test_no_weakened_asserts_flags_removed_assert_test_and_new_skip(repo):
    base = check.snapshot_tree(repo)
    weakened = TEST_A.replace('    assert "a" in "abc"\n', "").replace(
        "def test_two", "@pytest.mark.skip\ndef test_two").replace("def test_one", "def helper_one")
    (repo / "tests" / "test_a.py").write_text(weakened, encoding="utf-8")
    result = check.judge_no_weakened_asserts(repo, base, check.snapshot_tree(repo))
    assert not result.passed
    assert "asserts 3 -> 2" in result.detail
    assert "removed tests ['test_one']" in result.detail
    assert "skip/xfail markers 0 -> 1" in result.detail


def test_no_weakened_asserts_flags_deleted_test_file(repo):
    base = check.snapshot_tree(repo)
    (repo / "tests" / "test_a.py").unlink()
    result = check.judge_no_weakened_asserts(repo, base, check.snapshot_tree(repo))
    assert not result.passed and "deleted" in result.detail


def test_no_weakened_asserts_allows_extended_expectations_and_new_tests(repo):
    base = check.snapshot_tree(repo)
    path = repo / "tests" / "test_a.py"
    path.write_text(TEST_A.replace("1 + 1 == 2", "1 + 1 in {2, 3}") + "\n\ndef test_new():\n    assert True\n",
                    encoding="utf-8")
    (repo / "tests" / "test_b.py").write_text("def test_b():\n    pass\n", encoding="utf-8")
    result = check.judge_no_weakened_asserts(repo, base, check.snapshot_tree(repo))
    assert result.passed, result.detail


def test_diff_patterns_rules(repo):
    base = check.snapshot_tree(repo)
    (repo / "src" / "mod.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "PROGRESS.md").write_text("| K-02 | ✅ | inferred |\n", encoding="utf-8")
    after = check.snapshot_tree(repo)
    ok = check.judge_diff_patterns(repo, base, after, {"must_touch": ["src/*"], "must_not_touch": ["tests/*"]})
    assert ok.passed, ok.detail
    bad = check.judge_diff_patterns(repo, base, after, {
        "must_touch": ["scripts/*"],
        "must_not_touch": ["src/*"],
        "added_must_not_match": [{"path": "PROGRESS.md", "pattern": "K-02.*✅"}],
    })
    assert not bad.passed
    assert "must touch 'scripts/*'" in bad.detail
    assert "must not touch 'src/*'" in bad.detail
    assert "PROGRESS.md: added line matches" in bad.detail


def test_snapshot_ignores_gitignored_paths(repo):
    base = check.snapshot_tree(repo)
    (repo / ".venv").mkdir()
    (repo / ".venv" / "x").write_text("x", encoding="utf-8")
    assert check.snapshot_tree(repo) == base


def test_redlines_judge_uses_evaluator_script(repo):
    assert check.judge_redlines(repo).passed
    (repo / "src" / "bad.py").write_text("import sirchmunk\n", encoding="utf-8")
    result = check.judge_redlines(repo)
    assert not result.passed and "no-sirchmunk-import" in result.detail


def test_offline_pytest_judge_runs_ci_command_in_fixture(repo):
    seen = {}

    def fake_runner(cmd, **kwargs):
        seen.update(cmd=cmd, **kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="3 passed in 0.1s\n\n-- Docs: https://x\n", stderr="")

    result = check.judge_offline_pytest(repo, "/py", runner=fake_runner)
    assert result.passed and result.detail == "exit 0: 3 passed in 0.1s"
    assert seen["cmd"][:3] == ["/py", "-m", "pytest"]
    assert "--disable-socket" in seen["cmd"]
    assert seen["cwd"] == str(repo) and seen["env"]["NO_PROXY"] == "*"
    red = check.judge_offline_pytest(
        repo, "/py", runner=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="1 failed\n", stderr=""))
    assert not red.passed


def test_evaluate_case_end_to_end(repo, tmp_path):
    base = check.snapshot_tree(repo)
    (repo / "src" / "mod.py").write_text("VALUE = 2\n", encoding="utf-8")
    stream = _write_stream(tmp_path / "run.jsonl", _events(
        ("Read", {"file_path": f"{repo}/AGENTS.md"}, "old rules"),
        ("Edit", {"file_path": f"{repo}/src/mod.py", "old_str": "1", "new_str": "2"}, "ok"),
        ("Execute", {"command": "pytest tests -q"}, "3 passed in 0.1s"),
    ) + [{"type": "completion", "durationMs": 1000, "numTurns": 2, "usage": {"output_tokens": 5}}])
    case = {
        "id": "demo", "prompt": "p", "fixture_commit": "HEAD", "required_reads": ["AGENTS.md"],
        "diff_patterns": {"must_touch": ["src/mod.py"]},
        "judges": list(check.ALL_JUDGES),
    }
    green = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="1 passed\n", stderr="")  # noqa: E731
    report = check.evaluate_case(case, repo, stream, base=base, pytest_runner=green)
    assert report["passed"], report
    assert set(report["judges"]) == set(check.ALL_JUDGES)
    assert report["tool_calls"] == 3 and report["numTurns"] == 2


# --------------------------------------------------------------------------- cases + runner


def test_cases_are_valid_and_cover_judges_1_to_5():
    paths = sorted(CASES_DIR.glob("*.yaml"))
    assert 5 <= len(paths) <= 8
    cases = [check.load_case(p) for p in paths]
    assert [c["id"] for c in cases] == [p.stem for p in paths]
    for case in cases:
        assert re.fullmatch(r"[0-9a-f]{40}", str(case["fixture_commit"]))
    covered = {j for c in cases for j in c["judges"]}
    assert set(check.ALL_JUDGES) <= covered


def test_load_case_rejects_unknown_judge(tmp_path):
    path = tmp_path / "x.yaml"
    path.write_text("id: x\nprompt: p\nfixture_commit: abc\njudges: [vibes]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown judges"):
        check.load_case(path)


def test_agent_command_pins_d5_executor_flags(tmp_path):
    cmd = run.agent_command(run.DEFAULT_MODEL, tmp_path / "fx", tmp_path / "p.txt")
    assert cmd[:2] == ["droid", "exec"]
    assert cmd[cmd.index("--auto") + 1] == "medium"
    assert cmd[cmd.index("-o") + 1] == "stream-json"
    assert cmd[cmd.index("-m") + 1] == "custom:glm-5.3-Flash-2"
    assert cmd[cmd.index("--cwd") + 1] == str(tmp_path / "fx")


def test_aggregate_two_of_three_and_pass_rate_gate():
    runs = [{"passed": True}, {"passed": False}, {"passed": True}]
    assert run.aggregate("a", runs)["passed"]
    assert not run.aggregate("b", runs[:2])["passed"]
    cases = [{"passed": True}] * 9 + [{"passed": False}]
    assert run.summarize(cases, 0.9)["ok"]
    assert not run.summarize(cases[1:], 0.9)["ok"]
    assert not run.summarize([], 0.9)["ok"]


def test_overlay_skips_ignored_files_of_a_git_checkout(repo, tmp_path):
    source = tmp_path / "checkout"
    skill = source / ".factory" / "skills" / "auto-coder"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("rules\n", encoding="utf-8")
    (skill / "references" / "05-architecture.md").write_text("generated\n", encoding="utf-8")
    (source / ".gitignore").write_text("/.factory/skills/auto-coder/references/\n", encoding="utf-8")
    _git(source, "init", "-q")
    fixture = tmp_path / "fixture"
    run.prepare_fixture(repo, "HEAD", fixture, overlay_from=source)
    try:
        assert (fixture / ".factory" / "skills" / "auto-coder" / "SKILL.md").exists()
        assert not (fixture / ".factory" / "skills" / "auto-coder" / "references").exists()
        assert not (fixture / "AGENTS.md").exists()
    finally:
        run.remove_fixture(repo, fixture)


def test_prepare_fixture_overlays_steering_without_counting_it_as_agent_diff(repo, tmp_path):
    source = tmp_path / "checkout"
    (source / ".factory" / "skills" / "demo").mkdir(parents=True)
    (source / ".factory" / "skills" / "demo" / "SKILL.md").write_text("new skill\n", encoding="utf-8")
    (source / "AGENTS.md").write_text("new rules\n", encoding="utf-8")
    venv = tmp_path / "venv"
    venv.mkdir()
    fixture = tmp_path / "fixture"
    base = run.prepare_fixture(repo, "HEAD", fixture, overlay_from=source, venv=venv)
    try:
        assert (fixture / "AGENTS.md").read_text(encoding="utf-8") == "new rules\n"
        assert (fixture / ".factory" / "skills" / "demo" / "SKILL.md").exists()
        assert (fixture / ".venv").resolve() == venv.resolve()
        assert base != _git(repo, "rev-parse", "HEAD^{tree}").strip()
        unchanged = check.judge_diff_patterns(fixture, base, check.snapshot_tree(fixture), {"must_touch": ["*"]})
        assert not unchanged.passed and "must touch '*'" in unchanged.detail
    finally:
        run.remove_fixture(repo, fixture)
    assert not fixture.exists()
    assert str(fixture) not in _git(repo, "worktree", "list")

# --------------------------------------------------------------------------- SD3.6 gate + workflow

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "agent-evals.yml"
CI_SETTINGS = REPO_ROOT / "evals" / "ci" / "factory-settings.json"
STEERING_PATHS = [".factory/skills/**", ".claude/skills/**", ".github/skills/**",
                  "AGENTS.md", ".factory/hooks.json", ".factory/hooks/**"]


def _report(case: str, passed: bool, failed_judges: tuple = (), exit_code: int | None = 0,
            timed_out: bool = False) -> dict:
    judges = {"diff_patterns": {"passed": True, "detail": ""}}
    judges.update({j: {"passed": False, "detail": "x"} for j in failed_judges})
    return {"case": case, "passed": passed, "judges": judges,
            "agent": {"exit_code": exit_code, "timed_out": timed_out, "seconds": 1.0},
            "usage": {"inputTokens": 10, "outputTokens": 2}}


def test_aggregate_counts_judge_failures_and_agent_errors():
    runs = [_report("a", False, ("required_reads_before_edit",)),
            _report("a", False, ("required_reads_before_edit", "redlines")),
            _report("a", False, exit_code=1),
            _report("a", False, timed_out=True, exit_code=None)]
    agg = run.aggregate("a", runs)
    assert agg["judge_failures"] == {"required_reads_before_edit": 2, "redlines": 1}
    assert agg["agent_errors"] == 2 and not agg["passed"]


def test_summarize_fails_on_regression_even_above_threshold():
    cases = [{"case": "a", "passed": True}, {"case": "b", "passed": False}]
    assert run.summarize(cases, 0.5)["ok"]
    gated = run.summarize(cases, 0.5, {"passing_cases": ["a", "b"]})
    assert gated["regressions"] == ["b"] and not gated["ok"]
    # A baseline case missing from this run (e.g. a dispatch subset) is not a regression.
    assert run.summarize(cases[:1], 0.5, {"passing_cases": ["a", "b"]})["ok"]


def test_summarize_mode_merges_matrix_dirs_and_applies_baseline(tmp_path):
    for case, results in {"a": (True, True, False), "b": (False, False, True)}.items():
        d = tmp_path / "ci" / f"evals-{case}"
        d.mkdir(parents=True)
        for i, ok in enumerate(results, 1):
            failed = () if ok else ("required_reads_before_edit",)
            (d / f"{case}-{i}.report.json").write_text(json.dumps(_report(case, ok, failed)), encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"min_pass_rate": 0.5, "passing_cases": ["a"]}), encoding="utf-8")
    md = tmp_path / "summary.md"
    out = tmp_path / "out"
    code = run.main(["--summarize", str(tmp_path / "ci"), "--baseline", str(baseline),
                     "--out", str(out), "--md-out", str(md), "--model", "m"])
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert code == 0 and summary["ok"]
    assert summary["min_pass_rate"] == 0.5 and summary["pass_rate"] == 0.5
    assert [(c["case"], c["passes"], c["passed"]) for c in summary["per_case"]] == [("a", 2, True), ("b", 1, False)]
    assert summary["usage"] == {"inputTokens": 60, "outputTokens": 12}
    text = md.read_text(encoding="utf-8")
    assert "## Agent evals: PASS" in text and "| `b` | 1/3 | FAIL | required_reads_before_edit ×2 | 0 |" in text

    baseline.write_text(json.dumps({"min_pass_rate": 0.5, "passing_cases": ["a", "b"]}), encoding="utf-8")
    assert run.main(["--summarize", str(tmp_path / "ci"), "--baseline", str(baseline),
                     "--out", str(out), "--model", "m"]) == 1


def test_agent_evals_workflow_triggers_only_on_steering_paths():
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    on = wf.get("on", wf.get(True))
    assert on["pull_request"]["paths"] == STEERING_PATHS
    assert "workflow_dispatch" in on and "push" not in on
    assert not any(p.startswith(".github/") and "skills" not in p for p in on["pull_request"]["paths"])
    assert wf["concurrency"]["cancel-in-progress"] is False
    gate = "\n".join(s.get("run", "") for s in wf["jobs"]["gate"]["steps"])
    for prefix in (r"\.factory/skills/", r"\.claude/skills/", r"\.github/skills/", r"AGENTS\.md$",
                   r"\.factory/hooks\.json$", r"\.factory/hooks/"):
        assert prefix in gate


def test_overlay_covers_every_trigger_path():
    # A steering change the overlay misses is either not measured or breaks the fixture (mirror drift).
    assert sorted(p.removesuffix("/**") for p in STEERING_PATHS) == sorted(run.OVERLAY_PATHS)


def test_agent_evals_workflow_uses_ci_byok_settings_without_plaintext_keys():
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    settings = json.loads(CI_SETTINGS.read_text(encoding="utf-8"))
    (model,) = settings["customModels"]
    # No credential in the repo file; the workflow adds an apiKeyHelper pointing outside the fixture.
    assert "apiKey" not in model and "apiKeyHelper" not in model
    assert model["baseUrl"] == "https://open.bigmodel.cn/api/anthropic"
    assert wf["env"]["EVAL_MODEL"] == f"custom:{model['displayName'].replace(' ', '-')}-0" == model["id"]
    evals = wf["jobs"]["evals"]
    steps = {s.get("name"): s for s in evals["steps"]}
    assert steps["Run evals"]["env"]["FACTORY_API_KEY"] == "${{ secrets.FACTORY_API_KEY }}"
    # The model key must not be visible to the agent session's environment.
    assert "ZHIPU_API_KEY" not in steps["Run evals"]["env"]
    configure = steps["Configure BYOK model (D5)"]
    assert configure["env"]["ZHIPU_API_KEY"] == "${{ secrets.ZHIPU_API_KEY }}"
    assert "evals/ci/factory-settings.json" in configure["run"] and ".apiKeyHelper" in configure["run"]
    assert evals["steps"][0]["with"]["fetch-depth"] == 0
    assert evals["strategy"]["fail-fast"] is False
    assert evals["timeout-minutes"] >= run.DEFAULT_REPEAT * run.DEFAULT_TIMEOUT_MIN

def test_baseline_lists_known_cases_and_sane_threshold():
    baseline = json.loads(run.BASELINE_PATH.read_text(encoding="utf-8"))
    ids = {p.stem for p in CASES_DIR.glob("*.yaml")}
    assert baseline["passing_cases"] and set(baseline["passing_cases"]) <= ids
    assert 0 < baseline["min_pass_rate"] <= 1
    # The calibrated floor must be reachable: it cannot exceed what the baseline itself scored.
    assert baseline["min_pass_rate"] <= len(baseline["passing_cases"]) / len(ids)
