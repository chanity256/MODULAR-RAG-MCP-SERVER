"""Ingestion Storage 模块（阶段 C11+）。

- ``BM25Indexer``：消费 ``SparseEncoder`` 的 ``SparseDocStats`` 构建
  倒排索引 + IDF，JSON 持久化到 ``data/db/bm25/``，支持增量与重建。
- ``VectorUpserter``：消费 ``DenseEncoder`` 的向量输出，按
  ``ingestion.batch_size`` 分批幂等写入 ``BaseVectorStore``（C12）。
"""

from ingestion.storage.bm25_indexer import (
    BM25Index,
    BM25IndexError,
    BM25Indexer,
    Posting,
    TermEntry,
)
from ingestion.storage.vector_upserter import (
    UpsertSummary,
    VectorUpsertError,
    VectorUpserter,
)

__all__ = [
    "BM25Index",
    "BM25IndexError",
    "BM25Indexer",
    "Posting",
    "TermEntry",
    "UpsertSummary",
    "VectorUpsertError",
    "VectorUpserter",
]
