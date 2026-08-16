"""Ingestion Storage 模块（阶段 C11+）。

- ``BM25Indexer``：消费 ``SparseEncoder`` 的 ``SparseDocStats`` 构建
  倒排索引 + IDF，JSON 持久化到 ``data/db/bm25/``，支持增量与重建。
"""

from ingestion.storage.bm25_indexer import (
    BM25Index,
    BM25IndexError,
    BM25Indexer,
    Posting,
    TermEntry,
)

__all__ = ["BM25Index", "BM25IndexError", "BM25Indexer", "Posting", "TermEntry"]
