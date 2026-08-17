"""``HybridSearch`` 集成测试（阶段 D5）。

离线真实组件协作：真 ``ChromaStore``（嵌入式 PersistentClient，tmp 目录）
+ 真 ``BM25Indexer``（tmp 索引路径）+ 确定性哈希 ``BaseEmbedding`` +
真 ``QueryProcessor`` / ``RRFFusion``，仅免除网络依赖。

覆盖（spec D5 验收标准）：
- 对 fixtures 数据返回 Top-K（含 chunk 文本与 metadata）。
- ``filters`` 过滤（Dense 路 Pre-filter + Sparse 路 Post-filter 兜底）。
- Dense/Sparse 任一路径失败降级到单路结果；两路均失败抛错。
- trace 阶段链完整（query_processing/dense_retrieval/sparse_retrieval/fusion/hybrid）。
"""

from __future__ import annotations


import shutil
from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.query_engine.dense_retriever import DenseRetriever
from core.query_engine.fusion import RRFFusion
from core.query_engine.hybrid_search import HybridSearch, HybridSearchError
from core.query_engine.query_processor import QueryProcessor
from core.query_engine.sparse_retriever import SparseRetriever
from core.settings import load_settings
from core.trace.trace_context import TraceContext
from core.types import RetrievalResult
from ingestion.embedding import SparseDocStats
from ingestion.embedding.sparse_encoder import default_tokenize
from ingestion.storage import BM25Indexer
from libs.embedding.base_embedding import BaseEmbedding
from libs.vector_store.base_vector_store import VectorRecord
from libs.vector_store.chroma_store import ChromaStore

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
      top_k_dense: 20
      top_k_sparse: 20
      top_k_final: 10
    rerank:
      backend: none
    evaluation:
      backends: [custom]
    """
)

#: 测试语料：chunk_id -> (text, metadata)。azure 主题跨两个 collection。
_CORPUS = {
    "doc_a_0000_h1": ("azure active directory authentication", {"collection": "eng", "doc_type": "pdf", "source_path": "a.pdf", "page": 1}),
    "doc_a_0001_h2": ("azure deployment endpoint and keys", {"collection": "eng", "doc_type": "pdf", "source_path": "a.pdf", "page": 2}),
    "doc_b_0000_h3": ("bm25 sparse retrieval scoring", {"collection": "ml", "doc_type": "pdf", "source_path": "b.pdf", "page": 1}),
    "doc_b_0001_h4": ("azure ad single sign on overview", {"collection": "ml", "doc_type": "pdf", "source_path": "b.pdf", "page": 5}),
}

#: 词表：语料 + 测试查询的 token 并集（bag-of-words 向量空间）。
_VOCAB = sorted(
    {
        token
        for text, _ in _CORPUS.values()
        for token in default_tokenize(text)
    }
    | {"inverted"}
)
_VOCAB_SET = frozenset(_VOCAB)


class _KeywordEmbedding(BaseEmbedding):
    """确定性 bag-of-words embedding：token 计数向量（离线、语义可预期）。

    Dense 相似度退化为词重合度（cosine），使测试断言确定可复现，
    同时保持 Dense/Sparse 互补结构真实。
    """

    @property
    def dimension(self) -> int | None:
        return len(_VOCAB)

    def embed(self, texts, *, trace=None, **kwargs):
        vectors = []
        for text in texts:
            counts = {}
            for token in default_tokenize(text):
                if token in _VOCAB_SET:
                    counts[token] = counts.get(token, 0) + 1
            vectors.append([float(counts.get(token, 0)) for token in _VOCAB])
        return vectors


def _embed_text(text: str) -> list[float]:
    return _KeywordEmbedding().embed([text])[0]


class _ExplodingDenseRetriever:
    """总是抛错的 Dense 路 fake（降级测试用）。"""

    def retrieve(self, query, top_k, filters=None, *, trace=None):
        raise RuntimeError("dense backend offline")


class _ExplodingSparseRetriever:
    """总是抛错的 Sparse 路 fake（降级测试用）。"""

    def retrieve(self, keywords, top_k, *, trace=None):
        raise RuntimeError("bm25 index corrupted")


@pytest.fixture
def settings(tmp_path: Path):
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    return load_settings(path)


@pytest.fixture
def components(tmp_path: Path, settings):
    """真 Chroma + 真 BM25 + 确定性 embedding 的完整查询栈。"""
    persist = tmp_path / "chroma_db"
    settings = replace(settings, vector_store=replace(settings.vector_store, persist_path=str(persist)))
    store = ChromaStore(settings.vector_store, collection="hybrid_test")
    indexer = BM25Indexer(index_path=tmp_path / "bm25" / "index.json")

    records = []
    stats = []
    for chunk_id, (text, metadata) in _CORPUS.items():
        records.append(
            VectorRecord(id=chunk_id, vector=_embed_text(text), text=text, metadata=dict(metadata))
        )
        tokens = default_tokenize(text)
        stats.append(
            SparseDocStats(
                chunk_id=chunk_id,
                term_freqs=dict(__import__("collections").Counter(tokens)),
                length=len(tokens),
            )
        )
    store.upsert(records)
    indexer.build(stats)

    dense = DenseRetriever(settings, embedding_client=_KeywordEmbedding(), vector_store=store)
    sparse = SparseRetriever(settings, bm25_indexer=indexer, vector_store=store)
    hybrid = HybridSearch(
        settings,
        query_processor=QueryProcessor(settings),
        dense_retriever=dense,
        sparse_retriever=sparse,
        fusion=RRFFusion(settings),
    )
    yield hybrid
    shutil.rmtree(persist, ignore_errors=True)


# ---------------------------------------------------------------------------
# 混合检索主链路
# ---------------------------------------------------------------------------


def test_hybrid_returns_topk_with_text_and_metadata(components):
    results = components.search("azure authentication", 5)
    assert 1 <= len(results) <= 5
    assert all(isinstance(r, RetrievalResult) for r in results)
    assert all(r.text for r in results)
    assert all("source_path" in r.metadata for r in results)
    # "azure authentication" 两路都应命中 doc_a_0000_h1（唯一同时含两词的 chunk）。
    assert results[0].chunk_id == "doc_a_0000_h1"


def test_hybrid_topk_limit_respected(components):
    assert len(components.search("azure", 2)) <= 2


def test_hybrid_topk_defaults_to_settings(tmp_path, settings, components):
    results = components.search("azure")  # top_k=None → retrieval.top_k_final=10
    assert len(results) <= settings.retrieval.top_k_final


def test_hybrid_sparse_only_keyword_still_found(components):
    """两路互补：纯关键词（BM25 强）也能经融合召回。"""
    results = components.search("bm25 inverted scoring", 5)
    assert results[0].chunk_id == "doc_b_0000_h3"


def test_invalid_topk_fails_fast(components):
    with pytest.raises(ValueError):
        components.search("azure", 0)
    with pytest.raises(ValueError):
        components.search("azure", 1.5)


def test_empty_query_fails_fast_via_processor(components):
    with pytest.raises(ValueError):
        components.search("   ")


# ---------------------------------------------------------------------------
# filters：Dense Pre-filter + Sparse Post-filter 兜底
# ---------------------------------------------------------------------------


def test_filters_collection_narrows_results(components):
    results = components.search("azure", 10, filters={"collection": "ml"})
    assert results
    assert all(r.metadata["collection"] == "ml" for r in results)
    assert {r.chunk_id for r in results} <= {"doc_b_0000_h3", "doc_b_0001_h4"}


def test_filters_exclude_all_returns_empty(components):
    assert components.search("azure", 10, filters={"collection": "nonexistent"}) == []


def test_filters_missing_metadata_field_lenient_include(components):
    """Post-filter 缺失字段宽松包含：无 doc_type 的候选不被误杀。"""
    results = components.search("azure", 10, filters={"language": "zh"})
    # 语料无 language 字段 → 全部宽松包含。
    assert results


def test_apply_metadata_filters_unit():
    candidates = [
        RetrievalResult(chunk_id="a", score=1.0, text="t", metadata={"collection": "x"}),
        RetrievalResult(chunk_id="b", score=0.9, text="t", metadata={}),
        RetrievalResult(chunk_id="c", score=0.8, text="t", metadata={"collection": "y"}),
    ]
    kept = HybridSearch._apply_metadata_filters(candidates, {"collection": "x"})
    assert [c.chunk_id for c in kept] == ["a", "b"]  # b 缺字段 → 宽松包含
    assert HybridSearch._apply_metadata_filters(candidates, None) == candidates


# ---------------------------------------------------------------------------
# 单路降级与双路失败
# ---------------------------------------------------------------------------


def test_dense_failure_degrades_to_sparse(tmp_path, settings, components):
    hybrid = HybridSearch(
        settings,
        query_processor=QueryProcessor(settings),
        dense_retriever=_ExplodingDenseRetriever(),
        sparse_retriever=components.sparse_retriever,
        fusion=RRFFusion(settings),
    )
    results = hybrid.search("bm25 scoring", 5)
    assert results
    assert results[0].chunk_id == "doc_b_0000_h3"
    assert list(hybrid.last_route_failures) == ["dense"]
    assert hybrid.last_dense_results == []
    assert hybrid.last_sparse_results


def test_sparse_failure_degrades_to_dense(tmp_path, settings, components):
    hybrid = HybridSearch(
        settings,
        query_processor=QueryProcessor(settings),
        dense_retriever=components.dense_retriever,
        sparse_retriever=_ExplodingSparseRetriever(),
        fusion=RRFFusion(settings),
    )
    results = hybrid.search("azure authentication", 5)
    assert results
    assert results[0].chunk_id == "doc_a_0000_h1"
    assert list(hybrid.last_route_failures) == ["sparse"]


def test_both_routes_fail_raise(tmp_path, settings):
    hybrid = HybridSearch(
        settings,
        query_processor=QueryProcessor(settings),
        dense_retriever=_ExplodingDenseRetriever(),
        sparse_retriever=_ExplodingSparseRetriever(),
        fusion=RRFFusion(settings),
    )
    with pytest.raises(HybridSearchError, match="both retrieval routes failed"):
        hybrid.search("anything", 5)


def test_single_route_degradation_keeps_route_scores(tmp_path, settings, components):
    """降级结果保留存活路原始分数与排序（不做单路 RRF 重打分）。"""
    hybrid = HybridSearch(
        settings,
        query_processor=QueryProcessor(settings),
        dense_retriever=components.dense_retriever,
        sparse_retriever=_ExplodingSparseRetriever(),
        fusion=RRFFusion(settings),
    )
    results = hybrid.search("azure", 10)
    dense_scores = [r.score for r in hybrid.last_dense_results]
    assert [r.score for r in results] == dense_scores[: len(results)]


def test_no_data_returns_empty(tmp_path, settings):
    """空库 + 空索引：两路均空（非失败）→ 友好空结果。"""
    persist = tmp_path / "empty_chroma"
    settings = replace(settings, vector_store=replace(settings.vector_store, persist_path=str(persist)))
    store = ChromaStore(settings.vector_store, collection="empty")
    indexer = BM25Indexer(index_path=tmp_path / "empty_bm25" / "index.json")
    hybrid = HybridSearch(
        settings,
        query_processor=QueryProcessor(settings),
        dense_retriever=DenseRetriever(settings, embedding_client=_KeywordEmbedding(), vector_store=store),
        sparse_retriever=SparseRetriever(settings, bm25_indexer=indexer, vector_store=store),
        fusion=RRFFusion(settings),
    )
    assert hybrid.search("azure", 5) == []
    assert hybrid.last_route_failures == {}
    shutil.rmtree(persist, ignore_errors=True)


# ---------------------------------------------------------------------------
# trace 阶段链与中间结果
# ---------------------------------------------------------------------------


def test_trace_contains_all_stages(components):
    trace = TraceContext()
    components.search("azure authentication", 5, trace=trace)
    stages = {s["stage"] for s in trace.stages}
    assert {
        "query_processing",
        "dense_retrieval",
        "sparse_retrieval",
        "fusion",
        "hybrid",
    } <= stages


def test_last_intermediate_results_populated(components):
    components.search("azure", 10)
    assert components.last_dense_results
    assert components.last_sparse_results
    assert components.last_fused_results
    assert components.last_route_failures == {}
    assert set(r.chunk_id for r in components.last_fused_results) >= {
        r.chunk_id for r in components.last_dense_results
    } | {r.chunk_id for r in components.last_sparse_results}


def test_hybrid_stage_details_recorded(components):
    trace = TraceContext()
    components.search("azure", 3, trace=trace)
    stage = trace.get_stage("hybrid")
    assert stage["method"] == "hybrid_search"
    assert stage["details"]["top_k"] == 3
    assert stage["details"]["degraded"] == []
    assert stage["details"]["routes"]["dense"] > 0
    assert stage["details"]["routes"]["sparse"] > 0
    assert "elapsed_ms" in stage["details"]
