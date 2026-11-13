"""Trace 持久化便捷入口（Dashboard / CLI / MCP 共用）。

直接追加 JSON Lines，不走全局 logger 单例（避免测试/多路径互相污染）。
写入失败不抛出——可观测性属于旁路，不允许影响主链路。

``log_file`` 以 ``.jsonl`` 结尾时写该单文件；否则视为目录，按本地日期写
``traces-YYYY-MM-DD.jsonl``（LOGGING_SPEC 3.3）。每条记录单行追加、不重命名，
多进程同写安全。
"""

from __future__ import annotations

import json
from datetime import date

from core.trace.trace_context import TraceContext
from observability.log_files import trace_target
from observability.logger import get_logger

__all__ = ["persist_trace", "DEFAULT_TRACE_LOG"]

DEFAULT_TRACE_LOG = "logs/traces"


def persist_trace(trace: TraceContext, log_file: str | None = None, *, day: date | None = None) -> None:
    """``finish`` + 追加一行 JSON 到 trace 日志。

    Args:
        trace: 待落盘的追踪上下文。
        log_file: 单文件路径（``.jsonl``）或按日切分的目录；缺省 ``logs/traces``。
        day: 目录模式下的文件日期（缺省本地今天；测试注入）。
    """
    trace.finish()
    try:
        path = trace_target(log_file or DEFAULT_TRACE_LOG, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(trace.to_dict(), ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        get_logger("modular-rag.trace-persist").warning(
            "trace persistence failed: %s: %s", type(exc).__name__, exc
        )
