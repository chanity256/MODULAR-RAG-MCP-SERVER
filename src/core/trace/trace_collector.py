"""TraceCollector（F1）：收集完成的 TraceContext 并持久化。

``collect(trace)``：
1. 若 trace 未 ``finish()`` 则先补调（保证总耗时/结束时间存在）；
2. 追加到内存列表（供测试/立即读取）；
3. 经 ``observability.logger.write_trace`` 追加到 ``logs/traces.jsonl``。

写入失败（如目录只读）不抛出——trace 持久化属于旁路可观测性，
不允许影响主链路。
"""

from __future__ import annotations

from typing import Any, Callable

from core.trace.trace_context import TraceContext
from observability.logger import get_logger, write_trace

__all__ = ["TraceCollector"]


class TraceCollector:
    """收集 TraceContext 并触发 JSONL 持久化。"""

    def __init__(
        self,
        writer: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """
        Args:
            writer: 持久化函数（默认 ``write_trace``）；测试可注入假写入器。
        """
        self._writer = writer or write_trace
        self._logger = get_logger("modular-rag.trace-collector")
        self.traces: list[dict[str, Any]] = []

    def collect(self, trace: TraceContext) -> None:
        """收集一个 trace（自动 finish + 持久化），失败仅告警。"""
        trace.finish()
        data = trace.to_dict()
        self.traces.append(data)
        try:
            self._writer(data)
        except Exception as exc:  # 持久化失败不影响主链路
            self._logger.warning("trace persistence failed: %s: %s", type(exc).__name__, exc)

    def __len__(self) -> int:
        return len(self.traces)
