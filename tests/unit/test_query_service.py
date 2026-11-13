"""QueryService 单测（WC1.6）：fake 检索/精排，零网络、零真实模型。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.trace.trace_context import TRACE_TYPE_QUERY, TraceContext
from core.types import RetrievalResult
from observability.dashboard.services.query_service import QueryService


def _result(chunk_id: str, score: float = 0.5) -> RetrievalResult:
    return RetrievalResult(chunk_id=chunk_id, score=score, text=f"text {chunk_id}", metadata={})


class FakeSearch:
    def __init__(self, results=None, error: Exception | None = None):
        self.results = results if results is not None else [_result("a"), _result("b")]
        self.error = error
        self.calls: list[dict] = []

    def search(self, query, top_k=None, filters=None, *, trace=None):
        self.calls.append({"query": query, "top_k": top_k, "filters": filters, "trace": trace})
        if self.error is not None:
            raise self.error
        return list(self.results)


class FakeReranker:
    def __init__(self, reorder=True):
        self.calls: list[dict] = []
        self.reorder = reorder

    def rerank(self, query, results, *, trace=None):
        self.calls.append({"query": query, "results": list(results), "trace": trace})
        return list(reversed(results)) if self.reorder else list(results)


class TestSearch:
    def test_returns_retrieval_results_from_hybrid(self):
        search = FakeSearch()
        service = QueryService(search=search, rerank_enabled=False)
        results = service.search("what is rrf")
        assert [r.chunk_id for r in results] == ["a", "b"]
        assert all(isinstance(r, RetrievalResult) for r in results)
        assert search.calls[0]["query"] == "what is rrf"

    def test_top_k_and_collection_passed_as_filter(self):
        search = FakeSearch()
        QueryService(search=search, rerank_enabled=False).search("q", collection="docs", top_k=3)
        assert search.calls[0]["top_k"] == 3
        assert search.calls[0]["filters"] == {"collection": "docs"}

    def test_defaults_pass_none_top_k_and_no_filters(self):
        search = FakeSearch()
        QueryService(search=search, rerank_enabled=False).search("q")
        assert search.calls[0]["top_k"] is None
        assert search.calls[0]["filters"] is None

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_collection_means_no_filter(self, blank):
        search = FakeSearch()
        QueryService(search=search, rerank_enabled=False).search("q", collection=blank)
        assert search.calls[0]["filters"] is None

    def test_collection_is_stripped(self):
        search = FakeSearch()
        QueryService(search=search, rerank_enabled=False).search("q", collection=" docs ")
        assert search.calls[0]["filters"] == {"collection": "docs"}

    @pytest.mark.parametrize("bad", [0, -2, True, 1.5, "3"])
    def test_invalid_top_k_rejected(self, bad):
        search = FakeSearch()
        with pytest.raises(ValueError, match="top_k"):
            QueryService(search=search).search("q", top_k=bad)
        assert search.calls == []

    @pytest.mark.parametrize("bad", ["", "  \n"])
    def test_blank_query_rejected(self, bad):
        search = FakeSearch()
        with pytest.raises(ValueError, match="query"):
            QueryService(search=search).search(bad)
        assert search.calls == []

    def test_non_string_query_rejected(self):
        with pytest.raises(TypeError):
            QueryService(search=FakeSearch()).search(None)  # type: ignore[arg-type]

    def test_downstream_error_propagates(self):
        service = QueryService(search=FakeSearch(error=RuntimeError("both routes down")))
        with pytest.raises(RuntimeError, match="both routes down"):
            service.search("q")


class TestRerank:
    def test_rerank_applied_by_default(self):
        reranker = FakeReranker()
        service = QueryService(search=FakeSearch(), reranker=reranker)
        results = service.search("q")
        assert [r.chunk_id for r in results] == ["b", "a"]
        assert reranker.calls[0]["query"] == "q"
        assert [r.chunk_id for r in reranker.calls[0]["results"]] == ["a", "b"]

    def test_rerank_disabled_skips_reranker(self):
        reranker = FakeReranker()
        service = QueryService(search=FakeSearch(), reranker=reranker, rerank_enabled=False)
        results = service.search("q")
        assert [r.chunk_id for r in results] == ["a", "b"]
        assert reranker.calls == []
        assert service.rerank_enabled is False

    def test_empty_results_skip_rerank_and_return_empty_list(self):
        reranker = FakeReranker()
        service = QueryService(search=FakeSearch(results=[]), reranker=reranker)
        assert service.search("q") == []
        assert reranker.calls == []

    def test_reranker_not_built_when_disabled(self, monkeypatch):
        import core.query_engine as query_engine

        def boom(*args, **kwargs):
            raise AssertionError("Reranker must not be constructed")

        monkeypatch.setattr(query_engine, "Reranker", boom)
        QueryService(search=FakeSearch(), rerank_enabled=False).search("q")

    def test_reranker_built_lazily_from_settings(self, monkeypatch):
        import core.query_engine as query_engine

        built: list = []
        fake = FakeReranker()

        def factory(settings):
            built.append(settings)
            return fake

        monkeypatch.setattr(query_engine, "Reranker", factory)
        sentinel = object()
        service = QueryService(sentinel, search=FakeSearch())
        assert built == []
        service.search("q")
        service.search("q")
        assert built == [sentinel]
        assert len(fake.calls) == 2

    def test_search_built_lazily_from_settings(self, monkeypatch):
        import core.query_engine as query_engine

        built: list = []
        fake = FakeSearch()

        def factory(settings):
            built.append(settings)
            return fake

        monkeypatch.setattr(query_engine, "HybridSearch", factory)
        sentinel = object()
        service = QueryService(sentinel, rerank_enabled=False)
        assert built == []
        service.search("q")
        assert built == [sentinel]


class TestTrace:
    def test_trace_passed_to_search_and_rerank_unchanged(self):
        trace = TraceContext(trace_type=TRACE_TYPE_QUERY)
        search, reranker = FakeSearch(), FakeReranker()
        QueryService(search=search, reranker=reranker).search("q", trace=trace)
        assert search.calls[0]["trace"] is trace
        assert reranker.calls[0]["trace"] is trace

    def test_no_trace_means_none_passed_downstream(self):
        search, reranker = FakeSearch(), FakeReranker()
        QueryService(search=search, reranker=reranker).search("q")
        assert search.calls[0]["trace"] is None
        assert reranker.calls[0]["trace"] is None

    def test_service_never_finishes_or_persists_trace(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import core.trace.persist as persist_module

        persisted: list = []
        monkeypatch.setattr(persist_module, "persist_trace", lambda *a, **k: persisted.append(a))

        trace = TraceContext(trace_type=TRACE_TYPE_QUERY)
        QueryService(search=FakeSearch(), reranker=FakeReranker()).search("q", trace=trace)

        assert persisted == []
        assert trace.total_elapsed_ms is None
        assert not list(Path(tmp_path).rglob("traces.jsonl"))

    def test_service_source_never_references_trace_persistence(self):
        import observability.dashboard.services.query_service as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "persist_trace" not in source
        assert "TraceContext(" not in source
        assert ".finish(" not in source
