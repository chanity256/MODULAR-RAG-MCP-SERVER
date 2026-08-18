"""DocumentManager：跨存储的文档生命周期管理（阶段 G2）。

协调 4 个存储后端完成 list / detail / delete / stats：
- VectorStore（Chroma）：chunk 向量 + 正文 + metadata（权威账本）
- BM25Indexer：稀疏倒排（按 ``doc_hash`` / chunk_id 前缀移除）
- ImageStorage：图片文件 + SQLite 索引（按 ``doc_hash`` 删除）
- FileIntegrity：摄取历史（删除后允许重新摄入）

``doc_id`` 语义与 E5 ``get_document_summary`` 一致：文档的 ``source_path``。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from core.settings import Settings
from ingestion.storage import BM25Indexer, ImageStorage
from libs.loader.file_integrity import FileIntegrityChecker, SQLiteIntegrityChecker
from libs.vector_store import BaseVectorStore, VectorStoreFactory
from observability.logger import get_logger

__all__ = [
    "DocumentInfo",
    "DocumentDetail",
    "DeleteResult",
    "CollectionStats",
    "DocumentManager",
]


@dataclass
class DocumentInfo:
    """已摄入文档的列表项。"""

    source_path: str
    collection: str
    chunk_count: int
    image_count: int = 0
    ingested_at: str | None = None
    doc_hash: str | None = None
    title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "collection": self.collection,
            "chunk_count": self.chunk_count,
            "image_count": self.image_count,
            "ingested_at": self.ingested_at,
            "doc_hash": self.doc_hash,
            "title": self.title,
        }


@dataclass
class DocumentDetail:
    """单个文档的完整详情（chunk + 图片）。"""

    source_path: str
    collection: str
    chunk_count: int
    chunks: list[dict[str, Any]] = field(default_factory=list)
    images: list[dict[str, Any]] = field(default_factory=list)
    ingested_at: str | None = None
    doc_hash: str | None = None
    title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "collection": self.collection,
            "chunk_count": self.chunk_count,
            "chunks": list(self.chunks),
            "images": list(self.images),
            "ingested_at": self.ingested_at,
            "doc_hash": self.doc_hash,
            "title": self.title,
        }


@dataclass
class DeleteResult:
    """跨存储删除的计数汇总。"""

    source_path: str
    collection: str
    chroma_deleted: int = 0
    bm25_deleted: int = 0
    images_deleted: int = 0
    integrity_removed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "collection": self.collection,
            "chroma_deleted": self.chroma_deleted,
            "bm25_deleted": self.bm25_deleted,
            "images_deleted": self.images_deleted,
            "integrity_removed": self.integrity_removed,
        }


@dataclass
class CollectionStats:
    """集合级统计。"""

    name: str
    document_count: int = 0
    chunk_count: int = 0
    image_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "document_count": self.document_count,
            "chunk_count": self.chunk_count,
            "image_count": self.image_count,
        }


class DocumentManager:
    """跨 4 个存储的文档生命周期协调器。"""

    def __init__(
        self,
        chroma_store: BaseVectorStore | None = None,
        bm25_indexer: BM25Indexer | None = None,
        image_storage: ImageStorage | None = None,
        file_integrity: FileIntegrityChecker | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings
        self._store = chroma_store
        self._bm25 = bm25_indexer
        self._images = image_storage
        self._integrity = file_integrity
        self._logger = get_logger("modular-rag.ingestion.document_manager")

    def _get_store(self) -> BaseVectorStore:
        if self._store is None:
            if self._settings is None:
                from core.settings import load_settings

                self._settings = load_settings()
            self._store = VectorStoreFactory.create(self._settings)
        return self._store

    def _get_bm25(self) -> BM25Indexer:
        if self._bm25 is None:
            indexer = BM25Indexer(self._settings)
            try:
                indexer.load()
            except FileNotFoundError:
                pass
            self._bm25 = indexer
        return self._bm25

    def _get_images(self) -> ImageStorage:
        if self._images is None:
            self._images = ImageStorage()
        return self._images

    def _get_integrity(self) -> FileIntegrityChecker:
        if self._integrity is None:
            self._integrity = SQLiteIntegrityChecker()
        return self._integrity

    def list_documents(self, collection: str | None = None) -> list[DocumentInfo]:
        """列出已摄入文档（按 source_path + collection 聚合）。"""
        filters = {"collection": collection} if collection else None
        records = self._get_store().get_by_metadata(filters)
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            metadata = record.get("metadata") or {}
            source = metadata.get("source_path") or metadata.get("source")
            if not source:
                continue
            col = metadata.get("collection") or collection or "default"
            grouped[(str(source), str(col))].append(record)

        history = self._history_index()
        documents: list[DocumentInfo] = []
        for (source, col), chunks in grouped.items():
            doc_hash = self._doc_hash_of(chunks)
            ingested_at = None
            if doc_hash and doc_hash in history:
                ingested_at = history[doc_hash].get("processed_at")
            elif source in history:
                ingested_at = history[source].get("processed_at")
            first_meta = (chunks[0].get("metadata") or {}) if chunks else {}
            documents.append(
                DocumentInfo(
                    source_path=source,
                    collection=col,
                    chunk_count=len(chunks),
                    image_count=self._image_count(doc_hash, chunks),
                    ingested_at=ingested_at,
                    doc_hash=doc_hash,
                    title=first_meta.get("title") or source,
                )
            )
        documents.sort(key=lambda item: (item.collection, item.source_path))
        return documents

    def get_document_detail(self, doc_id: str, collection: str | None = None) -> DocumentDetail | None:
        """按 ``source_path``（doc_id）取回文档详情；不存在返回 None。"""
        if not isinstance(doc_id, str) or not doc_id:
            raise ValueError("doc_id must be a non-empty string")
        filters: dict[str, Any] = {"source_path": doc_id}
        if collection:
            filters["collection"] = collection
        chunks = self._get_store().get_by_metadata(filters)
        if not chunks:
            return None
        collections = {
            (chunk.get("metadata") or {}).get("collection") or collection or "default"
            for chunk in chunks
        }
        col = collection or next(iter(sorted(collections)))
        if collection is None and len(collections) > 1:
            chunks = [
                chunk
                for chunk in chunks
                if (chunk.get("metadata") or {}).get("collection") == col
            ]
        doc_hash = self._doc_hash_of(chunks)
        history = self._history_index()
        ingested_at = None
        if doc_hash and doc_hash in history:
            ingested_at = history[doc_hash].get("processed_at")
        elif doc_id in history:
            ingested_at = history[doc_id].get("processed_at")
        images = []
        if doc_hash:
            images = [record.to_dict() for record in self._get_images().list_images(doc_hash=doc_hash)]
        first_meta = (chunks[0].get("metadata") or {}) if chunks else {}
        return DocumentDetail(
            source_path=doc_id,
            collection=col,
            chunk_count=len(chunks),
            chunks=chunks,
            images=images,
            ingested_at=ingested_at,
            doc_hash=doc_hash,
            title=first_meta.get("title") or doc_id,
        )

    def delete_document(self, source_path: str, collection: str) -> DeleteResult:
        """协调删除 Chroma + BM25 + ImageStorage + FileIntegrity。"""
        if not isinstance(source_path, str) or not source_path:
            raise ValueError("source_path must be a non-empty string")
        if not isinstance(collection, str) or not collection:
            raise ValueError("collection must be a non-empty string")
        chunks = self._get_store().get_by_metadata(
            {"source_path": source_path, "collection": collection}
        )
        doc_hash = self._doc_hash_of(chunks)
        chroma_deleted = self._get_store().delete_by_metadata(
            {"source_path": source_path, "collection": collection}
        )
        bm25_deleted = 0
        if doc_hash:
            bm25_deleted = self._get_bm25().remove_document(doc_hash)
            try:
                self._get_bm25().save()
            except Exception as exc:  # 持久化失败不回滚已删除内存索引
                self._logger.warning("bm25 save after delete failed: %s", exc)
        images_deleted = 0
        if doc_hash:
            images_deleted = self._get_images().delete_images(doc_hash=doc_hash)
        integrity_removed = False
        if doc_hash:
            integrity_removed = bool(self._get_integrity().remove_record(doc_hash))
        self._logger.info(
            "deleted document %s collection=%s chroma=%d bm25=%d images=%d integrity=%s",
            source_path, collection, chroma_deleted, bm25_deleted, images_deleted, integrity_removed,
        )
        return DeleteResult(
            source_path=source_path,
            collection=collection,
            chroma_deleted=chroma_deleted,
            bm25_deleted=bm25_deleted,
            images_deleted=images_deleted,
            integrity_removed=integrity_removed,
        )

    def get_collection_stats(self, collection: str | None = None) -> list[CollectionStats]:
        """返回集合级统计；``collection`` 给定时只返回该集合。"""
        documents = self.list_documents(collection)
        buckets: dict[str, CollectionStats] = {}
        for document in documents:
            bucket = buckets.setdefault(
                document.collection,
                CollectionStats(name=document.collection),
            )
            bucket.document_count += 1
            bucket.chunk_count += document.chunk_count
            bucket.image_count += document.image_count
        if collection is not None:
            return [buckets.get(collection, CollectionStats(name=collection))]
        return [buckets[name] for name in sorted(buckets)]

    def _history_index(self) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        try:
            records = self._get_integrity().list_processed()
        except Exception as exc:
            self._logger.warning("list_processed failed: %s", exc)
            return index
        for record in records:
            file_hash = record.get("file_hash")
            file_path = record.get("file_path")
            if file_hash:
                index[str(file_hash)] = record
            if file_path:
                index[str(file_path)] = record
        return index

    def _image_count(self, doc_hash: str | None, chunks: list[dict[str, Any]]) -> int:
        if doc_hash:
            try:
                return int(self._get_images().count(doc_hash=doc_hash))
            except Exception:
                pass
        refs: set[str] = set()
        for chunk in chunks:
            metadata = chunk.get("metadata") or {}
            for image_id in metadata.get("image_refs") or []:
                refs.add(str(image_id))
        return len(refs)

    @staticmethod
    def _doc_hash_of(chunks: list[dict[str, Any]]) -> str | None:
        for chunk in chunks:
            metadata = chunk.get("metadata") or {}
            for key in ("sha256", "doc_hash"):
                value = metadata.get(key)
                if isinstance(value, str) and value:
                    return value
            chunk_id = chunk.get("id") or ""
            if isinstance(chunk_id, str) and "_" in chunk_id:
                prefix = chunk_id.split("_", 1)[0]
                if len(prefix) >= 16:
                    return prefix
        return None
