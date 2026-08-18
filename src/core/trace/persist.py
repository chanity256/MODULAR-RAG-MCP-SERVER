"""Trace 持久化便捷入口（Dashboard / CLI / MCP 共用）。

直接追加 JSON Lines，不走全局 logger 单例（避免测试/多路径互相污染）。
写入失败不抛出——可观测性属于旁路，不允许影响主链路。
"""

from __future__ import annotations

import json
from pathlib import Path

from core.trace.trace_context import TraceContext
from observability.logger import get_logger

__all__ = ["persist_trace", "DEFAULT_TRACE_LOG"]

DEFAULT_TRACE_LOG = "logs/traces.jsonl"


def persist_trace(trace: TraceContext, log_file: str | None = None) -> None:
    """``finish`` + 追加一行 JSON 到 traces.jsonl。"""
    trace.finish()
    path = Path(log_file or DEFAULT_TRACE_LOG)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(trace.to_dict(), ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        get_logger("modular-rag.trace-persist").warning(
            "trace persistence failed: %s: %s", type(exc).__name__, exc
        )
