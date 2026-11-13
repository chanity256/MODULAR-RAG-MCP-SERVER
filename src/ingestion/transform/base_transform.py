"""Transform 抽象基类（阶段 C5）。

Transform 是 Ingestion Pipeline 中 Loader/Splitter 之后的"增强"环节
（ChunkRefiner / MetadataEnricher / ImageCaptioner 等）。

设计要点：
- 原子化、幂等：单条 chunk 失败不阻塞整个批次（实现方负责降级）。
- 显式 ``trace`` 参数：阶段 F 打点时记录各阶段数据（当前占位可用）。
- 输入输出类型一致：``List[Chunk] -> List[Chunk]``，可自由组合成流水线。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from core.trace.trace_context import TraceContext
from core.types import Chunk

__all__ = ["BaseTransform"]


class BaseTransform(ABC):
    """Transform 抽象基类，所有增强处理实现需继承并实现 ``transform``。"""

    #: 组件名（供 trace/日志标识，子类可覆盖）。
    name: str = "transform"

    @abstractmethod
    def transform(
        self,
        chunks: Sequence[Chunk],
        trace: TraceContext | None = None,
    ) -> list[Chunk]:
        """对 chunk 列表做增强处理，返回新的 ``Chunk`` 列表。

        Args:
            chunks: 待处理 chunk 序列。
            trace: 可选追踪上下文。

        Returns:
            处理后的 ``Chunk`` 列表（顺序与数量与输入一致，除非显式合并）。
        """
        raise NotImplementedError
