"""``scripts/query.py`` CLI 入口单元测试（阶段 D7）。

纯离线：经 importlib 按路径加载 scripts/query.py（scripts 非 package），
断言基于可导入纯函数（build_parser/parse_args/run_query/
render_result_line/render_no_results/render_verbose_sections/main），
search/reranker 用可控 fake，零网络、零子进程、零真实持久化；
另含一条真组件（Chroma+BM25+确定性 embedding）注入 main 的离线链路用例。

覆盖：
- 参数解析：--query 必填、--top-k/--collection/--verbose/--no-rerank/--config 默认值。
- run_query：filters 透传（collection）、无 reranker 跳过精排、
  reranker 应用与 fallback 标记、verbose 各阶段段头。
- 渲染：结果行（score/source/page/单行摘要/截断省略号）、无结果友好提示。
- main 退出码：0 查询完成（含无命中）；1 检索链路失败；
  2 用法/配置错误（空查询、配置缺失、非法参数）。
- 真 HybridSearch 注入的端到端离线链路（--no-rerank）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from core.query_engine.hybrid_search import HybridSearchError
from core.query_engine.query_processor import QueryProcessingError
from core.types import RetrievalResult

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "query.py"
_MODULE_NAME = "scripts_query"

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
      top_k_final: 10
    rerank:
      backend: none
    evaluation:
      backends: [custom]
    """
)


def _load_cli():
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


# ---------------------------------------------------------------------------
# fake 查询栈
# ---------------------------------------------------------------------------


class FakeSearch:
    """具备 ``search(query, top_k, filters, trace=...)`` 签法的可控 fake。"""

    def __init__(self, results=None, *, dense=None, sparse=None, error=None):
        self.calls: list[dict] = []
        self._results = results or []
        self.last_dense_results = dense or []
        self.last_sparse_results = sparse or []
        self.last_fused_results = list(self._results)
        self._error = error

    def search(self, query, top_k=None, filters=None, *, trace=None):
        self.calls.append(
            {"query": query, "top_k": top_k, "filters": dict(filters or {}), "traced": trace is not None}
        )
        if self._error is not None:
            raise self._error
        return list(self._results)


class FakeReranker:
    """可控精排 fake：按指定顺序重排或触发回退。"""

    def __init__(self, *, order=None, fallback=False):
        self.backend_name = "fake_backend"
        self.last_fallback = False
        self.calls: list[list[str]] = []
        self._order = order
        self._fallback = fallback

    def rerank(self, query, results, *, trace=None):
        self.last_fallback = self._fallback
        self.calls.append([r.chunk_id for r in results])
        if self._fallback:
            return list(results)  # 回退：原序
        if self._order is not None:
            by_id = {r.chunk_id: r for r in results}
            return [by_id[cid] for cid in self._order if cid in by_id]
        return list(results)


def _result(chunk_id: str, score: float, text: str = "", metadata: dict | None = None) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id, score=score, text=text or f"text {chunk_id}", metadata=metadata or {}
    )


@pytest.fixture
def lines():
    collected: list[str] = []
    return collected


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def test_parse_args_defaults():
    args = cli.parse_args(["--query", "azure 配置"])
    assert args.query == "azure 配置"
    assert args.top_k is None
    assert args.collection is None
    assert args.verbose is False
    assert args.no_rerank is False
    assert args.config.endswith("settings.yaml")


def test_parse_args_all_flags():
    args = cli.parse_args(
        ["--query", "q", "--top-k", "5", "--collection", "docs", "--verbose", "--no-rerank"]
    )
    assert args.top_k == 5
    assert args.collection == "docs"
    assert args.verbose is True
    assert args.no_rerank is True


def test_missing_query_exits_with_usage_code():
    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args([])
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# run_query 行为
# ---------------------------------------------------------------------------


def test_run_query_renders_topk_results(lines):
    search = FakeSearch(
        [
            _result("c1", 0.9123, "azure ad 配置", {"source_path": "docs/a.pdf", "page": 3}),
            _result("c2", 0.4, "bm25", {"source_path": "docs/b.pdf"}),
        ]
    )
    outcome = cli.run_query(search, None, "azure", out=lines.append)
    assert outcome.rerank_applied is False
    assert len(outcome.results) == 2
    joined = "\n".join(lines)
    assert "[1] score=0.9123  source=docs/a.pdf  page=3" in joined
    assert "[2] score=0.4000  source=docs/b.pdf" in joined
    assert "azure ad 配置" in joined


def test_run_query_collection_filter_passed(lines):
    search = FakeSearch([_result("c1", 0.5)])
    cli.run_query(search, None, "q", collection="docs", out=lines.append)
    assert search.calls[0]["filters"] == {"collection": "docs"}
    assert "collection='docs'" in "\n".join(lines)


def test_run_query_no_collection_no_filters():
    search = FakeSearch([_result("c1", 0.5)])
    cli.run_query(search, None, "q")
    assert search.calls[0]["filters"] == {}


def test_run_query_topk_passthrough():
    search = FakeSearch([_result("c1", 0.5)])
    cli.run_query(search, None, "q", 7)
    assert search.calls[0]["top_k"] == 7


def test_run_query_no_results_friendly_hint(lines):
    search = FakeSearch([])
    outcome = cli.run_query(search, None, "q", out=lines.append)
    joined = "\n".join(lines)
    assert "未找到相关文档" in joined
    assert "scripts/ingest.py" in joined
    assert outcome.results == []
    assert outcome.rerank_applied is False


def test_run_query_applies_reranker(lines):
    search = FakeSearch([_result("c1", 0.5), _result("c2", 0.4)])
    reranker = FakeReranker(order=["c2", "c1"])
    outcome = cli.run_query(search, reranker, "q", out=lines.append)
    assert reranker.calls == [["c1", "c2"]]
    assert [r.chunk_id for r in outcome.results] == ["c2", "c1"]
    assert outcome.rerank_applied is True
    assert outcome.rerank_backend == "fake_backend"
    assert outcome.rerank_fallback is False


def test_run_query_reranker_fallback_flagged(lines):
    search = FakeSearch([_result("c1", 0.5)])
    reranker = FakeReranker(fallback=True)
    outcome = cli.run_query(search, reranker, "q", out=lines.append)
    assert outcome.rerank_fallback is True
    assert [r.chunk_id for r in outcome.results] == ["c1"]  # 原序保留


def test_run_query_empty_results_skips_reranker():
    search = FakeSearch([])
    reranker = FakeReranker()
    cli.run_query(search, reranker, "q")
    assert reranker.calls == []


def test_run_query_verbose_sections(lines):
    dense = [_result("d1", 0.9)]
    sparse = [_result("s1", 3.2)]
    fused = [_result("d1", 0.03), _result("s1", 0.02)]
    search = FakeSearch([fused[0]], dense=dense, sparse=sparse)
    search.last_fused_results = fused
    reranker = FakeReranker()
    cli.run_query(search, reranker, "q", verbose=True, out=lines.append)
    joined = "\n".join(lines)
    assert "--- dense recall (1 hits) ---" in joined
    assert "--- sparse recall (1 hits) ---" in joined
    assert "--- fusion (2 fused) ---" in joined
    assert "--- rerank (backend=fake_backend, fallback=false) ---" in joined
    assert "d1" in joined and "s1" in joined


def test_run_query_verbose_no_rerank_shows_skipped(lines):
    search = FakeSearch([], dense=[], sparse=[])
    cli.run_query(search, None, "q", verbose=True, out=lines.append)
    assert "--- rerank (skipped) ---" in "\n".join(lines)


# ---------------------------------------------------------------------------
# 渲染细节
# ---------------------------------------------------------------------------


def test_render_result_line_folds_whitespace_and_truncates():
    long_text = "word " * 100
    result = _result("c1", 0.5, long_text)
    line = cli.render_result_line(1, result, excerpt_chars=50)
    body = line.split("\n")[1].strip()
    assert len(body) <= 50
    assert body.endswith("…")
    assert "\n" not in body


def test_render_result_line_missing_source_and_page():
    line = cli.render_result_line(2, _result("c1", 0.25))
    assert "[2] score=0.2500  source=-" in line
    assert "page" not in line


def test_outcome_to_dict():
    search = FakeSearch([_result("c1", 0.5)], dense=[_result("c1", 0.9)], sparse=[_result("c1", 2.0)])
    outcome = cli.run_query(search, None, "q")
    payload = outcome.to_dict()
    assert payload["query"] == "q"
    assert payload["results"][0]["chunk_id"] == "c1"
    assert payload["dense_ids"] == ["c1"]
    assert payload["rerank_applied"] is False
    assert payload["trace_id"]


# ---------------------------------------------------------------------------
# main 退出码与编排
# ---------------------------------------------------------------------------


def test_main_ok_with_injected_stack(lines):
    search = FakeSearch([_result("c1", 0.9, "命中", {"source_path": "a.pdf"})])
    code = cli.main(["--query", "q", "--no-rerank"], search=search, out=lines.append)
    assert code == 0
    assert "命中" in "\n".join(lines)


def test_main_no_results_is_ok_with_hint(lines):
    search = FakeSearch([])
    code = cli.main(["--query", "q", "--no-rerank"], search=search, out=lines.append)
    assert code == 0
    assert "未找到相关文档" in "\n".join(lines)


def test_main_builds_default_reranker_from_config(tmp_path, lines):
    """注入 search、未注入 reranker 且未 --no-rerank：按 --config 构建默认精排器。"""
    config = tmp_path / "settings.yaml"
    config.write_text(_VALID_YAML, encoding="utf-8")
    search = FakeSearch([_result("c1", 0.5)])
    code = cli.main(
        ["--query", "q", "--config", str(config)], search=search, out=lines.append
    )
    assert code == 0
    # backend=none → NoneReranker 恒等，结果顺序不变。


def test_main_invalid_query_returns_usage(lines):
    search = FakeSearch(error=QueryProcessingError("query must be a non-empty"))
    code = cli.main(["--query", "   ", "--no-rerank"], search=search, out=lines.append)
    assert code == 2
    assert "查询无效" in "\n".join(lines)


def test_main_bad_argument_returns_usage(lines):
    search = FakeSearch(error=ValueError("top_k must be positive"))
    code = cli.main(["--query", "q", "--top-k", "0", "--no-rerank"], search=search, out=lines.append)
    assert code == 2


def test_main_search_failure_returns_failure(lines):
    search = FakeSearch(error=HybridSearchError("both retrieval routes failed"))
    code = cli.main(["--query", "q", "--no-rerank"], search=search, out=lines.append)
    assert code == 1
    assert "检索失败" in "\n".join(lines)


def test_main_missing_config_returns_usage(tmp_path, lines):
    code = cli.main(
        ["--query", "q", "--config", str(tmp_path / "missing.yaml"), "--no-rerank"],
        out=lines.append,
    )
    assert code == 2
    assert "配置加载失败" in "\n".join(lines)


def test_main_no_rerank_skips_reranker_construction(tmp_path, monkeypatch, lines):
    """--no-rerank：不加载配置构建精排器（注入 search 时零额外 IO）。"""
    config = tmp_path / "settings.yaml"
    config.write_text(_VALID_YAML, encoding="utf-8")
    search = FakeSearch([_result("c1", 0.5)])

    def _boom_settings(path):
        raise AssertionError("settings must not be loaded when fully injected")

    import core.settings as settings_module

    monkeypatch.setattr(settings_module, "load_settings", _boom_settings)
    code = cli.main(
        ["--query", "q", "--no-rerank"], search=search, reranker=None, out=lines.append
    )
    assert code == 0


# ---------------------------------------------------------------------------
# 真组件离线链路（注入 main 的端到端验证）
# ---------------------------------------------------------------------------


def test_main_real_hybrid_stack_offline(tmp_path, lines):
    """真 ChromaStore + 真 BM25Indexer + 确定性 embedding 注入 main，全离线。"""
    import shutil
    from collections import Counter
    from dataclasses import replace

    from core.query_engine import DenseRetriever, HybridSearch, RRFFusion, SparseRetriever
    from core.settings import load_settings
    from ingestion.embedding import SparseDocStats
    from ingestion.embedding.sparse_encoder import default_tokenize
    from ingestion.storage import BM25Indexer
    from libs.embedding.base_embedding import BaseEmbedding
    from libs.vector_store.base_vector_store import VectorRecord
    from libs.vector_store.chroma_store import ChromaStore

    config = tmp_path / "settings.yaml"
    config.write_text(_VALID_YAML, encoding="utf-8")
    settings = load_settings(config)
    persist = tmp_path / "chroma_db"
    settings = replace(
        settings, vector_store=replace(settings.vector_store, persist_path=str(persist))
    )

    corpus = {
        "doc_a_0000_z1": ("azure active directory authentication", {"source_path": "a.pdf", "page": 1, "collection": "docs"}),
        "doc_b_0000_z2": ("bm25 sparse retrieval scoring", {"source_path": "b.pdf", "page": 2, "collection": "docs"}),
    }
    vocab = sorted({t for text, _ in corpus.values() for t in default_tokenize(text)})
    vocab_set = frozenset(vocab)

    class _KeywordEmbedding(BaseEmbedding):
        @property
        def dimension(self):
            return len(vocab)

        def embed(self, texts, *, trace=None, **kwargs):
            vectors = []
            for text in texts:
                counts = Counter(t for t in default_tokenize(text) if t in vocab_set)
                vectors.append([float(counts.get(t, 0)) for t in vocab])
            return vectors

    store = ChromaStore(settings.vector_store, collection="query_cli_test")
    indexer = BM25Indexer(index_path=tmp_path / "bm25" / "index.json")
    for chunk_id, (text, metadata) in corpus.items():
        store.upsert([VectorRecord(id=chunk_id, vector=_KeywordEmbedding().embed([text])[0], text=text, metadata=metadata)])
    stats = [
        SparseDocStats(
            chunk_id=cid,
            term_freqs=dict(Counter(default_tokenize(text))),
            length=len(default_tokenize(text)),
        )
        for cid, (text, _) in corpus.items()
    ]
    indexer.build(stats)

    hybrid = HybridSearch(
        settings,
        dense_retriever=DenseRetriever(settings, embedding_client=_KeywordEmbedding(), vector_store=store),
        sparse_retriever=SparseRetriever(settings, bm25_indexer=indexer, vector_store=store),
        fusion=RRFFusion(settings),
    )
    code = cli.main(
        ["--query", "azure authentication", "--no-rerank"], search=hybrid, out=lines.append
    )
    assert code == 0
    joined = "\n".join(lines)
    assert "[1]" in joined
    assert "a.pdf" in joined  # Top-1 命中 azure 主题文档
    shutil.rmtree(persist, ignore_errors=True)
