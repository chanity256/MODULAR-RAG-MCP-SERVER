"""LOGGING_SPEC LG4：应用运行日志按日落盘（setup_app_logging / DailyFileHandler）。"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.settings import ObservabilityConfig
from observability.logger import DailyFileHandler, setup_app_logging, setup_cli_logging

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"


class _Clock:
    def __init__(self, day: date) -> None:
        self.day = day

    def __call__(self) -> date:
        return self.day


def _settings(tmp_path: Path, **overrides) -> SimpleNamespace:
    values = {
        "log_file": str(tmp_path / "traces"),
        "app_log_dir": str(tmp_path / "app"),
        "retention_days": 30,
        "log_level": "INFO",
    }
    values.update(overrides)
    return SimpleNamespace(observability=ObservabilityConfig(**values))


def _file_handlers() -> list[DailyFileHandler]:
    return [h for h in logging.getLogger().handlers if isinstance(h, DailyFileHandler)]


def test_writes_component_file_for_today(tmp_path):
    clock = _Clock(date(2026, 10, 17))
    path = setup_app_logging(_settings(tmp_path), "api", today=clock)
    assert path == tmp_path / "app" / "api-2026-10-17.log"
    logging.getLogger("modular-rag.test").warning("disk almost full")
    line = path.read_text(encoding="utf-8").strip()
    assert line.endswith("| WARNING | modular-rag.test | disk almost full")


def test_log_level_from_settings_is_applied(tmp_path):
    path = setup_app_logging(_settings(tmp_path, log_level="WARNING"), "query", today=_Clock(date(2026, 10, 17)))
    logging.getLogger("modular-rag.test").info("hidden")
    logging.getLogger("modular-rag.test").error("shown")
    text = path.read_text(encoding="utf-8")
    assert "shown" in text and "hidden" not in text
    assert logging.getLogger().level == logging.WARNING


def test_setup_is_idempotent(tmp_path):
    clock = _Clock(date(2026, 10, 17))
    setup_app_logging(_settings(tmp_path), "mcp", today=clock)
    setup_app_logging(_settings(tmp_path), "mcp", today=clock)
    assert len(_file_handlers()) == 1


def test_uvicorn_access_log_is_not_written_to_file(tmp_path):
    path = setup_app_logging(_settings(tmp_path), "api", today=_Clock(date(2026, 10, 17)))
    # 直接交给 handler：其他用例可能已对 uvicorn logger 做过 dictConfig（propagate=False）。
    handler = _file_handlers()[0]
    for name, msg in (("uvicorn.access", '"GET /api/health HTTP/1.1" 200'), ("uvicorn.error", "Uvicorn running")):
        handler.handle(logging.makeLogRecord({"name": name, "msg": msg, "levelno": logging.INFO, "levelname": "INFO"}))
    text = path.read_text(encoding="utf-8")
    assert "Uvicorn running" in text and "GET /api/health" not in text


def test_day_rollover_switches_file_and_cleans_up(tmp_path):
    traces = tmp_path / "traces"
    app = tmp_path / "app"
    traces.mkdir()
    app.mkdir()
    (traces / "traces-2026-10-10.jsonl").write_text("{}\n", encoding="utf-8")
    (traces / "traces-2026-10-16.jsonl").write_text("{}\n", encoding="utf-8")
    (app / "ingest-2026-10-10.log").write_text("old\n", encoding="utf-8")
    clock = _Clock(date(2026, 10, 16))
    setup_app_logging(_settings(tmp_path, retention_days=3), "api", today=clock)
    assert not (traces / "traces-2026-10-10.jsonl").exists()  # 启动即清理
    assert not (app / "ingest-2026-10-10.log").exists()  # 覆盖其他组件
    logging.getLogger("modular-rag.test").warning("day one")
    assert "day one" in (app / "api-2026-10-16.log").read_text(encoding="utf-8")

    clock.day = date(2026, 10, 20)
    logging.getLogger("modular-rag.test").warning("day five")
    assert "day five" in (app / "api-2026-10-20.log").read_text(encoding="utf-8")
    assert not (traces / "traces-2026-10-16.jsonl").exists()  # 跨天触发清理
    assert not (app / "api-2026-10-16.log").exists()


def test_single_file_trace_mode_is_never_cleaned(tmp_path):
    legacy = tmp_path / "traces.jsonl"
    legacy.write_text("{}\n", encoding="utf-8")
    setup_app_logging(_settings(tmp_path, log_file=str(legacy), retention_days=1), "api", today=_Clock(date(2026, 10, 17)))
    assert legacy.exists()


def test_unwritable_directory_keeps_stderr_only(tmp_path):
    blocker = tmp_path / "app"
    blocker.write_text("not a directory", encoding="utf-8")
    assert setup_app_logging(_settings(tmp_path), "api", today=_Clock(date(2026, 10, 17))) is None
    assert _file_handlers() == []


def test_invalid_component_name_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="component"):
        DailyFileHandler(tmp_path, "../evil")


class TestSetupCliLogging:
    def test_loads_config_from_argv(self, isolated_log_root):
        path = setup_cli_logging("ingest", ["--path", "x", "--config", str(_REPO_CONFIG)], "missing.yaml")
        assert path is not None and path.name.startswith("ingest-")
        assert path.resolve().is_relative_to(isolated_log_root.resolve())

    def test_bad_config_is_silently_skipped(self, tmp_path):
        assert setup_cli_logging("query", ["--config", str(tmp_path / "nope.yaml")], "x.yaml") is None
        assert _file_handlers() == []
