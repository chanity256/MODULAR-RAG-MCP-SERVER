"""``DenseRetriever`` 单元测试（阶段 D2）。

纯离线：注入 fake embedding（记录调用、可控输出）与 fake vector store
（记录参数、可控返回），验证编排契约：

- 调用顺序与参数：``embed([query])`` → ``store.query(vector, top_k, filters)``。
- ``QueryResult → RetrievalResult`` 的字段映射（chunk_id/score/text/metadata）。
- filters 透传（Pre-filter 语义归 store）。
- fail-fast：非法 query/top_k/filters、embedding 单条输出契约破坏。
- store 异常原样传播（降级决策归 D5 HybridSearch）。
- trace 打点 ``dense_retrieval`` 阶段。
- ``RetrievalResult`` 序列化契约（D2 验收项）。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import Settings, load_settings
from core.trace.trace_context import TraceContext
from core.types import RetrievalResult
from libs.embedding.base_embedding import BaseEmbedding
from libs.vector_store.base_vector_store import BaseVectorStore, QueryResult
from core.query_engine.dense_retriever import DenseRetrievalError, DenseRetriever

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


class _FakeEmbedding(BaseEmbedding):
    """记录调用并返回可控向量序列的 fake。"""

    def __init__(self, vectors: list[list[float]] | None = None):
        self.calls: list[list[str]] = []
        self._vectors = vectors if vectors is not None else [[0.1, 0.2, 0.3]]

    @property
    def dimension(self) -> int | None:
        return len(self._vectors[0]) if self._vectors else None

    def embed(self, texts, *, trace=None, **kwargs):
        self.calls.append(list(texts))
        return [list(v) for v in self._vectors]


class _FakeStore(BaseVectorStore):
    """记录 query 参数并返回可控结果的 fake。"""

    def __init__(self, hits: list[QueryResult] | None = None, error: Exception | None = None):
        self.query_calls: list[dict] = []
        self._hits = hits or []
        self._error = error

    def upsert(self, records, *, trace=None, **kwargs):
        return 0

    def query(self, vector, top_k=10, *, filters=None, trace=None, **kwargs):
        self.query_calls.append(
            {"vector": list(vector), "top_k": top_k, "filters": dict(filters or {})}
        )
        if self._error is not None:
            raise self._error
        return list(self._hits)

    def get_by_ids(self, ids, *, trace=None, **kwargs):
        return []

    def delete_by_metadata(self, filters, *, trace=None, **kwargs):
        return 0

    def get_by_metadata(self, filters=None, *, trace=None, **kwargs):
        return []

    def get_collection_stats(self, *, trace=None, **kwargs):
        return {"name": "fake", "chunk_count": 0, "document_count": 0, "collections": []}


# ---------------------------------------------------------------------------
# 编排契约
# ---------------------------------------------------------------------------


def test_orchestrates_embed_then_query(tmp_path):
    embedding = _FakeEmbedding()
    store = _FakeStore()
    retriever = DenseRetriever(_settings(tmp_path), embedding_client=embedding, vector_store=store)
    results = retriever.retrieve("how to configure azure", 5)
    assert embedding.calls == [["how to configure azure"]]
    assert len(store.query_calls) == 1
    call = store.query_calls[0]
    assert call["vector"] == [0.1, 0.2, 0.3]
    assert call["top_k"] == 5
    assert call["filters"] == {}
    assert results == []


def test_maps_queryresult_to_retrievalresult(tmp_path):
    hits = [
        QueryResult(id="doc_0001_ab12", score=0.87, text="azure config", metadata={"page": 3}),
        QueryResult(id="doc_0002_cd34", score=0.42, text="other", metadata={}),
    ]
    retriever = DenseRetriever(
        _settings(tmp_path), embedding_client=_FakeEmbedding(), vector_store=_FakeStore(hits)
    )
    results = retriever.retrieve("azure", 2)
    assert len(results) == 2
    first = results[0]
    assert isinstance(first, RetrievalResult)
    assert first.chunk_id == "doc_0001_ab12"
    assert first.score == pytest.approx(0.87)
    assert first.text == "azure config"
    assert first.metadata == {"page": 3}
    assert results[1].chunk_id == "doc_0002_cd34"


def test_score_coerced_to_float(tmp_path):
    hits = [QueryResult(id="c1", score=1, text="t")]  # int score 也接受
    retriever = DenseRetriever(
        _settings(tmp_path), embedding_client=_FakeEmbedding(), vector_store=_FakeStore(hits)
    )
    results = retriever.retrieve("q", 1)
    assert isinstance(results[0].score, float)
    assert results[0].score == 1.0


def test_filters_passthrough_to_store(tmp_path):
    store = _FakeStore()
    retriever = DenseRetriever(
        _settings(tmp_path), embedding_client=_FakeEmbedding(), vector_store=store
    )
    retriever.retrieve("q", 3, filters={"collection": "docs", "doc_type": "pdf"})
    assert store.query_calls[0]["filters"] == {"collection": "docs", "doc_type": "pdf"}


def test_empty_store_returns_empty_list(tmp_path):
    retriever = DenseRetriever(
        _settings(tmp_path), embedding_client=_FakeEmbedding(), vector_store=_FakeStore()
    )
    assert retriever.retrieve("q", 10) == []


# ---------------------------------------------------------------------------
# fail-fast 契约
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_query", ["", "   ", "\n\t"])
def test_blank_query_fails_fast(tmp_path, bad_query):
    retriever = DenseRetriever(
        _settings(tmp_path), embedding_client=_FakeEmbedding(), vector_store=_FakeStore()
    )
    with pytest.raises(ValueError):
        retriever.retrieve(bad_query, 5)


def test_non_string_query_fails_fast(tmp_path):
    retriever = DenseRetriever(
        _settings(tmp_path), embedding_client=_FakeEmbedding(), vector_store=_FakeStore()
    )
    with pytest.raises(TypeError):
        retriever.retrieve(123, 5)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_top_k", [0, -1, 2.5, "3", None, True])
def test_invalid_top_k_fails_fast(tmp_path, bad_top_k):
    retriever = DenseRetriever(
        _settings(tmp_path), embedding_client=_FakeEmbedding(), vector_store=_FakeStore()
    )
    with pytest.raises(ValueError):
        retriever.retrieve("q", bad_top_k)


def test_non_dict_filters_fails_fast(tmp_path):
    retriever = DenseRetriever(
        _settings(tmp_path), embedding_client=_FakeEmbedding(), vector_store=_FakeStore()
    )
    with pytest.raises(TypeError):
        retriever.retrieve("q", 5, filters=["collection"])  # type: ignore[arg-type]


def test_embedding_multiple_vectors_raises_contract_error(tmp_path):
    embedding = _FakeEmbedding(vectors=[[0.1], [0.2]])  # 单条 query 返回 2 个向量
    retriever = DenseRetriever(
        _settings(tmp_path), embedding_client=embedding, vector_store=_FakeStore()
    )
    with pytest.raises(DenseRetrievalError, match="2 vectors"):
        retriever.retrieve("q", 5)


def test_embedding_empty_vector_raises_contract_error(tmp_path):
    embedding = _FakeEmbedding(vectors=[[]])
    retriever = DenseRetriever(
        _settings(tmp_path), embedding_client=embedding, vector_store=_FakeStore()
    )
    with pytest.raises(DenseRetrievalError, match="empty vector"):
        retriever.retrieve("q", 5)


def test_store_error_propagates(tmp_path):
    store = _FakeStore(error=RuntimeError("chroma down"))
    retriever = DenseRetriever(
        _settings(tmp_path), embedding_client=_FakeEmbedding(), vector_store=store
    )
    with pytest.raises(RuntimeError, match="chroma down"):
        retriever.retrieve("q", 5)


# ---------------------------------------------------------------------------
# trace 打点
# ---------------------------------------------------------------------------


def test_records_trace_stage(tmp_path):
    hits = [QueryResult(id="c1", score=0.5, text="t", metadata={"k": "v"})]
    retriever = DenseRetriever(
        _settings(tmp_path), embedding_client=_FakeEmbedding(), vector_store=_FakeStore(hits)
    )
    trace = TraceContext()
    retriever.retrieve("q", 4, filters={"collection": "x"}, trace=trace)
    stage = trace.get_stage("dense_retrieval")
    assert stage is not None
    assert stage["method"] == "dense_retriever"
    assert stage["details"]["top_k"] == 4
    assert stage["details"]["hits"] == 1
    assert stage["details"]["filter_keys"] == ["collection"]
    assert "elapsed_ms" in stage["details"]


def test_no_trace_no_error(tmp_path):
    retriever = DenseRetriever(
        _settings(tmp_path), embedding_client=_FakeEmbedding(), vector_store=_FakeStore()
    )
    assert retriever.retrieve("q", 1) == []


# ---------------------------------------------------------------------------
# RetrievalResult 契约（D2 前置任务验收项）
# ---------------------------------------------------------------------------


def test_retrievalresult_serialization_roundtrip():
    result = RetrievalResult(
        chunk_id="doc_0001_ab12cd", score=0.9123, text="azure 配置", metadata={"page": 2}
    )
    assert result.to_dict() == {
        "chunk_id": "doc_0001_ab12cd",
        "score": 0.9123,
        "text": "azure 配置",
        "metadata": {"page": 2},
    }
    cloned = RetrievalResult.from_dict(result.to_dict())
    assert cloned == result
    assert RetrievalResult.from_json(result.to_json()) == result


def test_retrievalresult_from_dict_ignores_unknown_fields():
    cloned = RetrievalResult.from_dict(
        {
            "chunk_id": "c1",
            "score": 1,
            "text": "t",
            "metadata": {},
            "future_field": "ignored",
        }
    )
    assert cloned.chunk_id == "c1"
    assert cloned.score == 1.0


@pytest.mark.parametrize(
    "kwargs, exc",
    [
        ({"chunk_id": "", "score": 1.0, "text": "t"}, ValueError),
        ({"chunk_id": "c", "score": "high", "text": "t"}, TypeError),
        ({"chunk_id": "c", "score": True, "text": "t"}, TypeError),
        ({"chunk_id": "c", "score": 1.0, "text": 5}, TypeError),
        ({"chunk_id": "c", "score": 1.0, "text": "t", "metadata": []}, TypeError),
    ],
)
def test_retrievalresult_construction_validation(kwargs, exc):
    with pytest.raises(exc):
        RetrievalResult(**kwargs)


def test_retrievalresult_exported_from_core_package():
    import core

    assert core.RetrievalResult is RetrievalResult


def test_default_construction_uses_factories(tmp_path):
    """未注入组件时经工厂创建（fake provider 注册验证路由）。"""
    from libs.embedding.embedding_factory import EmbeddingFactory
    from libs.vector_store.vector_store_factory import VectorStoreFactory

    saved_emb = dict(EmbeddingFactory._registry)
    saved_vs = dict(VectorStoreFactory._registry)
    EmbeddingFactory._registry.clear()
    VectorStoreFactory._registry.clear()
    try:
        EmbeddingFactory.register("openai")(lambda cfg, **kw: _FakeEmbedding())

        class _CtorStore(_FakeStore):
            def __init__(self, vs_config, **kwargs):
                super().__init__()

        VectorStoreFactory.register("chroma")(_CtorStore)
        retriever = DenseRetriever(_settings(tmp_path))
        assert isinstance(retriever.embedding_client, _FakeEmbedding)
        assert isinstance(retriever.vector_store, _CtorStore)
    finally:
        EmbeddingFactory._registry.clear()
        EmbeddingFactory._registry.update(saved_emb)
        VectorStoreFactory._registry.clear()
        VectorStoreFactory._registry.update(saved_vs)
