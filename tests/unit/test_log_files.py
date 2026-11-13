"""LOGGING_SPEC：日志路径解析与测试隔离。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from core.settings import ObservabilityConfig
from core.trace.persist import persist_trace
from core.trace.trace_context import TRACE_TYPE_INGESTION, TraceContext
from observability.log_files import LOG_ROOT_ENV, cleanup_expired, resolve_log_path, trace_target


class TestResolveLogPath:
    def test_relative_path_resolves_against_log_root(self, tmp_path, monkeypatch):
        monkeypatch.setenv(LOG_ROOT_ENV, str(tmp_path))
        assert resolve_log_path("./logs/traces.jsonl") == tmp_path / "logs" / "traces.jsonl"

    def test_absolute_path_is_untouched(self, tmp_path, monkeypatch):
        monkeypatch.setenv(LOG_ROOT_ENV, str(tmp_path / "root"))
        target = tmp_path / "elsewhere" / "t.jsonl"
        assert resolve_log_path(target) == target

    def test_without_log_root_falls_back_to_cwd_relative(self, monkeypatch):
        monkeypatch.delenv(LOG_ROOT_ENV, raising=False)
        assert resolve_log_path("logs/traces.jsonl") == Path("logs/traces.jsonl")

    def test_blank_log_root_is_ignored(self, monkeypatch):
        monkeypatch.setenv(LOG_ROOT_ENV, "  ")
        assert resolve_log_path("logs/x.jsonl") == Path("logs/x.jsonl")


class TestCleanupExpired:
    _TODAY = date(2026, 10, 17)

    def _touch(self, directory: Path, *names: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            (directory / name).write_text("x", encoding="utf-8")

    def test_keeps_exactly_retention_days_including_today(self, tmp_path):
        self._touch(
            tmp_path,
            "traces-2026-10-17.jsonl",  # 今天
            "traces-2026-10-15.jsonl",  # 第 3 天：保留
            "traces-2026-10-14.jsonl",  # 第 4 天：删除
            "traces-2026-01-01.jsonl",
        )
        removed = cleanup_expired(tmp_path, "traces", ".jsonl", 3, self._TODAY)
        assert [p.name for p in removed] == ["traces-2026-01-01.jsonl", "traces-2026-10-14.jsonl"]
        assert sorted(p.name for p in tmp_path.iterdir()) == ["traces-2026-10-15.jsonl", "traces-2026-10-17.jsonl"]

    def test_zero_retention_never_deletes(self, tmp_path):
        self._touch(tmp_path, "traces-2000-01-01.jsonl")
        assert cleanup_expired(tmp_path, "traces", ".jsonl", 0, self._TODAY) == []
        assert (tmp_path / "traces-2000-01-01.jsonl").exists()

    def test_only_strict_names_of_the_prefix_are_touched(self, tmp_path):
        keep = [
            "traces.jsonl",
            "traces.jsonl.migrated",
            "traces-2000-01-01.jsonl.bak",
            "traces-2000-13-01.jsonl",  # 非法日期
            "api-2000-01-01.log",  # 其他前缀
            "notes.md",
        ]
        self._touch(tmp_path, *keep)
        (tmp_path / "traces-2000-01-02.jsonl").mkdir()  # 同名目录不删
        assert cleanup_expired(tmp_path, "traces", ".jsonl", 1, self._TODAY) == []
        assert all((tmp_path / name).exists() for name in keep)

    def test_missing_directory_and_unlink_failure(self, tmp_path, monkeypatch):
        assert cleanup_expired(tmp_path / "nope", "traces", ".jsonl", 1, self._TODAY) == []
        self._touch(tmp_path, "traces-2000-01-01.jsonl")

        def _boom(self, *args, **kwargs):
            raise PermissionError("locked")

        monkeypatch.setattr(Path, "unlink", _boom)
        assert cleanup_expired(tmp_path, "traces", ".jsonl", 1, self._TODAY) == []


def test_session_log_root_is_isolated_from_repo(isolated_log_root):
    repo_logs = (Path(__file__).resolve().parents[2] / "logs").resolve()
    assert not isolated_log_root.resolve().is_relative_to(repo_logs)


def test_default_config_trace_lands_in_log_root(isolated_log_root):
    """回归：默认 ``observability.log_file`` 的 trace 不得写进仓库 logs/（L1）。"""
    trace = TraceContext(trace_type=TRACE_TYPE_INGESTION, metadata={"source_path": "x.md"})
    persist_trace(trace, log_file=ObservabilityConfig().log_file)
    target = trace_target(ObservabilityConfig().log_file)
    assert target.resolve().is_relative_to(isolated_log_root.resolve())
    ids = [json.loads(line)["trace_id"] for line in target.read_text(encoding="utf-8").splitlines()]
    assert trace.trace_id in ids
