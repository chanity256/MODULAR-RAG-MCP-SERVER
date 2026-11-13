"""DataService：封装 DocumentManager / ImageStorage 读取（阶段 G3）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.settings import Settings
from ingestion.document_manager import (
    CollectionStats,
    DeleteResult,
    DocumentDetail,
    DocumentInfo,
    DocumentManager,
)
from ingestion.storage import ImageStorage

__all__ = ["DataService"]


class DataService:
    """Dashboard 数据浏览 / 管理页的读取门面。"""

    def __init__(
        self,
        manager: DocumentManager | None = None,
        image_storage: ImageStorage | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._manager = manager
        self._images = image_storage
        self._settings = settings

    def _get_manager(self) -> DocumentManager:
        if self._manager is None:
            self._manager = DocumentManager(settings=self._settings)
        return self._manager

    def _get_images(self) -> ImageStorage:
        if self._images is None:
            self._images = ImageStorage()
        return self._images

    def list_documents(self, collection: str | None = None) -> list[DocumentInfo]:
        return self._get_manager().list_documents(collection)

    def get_document_detail(self, doc_id: str, collection: str | None = None) -> DocumentDetail | None:
        return self._get_manager().get_document_detail(doc_id, collection)

    def delete_document(self, source_path: str, collection: str) -> DeleteResult:
        return self._get_manager().delete_document(source_path, collection)

    def get_collection_stats(self, collection: str | None = None) -> list[CollectionStats]:
        return self._get_manager().get_collection_stats(collection)

    def list_images(self, collection: str | None = None, doc_hash: str | None = None) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self._get_images().list_images(collection=collection, doc_hash=doc_hash)]

    def image_bytes(self, image_id: str) -> tuple[bytes, str] | None:
        record = self._get_images().get(image_id)
        if record is None:
            return None
        path = Path(record.file_path)
        if not path.is_file():
            return None
        suffix = path.suffix.lower() or ".png"
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}.get(
            suffix, "image/png"
        )
        return path.read_bytes(), mime
