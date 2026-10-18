"""docs_guard.py hook 的离线确定性单测。

用临时目录伪造项目 docs/ 树，用可执行假脚本冒充 pytest 判定，
伪造 stdin JSON 调用 hook 子命令，覆盖：
快照对比（增/删/改）、无改动放行、stop_hook_active 放行、
语义映射、结构失败拦截、快照缺失、故障 fail-open。

不触网、不依赖真实 venv。
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".factory" / "hooks" / "docs_guard.py"

PASSING_PYTEST = """#!/bin/sh
echo "2 passed in 0.01s"
exit 0
"""

FAILING_PYTEST = """#!/bin/sh
echo "FAILED tests/unit/test_docs_links.py::test_relative_links_resolve"
echo "1 failed, 1 passed in 0.02s"
exit 1
"""

BROKEN_PYTEST = """#!/bin/sh
echo "ERROR: collection failed"
echo "no tests ran in 0.01s"
exit 2
"""


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture()
def env_factory(tmp_path: Path):
    def _make(project: Path, *, pytest_body: str = PASSING_PYTEST) -> tuple[dict, Path]:
        fake_pytest = tmp_path / "fake_pytest" / f"pytest_{abs(hash(pytest_body)) & 0xFFFF}.sh"
        fake_pytest.parent.mkdir(exist_ok=True)
        _write_exec(fake_pytest, pytest_body)
        env = os.environ.copy()
        env["DOCS_GUARD_PROJECT_DIR"] = str(project)
        env["DOCS_GUARD_SNAPSHOT_DIR"] = str(tmp_path / "snapshots")
        env["DOCS_GUARD_PYTHON"] = str(fake_pytest)
        env["DOCS_GUARD_TIMEOUT"] = "10"
        return env, fake_pytest

    return _make


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "docs" / "backlog").mkdir(parents=True)
    (root / "docs" / "MOC.md").write_text("# MOC\n", encoding="utf-8")
    (root / "docs" / "backlog" / "README.md").write_text("# Backlog\n", encoding="utf-8")
    return root


def _run_hook(env: dict, command: str, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(HOOK), command],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _payload(project: Path, *, event: str, stop_hook_active: bool = False) -> dict:
    return {
        "session_id": "test-session-1",
        "hook_event_name": event,
        "stop_hook_active": stop_hook_active,
        "cwd": str(project),
    }


def _snapshot(env: dict, project: Path) -> subprocess.CompletedProcess:
    return _run_hook(env, "snapshot", _payload(project, event="UserPromptSubmit"))


class TestSnapshotDiff:
    def test_snapshot_then_change_blocks_with_changed_files(
        self, tmp_path: Path, project: Path, env_factory
    ) -> None:
        env, _ = env_factory(project, pytest_body=FAILING_PYTEST)
        assert _snapshot(env, project).returncode == 0

        (project / "docs" / "NEW.md").write_text("x\n", encoding="utf-8")
        (project / "docs" / "MOC.md").write_text("# MOC v2\n", encoding="utf-8")
        (project / "docs" / "backlog" / "README.md").unlink()

        proc = _run_hook(env, "stop", _payload(project, event="Stop"))
        assert proc.returncode == 0
        out = json.loads(proc.stdout)
        assert out["decision"] == "block"
        assert "docs/NEW.md" in out["reason"]
        assert "docs/MOC.md" in out["reason"]
        assert "docs/backlog/README.md" in out["reason"]
        assert "docs-governance" in out["reason"]

    def test_no_change_passes(self, project: Path, env_factory) -> None:
        env, _ = env_factory(project)
        assert _snapshot(env, project).returncode == 0
        proc = _run_hook(env, "stop", _payload(project, event="Stop"))
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""

    def test_stop_hook_active_passes_even_with_changes(
        self, project: Path, env_factory
    ) -> None:
        env, _ = env_factory(project, pytest_body=FAILING_PYTEST)
        assert _snapshot(env, project).returncode == 0
        (project / "docs" / "MOC.md").write_text("# MOC v2\n", encoding="utf-8")
        proc = _run_hook(env, "stop", _payload(project, event="Stop", stop_hook_active=True))
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""


class TestSemanticReminders:
    def test_backlog_change_lists_related_docs_with_structure_passing(
        self, project: Path, env_factory
    ) -> None:
        env, _ = env_factory(project)
        assert _snapshot(env, project).returncode == 0
        (project / "docs" / "backlog" / "README.md").write_text("# Backlog v2\n", encoding="utf-8")
        proc = _run_hook(env, "stop", _payload(project, event="Stop"))
        assert proc.returncode == 0
        out = json.loads(proc.stdout)
        assert out["decision"] == "block"
        assert "docs/backlog/README.md" in out["reason"]
        assert "docs/MOC.md" in out["reason"]
        # 结构检查通过，不应出现「必须修」
        assert "必须修" not in out["reason"]

    def test_usage_guide_change_mentions_agents(self, project: Path, env_factory) -> None:
        env, _ = env_factory(project)
        assert _snapshot(env, project).returncode == 0
        (project / "docs" / "reference").mkdir()
        (project / "docs" / "reference" / "USAGE_GUIDE.md").write_text("guide\n", encoding="utf-8")
        proc = _run_hook(env, "stop", _payload(project, event="Stop"))
        out = json.loads(proc.stdout)
        assert "AGENTS.md" in out["reason"]


class TestMissingSnapshotAndFaults:
    def test_missing_snapshot_only_structure_check(self, project: Path, env_factory) -> None:
        # 无快照：结构失败仍拦截，但不发语义提醒。
        env, _ = env_factory(project, pytest_body=FAILING_PYTEST)
        proc = _run_hook(env, "stop", _payload(project, event="Stop"))
        assert proc.returncode == 0
        out = json.loads(proc.stdout)
        assert out["decision"] == "block"
        assert "必须修" in out["reason"]
        assert "语义同步提醒" not in out["reason"]

    def test_missing_snapshot_structure_passing_is_silent(
        self, project: Path, env_factory
    ) -> None:
        env, _ = env_factory(project)
        proc = _run_hook(env, "stop", _payload(project, event="Stop"))
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""

    def test_missing_python_fails_open(self, project: Path, env_factory) -> None:
        env, _ = env_factory(project, pytest_body=FAILING_PYTEST)
        assert _snapshot(env, project).returncode == 0
        (project / "docs" / "MOC.md").write_text("# MOC v2\n", encoding="utf-8")
        env["DOCS_GUARD_PYTHON"] = str(project / ".venv" / "missing" / "python")
        proc = _run_hook(env, "stop", _payload(project, event="Stop"))
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""
        assert "docs_guard" in proc.stderr

    def test_pytest_collection_error_fails_open(self, project: Path, env_factory) -> None:
        env, _ = env_factory(project, pytest_body=BROKEN_PYTEST)
        assert _snapshot(env, project).returncode == 0
        (project / "docs" / "MOC.md").write_text("# MOC v2\n", encoding="utf-8")
        proc = _run_hook(env, "stop", _payload(project, event="Stop"))
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""

    def test_bad_stdin_fails_open(self, env_factory, project: Path) -> None:
        env, _ = env_factory(project)
        proc = subprocess.run(
            ["python3", str(HOOK), "stop"],
            input="not json at all",
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""

    def test_unknown_subcommand_fails_open(self, env_factory, project: Path) -> None:
        env, _ = env_factory(project)
        proc = _run_hook(env, "bogus", _payload(project, event="Stop"))
        assert proc.returncode == 0
