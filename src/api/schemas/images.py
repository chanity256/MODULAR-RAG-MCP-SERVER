"""图片索引模型（不暴露服务端 file_path）。"""

from __future__ import annotations

from pydantic import BaseModel

__all__ = ["ImageInfo"]


class ImageInfo(BaseModel):
    image_id: str
    collection: str | None = None
    doc_hash: str | None = None
    page_num: int | None = None
    created_at: str | None = None
