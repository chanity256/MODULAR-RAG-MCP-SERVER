"""Ingestion Storage 模块（阶段 C11+）。

- ``BM25Indexer``：消费 ``SparseEncoder`` 的 ``SparseDocStats`` 构建
  倒排索引 + IDF，JSON 持久化到 ``data/db/bm25/``，支持增量与重建。
- ``VectorUpserter``：消费 ``DenseEncoder`` 的向量输出，按
  ``ingestion.batch_size`` 分批幂等写入 ``BaseVectorStore``（C12）。
- ``ImageStorage``：图片字节落盘 ``data/images/{collection}/``（缺省
  ``{doc_hash}/``)，SQLite 索引 ``data/db/image_index.db`` 维护
  ``image_id → file_path`` 映射，支持按 collection/doc_hash 查询删除（C13）。
"""

from ingestion.storage.bm25_indexer import (
    BM25Index,
    BM25IndexError,
    BM25Indexer,
    Posting,
    TermEntry,
)
from ingestion.storage.image_storage import (
    ImageRecord,
    ImageStorage,
    ImageStorageError,
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
    "ImageRecord",
    "ImageStorage",
    "ImageStorageError",
    "UpsertSummary",
    "VectorUpsertError",
    "VectorUpserter",
]
