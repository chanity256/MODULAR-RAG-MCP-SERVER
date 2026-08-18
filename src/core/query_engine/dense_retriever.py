"""DenseRetriever：稠密语义召回（阶段 D2）。

组合 ``BaseEmbedding``（query 向量化）与 ``BaseVectorStore``（向量检索），
完成语义召回：

- **依赖注入**（测试/替换用）：``embedding_client`` / ``vector_store``
  均可注入 fake；未注入时经 ``EmbeddingFactory`` / ``VectorStoreFactory``
  按 ``Settings`` 创建（导入 ``libs.embedding`` / ``libs.vector_store``
  包即触发 provider 注册）。
- **内部流程**（spec D2 约定）::

      query → embedding.embed([query]) → vector_store.query(vector, top_k, filters)
            → QueryResult → RetrievalResult(chunk_id/score/text/metadata)

- **fail-fast 契约校验**（违反抛 ``DenseRetrievalError``）：
  embedding 对单条 query 必须返回恰好 1 个非空向量——上游 provider
  契约被破坏属于需要暴露的 bug，而非可降级故障。
- **透传语义**：``filters`` 原样透传给 ``VectorStore.query``（Chroma
  ``where`` 子句 Pre-filter，如 ``{"collection": "x"}``）；store 抛出的
  异常原样传播（由 D5 HybridSearch 决定降级到 Sparse 单路）。
- **trace 打点**：``dense_retrieval`` 阶段（method=dense_retriever，
  details 含 top_k/filter_keys/hits/elapsed_ms，对齐 F3 约定）。
"""

from __future__ import annotations

import time
from typing import Any

from core.settings import Settings
from core.trace.trace_context import TraceContext
from core.types import RetrievalResult
from libs.embedding import BaseEmbedding, EmbeddingFactory
from libs.vector_store import BaseVectorStore, VectorStoreFactory
from observability.logger import get_logger

__all__ = ["DenseRetriever", "DenseRetrievalError"]


class DenseRetrievalError(RuntimeError):
    """Dense 召回链路契约被破坏（embedding 单条输出不合法）。"""


class DenseRetriever:
    """稠密语义召回器：query embedding + VectorStore 向量检索。

    Args:
        settings: 全局配置（驱动未注入组件的默认构造）。
        embedding_client: 注入的 ``BaseEmbedding``（测试 fake）；
            缺省经 ``EmbeddingFactory.create(settings)`` 创建。
        vector_store: 注入的 ``BaseVectorStore``（测试 fake）；
            缺省经 ``VectorStoreFactory.create(settings)`` 创建。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        embedding_client: BaseEmbedding | None = None,
        vector_store: BaseVectorStore | None = None,
    ) -> None:
        self._settings = settings
        self._logger = get_logger("modular-rag.query_engine.dense_retriever")
        # 注入优先（同 C8/C12 纪律：不得用真值判断丢弃合法注入实例）。
        self._embedding = (
            embedding_client if embedding_client is not None else EmbeddingFactory.create(settings)
        )
        self._store = (
            vector_store if vector_store is not None else VectorStoreFactory.create(settings)
        )

    @property
    def embedding_client(self) -> BaseEmbedding:
        """底层 embedding 客户端（只读视图语义）。"""
        return self._embedding

    @property
    def vector_store(self) -> BaseVectorStore:
        """底层向量库（只读视图语义）。"""
        return self._store

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        top_k: int,
        filters: dict[str, Any] | None = None,
        *,
        trace: TraceContext | None = None,
    ) -> list[RetrievalResult]:
        """语义召回 Top-K。

        Args:
            query: 原始查询文本（原样生成 embedding，见 D1 ProcessedQuery）。
            top_k: 返回结果数量（正整数）。
            filters: 可选 metadata 过滤条件（透传 store Pre-filter）。
            trace: 可选追踪上下文，记录 ``dense_retrieval`` 阶段。

        Returns:
            ``RetrievalResult`` 列表（顺序由 store 返回顺序决定，
            Chroma 按 score 降序）。

        Raises:
            TypeError: ``query`` 非 str / ``filters`` 非 dict 或 None。
            ValueError: ``query`` 为空/纯空白、``top_k`` 非正整数。
            DenseRetrievalError: embedding 单条输出不符合契约。
        """
        started = time.perf_counter()
        self._validate_args(query, top_k, filters)
        vectors = self._embedding.embed([query], trace=trace)
        if len(vectors) != 1:
            raise DenseRetrievalError(
                f"embedding returned {len(vectors)} vectors for a single query"
            )
        vector = vectors[0]
        if not vector:
            raise DenseRetrievalError("embedding returned an empty vector for the query")
        hits = self._store.query(vector, top_k, filters=filters, trace=trace)
        results = [
            RetrievalResult(chunk_id=hit.id, score=float(hit.score), text=hit.text, metadata=dict(hit.metadata))
            for hit in hits
        ]
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        if trace is not None:
            trace.record_stage(
                "dense_retrieval",
                method="dense_retriever",
                details={
                    "top_k": top_k,
                    "filter_keys": sorted(filters) if filters else [],
                    "hits": len(results),
                    "elapsed_ms": elapsed_ms,
                    "preview": _preview_hits(results),
                },
            )
        self._logger.info(
            "dense retrieved %d/%d hits for query (%.1fms)",
            len(results), top_k, elapsed_ms,
        )
        return results

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_args(query: Any, top_k: Any, filters: Any) -> None:
        """调用方契约校验（fail-fast，信息可读）。"""
        if not isinstance(query, str):
            raise TypeError(f"query must be a string, got {type(query).__name__}")
        if not query.strip():
            raise ValueError("query must be a non-empty, non-whitespace string")
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
            raise ValueError(f"top_k must be a positive integer, got {top_k!r}")
        if filters is not None and not isinstance(filters, dict):
            raise TypeError(f"filters must be a dict or None, got {type(filters).__name__}")


def _preview_hits(results: list[RetrievalResult], limit: int = 8) -> list[dict[str, Any]]:
    """Dashboard Query 追踪页用的轻量预览（不改变既有计数契约）。"""
    return [
        {"chunk_id": item.chunk_id, "score": float(item.score)}
        for item in results[:limit]
    ]
