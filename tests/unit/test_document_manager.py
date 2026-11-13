"""DocumentManager 跨存储协调测试（阶段 G2）。

纯离线：内存向量库 + 真 BM25Indexer / ImageStorage / SQLiteIntegrityChecker
全部落 ``tmp_path``。覆盖 list / detail / delete / stats 与删除后不可再 list。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ingestion.document_manager import (
    CollectionStats,
    DeleteResult,
    DocumentDetail,
    DocumentInfo,
    DocumentManager,
)
from ingestion.embedding import SparseDocStats
from ingestion.storage import BM25Indexer, ImageStorage
from libs.loader.file_integrity import SQLiteIntegrityChecker
from libs.vector_store.base_vector_store import BaseVectorStore, QueryResult, VectorRecord

_DOC_A = "aa" * 32
_DOC_B = "bb" * 32
_PNG = b"\x89PNG\r\n\x1a\nfake-image-payload"


class FakeStore(BaseVectorStore):
    def __init__(self) -> None:
        self._store: dict[str, VectorRecord] = {}

    def upsert(self, records, *, trace=None, **kwargs):
        for rec in records:
            self._store[rec.id] = rec
        return len(list(records))

    def query(self, vector, top_k=10, *, filters=None, trace=None, **kwargs):
        return [
            QueryResult(id=rec.id, score=1.0, text=rec.text, metadata=dict(rec.metadata))
            for rec in list(self._store.values())[:top_k]
        ]

    def get_by_ids(self, ids, *, trace=None, **kwargs):
        out = []
        for rid in dict.fromkeys(ids):
            rec = self._store.get(rid)
            if rec is not None:
                out.append({"id": rec.id, "text": rec.text, "metadata": dict(rec.metadata)})
        return out

    def delete_by_metadata(self, filters, *, trace=None, **kwargs):
        if not isinstance(filters, dict) or not filters:
            raise ValueError("filters must be a non-empty dict (refuse to wipe all)")
        deleted = 0
        for rid, rec in list(self._store.items()):
            if all(rec.metadata.get(k) == v for k, v in filters.items()):
                del self._store[rid]
                deleted += 1
        return deleted

    def get_by_metadata(self, filters=None, *, trace=None, **kwargs):
        records = []
        for rec in self._store.values():
            if filters and not all(rec.metadata.get(k) == v for k, v in filters.items()):
                continue
            records.append({"id": rec.id, "text": rec.text, "metadata": dict(rec.metadata)})
        records.sort(key=lambda item: item["id"])
        return records

    def get_collection_stats(self, *, trace=None, **kwargs):
        records = self.get_by_metadata()
        sources = {item["metadata"].get("source_path") for item in records if item["metadata"].get("source_path")}
        return {"name": "fake", "chunk_count": len(records), "document_count": len(sources), "collections": []}


def _chunk(chunk_id: str, source: str, collection: str, sha256: str, title: str, text: str = "body") -> VectorRecord:
    return VectorRecord(
        id=chunk_id,
        vector=[1.0, 0.0],
        text=text,
        metadata={
            "source_path": source,
            "collection": collection,
            "sha256": sha256,
            "title": title,
            "image_refs": [f"{sha256}_0_0"] if sha256 == _DOC_A else [],
        },
    )


@pytest.fixture
def stack(tmp_path: Path):
    store = FakeStore()
    store.upsert([
        _chunk(f"{_DOC_A}_0000_deadbeef", "docs/a.pdf", "alpha", _DOC_A, "Doc A", "alpha one"),
        _chunk(f"{_DOC_A}_0001_feedface", "docs/a.pdf", "alpha", _DOC_A, "Doc A", "alpha two"),
        _chunk(f"{_DOC_B}_0000_cafebabe", "docs/b.pdf", "beta", _DOC_B, "Doc B", "beta one"),
    ])
    indexer = BM25Indexer(index_path=tmp_path / "bm25.json").build([
        SparseDocStats(chunk_id=f"{_DOC_A}_0000_deadbeef", term_freqs={"alpha": 1}, length=1),
        SparseDocStats(chunk_id=f"{_DOC_A}_0001_feedface", term_freqs={"guide": 1}, length=1),
        SparseDocStats(chunk_id=f"{_DOC_B}_0000_cafebabe", term_freqs={"beta": 1}, length=1),
    ])
    images = ImageStorage(images_root=str(tmp_path / "images"), db_path=str(tmp_path / "images.db"))
    images.put(f"{_DOC_A}_0_0", _PNG, collection="alpha", doc_hash=_DOC_A, page_num=0)
    integrity = SQLiteIntegrityChecker(str(tmp_path / "history.db"))
    integrity.mark_success(_DOC_A, "docs/a.pdf")
    integrity.mark_success(_DOC_B, "docs/b.pdf")
    manager = DocumentManager(
        chroma_store=store,
        bm25_indexer=indexer,
        image_storage=images,
        file_integrity=integrity,
    )
    return {
        "manager": manager,
        "store": store,
        "indexer": indexer,
        "images": images,
        "integrity": integrity,
    }


def test_list_documents_aggregates_source_and_counts(stack):
    docs = stack["manager"].list_documents()
    assert [item.source_path for item in docs] == ["docs/a.pdf", "docs/b.pdf"]
    alpha = docs[0]
    assert isinstance(alpha, DocumentInfo)
    assert alpha.collection == "alpha"
    assert alpha.chunk_count == 2
    assert alpha.image_count == 1
    assert alpha.doc_hash == _DOC_A
    assert alpha.title == "Doc A"
    assert alpha.ingested_at
    assert stack["manager"].list_documents("beta")[0].source_path == "docs/b.pdf"


def test_get_document_detail_returns_chunks_and_images(stack):
    detail = stack["manager"].get_document_detail("docs/a.pdf", "alpha")
    assert isinstance(detail, DocumentDetail)
    assert detail.chunk_count == 2
    assert [chunk["id"] for chunk in detail.chunks] == [
        f"{_DOC_A}_0000_deadbeef",
        f"{_DOC_A}_0001_feedface",
    ]
    assert detail.images[0]["image_id"] == f"{_DOC_A}_0_0"
    assert detail.title == "Doc A"
    assert stack["manager"].get_document_detail("missing.pdf") is None


def test_get_document_detail_rejects_empty_id(stack):
    with pytest.raises(ValueError):
        stack["manager"].get_document_detail("")


def test_delete_document_coordinates_four_stores(stack):
    result = stack["manager"].delete_document("docs/a.pdf", "alpha")
    assert isinstance(result, DeleteResult)
    assert result.chroma_deleted == 2
    assert result.bm25_deleted == 2
    assert result.images_deleted == 1
    assert result.integrity_removed is True
    remaining = stack["manager"].list_documents()
    assert [item.source_path for item in remaining] == ["docs/b.pdf"]
    assert stack["manager"].get_document_detail("docs/a.pdf", "alpha") is None
    assert stack["indexer"].query(["alpha"], 3) == []
    assert stack["images"].count(doc_hash=_DOC_A) == 0
    assert stack["integrity"].should_skip(_DOC_A) is False


def test_delete_document_rejects_empty_args(stack):
    with pytest.raises(ValueError):
        stack["manager"].delete_document("", "alpha")
    with pytest.raises(ValueError):
        stack["manager"].delete_document("docs/a.pdf", "")


def test_get_collection_stats_groups_documents(stack):
    stats = stack["manager"].get_collection_stats()
    assert [item.name for item in stats] == ["alpha", "beta"]
    assert stats[0] == CollectionStats(name="alpha", document_count=1, chunk_count=2, image_count=1)
    only_beta = stack["manager"].get_collection_stats("beta")
    assert only_beta[0].document_count == 1
    assert only_beta[0].chunk_count == 1
    missing = stack["manager"].get_collection_stats("ghost")
    assert missing == [CollectionStats(name="ghost")]


def test_dataclasses_to_dict_roundtrip():
    info = DocumentInfo("a.pdf", "c", 2, image_count=1, ingested_at="t", doc_hash="h", title="T")
    assert info.to_dict()["source_path"] == "a.pdf"
    detail = DocumentDetail("a.pdf", "c", 1, chunks=[{"id": "x"}], images=[], title="T")
    assert detail.to_dict()["chunk_count"] == 1
    deleted = DeleteResult("a.pdf", "c", chroma_deleted=1, integrity_removed=True)
    assert deleted.to_dict()["integrity_removed"] is True
    stats = CollectionStats("c", document_count=2, chunk_count=4)
    assert stats.to_dict()["name"] == "c"


# ---------------------------------------------------------------------------
# IR2：多哈希收集 / 遍历删除 / 版本替换前置清理
# ---------------------------------------------------------------------------

_OLD = "a1" * 32
_NEW = "a2" * 32


def test_doc_hashes_of_collects_metadata_keys_and_id_prefixes():
    prefix_only = "cd" * 32
    chunks = [
        {"id": f"{_OLD}_0000_deadbeef", "metadata": {"sha256": _OLD}},
        {"id": "x", "metadata": {"doc_hash": _NEW}},
        {"id": f"{prefix_only}_0000_cafebabe", "metadata": {}},
        {"id": "short_0000_cafebabe", "metadata": {}},
    ]
    assert DocumentManager._doc_hashes_of(chunks) == {_OLD, _NEW, prefix_only}
    assert DocumentManager._doc_hashes_of([]) == set()
    assert DocumentManager._primary_hash({_NEW, _OLD}) == _OLD
    assert DocumentManager._primary_hash(set()) is None


def _replacement_stack(tmp_path: Path):
    """docs/a.pdf 在 alpha 有旧版本 _OLD；docs/copy.pdf（同内容副本）在 beta 共享 _DOC_B。"""
    store = FakeStore()
    store.upsert([
        _chunk(f"{_OLD}_0000_deadbeef", "docs/a.pdf", "alpha", _OLD, "Doc A v1"),
        _chunk(f"{_OLD}_0001_feedface", "docs/a.pdf", "alpha", _OLD, "Doc A v1"),
        _chunk(f"{_DOC_B}_0000_cafebabe", "docs/b.pdf", "beta", _DOC_B, "Doc B"),
    ])
    indexer = BM25Indexer(index_path=tmp_path / "bm25.json").build([
        SparseDocStats(chunk_id=f"{_OLD}_0000_deadbeef", term_freqs={"old": 1}, length=1),
        SparseDocStats(chunk_id=f"{_OLD}_0001_feedface", term_freqs={"old": 1}, length=1),
        SparseDocStats(chunk_id=f"{_DOC_B}_0000_cafebabe", term_freqs={"beta": 1}, length=1),
    ])
    images = ImageStorage(images_root=str(tmp_path / "images"), db_path=str(tmp_path / "images.db"))
    images.put(f"{_OLD}_0_0", _PNG, doc_hash=_OLD, page_num=0)
    images.put(f"{_DOC_B}_0_0", _PNG, doc_hash=_DOC_B, page_num=0)
    integrity = SQLiteIntegrityChecker(str(tmp_path / "history.db"))
    integrity.mark_success(_OLD, "docs/a.pdf", collection="alpha")
    integrity.mark_success(_DOC_B, "docs/b.pdf", collection="beta")
    manager = DocumentManager(
        chroma_store=store, bm25_indexer=indexer, image_storage=images, file_integrity=integrity
    )
    return manager, store, indexer, images, integrity


def test_prepare_version_replacement_is_noop_without_record_or_same_hash(tmp_path):
    manager, store, indexer, _, _ = _replacement_stack(tmp_path)
    assert manager.prepare_version_replacement("docs/new.pdf", "alpha", _NEW) is None
    assert manager.prepare_version_replacement("docs/a.pdf", "alpha", _OLD) is None
    # 同路径换集合：历史按 (source_path, collection) 查，beta 下无记录
    assert manager.prepare_version_replacement("docs/a.pdf", "beta", _NEW) is None
    assert len(store.get_by_metadata()) == 3
    assert len(indexer) == 3


def test_prepare_version_replacement_clears_old_version_only(tmp_path):
    manager, store, indexer, images, integrity = _replacement_stack(tmp_path)

    result = manager.prepare_version_replacement("docs/a.pdf", "alpha", _NEW)

    assert result is not None
    assert (result.chroma_deleted, result.bm25_deleted, result.images_deleted) == (2, 2, 1)
    assert store.get_by_metadata({"source_path": "docs/a.pdf"}) == []
    assert images.count(doc_hash=_OLD) == 0
    # 其他文档不受影响
    assert len(store.get_by_metadata({"collection": "beta"})) == 1
    assert len(indexer) == 1
    assert images.count(doc_hash=_DOC_B) == 1
    # 历史记录保留，交给随后的 mark_success / mark_failed 覆盖
    assert integrity.get_record("docs/a.pdf", "alpha")["file_hash"] == _OLD
    reloaded = BM25Indexer(index_path=tmp_path / "bm25.json").load()
    assert len(reloaded) == 1


def test_prepare_version_replacement_is_idempotent(tmp_path):
    manager, store, indexer, images, _ = _replacement_stack(tmp_path)
    manager.prepare_version_replacement("docs/a.pdf", "alpha", _NEW)
    snapshot = (len(store.get_by_metadata()), len(indexer), images.count())

    again = manager.prepare_version_replacement("docs/a.pdf", "alpha", _NEW)

    assert again is not None
    assert (again.chroma_deleted, again.bm25_deleted, again.images_deleted) == (0, 0, 0)
    assert (len(store.get_by_metadata()), len(indexer), images.count()) == snapshot


def test_prepare_version_replacement_keeps_hash_shared_by_other_scope(tmp_path):
    manager, store, indexer, images, integrity = _replacement_stack(tmp_path)
    # docs/copy.pdf 与 docs/a.pdf 旧版本同内容（同哈希）。真实 chunk_id 会相同并互相覆盖，
    # 这里换一个 index 以便 FakeStore 中两条记录并存。
    store.upsert([_chunk(f"{_OLD}_0009_00000000", "docs/copy.pdf", "gamma", _OLD, "Copy")])

    result = manager.prepare_version_replacement("docs/a.pdf", "alpha", _NEW)

    assert result.chroma_deleted == 2
    assert result.bm25_deleted == 0
    assert result.images_deleted == 0
    assert images.count(doc_hash=_OLD) == 1
    assert len(store.get_by_metadata({"source_path": "docs/copy.pdf"})) == 1


def test_prepare_version_replacement_bm25_save_failure_propagates(tmp_path, monkeypatch):
    manager, _, indexer, _, _ = _replacement_stack(tmp_path)

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(indexer, "save", _boom)
    with pytest.raises(OSError):
        manager.prepare_version_replacement("docs/a.pdf", "alpha", _NEW)


def test_delete_document_keeps_other_collections_history_and_shared_hash(tmp_path):
    manager, store, indexer, images, integrity = _replacement_stack(tmp_path)
    store.upsert([_chunk(f"{_OLD}_0009_00000000", "docs/a.pdf", "gamma", _OLD, "Doc A in gamma")])
    integrity.mark_success(_OLD, "docs/a.pdf", collection="gamma")

    result = manager.delete_document("docs/a.pdf", "alpha")

    assert result.chroma_deleted == 2
    assert result.bm25_deleted == 0
    assert result.images_deleted == 0
    assert result.integrity_removed is True
    assert integrity.get_record("docs/a.pdf", "alpha") is None
    assert integrity.get_record("docs/a.pdf", "gamma") is not None
    assert len(store.get_by_metadata({"collection": "gamma"})) == 1


def test_delete_document_is_idempotent_for_mixed_generations(tmp_path):
    manager, store, indexer, images, integrity = _replacement_stack(tmp_path)
    store.upsert([_chunk(f"{_NEW}_0000_cafebabe", "docs/a.pdf", "alpha", _NEW, "Doc A v2")])
    indexer.add([SparseDocStats(chunk_id=f"{_NEW}_0000_cafebabe", term_freqs={"new": 1}, length=1)])

    first = manager.delete_document("docs/a.pdf", "alpha")
    second = manager.delete_document("docs/a.pdf", "alpha")

    assert (first.chroma_deleted, first.bm25_deleted, first.images_deleted) == (3, 3, 1)
    assert first.integrity_removed is True
    assert (second.chroma_deleted, second.bm25_deleted, second.images_deleted) == (0, 0, 0)
    assert second.integrity_removed is False
    assert len(indexer) == 1
