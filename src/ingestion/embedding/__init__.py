"""Ingestion Embedding 模块（阶段 C8+）。

- ``DenseEncoder``：chunks 文本批量稠密向量化（包装 ``BaseEmbedding``）。
"""

from ingestion.embedding.dense_encoder import DenseEncoder, DenseEncodingError

__all__ = ["DenseEncoder", "DenseEncodingError"]
