"""DenseEncoder：chunk 文本批量稠密向量化（阶段 C8）。

将 ``chunks.text`` 依序送入 ``BaseEmbedding``（由 ``EmbeddingFactory``
按 ``settings.embedding.provider`` 创建，测试可注入 fake），返回与
chunks 等长、等维的向量列表，供 C12 ``VectorUpserter`` 写入向量库。

契约校验（fail-fast）：
- 向量数与 chunk 数一致；
- 每个向量非空且维度一致（与首个向量比对）。

不匹配时抛 ``DenseEncodingError``（上游 provider 契约被破坏，属于
需要暴露的 bug，而非可降级的运行时故障）。
"""

from __future__ import annotations

import time
from typing import Sequence

from core.settings import Settings
from core.trace.trace_context import TraceContext
from core.types import Chunk
from libs.embedding import BaseEmbedding, EmbeddingFactory
from observability.logger import get_logger

__all__ = ["DenseEncoder", "DenseEncodingError"]


class DenseEncodingError(RuntimeError):
    """Embedding 输出不符合契约（数量/维度不匹配）。"""


class DenseEncoder:
    """包装 ``BaseEmbedding`` 的 chunk 稠密编码器。"""

    def __init__(self, settings: Settings, embedding: BaseEmbedding | None = None) -> None:
        self._settings = settings
        self._logger = get_logger("modular-rag.ingestion.dense_encoder")
        self._embedding = embedding if embedding is not None else EmbeddingFactory.create(settings)

    @property
    def dimension(self) -> int | None:
        """底层 embedding 的已知维度（未知返回 None）。"""
        return self._embedding.dimension

    def encode(
        self,
        chunks: Sequence[Chunk],
        *,
        trace: TraceContext | None = None,
    ) -> list[list[float]]:
        """对 chunks 文本批量生成稠密向量（顺序与 chunks 一致）。"""
        if not chunks:
            return []
        started = time.perf_counter()
        texts = [chunk.text for chunk in chunks]
        vectors = self._embedding.embed(texts, trace=trace)
        self._validate(vectors, len(chunks))
        dimension = len(vectors[0])
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        if trace is not None:
            trace.record_stage(
                "embedding",
                method="dense_encoder",
                details={
                    "chunks": len(chunks),
                    "dimension": dimension,
                    "elapsed_ms": elapsed_ms,
                },
            )
        self._logger.info("dense encoded %d chunks (dim=%d, %.1fms)", len(chunks), dimension, elapsed_ms)
        return vectors

    @staticmethod
    def _validate(vectors: list[list[float]], expected: int) -> None:
        if len(vectors) != expected:
            raise DenseEncodingError(
                f"embedding returned {len(vectors)} vectors for {expected} chunks"
            )
        if not vectors or not vectors[0]:
            raise DenseEncodingError("embedding returned empty vector")
        dimension = len(vectors[0])
        for i, vector in enumerate(vectors):
            if len(vector) != dimension:
                raise DenseEncodingError(
                    f"inconsistent dimension at index {i}: "
                    f"expected {dimension}, got {len(vector)}"
                )
