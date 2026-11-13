"""F2：JSON Lines logger 测试（JSONFormatter / get_trace_logger / write_trace）。"""

from __future__ import annotations

import json
import logging

from observability.logger import JSONFormatter, get_trace_logger, write_trace


def _fresh_trace_logger():
    """移除全局 traces logger 的 handlers，强制按新路径重建。"""
    logger = logging.getLogger("modular-rag.traces")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    return logger


class TestJSONFormatter:
    def test_formats_record_as_single_line_json(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            "modular-rag", logging.INFO, __file__, 1, "hello %s", ("world",), None
        )
        line = formatter.format(record)
        data = json.loads(line)
        assert data["message"] == "hello world"
        assert data["level"] == "INFO"
        assert data["logger"] == "modular-rag"
        assert "\n" not in line.strip()

    def test_merges_extra_dict(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            "modular-rag", logging.INFO, __file__, 1, "m", (), None
        )
        record.extra = {"trace_type": "query", "n": 3}
        data = json.loads(formatter.format(record))
        assert data["trace_type"] == "query" and data["n"] == 3


class TestWriteTrace:
    def test_write_trace_appends_jsonl_line(self, tmp_path):
        _fresh_trace_logger()
        log_file = tmp_path / "traces.jsonl"
        write_trace(
            {"trace_id": "t1", "trace_type": "query", "stages": []},
            log_file=str(log_file),
        )
        write_trace(
            {"trace_id": "t2", "trace_type": "ingestion", "stages": []},
            log_file=str(log_file),
        )
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        first, second = (json.loads(line) for line in lines)
        assert first["trace_type"] == "query" and first["trace_id"] == "t1"
        assert second["trace_type"] == "ingestion"

    def test_write_trace_creates_parent_dirs(self, tmp_path):
        _fresh_trace_logger()
        log_file = tmp_path / "nested" / "dir" / "traces.jsonl"
        write_trace({"trace_type": "query"}, log_file=str(log_file))
        assert log_file.exists()
        assert json.loads(log_file.read_text())["trace_type"] == "query"


class TestGetTraceLogger:
    def test_returns_configured_logger_idempotent(self, tmp_path):
        _fresh_trace_logger()
        logger = get_trace_logger(log_file=str(tmp_path / "a.jsonl"))
        assert logger.name == "modular-rag.traces"
        assert logger.handlers and isinstance(logger.handlers[0].formatter, JSONFormatter)
        assert get_trace_logger(log_file=str(tmp_path / "b.jsonl")) is logger
        for handler in list(logger.handlers):  # 清理，避免污染其他用例
            logger.removeHandler(handler)
            handler.close()
