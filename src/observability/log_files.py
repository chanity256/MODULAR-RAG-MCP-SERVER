"""日志文件路径工具（LOGGING_SPEC 3.1 / 3.2）。

纯函数，无全局副作用：路径解析（``RAG_LOG_ROOT``）、按日文件命名、过期清理。
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["LOG_ROOT_ENV", "resolve_log_path"]

#: 相对日志路径的解析根；未设置时相对 cwd（旧行为）。测试经此隔离仓库 ``logs/``。
LOG_ROOT_ENV = "RAG_LOG_ROOT"


def resolve_log_path(path: str | Path) -> Path:
    """绝对路径原样返回；相对路径在设置了 ``RAG_LOG_ROOT`` 时相对它解析。"""
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    root = os.environ.get(LOG_ROOT_ENV, "").strip()
    return Path(root) / candidate if root else candidate
