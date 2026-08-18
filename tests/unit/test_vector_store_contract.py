"""``BaseVectorStore`` 契约测试与 ``VectorStoreFactory`` 路由测试（阶段 B4）。

契约测试（contract test）约束 ``upsert`` / ``query`` 的输入输出 shape：
- ``upsert`` 接收 ``VectorRecord`` 序列，返回 int（写入数）。
- ``query`` 接收向量 + top_k + filters，返回按 score 降序的 ``QueryResult`` 列表。
- 工厂按 ``settings.vector_store.backend`` 路由，未知 backend 报错。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from libs.vector_store.base_vector_store import BaseVectorStore, QueryResult, VectorRecord
from libs.vector_store.vector_store_factory import VectorStoreBackendError, VectorStoreFactory

_VALID_YAML = dedent(
    """\
    llm:
      provider: azure
      model: gpt-4o
    embedding:
      provider: openai
      model: text-embedding-3-small
    vision_llm:
      provider: azure
      model: gpt-4o
    vector_store:
      backend: chroma
      persist_path: ./data/db/chroma
    retrieval:
      sparse_backend: bm25
      fusion_algorithm: rrf
    rerank:
      backend: none
    evaluation:
      backends: [custom]
    """
)


def _settings(tmp_path: Path, backend: str | None = None):
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    settings = load_settings(path)
    if backend is not None:
        settings = replace(settings, vector_store=replace(settings.vector_store, backend=backend))
    return settings


# ---------------------------------------------------------------------------
# Fake VectorStore（满足契约的内存实现）
# ---------------------------------------------------------------------------


class _FakeVectorStore(BaseVectorStore):
    def __init__(self, vs_config, **kwargs):
        self.vs_config = vs_config
        self.kwargs = kwargs
        self._store: dict[str, VectorRecord] = {}

    def upsert(self, records, *, trace=None, **kwargs):
        n = 0
        for r in records:
            if not isinstance(r, VectorRecord):
                raise ValueError("upsert requires VectorRecord instances")
            if not r.id or not isinstance(r.vector, list):
                raise ValueError("VectorRecord needs id + vector")
            self._store[r.id] = r
            n += 1
        return n

    def query(self, vector, top_k=10, *, filters=None, trace=None, **kwargs):
        results: list[QueryResult] = []
        for rec in self._store.values():
            if filters:
                ok = all(rec.metadata.get(k) == v for k, v in filters.items())
                if not ok:
                    continue
            # 简单点积作为 score（仅用于契约测试的确定性排序）。
            score = sum(a * b for a, b in zip(vector, rec.vector))
            results.append(QueryResult(id=rec.id, score=score, text=rec.text, metadata=rec.metadata))
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def get_by_ids(self, ids, *, trace=None, **kwargs):
        out = []
        seen = set()
        for rid in ids:
            if rid in seen:
                continue
            seen.add(rid)
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
        records.sort(key=lambda r: r["id"])
        return records

    def get_collection_stats(self, *, trace=None, **kwargs):
        records = self.get_by_metadata()
        sources = {r["metadata"].get("source_path") for r in records if r["metadata"].get("source_path")}
        return {"name": "fake", "chunk_count": len(records), "document_count": len(sources), "collections": []}


def _make_fake():
    def ctor(vs_config, **kwargs):
        return _FakeVectorStore(vs_config, **kwargs)
    return ctor


@pytest.fixture
def clean_registry():
    saved = dict(VectorStoreFactory._registry)
    VectorStoreFactory._registry.clear()
    yield VectorStoreFactory
    VectorStoreFactory._registry.clear()
    VectorStoreFactory._registry.update(saved)


# ---------------------------------------------------------------------------
# 工厂路由测试
# ---------------------------------------------------------------------------


def test_register_and_route_to_fake(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    settings = _settings(tmp_path, backend="chroma")
    vs = clean_registry.create(settings)
    assert isinstance(vs, _FakeVectorStore)


def test_route_to_different_backend(tmp_path, clean_registry):
    clean_registry.register("qdrant")(_make_fake())
    settings = _settings(tmp_path, backend="qdrant")
    vs = clean_registry.create(settings)
    assert isinstance(vs, _FakeVectorStore)


def test_unknown_backend_raises(tmp_path, clean_registry):
    settings = _settings(tmp_path, backend="nonexistent")
    with pytest.raises(VectorStoreBackendError) as exc_info:
        clean_registry.create(settings)
    assert "nonexistent" in str(exc_info.value)


def test_unknown_backend_error_lists_registered(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    settings = _settings(tmp_path, backend="bad")
    with pytest.raises(VectorStoreBackendError) as exc_info:
        clean_registry.create(settings)
    msg = str(exc_info.value)
    assert "bad" in msg
    assert "chroma" in msg


def test_kwargs_passthrough_to_ctor(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    settings = _settings(tmp_path, backend="chroma")
    vs = clean_registry.create(settings, extra_flag=True)
    assert vs.kwargs["extra_flag"] is True


# ---------------------------------------------------------------------------
# 契约测试：upsert / query 输入输出 shape
# ---------------------------------------------------------------------------


def test_upsert_returns_int_count(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    records = [
        VectorRecord(id="c1", vector=[1.0, 0.0], text="a", metadata={"page": 1}),
        VectorRecord(id="c2", vector=[0.0, 1.0], text="b", metadata={"page": 2}),
    ]
    n = vs.upsert(records)
    assert isinstance(n, int)
    assert n == 2


def test_upsert_is_idempotent(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    rec = VectorRecord(id="c1", vector=[1.0, 0.0], text="a")
    assert vs.upsert([rec]) == 1
    # 重复 upsert 同一 id 不产生重复记录。
    assert vs.upsert([rec]) == 1
    assert vs.query([1.0, 0.0], top_k=10) == [QueryResult(id="c1", score=1.0, text="a", metadata={})]


def test_query_returns_queryresult_list_sorted_by_score(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    vs.upsert([
        VectorRecord(id="c1", vector=[1.0, 0.0], text="a"),
        VectorRecord(id="c2", vector=[0.9, 0.1], text="b"),
        VectorRecord(id="c3", vector=[0.0, 1.0], text="c"),
    ])
    results = vs.query([1.0, 0.0], top_k=3)
    assert isinstance(results, list)
    assert all(isinstance(r, QueryResult) for r in results)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    assert results[0].id == "c1"  # 最相关


def test_query_top_k_limits_results(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    vs.upsert([
        VectorRecord(id=f"c{i}", vector=[float(i), 0.0], text=str(i)) for i in range(5)
    ])
    results = vs.query([1.0, 0.0], top_k=2)
    assert len(results) == 2


def test_query_filters_by_metadata(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    vs.upsert([
        VectorRecord(id="c1", vector=[1.0, 0.0], text="a", metadata={"collection": "x"}),
        VectorRecord(id="c2", vector=[1.0, 0.0], text="b", metadata={"collection": "y"}),
    ])
    results = vs.query([1.0, 0.0], top_k=10, filters={"collection": "x"})
    assert len(results) == 1
    assert results[0].id == "c1"


def test_query_result_to_dict_shape(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    vs.upsert([VectorRecord(id="c1", vector=[1.0, 0.0], text="a", metadata={"page": 1})])
    res = vs.query([1.0, 0.0], top_k=1)[0]
    d = res.to_dict()
    assert set(d.keys()) == {"id", "score", "text", "metadata"}
    assert d["metadata"] == {"page": 1}


def test_upsert_rejects_non_vectorrecord(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    with pytest.raises(ValueError):
        vs.upsert([{"id": "x", "vector": [1.0]}])  # type: ignore[list-item]


def test_query_empty_store_returns_empty_list(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    assert vs.query([1.0, 0.0], top_k=5) == []


def test_delete_by_metadata_removes_matching_records(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    vs.upsert([
        VectorRecord(id="c1", vector=[1.0, 0.0], text="a", metadata={"source_path": "a.pdf", "collection": "docs"}),
        VectorRecord(id="c2", vector=[1.0, 0.0], text="b", metadata={"source_path": "b.pdf", "collection": "docs"}),
        VectorRecord(id="c3", vector=[1.0, 0.0], text="c", metadata={"source_path": "a.pdf", "collection": "other"}),
    ])
    deleted = vs.delete_by_metadata({"source_path": "a.pdf", "collection": "docs"})
    assert deleted == 1
    remaining = vs.get_by_metadata()
    assert {r["id"] for r in remaining} == {"c2", "c3"}


def test_delete_by_metadata_rejects_empty_filters(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    with pytest.raises(ValueError):
        vs.delete_by_metadata({})


def test_get_by_metadata_filters_and_sorts(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    vs.upsert([
        VectorRecord(id="c2", vector=[1.0, 0.0], text="b", metadata={"collection": "docs"}),
        VectorRecord(id="c1", vector=[1.0, 0.0], text="a", metadata={"collection": "docs"}),
        VectorRecord(id="c3", vector=[1.0, 0.0], text="c", metadata={"collection": "other"}),
    ])
    records = vs.get_by_metadata({"collection": "docs"})
    assert [r["id"] for r in records] == ["c1", "c2"]


def test_get_collection_stats_counts_chunks_and_documents(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    vs.upsert([
        VectorRecord(id="c1", vector=[1.0, 0.0], text="a", metadata={"source_path": "a.pdf"}),
        VectorRecord(id="c2", vector=[1.0, 0.0], text="b", metadata={"source_path": "a.pdf"}),
        VectorRecord(id="c3", vector=[1.0, 0.0], text="c", metadata={"source_path": "b.pdf"}),
    ])
    stats = vs.get_collection_stats()
    assert stats["chunk_count"] == 3
    assert stats["document_count"] == 2


# ---------------------------------------------------------------------------
# 契约补齐（I4）：delete_by_metadata / get_by_ids 边界
# ---------------------------------------------------------------------------


def test_delete_by_metadata_no_match_returns_zero(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    vs.upsert([VectorRecord(id="c1", vector=[1.0, 0.0], text="a", metadata={"source_path": "a.pdf"})])
    assert vs.delete_by_metadata({"source_path": "missing.pdf"}) == 0
    assert len(vs.get_by_metadata()) == 1


def test_delete_by_metadata_removes_multiple_matches(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    vs.upsert([
        VectorRecord(id=f"c{i}", vector=[1.0, 0.0], text=str(i), metadata={"source_path": "a.pdf"})
        for i in range(3)
    ])
    assert vs.delete_by_metadata({"source_path": "a.pdf"}) == 3
    assert vs.get_by_metadata() == []


def test_delete_by_metadata_non_dict_filters_rejected(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    with pytest.raises(ValueError):
        vs.delete_by_metadata(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        vs.delete_by_metadata(["source_path"])  # type: ignore[arg-type]


def test_delete_by_metadata_idempotent_second_call_zero(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    vs.upsert([VectorRecord(id="c1", vector=[1.0, 0.0], text="a", metadata={"collection": "x"})])
    assert vs.delete_by_metadata({"collection": "x"}) == 1
    assert vs.delete_by_metadata({"collection": "x"}) == 0


def test_delete_by_metadata_accepts_trace_kwarg(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    vs.upsert([VectorRecord(id="c1", vector=[1.0, 0.0], text="a", metadata={"collection": "x"})])
    assert vs.delete_by_metadata({"collection": "x"}, trace=None) == 1


def test_query_after_delete_excludes_deleted(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    vs.upsert([
        VectorRecord(id="c1", vector=[1.0, 0.0], text="a", metadata={"keep": True}),
        VectorRecord(id="c2", vector=[1.0, 0.0], text="b", metadata={"drop": True}),
    ])
    vs.delete_by_metadata({"drop": True})
    results = vs.query([1.0, 0.0], top_k=10)
    assert [r.id for r in results] == ["c1"]


def test_get_by_ids_after_delete_skips_missing(tmp_path, clean_registry):
    clean_registry.register("chroma")(_make_fake())
    vs = clean_registry.create(_settings(tmp_path, backend="chroma"))
    vs.upsert([
        VectorRecord(id="c1", vector=[1.0, 0.0], text="a", metadata={"keep": True}),
        VectorRecord(id="c2", vector=[1.0, 0.0], text="b", metadata={"drop": True}),
    ])
    vs.delete_by_metadata({"drop": True})
    records = vs.get_by_ids(["c2", "c1"])  # c2 已删，缺失跳过不占位
    assert [r["id"] for r in records] == ["c1"]
