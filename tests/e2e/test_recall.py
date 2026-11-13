"""Recall 回归测试（阶段 H5，E2E）。

离线真实组件：真 ``ChromaStore`` + 真 ``BM25Indexer`` + 真
``QueryProcessor``/``RRFFusion``/``HybridSearch`` + 真 ``EvalRunner``，
唯一注入确定性 bag-of-words embedding（免除网络）。

对 golden test set 断言最小召回阈值（阈值写死在测试里，便于回归）：
- ``hit_at_k >= 0.8``（top_k=5）
- ``mrr >= 0.5``
- 全部 case 无错误
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.query_engine.dense_retriever import DenseRetriever
from core.query_engine.fusion import RRFFusion
from core.query_engine.hybrid_search import HybridSearch
from core.query_engine.query_processor import QueryProcessor
from core.query_engine.sparse_retriever import SparseRetriever
from core.settings import load_settings
from ingestion.embedding import SparseDocStats
from ingestion.embedding.sparse_encoder import default_tokenize
from libs.embedding.base_embedding import BaseEmbedding
from libs.evaluator.custom_evaluator import CustomEvaluator
from libs.vector_store.base_vector_store import VectorRecord
from libs.vector_store.chroma_store import ChromaStore
from observability.evaluation.eval_runner import EvalRunner
from ingestion.storage import BM25Indexer

# 回归阈值（spec H5：写死在测试里）。
HIT_AT_K_THRESHOLD = 0.8
MRR_THRESHOLD = 0.5
TOP_K = 5

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

#: 测试语料：chunk_id -> (text, metadata)。
_CORPUS = {
    "doc_cfg_0000_a1": (
        "azure openai configuration endpoint and api key setup",
        {"collection": "docs", "source_path": "azure_setup.pdf", "page": 1},
    ),
    "doc_cfg_0001_b2": (
        "azure active directory authentication single sign on",
        {"collection": "docs", "source_path": "azure_setup.pdf", "page": 2},
    ),
    "doc_bm2_0000_c3": (
        "bm25 inverted index scoring term frequency inverse document frequency",
        {"collection": "docs", "source_path": "bm25_guide.pdf", "page": 1},
    ),
    "doc_bm2_0001_d4": (
        "sparse retrieval keyword matching ranking algorithm",
        {"collection": "docs", "source_path": "bm25_guide.pdf", "page": 4},
    ),
    "doc_ing_0000_e5": (
        "pdf ingestion pipeline chunking embedding vector upsert",
        {"collection": "docs", "source_path": "ingest_guide.pdf", "page": 1},
    ),
    "doc_ing_0001_f6": (
        "file integrity sha256 incremental skip re ingestion",
        {"collection": "docs", "source_path": "ingest_guide.pdf", "page": 7},
    ),
}

_VOCAB = sorted(
    {token for text, _ in _CORPUS.values() for token in default_tokenize(text)}
)
_VOCAB_SET = frozenset(_VOCAB)


class _KeywordEmbedding(BaseEmbedding):
    """确定性 bag-of-words embedding（同 test_hybrid_search.py 手法）。"""

    @property
    def dimension(self) -> int | None:
        return len(_VOCAB)

    def embed(self, texts, *, trace=None, **kwargs):
        vectors = []
        for text in texts:
            counts = Counter(t for t in default_tokenize(text) if t in _VOCAB_SET)
            vectors.append([float(counts.get(token, 0)) for token in _VOCAB])
        return vectors


@pytest.fixture
def eval_stack(tmp_path: Path):
    settings = load_settings(_write_settings(tmp_path))
    persist = tmp_path / "chroma_db"
    settings = replace(
        settings, vector_store=replace(settings.vector_store, persist_path=str(persist))
    )
    store = ChromaStore(settings.vector_store, collection="recall_e2e")
    indexer = BM25Indexer(index_path=tmp_path / "bm25" / "index.json")

    records, stats = [], []
    for chunk_id, (text, metadata) in _CORPUS.items():
        records.append(
            VectorRecord(id=chunk_id, vector=_KeywordEmbedding().embed([text])[0], text=text, metadata=dict(metadata))
        )
        tokens = default_tokenize(text)
        stats.append(
            SparseDocStats(chunk_id=chunk_id, term_freqs=dict(Counter(tokens)), length=len(tokens))
        )
    store.upsert(records)
    indexer.build(stats)

    hybrid = HybridSearch(
        settings,
        query_processor=QueryProcessor(settings),
        dense_retriever=DenseRetriever(settings, embedding_client=_KeywordEmbedding(), vector_store=store),
        sparse_retriever=SparseRetriever(settings, bm25_indexer=indexer, vector_store=store),
        fusion=RRFFusion(settings),
    )
    runner = EvalRunner(settings, hybrid_search=hybrid, evaluator=CustomEvaluator())
    yield runner
    shutil.rmtree(persist, ignore_errors=True)


def _write_settings(tmp_path: Path) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    return path


def _write_golden_set(tmp_path: Path) -> Path:
    cases = [
        {
            "query": "how to configure azure openai endpoint",
            "expected_chunk_ids": ["doc_cfg_0000_a1"],
            "expected_sources": ["azure_setup.pdf"],
        },
        {
            "query": "bm25 scoring formula",
            "expected_chunk_ids": ["doc_bm2_0000_c3"],
            "expected_sources": ["bm25_guide.pdf"],
        },
        {
            "query": "azure authentication",
            "expected_chunk_ids": ["doc_cfg_0001_b2"],
            "expected_sources": ["azure_setup.pdf"],
        },
        {
            "query": "incremental ingestion file hash",
            "expected_chunk_ids": ["doc_ing_0001_f6"],
            "expected_sources": ["ingest_guide.pdf"],
        },
        {
            "query": "chunking and embedding pipeline",
            "expected_chunk_ids": ["doc_ing_0000_e5"],
            "expected_sources": ["ingest_guide.pdf"],
        },
    ]
    path = tmp_path / "golden_test_set.json"
    path.write_text(json.dumps({"test_cases": cases}, indent=2), encoding="utf-8")
    return path


def test_recall_hits_threshold(eval_stack, tmp_path):
    """golden set 全量回归：hit@5 与 mrr 达到写死阈值。"""
    report = eval_stack.run(_write_golden_set(tmp_path), top_k=TOP_K)

    assert report.failed == 0, [q.error for q in report.per_query if q.error]
    assert report.evaluated == report.total == 5
    assert report.metrics["hit_at_k"] >= HIT_AT_K_THRESHOLD, report.metrics
    assert report.metrics["mrr"] >= MRR_THRESHOLD, report.metrics


def test_recall_report_json_serializable(eval_stack, tmp_path):
    report = eval_stack.run(_write_golden_set(tmp_path), top_k=TOP_K)
    assert json.loads(json.dumps(report.to_dict()))["total"] == 5
