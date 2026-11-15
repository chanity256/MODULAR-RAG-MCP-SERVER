"""read_guard.py hook（EVALS_READ_DISCIPLINE_SPEC ERD1）的离线确定性单测。

在 tmp_path 伪造项目（放 01 / 04 规格、QA 计划，references 可选），伪造 stdin JSON
调用 hook 子命令，覆盖：record 记录与路径归一；pre 按 READ_MAP 拦截 / 放行、任一即可、
摄取链路要求 01 + 04、plan 与 QA 进度文件、ApplyPatch 多文件；reset 只在 startup /
clear 清状态；会话隔离；fail-open；以及 hooks.json 注册与映射表出处。
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".factory" / "hooks" / "read_guard.py"
STATE = Path(".factory") / "state"
SPEC_01 = "docs/frozen/01_DEV_SPEC_frozen.md"
SPEC_04 = "docs/frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md"
REFERENCE = ".factory/skills/auto-coder/references/05-architecture.md"
QA_PLAN = ".factory/skills/qa-tester/QA_TEST_PLAN.md"
QA_PROGRESS = ".factory/skills/qa-tester/QA_TEST_PROGRESS.md"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    for rel in (SPEC_01, SPEC_04, QA_PLAN, QA_PROGRESS, "src/core/mod.py", "src/ingestion/pipeline.py", "README.md"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x\n", encoding="utf-8")
    return root


def _run(project: Path, command: str, payload: dict | str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("FACTORY_PROJECT_DIR", None)
    env["READ_GUARD_PROJECT_DIR"] = str(project)
    return subprocess.run(
        ["python3", str(HOOK), command],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _read(project: Path, path: str, *, session: str = "s1", cwd: Path | None = None, **extra) -> None:
    proc = _run(project, "record", {
        "session_id": session,
        "hook_event_name": "PostToolUse",
        "cwd": str(cwd or project),
        "tool_name": "Read",
        "tool_input": {"file_path": path, **extra},
        "tool_response": "x\n",
    })
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == ""


def _pre(project: Path, tool_name: str, tool_input: dict, *, session: str = "s1") -> dict | None:
    proc = _run(project, "pre", {
        "session_id": session,
        "hook_event_name": "PreToolUse",
        "cwd": str(project),
        "tool_name": tool_name,
        "tool_input": tool_input,
    })
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return None
    return json.loads(proc.stdout)["hookSpecificOutput"]


def _edit(project: Path, rel: str, **kw) -> dict | None:
    return _pre(project, "Edit", {"file_path": str(project / rel), "old_str": "x", "new_str": "y"}, **kw)


def _denied(decision: dict | None) -> bool:
    return decision is not None and decision["permissionDecision"] == "deny"


def _reads(project: Path, session: str = "s1") -> list[str]:
    path = project / STATE / f"reads-{session}.jsonl"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


class TestRecord:
    def test_absolute_read_is_recorded_as_project_relative(self, project: Path) -> None:
        _read(project, str(project / SPEC_01))
        assert _reads(project) == [SPEC_01]

    def test_relative_path_resolves_against_cwd(self, project: Path) -> None:
        _read(project, "01_DEV_SPEC_frozen.md", cwd=project / "docs" / "frozen")
        assert _reads(project) == [SPEC_01]

    def test_symlinked_project_path_is_normalized(self, project: Path, tmp_path: Path) -> None:
        link = tmp_path / "link"
        link.symlink_to(project)
        _read(project, str(link / SPEC_01))
        assert _reads(project) == [SPEC_01]

    def test_read_with_limit_counts(self, project: Path) -> None:
        _read(project, str(project / SPEC_01), offset=1, limit=20)
        assert _edit(project, "src/core/mod.py") is None

    def test_outside_project_is_ignored(self, project: Path, tmp_path: Path) -> None:
        (tmp_path / "elsewhere.md").write_text("x", encoding="utf-8")
        _read(project, str(tmp_path / "elsewhere.md"))
        assert _reads(project) == []

    def test_non_read_tool_is_ignored(self, project: Path) -> None:
        proc = _run(project, "record", {"session_id": "s1", "tool_name": "Grep",
                                         "tool_input": {"file_path": str(project / SPEC_01)}})
        assert proc.returncode == 0
        assert _reads(project) == []


class TestPre:
    def test_source_edit_denied_until_spec_read(self, project: Path) -> None:
        out = _edit(project, "src/core/mod.py")
        assert _denied(out)
        assert out["hookEventName"] == "PreToolUse"
        reason = out["permissionDecisionReason"]
        assert "src/core/mod.py" in reason and SPEC_01 in reason and "AGENTS.md" in reason
        assert "references" not in reason  # 项目里没有 references/，不提示去读
        _read(project, str(project / SPEC_01))
        assert _edit(project, "src/core/mod.py") is None

    def test_references_are_an_alternative_when_present(self, project: Path) -> None:
        (project / REFERENCE).parent.mkdir(parents=True)
        (project / REFERENCE).write_text("x", encoding="utf-8")
        reason = _edit(project, "src/core/mod.py")["permissionDecisionReason"]
        assert ".factory/skills/auto-coder/references/*.md（任一）" in reason
        _read(project, str(project / REFERENCE))
        assert _edit(project, "src/core/mod.py") is None

    def test_create_new_file_is_checked(self, project: Path) -> None:
        out = _pre(project, "Create", {"file_path": str(project / "tests" / "unit" / "test_new.py"), "content": ""})
        assert _denied(out)

    @pytest.mark.parametrize("rel", [
        "src/ingestion/pipeline.py",
        "src/libs/loader/markitdown_loader.py",
        "scripts/ingest.py",
        "tests/unit/test_ingest_entry.py",
        "tests/unit/test_ingestion_loader_routing.py",
        "tests/unit/test_markitdown_loader.py",
    ])
    def test_ingestion_chain_needs_01_and_04(self, project: Path, rel: str) -> None:
        _read(project, str(project / SPEC_01))
        out = _edit(project, rel)
        assert _denied(out)
        assert SPEC_04 in out["permissionDecisionReason"]
        assert SPEC_01 not in out["permissionDecisionReason"]
        _read(project, str(project / SPEC_04))
        assert _edit(project, rel) is None

    def test_ingestion_without_any_read_lists_both(self, project: Path) -> None:
        reason = _edit(project, "src/ingestion/pipeline.py")["permissionDecisionReason"]
        assert SPEC_01 in reason and SPEC_04 in reason

    def test_spec_04_alone_does_not_unlock_ingestion(self, project: Path) -> None:
        _read(project, str(project / SPEC_04))
        assert _denied(_edit(project, "src/ingestion/pipeline.py"))

    def test_plan_files_need_spec_but_plan_readme_does_not(self, project: Path) -> None:
        assert _denied(_pre(project, "Create", {"file_path": str(project / ".agents/plans/X1.md"), "content": ""}))
        assert _edit(project, ".agents/plans/README.md") is None
        _read(project, str(project / SPEC_01))
        assert _pre(project, "Create", {"file_path": str(project / ".agents/plans/X1.md"), "content": ""}) is None

    def test_qa_progress_needs_qa_plan(self, project: Path) -> None:
        _read(project, str(project / SPEC_01))
        out = _edit(project, QA_PROGRESS)
        assert _denied(out) and QA_PLAN in out["permissionDecisionReason"]
        _read(project, str(project / QA_PLAN))
        assert _edit(project, QA_PROGRESS) is None

    @pytest.mark.parametrize("rel", ["README.md", "docs/reference/USAGE_GUIDE.md", "config/settings.yaml",
                                     ".factory/skills/qa-tester/SKILL.md"])
    def test_unmapped_paths_are_allowed(self, project: Path, rel: str) -> None:
        assert _edit(project, rel) is None

    def test_outside_project_is_allowed(self, project: Path, tmp_path: Path) -> None:
        assert _pre(project, "Edit", {"file_path": str(tmp_path / "src" / "x.py")}) is None

    def test_rule_is_skipped_when_its_reference_is_absent(self, project: Path) -> None:
        (project / QA_PLAN).unlink()
        assert _edit(project, QA_PROGRESS) is None

    def test_apply_patch_any_unread_target_denies(self, project: Path) -> None:
        _read(project, str(project / SPEC_01))
        patch = ("*** Begin Patch\n*** Update File: src/core/mod.py\n@@\n-x\n+y\n"
                 "*** Add File: src/ingestion/new_stage.py\n+x\n*** End Patch\n")
        out = _pre(project, "ApplyPatch", {"input": patch})
        assert _denied(out)
        assert "src/ingestion/new_stage.py" in out["permissionDecisionReason"]
        assert "src/core/mod.py" not in out["permissionDecisionReason"]
        delete = f"*** Begin Patch\n*** Delete File: {project / 'src/ingestion/pipeline.py'}\n*** End Patch\n"
        assert _denied(_pre(project, "ApplyPatch", {"patch": delete}))

    def test_other_tools_are_ignored(self, project: Path) -> None:
        assert _pre(project, "Execute", {"command": "echo x > src/core/mod.py"}) is None
        assert _pre(project, "Read", {"file_path": str(project / "src/core/mod.py")}) is None

    def test_sessions_are_isolated(self, project: Path) -> None:
        _read(project, str(project / SPEC_01), session="a")
        assert _edit(project, "src/core/mod.py", session="a") is None
        assert _denied(_edit(project, "src/core/mod.py", session="b"))

    def test_missing_session_id_uses_shared_file(self, project: Path) -> None:
        _run(project, "record", {"tool_name": "Read", "cwd": str(project),
                                 "tool_input": {"file_path": str(project / SPEC_01)}})
        assert (project / STATE / "reads.jsonl").exists()
        proc = _run(project, "pre", {"tool_name": "Edit", "cwd": str(project),
                                     "tool_input": {"file_path": str(project / "src/core/mod.py")}})
        assert proc.stdout.strip() == ""


class TestResetAndFaults:
    @pytest.mark.parametrize("source,removed", [("startup", True), ("clear", True), ("resume", False), ("compact", False)])
    def test_reset_only_on_fresh_sessions(self, project: Path, source: str, removed: bool) -> None:
        _read(project, str(project / SPEC_01))
        proc = _run(project, "reset", {"session_id": "s1", "source": source, "cwd": str(project)})
        assert proc.returncode == 0 and proc.stdout.strip() == ""
        assert (project / STATE / "reads-s1.jsonl").exists() is not removed

    def test_reset_prunes_stale_sessions_only(self, project: Path) -> None:
        _read(project, str(project / SPEC_01), session="old")
        _read(project, str(project / SPEC_01), session="recent")
        old = project / STATE / "reads-old.jsonl"
        stale = time.time() - 8 * 24 * 3600
        os.utime(old, (stale, stale))
        (project / STATE / "fix_mode").touch()
        _run(project, "reset", {"session_id": "new", "source": "startup"})
        assert not old.exists()
        assert (project / STATE / "reads-recent.jsonl").exists()
        assert (project / STATE / "fix_mode").exists()

    def test_reset_without_state_dir_is_silent(self, project: Path) -> None:
        proc = _run(project, "reset", {"session_id": "s1", "source": "startup"})
        assert proc.returncode == 0 and proc.stderr == ""

    def test_session_id_cannot_escape_state_dir(self, project: Path) -> None:
        _read(project, str(project / SPEC_01), session="../../evil")
        assert not (project / "evil.jsonl").exists()
        assert len(list((project / STATE).glob("reads-*.jsonl"))) == 1

    def test_bad_stdin_fails_open(self, project: Path) -> None:
        for command in ("pre", "record", "reset"):
            proc = _run(project, command, "not json")
            assert proc.returncode == 0 and proc.stdout.strip() == ""

    def test_corrupt_state_fails_open(self, project: Path) -> None:
        state = project / STATE / "reads-s1.jsonl"
        state.parent.mkdir(parents=True)
        state.write_bytes(b"\xff\xfe\x00garbage")
        proc = _run(project, "pre", {"session_id": "s1", "cwd": str(project), "tool_name": "Edit",
                                     "tool_input": {"file_path": str(project / "src/core/mod.py")}})
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""
        assert "read_guard" in proc.stderr

    def test_missing_fields_fail_open(self, project: Path) -> None:
        assert _run(project, "pre", {"tool_name": "Edit"}).stdout.strip() == ""
        assert _run(project, "pre", {"tool_name": "Edit", "tool_input": {"file_path": 3}}).stdout.strip() == ""
        assert _run(project, "record", {"tool_name": "Read", "tool_input": "x"}).returncode == 0

    def test_unknown_subcommand_fails_open(self, project: Path) -> None:
        proc = _run(project, "bogus", {})
        assert proc.returncode == 0
        assert "read_guard" in proc.stderr


class TestWiring:
    def _commands(self, event: str) -> list[tuple[str, str]]:
        hooks = json.loads((REPO_ROOT / ".factory" / "hooks.json").read_text(encoding="utf-8"))
        return [(g.get("matcher", ""), h["command"]) for g in hooks.get(event, []) for h in g["hooks"]]

    def _only(self, event: str, suffix: str) -> tuple[str, str]:
        entries = [(m, c) for m, c in self._commands(event) if "read_guard.py" in c]
        assert len(entries) == 1, entries
        matcher, command = entries[0]
        assert command.endswith(f"read_guard.py {suffix}")
        assert "$FACTORY_PROJECT_DIR" in command
        return matcher, command

    def test_pre_tool_use_registered_for_edit_tools(self) -> None:
        matcher, _ = self._only("PreToolUse", "pre")
        for tool in ("Edit", "Create", "ApplyPatch"):
            assert re.fullmatch(matcher, tool), tool
        for tool in ("Execute", "Read", "Grep"):
            assert not re.fullmatch(matcher, tool), tool

    def test_post_tool_use_registered_for_read_only(self) -> None:
        matcher, _ = self._only("PostToolUse", "record")
        assert re.fullmatch(matcher, "Read")
        for tool in ("Grep", "Edit", "Execute"):
            assert not re.fullmatch(matcher, tool), tool

    def test_session_start_reset_registered(self) -> None:
        self._only("SessionStart", "reset")

    def _module(self):
        spec = importlib.util.spec_from_file_location("read_guard", HOOK)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_every_rule_cites_agents_md_or_a_skill(self) -> None:
        for patterns, options, source in self._module().READ_MAP:
            assert patterns and options
            assert "AGENTS.md" in source or "skill" in source or "auto-coder" in source, source
            assert "evals" not in source

    def test_required_references_exist_in_this_repo(self) -> None:
        # 必读文件缺失时规则会被跳过，改名 / 移动规格必须同步映射表。references/ 是生成物，不查。
        for _, options, _ in self._module().READ_MAP:
            for option in options:
                if "*" not in option:
                    assert (REPO_ROOT / option).is_file(), option

class TestTranscript:
    """Script 包装模式：嵌套 Read 不触发 PostToolUse，pre 从 transcript 补读。"""

    def _transcript(self, tmp_path: Path, entries: list[tuple[str, str, bool | None]]) -> Path:
        path = tmp_path / "session.jsonl"
        lines = [json.dumps({"type": "session_start"}), "not json"]
        for call_id, file_path, is_error in entries:
            lines.append(json.dumps({"type": "message", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": call_id, "name": "Read", "input": {"file_path": file_path, "limit": 3},
                 "script_execution": {"run_id": "r1"}}]}}))
            if is_error is not None:
                lines.append(json.dumps({"type": "message", "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": call_id, "is_error": is_error, "content": "x"}]}}))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _pre(self, project: Path, transcript: Path | str) -> dict | None:
        proc = _run(project, "pre", {"session_id": "s1", "cwd": str(project), "transcript_path": str(transcript),
                                     "tool_name": "Edit", "tool_input": {"file_path": str(project / "src/core/mod.py")}})
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)["hookSpecificOutput"] if proc.stdout.strip() else None

    def test_successful_nested_read_counts(self, project: Path, tmp_path: Path) -> None:
        assert self._pre(project, self._transcript(tmp_path, [("c1", str(project / SPEC_01), False)])) is None

    def test_failed_or_unfinished_read_does_not_count(self, project: Path, tmp_path: Path) -> None:
        transcript = self._transcript(tmp_path, [("c1", str(project / SPEC_01), True),
                                                 ("c2", str(project / SPEC_01), None)])
        assert _denied(self._pre(project, transcript))

    def test_other_tools_in_transcript_do_not_count(self, project: Path, tmp_path: Path) -> None:
        path = tmp_path / "session.jsonl"
        path.write_text(json.dumps({"message": {"content": [
            {"type": "tool_use", "id": "g1", "name": "Grep", "input": {"file_path": str(project / SPEC_01)}},
            {"type": "tool_result", "tool_use_id": "g1", "content": "x"}]}}) + "\n", encoding="utf-8")
        assert _denied(self._pre(project, path))

    def test_missing_transcript_falls_back_to_records(self, project: Path, tmp_path: Path) -> None:
        assert _denied(self._pre(project, tmp_path / "absent.jsonl"))
        _read(project, str(project / SPEC_01))
        assert self._pre(project, tmp_path / "absent.jsonl") is None
