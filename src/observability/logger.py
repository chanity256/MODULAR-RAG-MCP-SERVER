"""结构化日志占位实现（阶段 A）。

阶段 A 仅提供最小可用的 stderr logger，避免污染未来 MCP 的 stdout 通道。
阶段 F2 将增强为 JSON Lines 格式输出与文件持久化。
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    root = logging.getLogger()
    # 仅在 root logger 尚无 handler 时配置，避免重复输出。
    if not root.handlers:
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    _CONFIGURED = True


def get_logger(name: str = "modular-rag") -> logging.Logger:
    """返回一个输出到 stderr 的 logger。"""
    _configure()
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# F2：JSON Lines 结构化日志 + trace 持久化
# ---------------------------------------------------------------------------

import json as _json
from pathlib import Path as _Path

from core.settings import Settings as _Settings

__all__ = ["get_logger", "get_trace_logger", "write_trace", "JSONFormatter"]

_TRACES_LOGGER_NAME = "modular-rag.traces"


class JSONFormatter(logging.Formatter):
    """把 LogRecord 渲染为单行 JSON（JSON Lines 格式）。

    固定字段：``time`` / ``level`` / ``logger`` / ``message``；
    额外字段合并 ``record.extra``（若有）。
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "time": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return _json.dumps(payload, ensure_ascii=False, default=str)


def get_trace_logger(log_file: str | None = None) -> logging.Logger:
    """返回配置了 JSONFormatter + FileHandler 的 trace logger。

    Args:
        log_file: 目标 JSONL 文件路径；``None`` 取
            ``settings.observability.log_file``（默认 ``logs/traces.jsonl``）。
    """
    _configure()
    logger = logging.getLogger(_TRACES_LOGGER_NAME)
    if logger.handlers:  # 已配置（多进程/重复调用幂等，取首路径）
        return logger
    path = _Path(log_file or _Settings().observability.log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def write_trace(trace_dict: dict, log_file: str | None = None) -> None:
    """把 ``trace.to_dict()`` 结果追加为一行 JSON（JSON Lines）。

    Args:
        trace_dict: 可 JSON 序列化的 trace 字典（含 ``trace_type``）。
        log_file: 目标文件路径；``None`` 取配置默认值。
    """
    logger = get_trace_logger(log_file)
    logger.info("trace", extra={"extra": dict(trace_dict)})
