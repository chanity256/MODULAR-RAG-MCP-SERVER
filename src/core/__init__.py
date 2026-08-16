"""Core 层：核心业务逻辑（数据契约、配置、查询引擎、响应构建、追踪）。

统一 re-export 核心数据类型（阶段 C1），简化导入路径：
``from core import Document, Chunk, ChunkRecord``。
"""

from core.types import Chunk, ChunkRecord, Document, ImageRef

__all__ = ["Document", "Chunk", "ChunkRecord", "ImageRef"]