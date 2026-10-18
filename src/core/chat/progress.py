"""sidecar 日志 → ``search_log`` 帧转换（WEBUI_SIDECAR_SPEC WS1）。

从 ``ChatEngine._call_retriever`` 抽出的纯逻辑：把 sidecar 日志回调事件
``(level, message, timestamp)`` 转成 Chat WS 协议的 ``search_log`` 帧，
同一 ``(level, message)`` 在去重窗口内的重复事件丢弃（对齐上游
chat.py:424-456 的 0.5s 去重）。ChatEngine 与 Playground Deep 的搜索
流式端点（WS2 ``WS /api/v1/search/ws``）共用本模块。
"""

from __future__ import annotations

import time
from typing import Any

__all__ = ["LOG_DEDUP_WINDOW_S", "SearchLogFramer"]

# search_log 去重窗口（对齐上游 chat.py:424-456 的 0.5s 去重）。
LOG_DEDUP_WINDOW_S = 0.5


class SearchLogFramer:
    """把 ``(level, message, timestamp)`` 日志事件转成 ``search_log`` 帧。

    用法：每次回调事件调用 :meth:`to_frame`，返回 ``None`` 表示窗口内
    重复、应丢弃；否则返回的帧字典直接发给前端。
    """

    def __init__(self, task_id: str, *, dedup_window_s: float = LOG_DEDUP_WINDOW_S) -> None:
        self._task_id = task_id
        self._dedup_window_s = dedup_window_s
        self._last_seen: dict[tuple[str, str], float] = {}

    def to_frame(self, level: str, message: str, timestamp: float | None = None) -> dict[str, Any] | None:
        """转换一个日志事件；去重窗口内的重复事件返回 ``None``。"""
        now = time.monotonic()
        key = (str(level), str(message))
        previous = self._last_seen.get(key)
        self._last_seen[key] = now
        if previous is not None and now - previous < self._dedup_window_s:
            return None
        frame: dict[str, Any] = {
            "type": "search_log",
            "task_id": self._task_id,
            "level": str(level),
            "message": str(message),
        }
        if timestamp is not None:
            frame["timestamp"] = timestamp
        return frame
