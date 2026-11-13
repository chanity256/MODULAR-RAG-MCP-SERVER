"""``core.chat.progress`` 纯逻辑单测（WEBUI_SIDECAR_SPEC WS1）：日志 → search_log 帧。"""

from __future__ import annotations

import time

import pytest

from core.chat.progress import LOG_DEDUP_WINDOW_S, SearchLogFramer


@pytest.fixture
def monotonic(monkeypatch):
    """可控的单调时钟（engine 去重语义基于 time.monotonic）。"""
    now = {"t": 100.0}

    def advance(seconds: float) -> None:
        now["t"] += seconds

    monkeypatch.setattr(time, "monotonic", lambda: now["t"])
    return advance


def test_frame_shape_with_and_without_timestamp():
    framer = SearchLogFramer("trace-1")
    assert framer.to_frame("info", "analyzing", 1.5) == {
        "type": "search_log", "task_id": "trace-1", "level": "info", "message": "analyzing", "timestamp": 1.5,
    }
    assert framer.to_frame("warning", "slow") == {
        "type": "search_log", "task_id": "trace-1", "level": "warning", "message": "slow",
    }


def test_dedup_same_level_message_within_window(monkeypatch):
    """同一 (level, message) 在 0.5s 窗口内的重复事件丢弃（对齐 Chat 语义）。"""
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    framer = SearchLogFramer("t")

    assert framer.to_frame("info", "analyzing", 1.5) is not None
    assert framer.to_frame("info", "analyzing", 1.6) is None  # 窗口内重复
    assert framer.to_frame("warning", "analyzing") is not None  # level 不同不去重
    assert framer.to_frame("info", "retrying") is not None  # message 不同不去重


def test_dedup_window_expiry_allows_repeat(monkeypatch):
    """窗口过后同文案再次放行（边界：恰好等于窗口长度即过期）。"""
    now = {"t": 100.0}
    monkeypatch.setattr(time, "monotonic", lambda: now["t"])
    framer = SearchLogFramer("t")

    assert framer.to_frame("info", "analyzing") is not None
    now["t"] += LOG_DEDUP_WINDOW_S
    assert framer.to_frame("info", "analyzing") is not None
    now["t"] += LOG_DEDUP_WINDOW_S - 0.1
    assert framer.to_frame("info", "analyzing") is None


def test_dedup_window_is_configurable(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    framer = SearchLogFramer("t", dedup_window_s=10.0)

    assert framer.to_frame("info", "x") is not None
    assert framer.to_frame("info", "x") is None


def test_non_string_level_message_are_coerced():
    framer = SearchLogFramer("t")
    frame = framer.to_frame(1, "msg")  # type: ignore[arg-type]
    assert frame is not None and frame["level"] == "1" and frame["message"] == "msg"
