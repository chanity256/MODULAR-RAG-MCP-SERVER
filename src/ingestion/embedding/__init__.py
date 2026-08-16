"""Ingestion Embedding 模块（阶段 C8+）。

- ``DenseEncoder``：chunks 文本批量稠密向量化（包装 ``BaseEmbedding``）。
- ``SparseEncoder``：BM25 词频统计（``SparseDocStats``，供 C11 索引）。
- ``BatchProcessor``：批处理编排（顺序稳定 + 每批耗时记录）。
"""

from ingestion.embedding.batch_processor import BatchProcessor
from ingestion.embedding.dense_encoder import DenseEncoder, DenseEncodingError
from ingestion.embedding.sparse_encoder import SparseDocStats, SparseEncoder, default_tokenize

__all__ = [
    "BatchProcessor",
    "DenseEncoder",
    "DenseEncodingError",
    "SparseDocStats",
    "SparseEncoder",
    "default_tokenize",
]
