"""test_guard.py hook（AI_NATIVE_SDLC_SPEC SD3.7）的离线确定性单测。

在 tmp_path 伪造项目（tests/ 下放已有文件），伪造 stdin JSON 调用 hook 子命令，
覆盖：标记缺失放行；Edit / Create / ApplyPatch 拦截已有、放行新建；路径归一；
Execute 启发式的正反例；reset 只在 startup / clear 清标记；fail-open；以及
hooks.json 注册、auto-coder skill 与 hook 的标记路径一致、.gitignore。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".factory" / "hooks" / "test_guard.py"
MARKER = Path(".factory") / "state" / "fix_mode"
EXISTING = "tests/unit/test_existing.py"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "tests" / "unit").mkdir(parents=True)
    (root / EXISTING).write_text("def test_x():\n    assert 1\n", encoding="utf-8")
    (root / "tests" / "conftest.py").write_text("", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (root / MARKER).parent.mkdir(parents=True)
    (root / MARKER).touch()
    return root


def _run(project: Path, command: str, payload: dict | str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("FACTORY_PROJECT_DIR", None)
    env["TEST_GUARD_PROJECT_DIR"] = str(project)
    return subprocess.run(
        ["python3", str(HOOK), command],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _pre(project: Path, tool_name: str, tool_input: dict, *, cwd: Path | None = None) -> dict | None:
    proc = _run(project, "pre", {
        "session_id": "s1",
        "hook_event_name": "PreToolUse",
        "cwd": str(cwd or project),
        "tool_name": tool_name,
        "tool_input": tool_input,
    })
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return None
    return json.loads(proc.stdout)["hookSpecificOutput"]


def _denied(decision: dict | None) -> bool:
    return decision is not None and decision["permissionDecision"] == "deny"


class TestFileTools:
    def test_edit_existing_test_is_denied(self, project: Path) -> None:
        out = _pre(project, "Edit", {"file_path": str(project / EXISTING), "old_str": "1", "new_str": "2"})
        assert _denied(out)
        assert out["hookEventName"] == "PreToolUse"
        assert EXISTING in out["permissionDecisionReason"]
        assert "向用户报告" in out["permissionDecisionReason"]

    def test_no_marker_allows_everything(self, project: Path) -> None:
        (project / MARKER).unlink()
        assert _pre(project, "Edit", {"file_path": str(project / EXISTING)}) is None
        assert _pre(project, "Execute", {"command": f"rm {EXISTING}"}) is None

    def test_edit_source_file_is_allowed(self, project: Path) -> None:
        assert _pre(project, "Edit", {"file_path": str(project / "src" / "mod.py")}) is None

    def test_create_new_test_file_is_allowed(self, project: Path) -> None:
        assert _pre(project, "Create", {"file_path": str(project / "tests" / "unit" / "test_new.py"), "content": ""}) is None

    def test_create_over_existing_test_is_denied(self, project: Path) -> None:
        assert _denied(_pre(project, "Create", {"file_path": str(project / EXISTING), "content": ""}))

    def test_relative_path_resolves_against_cwd(self, project: Path) -> None:
        assert _denied(_pre(project, "Edit", {"file_path": "unit/test_existing.py"}, cwd=project / "tests"))

    def test_dotdot_out_of_tests_is_allowed(self, project: Path) -> None:
        assert _pre(project, "Edit", {"file_path": str(project / "tests" / ".." / "src" / "mod.py")}) is None

    def test_tests_dir_elsewhere_is_not_protected(self, project: Path) -> None:
        nested = project / "src" / "tests"
        nested.mkdir()
        (nested / "test_y.py").write_text("", encoding="utf-8")
        assert _pre(project, "Edit", {"file_path": str(nested / "test_y.py")}) is None

    def test_apply_patch_update_and_delete_denied_add_allowed(self, project: Path) -> None:
        update = f"*** Begin Patch\n*** Update File: {EXISTING}\n@@\n-x\n+y\n*** End Patch\n"
        delete = f"*** Begin Patch\n*** Delete File: {project / EXISTING}\n*** End Patch\n"
        add = "*** Begin Patch\n*** Add File: tests/unit/test_new.py\n+x\n*** End Patch\n"
        assert _denied(_pre(project, "ApplyPatch", {"input": update}))
        assert _denied(_pre(project, "ApplyPatch", {"patch": delete}))
        assert _pre(project, "ApplyPatch", {"input": add}) is None

    def test_other_tools_are_ignored(self, project: Path) -> None:
        assert _pre(project, "Read", {"file_path": str(project / EXISTING)}) is None


class TestExecuteHeuristics:
    @pytest.mark.parametrize("command", [
        f"sed -i '' 's/1/2/' {EXISTING}",
        f"sed --in-place 's/1/2/' {EXISTING}",
        f"perl -pi -e 's/1/2/' {EXISTING}",
        f"echo x > {EXISTING}",
        f"echo x >> {EXISTING}",
        f"printf x >{EXISTING}",
        f"cat > {EXISTING} <<'EOF'\nit's not a command\nEOF",
        f"rm -f {EXISTING}",
        "rm -rf tests/unit",
        "rm tests/unit/*.py",
        f"mv {EXISTING} /tmp/x.py",
        f"cp src/mod.py {EXISTING}",
        f"echo x | tee {EXISTING}",
        f"git checkout -- {EXISTING}",
        f"git restore {EXISTING}",
        f"git rm {EXISTING}",
        f"dd if=/dev/null of={EXISTING}",
        "find tests -name '*.py' -delete",
        f"cd /tmp && FOO=1 sudo rm {{ROOT}}/{EXISTING}",
        f"python -m pytest -q; truncate -s 0 {EXISTING}",
        f"sed -i '' \\\n  's/1/2/' {EXISTING}",
    ])
    def test_writes_to_existing_tests_are_denied(self, project: Path, command: str) -> None:
        command = command.replace("{ROOT}", str(project))
        assert _denied(_pre(project, "Execute", {"command": command})), command

    @pytest.mark.parametrize("command", [
        f"pytest {EXISTING} -q 2>&1 | tail -5",
        f"cat {EXISTING}",
        f"grep -n assert {EXISTING} > /tmp/out.txt",
        f"sed -n '1,5p' {EXISTING}",
        f"cp {EXISTING} /tmp/backup.py",
        "echo x > tests/unit/test_new.py",
        "cat > tests/unit/test_new.py <<'EOF'\nrm tests/unit/test_existing.py\nEOF",
        f"git diff {EXISTING}",
        "mkdir -p .factory/state && touch .factory/state/fix_mode",
        "rm -f .factory/state/fix_mode",
        f"python -c \"print(open('{EXISTING}').read())\" >&2",
    ])
    def test_reads_and_new_files_are_allowed(self, project: Path, command: str) -> None:
        assert _pre(project, "Execute", {"command": command}) is None, command

    def test_unbalanced_quotes_fail_open(self, project: Path) -> None:
        proc = _run(project, "pre", {
            "cwd": str(project), "tool_name": "Execute", "tool_input": {"command": f"rm {EXISTING} 'oops"},
        })
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""
        assert "test_guard" in proc.stderr


class TestResetAndFaults:
    @pytest.mark.parametrize("source,removed", [("startup", True), ("clear", True), ("resume", False), ("compact", False)])
    def test_reset_only_on_fresh_sessions(self, project: Path, source: str, removed: bool) -> None:
        proc = _run(project, "reset", {"hook_event_name": "SessionStart", "source": source, "cwd": str(project)})
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""
        assert (project / MARKER).exists() is not removed

    def test_reset_without_marker_is_silent(self, project: Path) -> None:
        (project / MARKER).unlink()
        proc = _run(project, "reset", {"source": "startup"})
        assert proc.returncode == 0
        assert proc.stderr == ""

    def test_bad_stdin_fails_open(self, project: Path) -> None:
        proc = _run(project, "pre", "not json")
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""

    def test_unknown_subcommand_fails_open(self, project: Path) -> None:
        proc = _run(project, "bogus", {})
        assert proc.returncode == 0
        assert "test_guard" in proc.stderr


class TestWiring:
    def _hooks(self) -> dict:
        return json.loads((REPO_ROOT / ".factory" / "hooks.json").read_text(encoding="utf-8"))

    def _commands(self, event: str) -> list[tuple[str, str]]:
        return [
            (group.get("matcher", ""), hook["command"])
            for group in self._hooks().get(event, [])
            for hook in group["hooks"]
        ]

    def test_pre_tool_use_registered_for_write_tools(self) -> None:
        entries = [(m, c) for m, c in self._commands("PreToolUse") if "test_guard.py" in c]
        assert len(entries) == 1
        matcher, command = entries[0]
        assert command.endswith("test_guard.py pre")
        assert "$FACTORY_PROJECT_DIR" in command
        for tool in ("Edit", "Create", "ApplyPatch", "Execute"):
            assert re.fullmatch(matcher, tool), tool
        for tool in ("Read", "Grep", "MultiEdit"):
            assert not re.fullmatch(matcher, tool), tool

    def test_session_start_reset_registered(self) -> None:
        commands = [c for _, c in self._commands("SessionStart")]
        assert any(c.endswith("test_guard.py reset") for c in commands)

    def test_docs_guard_hooks_still_registered(self) -> None:
        assert any(c.endswith("docs_guard.py snapshot") for _, c in self._commands("UserPromptSubmit"))
        assert any(c.endswith("docs_guard.py stop") for _, c in self._commands("Stop"))

    def test_auto_coder_skill_uses_the_hook_marker(self) -> None:
        skill = (REPO_ROOT / ".factory" / "skills" / "auto-coder" / "SKILL.md").read_text(encoding="utf-8")
        marker = MARKER.as_posix()
        assert f"touch {marker}" in skill
        assert f"rm -f {marker}" in skill
        assert ".factory/hooks/test_guard.py" in skill
        assert 'Path(".factory") / "state" / "fix_mode"' in HOOK.read_text(encoding="utf-8")

    def test_marker_dir_is_gitignored(self) -> None:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", MARKER.as_posix()],
            cwd=REPO_ROOT, capture_output=True, timeout=30,
        )
        assert proc.returncode == 0
