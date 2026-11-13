"""SD5.1: scripts/measure_process.py 的指标口径（离线，在 tmp_path 中构造临时 git 仓库）。"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "measure_process.py"
_spec = importlib.util.spec_from_file_location("measure_process_script", _SCRIPT)
mp = importlib.util.module_from_spec(_spec)
sys.modules["measure_process_script"] = mp
_spec.loader.exec_module(mp)

T0 = 1_760_000_000
H = 3600

FOO_SPEC = """# FOO_SPEC

| 编号 | 任务 | 状态 |
|------|------|------|
| FO1 | first | [ ] |
| FO2 | second | [ ] |
| FO2.1 | sub | [ ] |
"""

BAR_SPEC = """# BAR_SPEC

| 编号 | 任务 |
|------|------|
| BA1 | only |
"""

BAR_FROZEN = """# BAR_SPEC（已冻结）

全文重写，git 不会把它识别为重命名。

| 编号 | 任务 | 完成 |
|------|------|------|
| BA1 | only, rewritten with completely different words | [x] |
| BA2 | added at freeze time | [x] |
"""

QA_V1 = """# QA Test Progress

| Status | ID | Title | Note |
|--------|----|-------|------|
| ⬜ | G-01 | ingest | |
| ✅ | G-02 | dir | 2 total |
| ⬜ | H-01 | query | |
"""

QA_V2 = QA_V1.replace("| ⬜ | G-01 | ingest | |", "| ✅ | G-01 | ingest | 1 total |")


class _Repo:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        }
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str, ts: int | None = None) -> str:
        env = dict(self.env)
        if ts is not None:
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{ts} +0000"
        out = subprocess.run(
            ["git", "-C", str(self.root), *args], env=env, capture_output=True, text=True, check=True
        )
        return out.stdout.strip()

    def write(self, rel: str, body: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def commit(self, message: str, ts: int) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message, ts=ts)
        return self.git("rev-parse", "HEAD")


@pytest.fixture()
def repo(tmp_path: Path) -> dict:
    r = _Repo(tmp_path)
    shas: dict[str, str] = {}
    r.write("README.md", "x\n")
    shas["init"] = r.commit("chore: init", T0 - 10 * H)
    r.write("docs/specs/FOO_SPEC.md", FOO_SPEC)
    shas["charter"] = r.commit("docs(specs): [FO0] charter FOO_SPEC", T0)
    shas["other"] = r.commit("feat(x): [BA1] other series before BAR exists", T0 + 1 * H)
    shas["not_task"] = r.commit("feat(x): mention FO1 without brackets", T0 + int(1.5 * H))
    shas["first"] = r.commit("feat(x): [FO1] first task", T0 + 2 * H)
    shas["revert"] = r.commit(
        'Revert "feat(x): [FO1] first task"\n\nFix-Rounds: 1\n\nCo-authored-by: bot <b@example.invalid>',
        T0 + 3 * H,
    )
    shas["fix"] = r.commit("fix(x): [FO2] repair\n\nFix-Rounds: 0\nCo-authored-by: bot <b@example.invalid>", T0 + 4 * H)
    shas["hotfix"] = r.commit("hotfix: urgent\n\nFix-Rounds: 2", T0 + 5 * H)
    r.write(".github/skills/qa-tester/QA_TEST_PROGRESS.md", QA_V1)
    shas["qa1"] = r.commit("test(qa): record pending", T0 + 6 * H)
    r.write(".github/skills/qa-tester/QA_TEST_PROGRESS.md", QA_V2)
    shas["qa2"] = r.commit("test(qa): G-01 passes", T0 + 11 * H)
    r.git("mv", ".github/skills/qa-tester", ".factory-qa-tmp")
    (tmp_path / ".factory/skills").mkdir(parents=True)
    r.git("mv", ".factory-qa-tmp", ".factory/skills/qa-tester")
    shas["qa_move"] = r.commit("chore(skills): move qa-tester to .factory", T0 + 12 * H)
    (tmp_path / "docs/frozen").mkdir(parents=True)
    r.git("mv", "docs/specs/FOO_SPEC.md", "docs/frozen/FOO_SPEC_frozen.md")
    shas["freeze"] = r.commit("docs(governance): freeze FOO_SPEC", T0 + 20 * H)
    r.git("mv", "docs/frozen/FOO_SPEC_frozen.md", "docs/frozen/01_FOO_SPEC_frozen.md")
    shas["renumber"] = r.commit("docs(governance): number frozen specs", T0 + 21 * H)
    r.write("docs/specs/BAR_SPEC.md", BAR_SPEC)
    shas["bar_charter"] = r.commit("docs(specs): charter BAR_SPEC", T0 + 30 * H)
    r.write("docs/specs/NOTABLE_SPEC.md", "# no task table here\n")
    shas["notable"] = r.commit("docs(specs): charter NOTABLE_SPEC", T0 + 31 * H)
    shas["bar_task"] = r.commit("feat(y): [BA1] the only task", T0 + 33 * H)
    (tmp_path / "docs/specs/BAR_SPEC.md").unlink()
    r.write("docs/frozen/BAR_SPEC_frozen.md", BAR_FROZEN)
    shas["bar_freeze"] = r.commit("docs(governance): freeze BAR_SPEC", T0 + 40 * H)
    r.git("checkout", "-q", "-b", "side", shas["bar_freeze"])
    shas["side"] = r.commit("fix: side branch fix", T0 + 41 * H)
    r.git("checkout", "-q", "main")
    r.git("merge", "-q", "--no-ff", "-m", "fix: merge side (merge commits are not counted)", "side", ts=T0 + 42 * H)
    return {"root": tmp_path, "shas": shas}


def _by_name(report) -> dict:
    return {s.name: s for s in report.specs}


def test_charter_first_task_and_freeze_by_file_path(repo):
    report = mp.measure(repo["root"])
    shas = repo["shas"]
    foo = _by_name(report)["FOO_SPEC"]
    assert foo.series == "FO"  # 冻结后又被重命名，仍沿重命名链读到最新文件
    assert foo.charter_sha == shas["charter"]
    assert foo.first_task_sha == shas["first"]  # 立项提交自身、其他系列、无方括号的提交都不算
    assert foo.charter_to_first_task_h == 2.0
    assert foo.frozen_sha == shas["freeze"]
    assert foo.frozen_path == "docs/frozen/FOO_SPEC_frozen.md"
    assert foo.charter_to_frozen_h == 20.0


def test_freeze_detected_without_rename_and_spec_without_table(repo):
    specs = _by_name(mp.measure(repo["root"]))
    bar = specs["BAR_SPEC"]
    assert bar.series == "BA"
    assert bar.first_task_sha == repo["shas"]["bar_task"]  # 立项前的 [BA1] 提交不算
    assert bar.charter_to_first_task_h == 3.0
    assert bar.frozen_sha == repo["shas"]["bar_freeze"]
    assert bar.charter_to_frozen_h == 10.0
    notable = specs["NOTABLE_SPEC"]
    assert notable.series is None
    assert notable.first_task_sha is None and notable.frozen_sha is None
    assert list(specs) == ["FOO_SPEC", "BAR_SPEC", "NOTABLE_SPEC"]


def test_fix_rounds_matched_per_line_even_outside_trailer_block(repo):
    assert mp.measure(repo["root"]).fix_rounds == {0: 1, 1: 1, 2: 1}


def test_qa_turnaround_follows_progress_file_move(repo):
    report = mp.measure(repo["root"])
    assert report.qa_turnaround_h == [5.0]  # G-01：⬜ 于 qa1，✅ 于 qa2；迁移到 .factory 后不重复计
    assert report.qa_pass_without_pending == 1  # G-02 首次出现即 ✅；H-01 仍 ⬜ 不计


def test_revert_hotfix_fix_counts_skip_merges(repo):
    report = mp.measure(repo["root"])
    shas = repo["shas"]
    assert report.revert_hotfix == {
        "revert": [shas["revert"]],
        "hotfix": [shas["hotfix"]],
        "fix": [shas["fix"], shas["side"]],
    }
    assert report.commits_total == report.commits_non_merge + 1


def test_rev_limits_history(repo):
    report = mp.measure(repo["root"], rev=repo["shas"]["first"])
    foo = _by_name(report)["FOO_SPEC"]
    assert foo.frozen_sha is None and foo.series == "FO"
    assert report.fix_rounds == {}


def test_cli_json_and_markdown(repo, capsys):
    assert mp.main(["--repo", str(repo["root"]), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["charter_to_first_task_h"] == {"n": 2, "median": 2.5, "mean": 2.5, "min": 2.0, "max": 3.0}
    assert data["charter_to_frozen_h"]["n"] == 2
    assert data["fix_rounds"]["distribution"] == {"0": 1, "1": 1, "2": 1}
    assert data["qa_turnaround_h"]["n"] == 1
    assert data["revert_hotfix"] == {"revert": 1, "hotfix": 1, "fix": 2}

    assert mp.main(["--repo", str(repo["root"])]) == 0
    out = capsys.readouterr().out
    assert "| FOO_SPEC | FO |" in out
    assert "revert 1，hotfix 1，fix 2" in out


def test_cli_bad_repo_exits_2(tmp_path, capsys):
    assert mp.main(["--repo", str(tmp_path / "missing")]) == 2
    assert "git" in capsys.readouterr().err
