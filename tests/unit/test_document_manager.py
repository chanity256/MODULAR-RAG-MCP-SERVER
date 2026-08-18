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
