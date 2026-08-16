"""TraceContext 最小实现（阶段 C5 占位，阶段 F1 增强）。

为 Ingestion / Query 链路提供统一的追踪上下文载体：
- ``trace_id``：uuid4 生成的请求级唯一标识。
- ``record_stage(name, **data)``：各阶段组件显式调用，追加阶段数据。
- ``to_dict()``：可 JSON 序列化的字典。

阶段 F1 将在此基础上补充 ``trace_type`` / ``finish()`` / 耗时统计等能力。
"""

from __future__ import annotations

import uuid
from typing import Any

__all__ = ["TraceContext"]


class TraceContext:
    """请求级追踪上下文（显式调用模式，低侵入）。"""

    def __init__(self, trace_id: str | None = None, metadata: dict[str, Any] | None = None) -> None:
        self.trace_id = trace_id or uuid.uuid4().hex
        self.metadata: dict[str, Any] = dict(metadata or {})
        self.stages: list[dict[str, Any]] = []

    def record_stage(self, stage: str, **data: Any) -> None:
        """记录一个处理阶段（后写入的排在列表尾部）。"""
        self.stages.append({"stage": stage, **data})

    def get_stage(self, stage: str) -> dict[str, Any] | None:
        """返回最近一次同名阶段记录（无则 None）。"""
        for record in reversed(self.stages):
            if record.get("stage") == stage:
                return record
        return None

    def to_dict(self) -> dict[str, Any]:
        """序列化为可 JSON 输出的字典。"""
        return {
            "trace_id": self.trace_id,
            "metadata": dict(self.metadata),
            "stages": [dict(s) for s in self.stages],
        }
