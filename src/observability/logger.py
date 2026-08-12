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
