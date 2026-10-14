"""文档与 Chunk 模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from api.schemas.images import ImageInfo

__all__ = ["ChunkModel", "DocumentDetailModel", "DocumentSummary"]


class DocumentSummary(BaseModel):
    source_path: str
    collection: str
    chunk_count: int
    image_count: int = 0
    ingested_at: str | None = None
    doc_hash: str | None = None
    title: str | None = None


class ChunkModel(BaseModel):
    id: str | None = None
    text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentDetailModel(DocumentSummary):
    chunks: list[ChunkModel] = Field(default_factory=list)
    images: list[ImageInfo] = Field(default_factory=list)
