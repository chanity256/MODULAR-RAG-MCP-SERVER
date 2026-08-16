"""BatchProcessor：编码批处理编排（阶段 C10）。

把 chunks 切成固定大小批次并逐批驱动编码函数（dense/sparse 均适用），
拼接结果时保证与输入顺序一致，同时记录每批耗时（为 trace / 阶段 F
观测预留）。

契约：
- ``split``：``batch_size=2`` 对 5 个元素切为 ``[2, 2, 1]`` 三批；
  顺序稳定、不丢不重；空输入返回 ``[]``。
- ``run``：结果按批次顺序拼接，总长等于输入数；``ingestion.batch_size``
  为默认批大小（yaml 可配），显式参数优先；``< 1`` 抛 ``ValueError``。
- 编码函数抛错时异常原样传播（由上层 Pipeline 决定重试/降级）。
"""

from __future__ import annotations

import time
from typing import Callable, Sequence, TypeVar

from core.settings import Settings
from core.trace.trace_context import TraceContext
from core.types import Chunk
from observability.logger import get_logger

__all__ = ["BatchProcessor"]

T = TypeVar("T")
R = TypeVar("R")

#: 默认批大小（与 ``IngestionConfig.batch_size`` 默认值一致）。
DEFAULT_BATCH_SIZE = 32


class BatchProcessor:
    """通用批处理编排器（顺序稳定 + 每批耗时记录）。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        batch_size: int | None = None,
    ) -> None:
        if batch_size is None:
            batch_size = settings.ingestion.batch_size if settings is not None else DEFAULT_BATCH_SIZE
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError(f"batch_size must be a positive integer, got {batch_size!r}")
        self._batch_size = batch_size
        self._logger = get_logger("modular-rag.ingestion.batch_processor")

    @property
    def batch_size(self) -> int:
        return self._batch_size

    def split(self, items: Sequence[T]) -> list[list[T]]:
        """把 ``items`` 切成 ≤batch_size 的连续批次（保序、不丢不重）。"""
        return [list(items[i:i + self._batch_size]) for i in range(0, len(items), self._batch_size)]

    def run(
        self,
        items: Sequence[T],
        encode_fn: Callable[[list[T]], list[R]],
        *,
        trace: TraceContext | None = None,
        stage: str = "embedding",
    ) -> list[R]:
        """逐批调用 ``encode_fn`` 并按输入顺序拼接结果。"""
        results: list[R] = []
        batch_timings: list[float] = []
        batches = self.split(items)
        for batch in batches:
            started = time.perf_counter()
            batch_result = encode_fn(batch)
            batch_timings.append(round((time.perf_counter() - started) * 1000, 3))
            results.extend(batch_result)
        if len(results) != len(items):
            raise RuntimeError(
                f"encode_fn produced {len(results)} results for {len(items)} items"
            )
        if trace is not None and batches:
            trace.record_stage(
                stage,
                method="batch_processor",
                details={
                    "batches": len(batches),
                    "items": len(items),
                    "batch_size": self._batch_size,
                    "batch_timings_ms": batch_timings,
                },
            )
        self._logger.info(
            "processed %d items in %d batches (batch_size=%d)",
            len(items), len(batches), self._batch_size,
        )
        return results

    # ------------------------------------------------------------------
    # 便捷：直接驱动 Dense/Sparse 编码
    # ------------------------------------------------------------------

    def run_dense(
        self,
        chunks: Sequence[Chunk],
        dense_encoder,
        *,
        trace: TraceContext | None = None,
    ) -> list[list[float]]:
        """按批驱动 ``DenseEncoder.encode`` 并拼接向量。"""
        return self.run(list(chunks), dense_encoder.encode, trace=trace, stage="embedding")

    def run_sparse(
        self,
        chunks: Sequence[Chunk],
        sparse_encoder,
        *,
        trace: TraceContext | None = None,
    ) -> list:
        """按批驱动 ``SparseEncoder.encode_chunks`` 并拼接统计。"""
        return self.run(
            list(chunks), sparse_encoder.encode_chunks, trace=trace, stage="embedding"
        )
