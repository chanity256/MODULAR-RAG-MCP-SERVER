"""TraceContext：Ingestion / Query 链路统一的追踪上下文（F1 增强版）。

在 C5 最小版基础上补充：
- ``trace_type``：``"query"`` / ``"ingestion"``，区分链路类型。
- ``record_stage`` 自动附带 ``elapsed_ms``（距上一阶段记录的耗时，毫秒）。
- ``finish()``：标记 trace 结束并计算总耗时（幂等）。
- ``elapsed_ms(stage_name?)``：指定阶段或总耗时。
- ``to_dict()``：JSON 可序列化，含 trace_id/trace_type/started_at/
  finished_at/total_elapsed_ms/stages。
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

__all__ = ["TRACE_TYPE_INGESTION", "TRACE_TYPE_QUERY", "TraceContext"]

#: 合法的 trace 类型。
TRACE_TYPE_QUERY = "query"
TRACE_TYPE_INGESTION = "ingestion"
_VALID_TRACE_TYPES = (TRACE_TYPE_QUERY, TRACE_TYPE_INGESTION)


class TraceContext:
    """请求级追踪上下文（显式调用模式，低侵入）。"""

    def __init__(
        self,
        trace_id: str | None = None,
        trace_type: str = TRACE_TYPE_QUERY,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if trace_type not in _VALID_TRACE_TYPES:
            raise ValueError(
                f"trace_type must be one of {_VALID_TRACE_TYPES}, got {trace_type!r}"
            )
        self.trace_id = trace_id or uuid.uuid4().hex
        self.trace_type = trace_type
        self.metadata: dict[str, Any] = dict(metadata or {})
        self.stages: list[dict[str, Any]] = []
        self.started_at: str = datetime.now(timezone.utc).isoformat()
        self.finished_at: str | None = None
        self.total_elapsed_ms: float | None = None
        self._t0 = time.perf_counter()
        self._last_mark = self._t0

    def record_stage(self, stage: str, **data: Any) -> None:
        """记录一个处理阶段（后写入的排在列表尾部）。

        自动注入 ``elapsed_ms``（距上一阶段记录的时间差）；调用方显式
        传入的 ``elapsed_ms`` 优先（组件自测更精确）。
        """
        now = time.perf_counter()
        elapsed = round((now - self._last_mark) * 1000, 3)
        record: dict[str, Any] = {"stage": stage, "elapsed_ms": elapsed}
        record.update(data)
        self.stages.append(record)
        self._last_mark = now

    def get_stage(self, stage: str) -> dict[str, Any] | None:
        """返回最近一次同名阶段记录（无则 None）。"""
        for record in reversed(self.stages):
            if record.get("stage") == stage:
                return record
        return None

    def elapsed_ms(self, stage_name: str | None = None) -> float:
        """获取耗时（毫秒，3 位小数）。

        Args:
            stage_name: 指定阶段名时返回该阶段记录的 ``elapsed_ms``
                （无该阶段抛 ``KeyError``）；``None`` 返回总耗时
                （已 ``finish()`` 返回总耗时，否则返回至今耗时）。
        """
        if stage_name is None:
            if self.total_elapsed_ms is not None:
                return self.total_elapsed_ms
            return round((time.perf_counter() - self._t0) * 1000, 3)
        record = self.get_stage(stage_name)
        if record is None:
            raise KeyError(f"stage {stage_name!r} not recorded")
        return float(record.get("elapsed_ms", 0.0))

    def finish(self) -> None:
        """标记 trace 结束并计算总耗时（幂等，重复调用无副作用）。"""
        if self.total_elapsed_ms is not None:
            return
        self.total_elapsed_ms = round((time.perf_counter() - self._t0) * 1000, 3)
        self.finished_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """序列化为可直接 ``json.dumps()`` 的字典。"""
        return {
            "trace_id": self.trace_id,
            "trace_type": self.trace_type,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_elapsed_ms": self.total_elapsed_ms,
            "metadata": dict(self.metadata),
            "stages": [dict(s) for s in self.stages],
        }
