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
    """已摄入文档的列表项。

    ``doc_hash`` 为单值视图：多代版本并存时取 ``sorted(hashes)[0]``，
    不保证是最新版本；最新哈希以历史表 v2 的 ``file_hash`` 为准。
    """

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
    """单个文档的完整详情（chunk + 图片）。

    ``doc_hash`` 口径同 ``DocumentInfo``（``sorted(hashes)[0]``）；
    ``images`` 汇总全部哈希的图片。
    """

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
            hashes = self._doc_hashes_of(chunks)
            first_meta = (chunks[0].get("metadata") or {}) if chunks else {}
            documents.append(
                DocumentInfo(
                    source_path=source,
                    collection=col,
                    chunk_count=len(chunks),
                    image_count=self._image_count(hashes, chunks),
                    ingested_at=self._ingested_at(history, hashes, source),
                    doc_hash=self._primary_hash(hashes),
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
        hashes = self._doc_hashes_of(chunks)
        history = self._history_index()
        images = [
            record.to_dict()
            for doc_hash in sorted(hashes)
            for record in self._get_images().list_images(doc_hash=doc_hash)
        ]
        first_meta = (chunks[0].get("metadata") or {}) if chunks else {}
        return DocumentDetail(
            source_path=doc_id,
            collection=col,
            chunk_count=len(chunks),
            chunks=chunks,
            images=images,
            ingested_at=self._ingested_at(history, hashes, doc_id),
            doc_hash=self._primary_hash(hashes),
            title=first_meta.get("title") or doc_id,
        )

    def delete_document(self, source_path: str, collection: str) -> DeleteResult:
        """协调删除 Chroma + BM25 + ImageStorage + FileIntegrity。

        遍历该 ``(source_path, collection)`` 在 Chroma 中出现的**全部**
        doc_hash（多代版本并存时逐一清理），各存储删除计数按哈希聚合。
        顺序固定为 BM25 → Chroma → 图片 → 历史表（历史表最后：它是
        「是否存在」的判定基准）；重复执行结果一致（可重入幂等）。

        BM25 / 图片不区分 collection（chunk_id 与图片目录只含 doc_hash），
        因此某哈希仍被其他 ``(source_path, collection)`` 的 Chroma 记录引用时
        跳过该哈希的 BM25 / 图片删除，避免越界删掉其他集合的数据。
        BM25 ``save`` 失败仅告警、不回滚已删除的内存索引。
        """
        self._validate_scope(source_path, collection)
        chunks = self._get_store().get_by_metadata(
            {"source_path": source_path, "collection": collection}
        )
        hashes = self._doc_hashes_of(chunks)
        result = self._purge(source_path, collection, hashes)
        result.integrity_removed = self._remove_history(source_path, collection, hashes)
        self._logger.info(
            "deleted document %s collection=%s hashes=%d chroma=%d bm25=%d images=%d integrity=%s",
            source_path, collection, len(hashes), result.chroma_deleted, result.bm25_deleted,
            result.images_deleted, result.integrity_removed,
        )
        return result

    def prepare_version_replacement(
        self, source_path: str, collection: str, file_hash: str
    ) -> DeleteResult | None:
        """再摄取前清理同 ``(source_path, collection)`` 的旧版本残留（IR2.2）。

        仅当历史表记录的 ``file_hash`` 存在且不同于 ``file_hash`` 时执行：
        清理旧哈希及 Chroma 中该范围内除当前哈希外的全部哈希
        （BM25 → Chroma → 图片，跨集合共享的哈希跳过 BM25 / 图片）。
        历史表记录保留不删，由随后的 ``mark_success`` / ``mark_failed`` 覆盖，
        这样清理中途失败后重跑仍能识别为版本替换。

        Returns:
            执行了清理时返回删除计数；无需替换时返回 None。

        Raises:
            任一存储清理失败时原样抛出（调用方据此中止本次摄取）。
        """
        self._validate_scope(source_path, collection)
        record = self._get_integrity().get_record(source_path, collection)
        old_hash = (record or {}).get("file_hash")
        if not old_hash or old_hash == file_hash:
            return None
        chunks = self._get_store().get_by_metadata(
            {"source_path": source_path, "collection": collection}
        )
        hashes = (self._doc_hashes_of(chunks) | {str(old_hash)}) - {file_hash}
        result = self._purge(source_path, collection, hashes, strict_save=True)
        self._logger.info(
            "replaced old version of %s collection=%s old_hashes=%d chroma=%d bm25=%d images=%d",
            source_path, collection, len(hashes), result.chroma_deleted, result.bm25_deleted,
            result.images_deleted,
        )
        return result

    def rollback_ingestion(
        self,
        source_path: str,
        collection: str,
        doc_hash: str,
        *,
        include_bm25: bool = True,
    ) -> DeleteResult:
        """store 阶段失败后按 ``doc_hash`` 回滚本次写入（IR4.2）。

        与删除/版本替换共用 ``_purge``（BM25 → Chroma → 图片，跨集合共享的
        哈希跳过 BM25 / 图片），使三路存储回到「该文档不存在」的一致状态；
        历史表不动，由调用方随后 ``mark_failed`` 覆盖。重复执行结果一致。

        ``include_bm25=False`` 用于 BM25 内存态未能与磁盘对齐（``load`` 失败）
        的情形：此时 ``save`` 会用不完整的内存索引覆盖磁盘全量索引。

        Raises:
            任一存储清理失败时原样抛出（BM25 ``save`` 失败同样抛出）。
        """
        self._validate_scope(source_path, collection)
        return self._purge(
            source_path, collection, {doc_hash}, strict_save=True, include_bm25=include_bm25
        )

    @staticmethod
    def _validate_scope(source_path: str, collection: str) -> None:
        if not isinstance(source_path, str) or not source_path:
            raise ValueError("source_path must be a non-empty string")
        if not isinstance(collection, str) or not collection:
            raise ValueError("collection must be a non-empty string")

    def _purge(
        self,
        source_path: str,
        collection: str,
        hashes: set[str],
        *,
        strict_save: bool = False,
        include_bm25: bool = True,
    ) -> DeleteResult:
        """按固定顺序清理 BM25 → Chroma → 图片（不碰历史表）。

        ``strict_save=True`` 时 BM25 ``save`` 失败直接抛出：版本替换随后的
        store 会从磁盘重新 ``load`` 索引，吞掉失败会让旧 postings 复活。
        """
        exclusive = {
            doc_hash
            for doc_hash in hashes
            if not self._hash_used_elsewhere(doc_hash, source_path, collection)
        }
        bm25_deleted = 0
        if exclusive and include_bm25:
            bm25 = self._get_bm25()
            for doc_hash in sorted(exclusive):
                bm25_deleted += bm25.remove_document(doc_hash)
            try:
                bm25.save()
            except Exception as exc:  # 持久化失败不回滚已删除内存索引
                if strict_save:
                    raise
                self._logger.warning("bm25 save after delete failed: %s", exc)
        chroma_deleted = self._get_store().delete_by_metadata(
            {"source_path": source_path, "collection": collection}
        )
        images_deleted = 0
        for doc_hash in sorted(exclusive):
            images_deleted += self._get_images().delete_images(doc_hash=doc_hash)
        return DeleteResult(
            source_path=source_path,
            collection=collection,
            chroma_deleted=chroma_deleted,
            bm25_deleted=bm25_deleted,
            images_deleted=images_deleted,
        )

    def _hash_used_elsewhere(self, doc_hash: str, source_path: str, collection: str) -> bool:
        """Chroma 中是否有其他 ``(source_path, collection)`` 的 chunk 携带该哈希。"""
        store = self._get_store()
        for key in ("sha256", "doc_hash"):
            for record in store.get_by_metadata({key: doc_hash}):
                metadata = record.get("metadata") or {}
                if (
                    metadata.get("source_path") != source_path
                    or metadata.get("collection") != collection
                ):
                    return True
        return False

    def _remove_history(self, source_path: str, collection: str, hashes: set[str]) -> bool:
        """删除本范围历史记录，并清掉同路径、同哈希但已无数据的陈旧记录。

        陈旧记录典型来源：v1 迁移进来的 ``default`` 集合记录，或 Chroma 中
        已被其他集合覆盖的记录（chunk_id 不含 collection）。其他集合仍有
        数据的记录保留，以免其下次摄取失去增量判定基准。
        """
        integrity = self._get_integrity()
        removed = bool(integrity.remove_record(source_path=source_path, collection=collection))
        if not hashes:
            return removed
        try:
            records = integrity.list_processed()
        except Exception as exc:
            self._logger.warning("list_processed failed during delete: %s", exc)
            return removed
        store = self._get_store()
        for record in records:
            record_source = record.get("source_path") or record.get("file_path")
            record_hash = record.get("file_hash")
            record_collection = record.get("collection") or collection
            if record_hash not in hashes or record_source not in (source_path, None):
                continue
            if record_source is not None and record_collection != collection and store.get_by_metadata(
                {"source_path": source_path, "collection": record_collection}
            ):
                continue
            if record_source is None:
                ok = integrity.remove_record(record_hash, collection=record_collection)
            else:
                ok = integrity.remove_record(source_path=record_source, collection=record_collection)
            removed = bool(ok) or removed
        return removed

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

    def _image_count(self, hashes: set[str], chunks: list[dict[str, Any]]) -> int:
        if hashes:
            try:
                return sum(int(self._get_images().count(doc_hash=h)) for h in sorted(hashes))
            except Exception:
                pass
        refs: set[str] = set()
        for chunk in chunks:
            metadata = chunk.get("metadata") or {}
            for image_id in metadata.get("image_refs") or []:
                refs.add(str(image_id))
        return len(refs)

    @staticmethod
    def _doc_hashes_of(chunks: list[dict[str, Any]]) -> set[str]:
        """收集 chunks 涉及的全部去重 doc_hash（metadata 两键 + chunk_id 前缀）。"""
        hashes: set[str] = set()
        for chunk in chunks:
            metadata = chunk.get("metadata") or {}
            for key in ("sha256", "doc_hash"):
                value = metadata.get(key)
                if isinstance(value, str) and value:
                    hashes.add(value)
            chunk_id = chunk.get("id") or ""
            if isinstance(chunk_id, str) and "_" in chunk_id:
                prefix = chunk_id.split("_", 1)[0]
                if len(prefix) >= 16:
                    hashes.add(prefix)
        return hashes

    @staticmethod
    def _primary_hash(hashes: set[str]) -> str | None:
        """单值消费点的口径：取 ``sorted(hashes)[0]``（多代并存时不代表最新版本）。"""
        return sorted(hashes)[0] if hashes else None

    @staticmethod
    def _ingested_at(
        history: dict[str, dict[str, Any]], hashes: set[str], source: str
    ) -> str | None:
        for doc_hash in sorted(hashes):
            if doc_hash in history:
                return history[doc_hash].get("processed_at")
        if source in history:
            return history[source].get("processed_at")
        return None
