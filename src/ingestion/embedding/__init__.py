"""Ingestion Embedding 模块（阶段 C8+）。

- ``DenseEncoder``：chunks 文本批量稠密向量化（包装 ``BaseEmbedding``）。
- ``SparseEncoder``：BM25 词频统计（``SparseDocStats``，供 C11 索引）。
"""

from ingestion.embedding.dense_encoder import DenseEncoder, DenseEncodingError
from ingestion.embedding.sparse_encoder import SparseDocStats, SparseEncoder, default_tokenize

__all__ = [
    "DenseEncoder",
    "DenseEncodingError",
    "SparseDocStats",
    "SparseEncoder",
    "default_tokenize",
]
