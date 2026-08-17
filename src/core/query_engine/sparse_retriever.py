"""SparseRetriever：BM25 关键词召回（阶段 D3）。

组合 ``BM25Indexer``（倒排索引打分）与 ``BaseVectorStore.get_by_ids``
（按 chunk_id 取回 text/metadata），完成关键词召回：

- **内部流程**（spec D3 约定）::

      keywords → bm25_indexer.query(keywords, top_k) → [{chunk_id, score}]
              → vector_store.get_by_ids(chunk_ids) → [{id, text, metadata}]
              → 合并 score 与 text/metadata → List[RetrievalResult]

- **依赖注入**：``bm25_indexer`` / ``vector_store`` 均可注入（测试 fake）。
  未注入 ``vector_store`` 时经 ``VectorStoreFactory`` 按 ``Settings`` 创建；
  未注入 ``bm25_indexer`` 时创建 ``BM25Indexer(settings)`` 并**尽力加载**
  持久化索引（``data/db/bm25/index.json``，与 C14 Pipeline 同一路径约定；
  文件不存在时从空内存索引起步，查询返回空，不报错）。
- **id 空间对齐**：BM25 倒排表的 ``chunk_id`` 与 C12 向量库写入键共用
  （C4 DocumentChunker 确定性 ID），因此命中 id 可直接到向量库取回
  All-in-One 存储的正文与 metadata；索引中存在但库中已删除的死条目
  跳过（告警日志），不阻塞其余命中。
- **keywords 来源**：``QueryProcessor.process()`` 产出的
  ``ProcessedQuery.keywords``（与 C9 索引分词同一 token 空间，见 D1
  drift guard）。
- **trace 打点**：``sparse_retrieval`` 阶段（method=sparse_retriever，
  details 含 keyword_count/top_k/hits/merged/skipped/elapsed_ms，
  对齐 F3 约定）。
"""

from __future__ import annotations

import time
from typing import Any, Sequence

from core.settings import Settings
from core.trace.trace_context import TraceContext
from core.types import RetrievalResult
from ingestion.storage import BM25Indexer
from libs.vector_store import BaseVectorStore, VectorStoreFactory
from observability.logger import get_logger

__all__ = ["SparseRetriever"]


class SparseRetriever:
    """稀疏关键词召回器：BM25 打分 + 向量库按 id 取回正文。"""

    def __init__(
        self,
        settings: Settings,
        *,
        bm25_indexer: BM25Indexer | None = None,
        vector_store: BaseVectorStore | None = None,
    ) -> None:
        self._settings = settings
        self._logger = get_logger("modular-rag.query_engine.sparse_retriever")
        # 注入优先（同 C8/C12/D2 纪律：不得用真值判断丢弃合法注入实例）。
        self._indexer = bm25_indexer if bm25_indexer is not None else self._default_indexer()
        self._store = (
            vector_store if vector_store is not None else VectorStoreFactory.create(settings)
        )

    @property
    def bm25_indexer(self) -> BM25Indexer:
        """底层 BM25 索引器（只读视图语义）。"""
        return self._indexer

    @property
    def vector_store(self) -> BaseVectorStore:
        """底层向量库（只读视图语义）。"""
        return self._store

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def retrieve(
        self,
        keywords: Sequence[str],
        top_k: int,
        *,
        trace: TraceContext | None = None,
    ) -> list[RetrievalResult]:
        """关键词召回 Top-K（BM25 打分顺序，正文/metadata 经 get_by_ids 合并）。

        Args:
            keywords: 关键词列表（通常为 ``ProcessedQuery.keywords``）；
                空列表直接返回空结果（不触索引/不触库）。
            top_k: 返回结果数量（正整数）。
            trace: 可选追踪上下文，记录 ``sparse_retrieval`` 阶段。

        Returns:
            ``RetrievalResult`` 列表（BM25 score 降序；索引命中但库中
            缺失的死条目被跳过）。

        Raises:
            TypeError: ``keywords`` 非序列或含非 str 项。
            ValueError: ``top_k`` 非正整数。
        """
        started = time.perf_counter()
        self._validate_args(keywords, top_k)
        keyword_list = list(keywords)
        if not keyword_list:
            return []
        hits = self._indexer.query(keyword_list, top_k)
        records: list[dict[str, Any]] = []
        if hits:
            records = self._store.get_by_ids([hit["chunk_id"] for hit in hits])
        by_id = {record["id"]: record for record in records}
        results: list[RetrievalResult] = []
        skipped: list[str] = []
        for hit in hits:
            record = by_id.get(hit["chunk_id"])
            if record is None:
                skipped.append(hit["chunk_id"])
                continue
            results.append(
                RetrievalResult(
                    chunk_id=record["id"],
                    score=float(hit["score"]),
                    text=record.get("text", ""),
                    metadata=dict(record.get("metadata") or {}),
                )
            )
        if skipped:
            self._logger.warning(
                "sparse hits missing from vector store (skipped %d): %s",
                len(skipped), skipped,
            )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        if trace is not None:
            trace.record_stage(
                "sparse_retrieval",
                method="sparse_retriever",
                details={
                    "keyword_count": len(keyword_list),
                    "top_k": top_k,
                    "hits": len(hits),
                    "merged": len(results),
                    "skipped": len(skipped),
                    "elapsed_ms": elapsed_ms,
                },
            )
        self._logger.info(
            "sparse retrieved %d hits for %d keywords (%d skipped, %.1fms)",
            len(results), len(keyword_list), len(skipped), elapsed_ms,
        )
        return results

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _default_indexer(self) -> BM25Indexer:
        """创建默认索引器并尽力加载持久化索引（无文件 → 空索引起步）。"""
        indexer = BM25Indexer(self._settings)
        try:
            indexer.load()
            self._logger.info(
                "loaded persisted bm25 index (%d docs)", len(indexer)
            )
        except FileNotFoundError:
            pass  # 尚无持久化索引：空内存索引，查询返回空。
        return indexer

    @staticmethod
    def _validate_args(keywords: Any, top_k: Any) -> None:
        """调用方契约校验（fail-fast，信息可读）。"""
        if not isinstance(keywords, (list, tuple)):
            raise TypeError(
                f"keywords must be a list of strings, got {type(keywords).__name__}"
            )
        for item in keywords:
            if not isinstance(item, str) or not item:
                raise TypeError(f"keywords must contain non-empty strings, got {item!r}")
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
            raise ValueError(f"top_k must be a positive integer, got {top_k!r}")
