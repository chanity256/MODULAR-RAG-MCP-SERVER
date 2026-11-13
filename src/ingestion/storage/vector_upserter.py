"""VectorUpserter：向量库幂等写入（阶段 C12）。

接收 C8 ``DenseEncoder`` 的向量输出（与 chunks 平行、等长、等维的
``list[list[float]]``），组装 ``VectorRecord``（id + vector + text +
完整 metadata），按 ``ingestion.batch_size`` 分批调用
``BaseVectorStore.upsert()`` 写入向量数据库，完成 Dense 路径的持久化。

确定性 chunk_id（幂等性根基，spec 约定 ``hash(source_path + chunk_index
+ content_hash[:8])``）：
- 全链路的稳定 chunk_id 由 C4 ``DocumentChunker`` 统一生成——
  ``{doc_id}_{index:04d}_{content_hash[:8]}``（``doc_id`` 锚定来源文档、
  ``content_hash = sha256(text)[:8]``），即 spec 公式的具体落地；
- ``ChunkRecord.id`` 契约明确"与 ``Chunk.id`` 一致（幂等 upsert 的键）"，
  且 C11 BM25 索引（``SparseDocStats.chunk_id``）与 D3
  ``vector_store.get_by_ids()`` 共用同一 id 空间，因此本类直接以
  ``chunk.id`` 为写入键，不重新派生；
- 由此保证 spec 验收三条：同一 chunk 两次 upsert 产生相同 id（内容
  hash 不变 → id 不变）；内容变更时 id 变更（content_hash 变化）；
  重复写入由后端按 id 覆盖（Chroma 原生幂等），不产生重复记录。

契约校验（fail-fast，同 C8/C11 纪律；违反抛 ``VectorUpsertError``）：
- chunks 与 vectors 等长（空 chunks + 空 vectors 为合法空跑）；
- 向量非空、维度一致、元素为数值（bool 视为非法）；
- chunk_id 为非空字符串；单次调用内重复 chunk_id 视为契约破坏
  （跨调用重复 = 幂等重摄取，由后端覆盖，不算错误）；
- store.upsert 返回非法计数（非 int / 超出 ``[0, 批大小]``）视为
  provider 契约破坏。

store 抛出的异常原样传播（由上层 Pipeline 决定重试/降级，同 C10 纪律）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from core.settings import Settings
from core.trace.trace_context import TraceContext
from core.types import Chunk
from libs.vector_store import BaseVectorStore, VectorRecord, VectorStoreFactory
from observability.logger import get_logger

__all__ = ["VectorUpserter", "VectorUpsertError", "UpsertSummary", "DEFAULT_BATCH_SIZE"]

#: 默认批大小（与 ``IngestionConfig.batch_size`` / ``BatchProcessor`` 一致）。
DEFAULT_BATCH_SIZE = 32


class VectorUpsertError(RuntimeError):
    """upsert 输入或 store 返回不符合契约（需要暴露的 bug，而非可降级故障）。"""


@dataclass
class UpsertSummary:
    """一次 ``upsert`` 调用的结果统计。

    Attributes:
        chunks: 提交的 chunk 记录数。
        batches: 实际发起的 store.upsert 批次数。
        written: store 报告的写入/更新记录总数。
        ids: 与输入顺序一致的 chunk_id 列表（幂等重跑两次应完全相同）。
    """

    chunks: int = 0
    batches: int = 0
    written: int = 0
    ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """可 JSON 序列化 dict。"""
        return {
            "chunks": self.chunks,
            "batches": self.batches,
            "written": self.written,
            "ids": list(self.ids),
        }


class VectorUpserter:
    """Dense 向量批量幂等写入器（chunks + vectors → VectorStore）。

    Args:
        settings: 全局配置；``store`` 未注入时必须提供（工厂按
            ``settings.vector_store.backend`` 创建后端），并提供默认
            ``ingestion.batch_size``。
        store: 注入的 ``BaseVectorStore``（测试用 fake）；缺省经
            ``VectorStoreFactory.create(settings)`` 创建。
        batch_size: 显式批大小，优先于 ``settings.ingestion.batch_size``。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        store: BaseVectorStore | None = None,
        batch_size: int | None = None,
    ) -> None:
        if batch_size is None:
            batch_size = settings.ingestion.batch_size if settings is not None else DEFAULT_BATCH_SIZE
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError(f"batch_size must be a positive integer, got {batch_size!r}")
        if store is None:
            if settings is None:
                raise ValueError("settings is required when store is not injected")
            store = VectorStoreFactory.create(settings)
        self._settings = settings
        self._batch_size = batch_size
        self._store = store
        self._logger = get_logger("modular-rag.ingestion.vector_upserter")

    # ------------------------------------------------------------------
    # 只读视图
    # ------------------------------------------------------------------

    @property
    def store(self) -> BaseVectorStore:
        """底层向量库（只读视图语义，外部勿替换）。"""
        return self._store

    @property
    def batch_size(self) -> int:
        return self._batch_size

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def upsert(
        self,
        chunks: Sequence[Chunk],
        vectors: Sequence[list[float]],
        *,
        trace: TraceContext | None = None,
    ) -> UpsertSummary:
        """把 chunks + 向量分批幂等写入向量库（顺序与输入一致）。

        Args:
            chunks: C4 产出的 chunk 序列（``chunk.id`` 为幂等键）。
            vectors: C8 DenseEncoder 输出，与 chunks 平行等长等维。
            trace: 可选追踪上下文（storage 阶段打点；空跑不打点）。

        Returns:
            ``UpsertSummary``（ids 与输入顺序一致）。

        Raises:
            VectorUpsertError: 输入或 store 返回违反契约时。
        """
        started = time.perf_counter()
        if isinstance(chunks, Chunk):
            raise VectorUpsertError("expected a sequence of chunks, got a single Chunk instance")
        chunk_list = list(chunks)
        vector_list = list(vectors)
        if len(chunk_list) != len(vector_list):
            raise VectorUpsertError(
                f"got {len(vector_list)} vectors for {len(chunk_list)} chunks"
            )
        if not chunk_list:
            return UpsertSummary()  # 空跑：不触库、不打点
        records = self._build_records(chunk_list, vector_list)
        batches = [records[i:i + self._batch_size] for i in range(0, len(records), self._batch_size)]
        written = 0
        for batch in batches:
            written += self._upsert_batch(batch, trace=trace)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        if trace is not None:
            trace.record_stage(
                "storage",
                method="vector_upserter",
                details={
                    "chunks": len(records),
                    "batches": len(batches),
                    "batch_size": self._batch_size,
                    "written": written,
                    "elapsed_ms": elapsed_ms,
                },
            )
        self._logger.info(
            "vector upsert: %d chunks in %d batches (batch_size=%d, written=%d, %.1fms)",
            len(records), len(batches), self._batch_size, written, elapsed_ms,
        )
        return UpsertSummary(
            chunks=len(records),
            batches=len(batches),
            written=written,
            ids=[record.id for record in records],
        )

    # ------------------------------------------------------------------
    # 内部：记录组装与校验
    # ------------------------------------------------------------------

    def _build_records(
        self,
        chunks: list[Chunk],
        vectors: list[list[float]],
    ) -> list[VectorRecord]:
        """校验输入并组装 ``VectorRecord``（metadata 完整浅拷贝）。"""
        records: list[VectorRecord] = []
        seen: set[str] = set()
        dimension = 0  # 以首个合法向量的维度为基准（惰性确定）
        for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
            chunk_id = getattr(chunk, "id", None)
            if not isinstance(chunk_id, str) or not chunk_id:
                raise VectorUpsertError(f"chunk_id must be a non-empty string, got {chunk_id!r} (index {i})")
            if chunk_id in seen:
                raise VectorUpsertError(f"duplicate chunk_id in input: {chunk_id!r}")
            seen.add(chunk_id)
            self._validate_vector(vector, dimension, chunk_id, i)
            if dimension == 0:
                dimension = len(vector)
            metadata = getattr(chunk, "metadata", None)
            if not isinstance(metadata, dict):
                raise VectorUpsertError(
                    f"chunk metadata must be a dict, got {type(metadata).__name__} (chunk {chunk_id!r})"
                )
            text = getattr(chunk, "text", "")
            records.append(
                VectorRecord(
                    id=chunk_id,
                    vector=list(vector),
                    text=text if isinstance(text, str) else "",
                    metadata=dict(metadata),  # 完整 metadata，浅拷贝防 aliasing
                )
            )
        return records

    @staticmethod
    def _validate_vector(vector: Any, dimension: int, chunk_id: str, index: int) -> None:
        """校验单个向量：list、非空、元素为数值、维度一致。"""
        if not isinstance(vector, (list, tuple)):
            raise VectorUpsertError(
                f"vector must be a list, got {type(vector).__name__} (chunk {chunk_id!r})"
            )
        if not vector:
            raise VectorUpsertError(f"empty vector (chunk {chunk_id!r}, index {index})")
        for component in vector:
            if isinstance(component, bool) or not isinstance(component, (int, float)):
                raise VectorUpsertError(
                    f"vector components must be numbers, got {component!r} (chunk {chunk_id!r}, index {index})"
                )
        if dimension and len(vector) != dimension:
            raise VectorUpsertError(
                f"inconsistent dimension at index {index}: expected {dimension}, "
                f"got {len(vector)} (chunk {chunk_id!r})"
            )

    def _upsert_batch(self, batch: list[VectorRecord], *, trace: TraceContext | None) -> int:
        """单批写入并校验 store 返回计数；store 异常原样传播。"""
        count = self._store.upsert(batch, trace=trace)
        if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= len(batch):
            raise VectorUpsertError(
                f"vector store upsert returned invalid count {count!r} for {len(batch)} records"
            )
        return count
