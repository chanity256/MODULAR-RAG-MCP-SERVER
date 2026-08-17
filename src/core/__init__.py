"""Core 层：核心业务逻辑（数据契约、配置、查询引擎、响应构建、追踪）。

统一 re-export 核心数据类型（阶段 C1/D1/D2），简化导入路径：
``from core import Document, Chunk, ChunkRecord, ProcessedQuery, RetrievalResult``。
"""

from core.types import Chunk, ChunkRecord, Document, ImageRef, ProcessedQuery, RetrievalResult

__all__ = ["Document", "Chunk", "ChunkRecord", "ImageRef", "ProcessedQuery", "RetrievalResult"]