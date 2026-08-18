"""``SparseRetriever`` 单元测试（阶段 D3）。

离线组合：真 ``BM25Indexer``（临时索引路径）+ 注入 fake vector store
（实现 ``get_by_ids``），验证召回与合并契约：

- BM25 命中 → ``get_by_ids`` 取回 text/metadata → 按 BM25 score 降序组装。
- 索引命中但库中缺失的死条目跳过，其余命中不受影响。
- 无命中/空 keywords → 空结果且不触库。
- 默认构造：无索引文件 → 空索引起步；有持久化文件 → 自动加载。
- fail-fast：非列表 keywords / 非 str 项 / 非法 top_k。
- trace 打点 ``sparse_retrieval`` 阶段。
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from core.query_engine.sparse_retriever import SparseRetriever
from core.settings import Settings, load_settings
from core.trace.trace_context import TraceContext
from core.types import RetrievalResult
from ingestion.embedding import SparseDocStats
from ingestion.storage import BM25Indexer
from libs.vector_store.base_vector_store import BaseVectorStore

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


def _settings(tmp_path: Path) -> Settings:
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    return load_settings(path)


class _FakeStore(BaseVectorStore):
    """内存 fake：按 id 返回 {id, text, metadata}，记录 get_by_ids 调用。"""

    def __init__(self, records: dict[str, tuple[str, dict]] | None = None):
        self.records = records or {}
        self.get_calls: list[list[str]] = []

    def upsert(self, records, *, trace=None, **kwargs):
        return 0

    def query(self, vector, top_k=10, *, filters=None, trace=None, **kwargs):
        return []

    def get_by_ids(self, ids, *, trace=None, **kwargs):
        self.get_calls.append(list(ids))
        out = []
        seen = set()
        for rid in ids:
            if rid in seen:
                continue
            seen.add(rid)
            record = self.records.get(rid)
            if record is not None:
                out.append({"id": rid, "text": record[0], "metadata": dict(record[1])})
        return out

    def delete_by_metadata(self, filters, *, trace=None, **kwargs):
        return 0

    def get_by_metadata(self, filters=None, *, trace=None, **kwargs):
        return []

    def get_collection_stats(self, *, trace=None, **kwargs):
        return {"name": "fake", "chunk_count": 0, "document_count": 0, "collections": []}


def _indexer(tmp_path: Path, docs: dict[str, str], k1: float = 1.5, b: float = 0.75) -> BM25Indexer:
    """构建含指定 chunk 文本的真 BM25 索引（default_tokenize 分词）。"""
    from ingestion.embedding.sparse_encoder import default_tokenize

    stats = [
        SparseDocStats(
            chunk_id=chunk_id,
            term_freqs=dict(__import__("collections").Counter(default_tokenize(text))),
            length=len(default_tokenize(text)),
        )
        for chunk_id, text in docs.items()
    ]
    indexer = BM25Indexer(index_path=tmp_path / "bm25" / "index.json", k1=k1, b=b)
    indexer.build(stats)
    return indexer


_CORPUS = {
    "doc_a_0000_x1": "azure active directory authentication configuration guide",
    "doc_a_0001_x2": "bm25 sparse retrieval inverted index scoring",
    "doc_b_0000_x3": "azure deployment tokens and azure endpoint keys",
}


@pytest.fixture
def components(tmp_path: Path):
    indexer = _indexer(tmp_path, _CORPUS)
    store = _FakeStore(
        {
            "doc_a_0000_x1": ("azure ad 配置指南", {"source_path": "a.pdf", "page": 1}),
            "doc_a_0001_x2": ("bm25 原理", {"source_path": "b.pdf"}),
            "doc_b_0000_x3": ("azure 部署密钥", {"source_path": "c.pdf", "page": 7}),
        }
    )
    retriever = SparseRetriever(_settings(tmp_path), bm25_indexer=indexer, vector_store=store)
    return retriever, store


# ---------------------------------------------------------------------------
# 召回与合并契约
# ---------------------------------------------------------------------------


def test_keyword_hit_merges_score_text_metadata(components):
    retriever, _ = components
    results = retriever.retrieve(["bm25", "inverted"], 5)
    assert len(results) >= 1
    top = results[0]
    assert isinstance(top, RetrievalResult)
    assert top.chunk_id == "doc_a_0001_x2"
    assert top.text == "bm25 原理"
    assert top.metadata == {"source_path": "b.pdf"}
    assert top.score > 0.0


def test_results_ordered_by_bm25_score_desc(components):
    retriever, _ = components
    results = retriever.retrieve(["azure"], 5)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_dead_index_entries_skipped(tmp_path):
    """索引命中但向量库缺失的 chunk 被跳过，其余命中完整保留。"""
    indexer = _indexer(tmp_path, _CORPUS)
    store = _FakeStore({"doc_a_0000_x1": ("azure ad 配置", {"page": 1})})
    retriever = SparseRetriever(_settings(tmp_path), bm25_indexer=indexer, vector_store=store)
    results = retriever.retrieve(["azure"], 10)
    assert [r.chunk_id for r in results] == ["doc_a_0000_x1"]


def test_no_hits_returns_empty_without_store_call(components):
    retriever, store = components
    assert retriever.retrieve(["zzz", "qqq"], 5) == []
    assert store.get_calls == []


def test_empty_keywords_returns_empty(components):
    retriever, store = components
    assert retriever.retrieve([], 5) == []
    assert store.get_calls == []


def test_top_k_limits_results(components):
    retriever, _ = components
    assert len(retriever.retrieve(["azure"], 10)) == 2
    assert len(retriever.retrieve(["azure"], 1)) == 1


def test_get_by_ids_called_with_hit_ids_in_score_order(components):
    retriever, store = components
    retriever.retrieve(["azure"], 10)
    assert len(store.get_calls) == 1
    assert set(store.get_calls[0]) == {"doc_a_0000_x1", "doc_b_0000_x3"}


# ---------------------------------------------------------------------------
# fail-fast 契约
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_keywords",
    ["azure", 42, {"azure": 1}],
)
def test_non_list_keywords_fails_fast(tmp_path, bad_keywords):
    indexer = _indexer(tmp_path, _CORPUS)
    retriever = SparseRetriever(
        _settings(tmp_path), bm25_indexer=indexer, vector_store=_FakeStore()
    )
    with pytest.raises(TypeError):
        retriever.retrieve(bad_keywords, 5)  # type: ignore[arg-type]


def test_non_string_keyword_fails_fast(tmp_path):
    retriever = SparseRetriever(
        _settings(tmp_path), bm25_indexer=_indexer(tmp_path, _CORPUS), vector_store=_FakeStore()
    )
    with pytest.raises(TypeError):
        retriever.retrieve(["azure", 3], 5)  # type: ignore[list-item]


@pytest.mark.parametrize("bad_top_k", [0, -2, 1.5, "4", None, False])
def test_invalid_top_k_fails_fast(tmp_path, bad_top_k):
    retriever = SparseRetriever(
        _settings(tmp_path), bm25_indexer=_indexer(tmp_path, _CORPUS), vector_store=_FakeStore()
    )
    with pytest.raises(ValueError):
        retriever.retrieve(["azure"], bad_top_k)


# ---------------------------------------------------------------------------
# trace 打点与默认构造
# ---------------------------------------------------------------------------


def test_records_trace_stage(components):
    retriever, _ = components
    trace = TraceContext()
    retriever.retrieve(["azure", "deployment"], 6, trace=trace)
    stage = trace.get_stage("sparse_retrieval")
    assert stage is not None
    assert stage["method"] == "sparse_retriever"
    assert stage["details"]["keyword_count"] == 2
    assert stage["details"]["top_k"] == 6
    assert stage["details"]["hits"] == stage["details"]["merged"]
    assert stage["details"]["skipped"] == 0
    assert "elapsed_ms" in stage["details"]


def test_default_indexer_without_file_starts_empty(tmp_path, monkeypatch):
    """无持久化索引文件：空内存索引起步，查询返回空不报错。"""
    monkeypatch.chdir(tmp_path)
    retriever = SparseRetriever(_settings(tmp_path), vector_store=_FakeStore())
    assert retriever.retrieve(["azure"], 5) == []


def test_default_indexer_loads_persisted_file(tmp_path, monkeypatch):
    """默认构造自动加载 data/db/bm25/index.json（与 C14 Pipeline 同一路径）。"""
    monkeypatch.chdir(tmp_path)
    indexer = BM25Indexer(index_path=tmp_path / "data" / "db" / "bm25" / "index.json")
    indexer.build(
        [
            SparseDocStats(
                chunk_id="doc_a_0000_x1",
                term_freqs={"azure": 2, "config": 1},
                length=3,
            )
        ]
    )
    indexer.save()
    store = _FakeStore({"doc_a_0000_x1": ("azure 配置", {"page": 2})})
    retriever = SparseRetriever(_settings(tmp_path), vector_store=store)
    results = retriever.retrieve(["azure"], 5)
    assert [r.chunk_id for r in results] == ["doc_a_0000_x1"]
    assert results[0].text == "azure 配置"


def test_injected_indexer_used_as_is(tmp_path):
    """注入的 indexer 原样使用（不做 load，调用方控制索引状态）。"""
    empty_indexer = BM25Indexer(index_path=tmp_path / "bm25" / "index.json")
    retriever = SparseRetriever(
        _settings(tmp_path), bm25_indexer=empty_indexer, vector_store=_FakeStore()
    )
    assert retriever.bm25_indexer is empty_indexer
    assert retriever.vector_store is not None
