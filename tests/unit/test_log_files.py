"""LOGGING_SPEC：日志路径解析与测试隔离。"""

from __future__ import annotations

import json
from pathlib import Path

from core.settings import ObservabilityConfig
from core.trace.persist import persist_trace
from core.trace.trace_context import TRACE_TYPE_INGESTION, TraceContext
from observability.log_files import LOG_ROOT_ENV, resolve_log_path, trace_target


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
