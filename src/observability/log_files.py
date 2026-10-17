"""日志文件路径工具（LOGGING_SPEC 3.1 / 3.2）。

纯函数，无全局副作用：路径解析（``RAG_LOG_ROOT``）、按日文件命名、过期清理。
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

__all__ = [
    "LOG_ROOT_ENV",
    "TRACE_FILE_PREFIX",
    "TRACE_FILE_SUFFIX",
    "daily_path",
    "is_single_file",
    "local_today",
    "resolve_log_path",
    "trace_target",
]

#: 相对日志路径的解析根；未设置时相对 cwd（旧行为）。测试经此隔离仓库 ``logs/``。
LOG_ROOT_ENV = "RAG_LOG_ROOT"

TRACE_FILE_PREFIX = "traces"
TRACE_FILE_SUFFIX = ".jsonl"


def resolve_log_path(path: str | Path) -> Path:
    """绝对路径原样返回；相对路径在设置了 ``RAG_LOG_ROOT`` 时相对它解析。"""
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    root = os.environ.get(LOG_ROOT_ENV, "").strip()
    return Path(root) / candidate if root else candidate


def is_single_file(path: str | Path) -> bool:
    """``.jsonl`` 结尾为单文件模式（旧语义），否则为按日切分的目录。"""
    return Path(path).suffix == TRACE_FILE_SUFFIX


def local_today() -> date:
    # 文件名用本地日期，人工按"今天/昨天"查看时不必换算 UTC。
    return date.today()


def daily_path(directory: str | Path, prefix: str, suffix: str, day: date) -> Path:
    return Path(directory) / f"{prefix}-{day.isoformat()}{suffix}"


def trace_target(log_file: str | Path, day: date | None = None) -> Path:
    """trace 实际写入的文件：单文件模式即其本身，目录模式为当天文件。"""
    path = resolve_log_path(log_file)
    if is_single_file(path):
        return path
    return daily_path(path, TRACE_FILE_PREFIX, TRACE_FILE_SUFFIX, day or local_today())
