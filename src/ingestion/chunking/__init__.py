"""Ingestion Chunking 模块（阶段 C4）。

``DocumentChunker``：``libs.splitter`` 与 Ingestion Pipeline 之间的业务适配层。
"""

from ingestion.chunking.document_chunker import DocumentChunker

__all__ = ["DocumentChunker"]
