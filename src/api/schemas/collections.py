"""Collection 统计模型。"""

from __future__ import annotations

from pydantic import BaseModel

__all__ = ["CollectionStatsModel", "CollectionsResponse"]


class CollectionStatsModel(BaseModel):
    name: str
    document_count: int = 0
    chunk_count: int = 0
    image_count: int = 0


class CollectionsResponse(BaseModel):
    items: list[CollectionStatsModel]
