"""日志文件路径工具（LOGGING_SPEC 3.1 / 3.2）。

纯函数，无全局副作用：路径解析（``RAG_LOG_ROOT``）、按日文件命名、过期清理。
"""

from __future__ import annotations

import os
import re
from datetime import date, timedelta
from pathlib import Path

__all__ = [
    "LOG_ROOT_ENV",
    "TRACE_FILE_PREFIX",
    "TRACE_FILE_SUFFIX",
    "cleanup_expired",
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


def cleanup_expired(
    directory: str | Path,
    prefix: str,
    suffix: str,
    retention_days: int,
    today: date | None = None,
) -> list[Path]:
    """删除过期的按日文件，保留含今天在内最近 ``retention_days`` 个日期。

    只匹配严格的 ``<prefix>-YYYY-MM-DD<suffix>`` 文件名，其他文件不碰；
    ``retention_days <= 0`` 不清理；单个文件删除失败跳过、不抛出。
    """
    if retention_days <= 0:
        return []
    root = Path(directory)
    if not root.is_dir():
        return []
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d{{4}}-\d{{2}}-\d{{2}}){re.escape(suffix)}$")
    cutoff = (today or local_today()) - timedelta(days=retention_days)
    removed: list[Path] = []
    for path in root.iterdir():
        match = pattern.match(path.name)
        if match is None or not path.is_file():
            continue
        try:
            day = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if day > cutoff:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(path)
    return sorted(removed)
