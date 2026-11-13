"""SD6.2: skill 镜像 drift guard + scripts/sync_skills.py 行为（离线，样例写在 tmp_path）。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "sync_skills.py"
_spec = importlib.util.spec_from_file_location("sync_skills_script", _SCRIPT)
sync_skills = importlib.util.module_from_spec(_spec)
sys.modules["sync_skills_script"] = sync_skills
_spec.loader.exec_module(sync_skills)


def _write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _fake_repo(root: Path) -> None:
    for skill in sync_skills.SYNCED_SKILLS:
        _write(root, f".factory/skills/{skill}/SKILL.md", f"# {skill}\nrun .factory/skills/{skill}/x.py\n")
    _write(root, ".factory/skills/qa-tester/QA_TEST_PROGRESS.md", "state\n")
    _write(root, ".factory/skills/auto-coder/references/01-overview.md", "generated\n")
    _write(root, ".factory/skills/auto-coder/.spec_hash.sx", "abc\n")
    _write(root, ".factory/skills/auto-coder/scripts/__pycache__/x.pyc", "bin\n")
    _write(root, ".factory/skills/review-guidelines/SKILL.md", "droid only\n")


def _kinds(drifts) -> set[tuple[str, str]]:
    return {(d.kind, d.path) for d in drifts}


def test_repo_mirrors_have_no_drift():
    drifts = sync_skills.check(REPO_ROOT)
    assert drifts == [], (
        "skill mirrors drifted from .factory/skills; edit the source, then run "
        "`python scripts/sync_skills.py`:\n" + "\n".join(map(str, drifts))
    )


def test_repo_single_state_files_live_only_in_source():
    assert (REPO_ROOT / ".factory/skills/qa-tester/QA_TEST_PLAN.md").is_file()
    assert (REPO_ROOT / ".factory/skills/qa-tester/QA_TEST_PROGRESS.md").is_file()
    for mirror in sync_skills.MIRRORS:
        assert not list((REPO_ROOT / mirror / "qa-tester").glob("QA_*.md"))
        assert not (REPO_ROOT / mirror / "review-guidelines").exists()


class TestRender:
    def test_rewrites_skill_paths_per_mirror(self):
        src = "python .factory/skills/qa-tester/scripts/qa_bootstrap.py\n--path .factory/skills\n"
        assert sync_skills.render(src, ".claude/skills") == (
            "python .claude/skills/qa-tester/scripts/qa_bootstrap.py\n--path .claude/skills\n"
        )

    def test_keeps_single_state_paths_and_unrelated_factory_paths(self):
        src = (
            "Edit `.factory/skills/qa-tester/QA_TEST_PROGRESS.md`\n"
            ".factory/hooks.json\n"
            ".factory/skills-old/x\n"
        )
        assert sync_skills.render(src, ".github/skills") == src


class TestSync:
    def test_generates_mirrors_without_source_only_content(self, tmp_path):
        _fake_repo(tmp_path)
        sync_skills.sync(tmp_path)
        assert sync_skills.check(tmp_path) == []
        for mirror in sync_skills.MIRRORS:
            base = tmp_path / mirror
            assert (base / "auto-coder/SKILL.md").read_text(encoding="utf-8") == (
                f"# auto-coder\nrun {mirror}/auto-coder/x.py\n"
            )
            assert not (base / "qa-tester/QA_TEST_PROGRESS.md").exists()
            assert not (base / "auto-coder/references").exists()
            assert not (base / "auto-coder/.spec_hash.sx").exists()
            assert not (base / "auto-coder/scripts/__pycache__").exists()
            assert not (base / "review-guidelines").exists()

    def test_check_reports_missing_differs_and_stale(self, tmp_path):
        _fake_repo(tmp_path)
        sync_skills.sync(tmp_path)
        (tmp_path / ".claude/skills/resume-writer/SKILL.md").unlink()
        _write(tmp_path, ".github/skills/qa-tester/SKILL.md", "hand edited\n")
        _write(tmp_path, ".github/skills/skill-creator/old.py", "leftover\n")
        assert _kinds(sync_skills.check(tmp_path)) == {
            ("missing", ".claude/skills/resume-writer/SKILL.md"),
            ("differs", ".github/skills/qa-tester/SKILL.md"),
            ("stale", ".github/skills/skill-creator/old.py"),
        }
        assert sync_skills.main(["--check", "--root", str(tmp_path)]) == 1

    def test_sync_repairs_drift_but_keeps_out_of_scope_files(self, tmp_path):
        _fake_repo(tmp_path)
        _write(tmp_path, ".github/skills/setup/SKILL.md", "github-only skill\n")
        _write(tmp_path, ".claude/skills/auto-coder/references/01-overview.md", "mirror's own output\n")
        _write(tmp_path, ".claude/skills/qa-tester/stale.md", "leftover\n")
        assert sync_skills.main(["--root", str(tmp_path)]) == 0
        assert sync_skills.main(["--check", "--root", str(tmp_path)]) == 0
        assert not (tmp_path / ".claude/skills/qa-tester/stale.md").exists()
        assert (tmp_path / ".github/skills/setup/SKILL.md").is_file()
        assert (tmp_path / ".claude/skills/auto-coder/references/01-overview.md").is_file()
