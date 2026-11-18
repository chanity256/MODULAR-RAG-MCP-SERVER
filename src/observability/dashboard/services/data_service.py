"""DataService：封装 DocumentManager / ImageStorage 读取（阶段 G3）。

AR2（AGENTIC_UPLOAD_ROOTS_SPEC）：``delete_document`` 在四存储删除完成后
联动清理上传目录内的源文件（无其他 collection 引用时），响应经
``DeleteResult.source_file_removed`` 如实带出。
"""

from __future__ import annotations

import logging
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
from observability.dashboard.services.ingestion_service import DEFAULT_UPLOAD_DIR

__all__ = ["DataService"]

logger = logging.getLogger(__name__)


class DataService:
    """Dashboard 数据浏览 / 管理页的读取门面。"""

    def __init__(
        self,
        manager: DocumentManager | None = None,
        image_storage: ImageStorage | None = None,
        *,
        settings: Settings | None = None,
        upload_dir: str | Path | None = None,
    ) -> None:
        self._manager = manager
        self._images = image_storage
        self._settings = settings
        self._upload_dir = Path(upload_dir) if upload_dir is not None else DEFAULT_UPLOAD_DIR

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
        result = self._get_manager().delete_document(source_path, collection)
        result.source_file_removed = self._remove_upload_source_file(result)
        return result

    def _remove_upload_source_file(self, result: DeleteResult) -> bool:
        """AR2：四存储删除完成后联动清理上传目录内的源文件。

        三重门禁（任一不满足则保留文件，退化为既有行为）：

        1. 四存储**有实际删除**（无删除时调用方按 404 拒绝，不动文件）；
        2. 同 ``source_path`` 在其他 collection 已无 Chroma 记录；
        3. resolved 路径确实落在上传目录之内（CLI 摄取的
           ``data/documents`` 等文件不动）。

        文件删除失败只记 warning 并返回 False（响应如实带出），不回滚
        已完成的四存储删除（spec §3）。
        """
        removed_any = (
            result.chroma_deleted or result.bm25_deleted or result.images_deleted or result.integrity_removed
        )
        if not removed_any:
            return False
        try:
            if self._get_manager().source_referenced(result.source_path):
                return False
        except Exception:  # 引用检查失败 → 保守保留文件
            logger.warning(
                "delete_document: reference check failed for %s; keeping source file",
                result.source_path,
                exc_info=True,
            )
            return False
        resolved = Path(result.source_path).expanduser().resolve()
        upload_root = self._upload_dir.resolve()
        if resolved != upload_root and upload_root not in resolved.parents:
            return False
        if not resolved.is_file():
            return False
        try:
            resolved.unlink()
        except OSError as exc:
            logger.warning("delete_document: failed to remove source file %s: %s", resolved, exc)
            return False
        logger.info("delete_document: removed upload source file %s", resolved)
        return True

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
