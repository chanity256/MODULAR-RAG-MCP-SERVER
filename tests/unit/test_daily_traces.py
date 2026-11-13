"""LOGGING_SPEC LG2/LG3：trace 按日写入、TraceService 目录模式、可观测性配置。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import ConfigError, ObservabilityConfig, load_settings
from core.trace.persist import persist_trace
from core.trace.trace_context import TRACE_TYPE_INGESTION, TRACE_TYPE_QUERY, TraceContext
from observability.dashboard.services.trace_service import TraceService
from observability.log_files import daily_path, is_single_file, trace_target

_DAY1 = date(2026, 10, 16)
_DAY2 = date(2026, 10, 17)

_REQUIRED_YAML = dedent(
    """\
    llm: {provider: azure, model: gpt-4o}
    embedding: {provider: openai, model: text-embedding-3-small}
    vision_llm: {provider: azure, model: gpt-4o}
    vector_store: {backend: chroma}
    retrieval: {sparse_backend: bm25, fusion_algorithm: rrf}
    rerank: {backend: none}
    evaluation: {backends: [custom]}
    """
)


def _settings(tmp_path: Path, observability: str = ""):
    path = tmp_path / "settings.yaml"
    path.write_text(_REQUIRED_YAML + observability, encoding="utf-8")
    return load_settings(path)


def _trace(trace_type: str = TRACE_TYPE_QUERY, query: str = "q") -> TraceContext:
    return TraceContext(trace_type=trace_type, metadata={"query": query})


class TestPathHelpers:
    def test_single_file_detection(self):
        assert is_single_file("logs/traces.jsonl")
        assert not is_single_file("logs/traces")

    def test_daily_path_naming(self, tmp_path):
        assert daily_path(tmp_path, "api", ".log", _DAY1) == tmp_path / "api-2026-10-16.log"

    def test_trace_target_modes(self, tmp_path):
        assert trace_target(tmp_path / "t.jsonl", _DAY1) == tmp_path / "t.jsonl"
        assert trace_target(tmp_path / "traces", _DAY1) == tmp_path / "traces" / "traces-2026-10-16.jsonl"


class TestPersistTraceDirectoryMode:
    def test_writes_one_file_per_day(self, tmp_path):
        directory = tmp_path / "traces"
        first, second, third = _trace(query="a"), _trace(query="b"), _trace(query="c")
        persist_trace(first, log_file=str(directory), day=_DAY1)
        persist_trace(second, log_file=str(directory), day=_DAY1)
        persist_trace(third, log_file=str(directory), day=_DAY2)
        day1 = (directory / "traces-2026-10-16.jsonl").read_text(encoding="utf-8").splitlines()
        day2 = (directory / "traces-2026-10-17.jsonl").read_text(encoding="utf-8").splitlines()
        assert [json.loads(line)["trace_id"] for line in day1] == [first.trace_id, second.trace_id]
        assert [json.loads(line)["trace_id"] for line in day2] == [third.trace_id]

    def test_single_file_mode_unchanged(self, tmp_path):
        target = tmp_path / "t.jsonl"
        persist_trace(_trace(), log_file=str(target), day=_DAY1)
        assert target.is_file() and len(target.read_text(encoding="utf-8").splitlines()) == 1


class TestTraceServiceDirectoryMode:
    def _seed(self, directory: Path) -> tuple[TraceContext, TraceContext, TraceContext]:
        old = _trace(query="old")
        ingest = _trace(TRACE_TYPE_INGESTION)
        new = _trace(query="new")
        persist_trace(old, log_file=str(directory), day=_DAY1)
        persist_trace(ingest, log_file=str(directory), day=_DAY1)
        persist_trace(new, log_file=str(directory), day=_DAY2)
        return old, ingest, new

    def test_merges_daily_files_newest_first(self, tmp_path):
        directory = tmp_path / "traces"
        old, ingest, new = self._seed(directory)
        (directory / "notes.txt").write_text("ignored", encoding="utf-8")
        service = TraceService(directory)
        assert [t["trace_id"] for t in service.list_traces("query")] == [new.trace_id, old.trace_id]
        assert service.get_trace(ingest.trace_id)["trace_type"] == "ingestion"

    def test_unchanged_files_are_not_reparsed(self, tmp_path, monkeypatch):
        directory = tmp_path / "traces"
        self._seed(directory)
        service = TraceService(directory)
        service.list_traces()
        parsed: list[int] = []
        original = TraceService._parse
        monkeypatch.setattr(TraceService, "_parse", staticmethod(lambda text: parsed.append(1) or original(text)))
        persist_trace(_trace(query="later"), log_file=str(directory), day=_DAY2)
        assert len(service.list_traces("query")) == 3
        assert len(parsed) == 1  # 只重解析被追加的当天文件

    def test_removed_file_drops_out_of_cache(self, tmp_path):
        directory = tmp_path / "traces"
        old, _, new = self._seed(directory)
        service = TraceService(directory)
        assert len(service.list_traces()) == 3
        (directory / "traces-2026-10-16.jsonl").unlink()
        assert [t["trace_id"] for t in service.list_traces()] == [new.trace_id]

    def test_missing_directory_is_empty(self, tmp_path):
        assert TraceService(tmp_path / "nope").list_traces() == []

    def test_settings_default_reads_directory(self, tmp_path, isolated_log_root):
        settings = _settings(tmp_path)
        trace = _trace(query="via-settings")
        persist_trace(trace, log_file=settings.observability.log_file)
        service = TraceService(settings=settings)
        assert service.path.resolve().is_relative_to(isolated_log_root.resolve())
        assert any(t["trace_id"] == trace.trace_id for t in service.list_traces("query"))


class TestObservabilityConfig:
    def test_defaults(self, tmp_path):
        obs = _settings(tmp_path).observability
        assert (obs.log_file, obs.app_log_dir, obs.retention_days, obs.log_level) == (
            "./logs/traces", "./logs/app", 30, "INFO"
        )
        assert ObservabilityConfig().log_file == "./logs/traces"

    def test_values_from_yaml_and_level_normalized(self, tmp_path):
        obs = _settings(
            tmp_path,
            "observability:\n  app_log_dir: /x/app\n  retention_days: 0\n  logging:\n    log_level: debug\n",
        ).observability
        assert (obs.app_log_dir, obs.retention_days, obs.log_level) == ("/x/app", 0, "DEBUG")
        assert "app_log_dir" not in obs.extra and "retention_days" not in obs.extra

    @pytest.mark.parametrize("value", ["-1", "true", "'7'", "1.5"])
    def test_invalid_retention_days(self, tmp_path, value):
        with pytest.raises(ConfigError, match="observability.retention_days"):
            _settings(tmp_path, f"observability:\n  retention_days: {value}\n")

    def test_invalid_log_level(self, tmp_path):
        with pytest.raises(ConfigError, match="observability.logging.log_level"):
            _settings(tmp_path, "observability:\n  logging:\n    log_level: LOUD\n")
