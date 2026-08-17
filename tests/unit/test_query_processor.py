"""``core.query_engine.QueryProcessor`` 契约测试（阶段 D1）。

覆盖：
- 关键词提取：拉丁词小写化、CJK 逐字、停用词过滤、保序去重、确定性；
- 非空保证：全停用词查询回退原始 token；空/纯空白/纯标点查询 fail-fast；
- filters 契约：``None → {}``、dict 校验、浅拷贝透传（恒为 dict）；
- ``ProcessedQuery`` 序列化往返与未知字段容忍；
- trace 打点（``query_processing`` 阶段）；
- 分词与 C9 ``SparseEncoder.default_tokenize`` 的 drift guard；
- C9→C11 链路：提取的 keywords 可直接命中 BM25 索引。
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from core.query_engine import (
    DEFAULT_STOPWORDS,
    QueryProcessingError,
    QueryProcessor,
    default_tokenize as qp_tokenize,
)
from core.settings import load_settings
from core.trace.trace_context import TraceContext
from core.types import ProcessedQuery
from ingestion.embedding import SparseEncoder
from ingestion.embedding.sparse_encoder import default_tokenize as sparse_tokenize
from ingestion.storage import BM25Indexer
from core.types import Chunk

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
    retrieval:
      sparse_backend: bm25
      fusion_algorithm: rrf
    rerank:
      backend: none
    evaluation:
      backends: [custom]
    """
)


def _settings(tmp_path: Path):
    path = Path(tmp_path) / "settings.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_VALID_YAML, encoding="utf-8")
    return load_settings(path)


# ---------------------------------------------------------------------------
# 关键词提取
# ---------------------------------------------------------------------------


def test_english_keywords_stopwords_removed(tmp_path):
    processed = QueryProcessor(_settings(tmp_path)).process("How do I configure Azure OpenAI?")
    assert processed.keywords == ["configure", "azure", "openai"]


def test_latin_words_lowercased(tmp_path):
    processed = QueryProcessor(_settings(tmp_path)).process("Vector STORE Retrieval")
    assert processed.keywords == ["vector", "store", "retrieval"]


def test_cjk_char_by_char_with_function_chars_stopped(tmp_path):
    processed = QueryProcessor(_settings(tmp_path)).process("如何配置向量检索")
    assert processed.keywords == ["配", "置", "向", "量", "检", "索"]


def test_mixed_query_keywords(tmp_path):
    # token 顺序对齐 C9：拉丁词全部在前，CJK 逐字在后（drift guard 保证一致）。
    processed = QueryProcessor(_settings(tmp_path)).process("RAG 检索增强与 GPT-4o")
    assert processed.keywords == ["rag", "gpt", "4o", "检", "索", "增", "强"]


def test_dedup_preserves_first_occurrence_order(tmp_path):
    processed = QueryProcessor(_settings(tmp_path)).process("store store vector store")
    assert processed.keywords == ["store", "vector"]


def test_all_stopwords_fallback_to_raw_tokens(tmp_path):
    processed = QueryProcessor(_settings(tmp_path)).process("the and of the")
    assert processed.keywords == ["the", "and", "of"]


def test_all_cjk_stopwords_fallback(tmp_path):
    processed = QueryProcessor(_settings(tmp_path)).process("的 了 吗")
    assert processed.keywords == ["的", "了", "吗"]


def test_content_verbs_kept(tmp_path):
    """spec 3.1.2：提取关键实体与动词——实义动词不得进停用词。"""
    assert "find" not in DEFAULT_STOPWORDS
    assert "configure" not in DEFAULT_STOPWORDS
    processed = QueryProcessor(_settings(tmp_path)).process("find deployment errors")
    assert processed.keywords == ["find", "deployment", "errors"]


def test_deterministic(tmp_path):
    processor = QueryProcessor(_settings(tmp_path))
    a = processor.process("如何配置 Azure OpenAI 部署")
    b = processor.process("如何配置 Azure OpenAI 部署")
    assert a == b


def test_original_query_preserved_verbatim(tmp_path):
    raw = "  Azure  配置 "
    processed = QueryProcessor(_settings(tmp_path)).process(raw)
    assert processed.query == raw


# ---------------------------------------------------------------------------
# 失败语义（fail-fast）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "   ", "\n\t "])
def test_empty_or_whitespace_query_raises(tmp_path, bad):
    with pytest.raises(QueryProcessingError, match="non-empty"):
        QueryProcessor(_settings(tmp_path)).process(bad)


def test_punctuation_only_query_raises(tmp_path):
    with pytest.raises(QueryProcessingError, match="no extractable keywords"):
        QueryProcessor(_settings(tmp_path)).process("!!! ??? ###")


def test_non_string_query_raises_type_error(tmp_path):
    with pytest.raises(TypeError, match="query must be a string"):
        QueryProcessor(_settings(tmp_path)).process(123)


# ---------------------------------------------------------------------------
# filters 契约（可空实现）
# ---------------------------------------------------------------------------


def test_filters_default_empty_dict(tmp_path):
    processed = QueryProcessor(_settings(tmp_path)).process("azure deployment")
    assert processed.filters == {}
    assert isinstance(processed.filters, dict)


def test_filters_passthrough_shallow_copy(tmp_path):
    source = {"collection": "docs", "doc_type": "pdf"}
    processed = QueryProcessor(_settings(tmp_path)).process("azure", filters=source)
    assert processed.filters == source
    # 浅拷贝：两侧互不影响
    processed.filters["collection"] = "changed"
    assert source["collection"] == "docs"


def test_non_dict_filters_raises_type_error(tmp_path):
    with pytest.raises(TypeError, match="filters must be a dict or None"):
        QueryProcessor(_settings(tmp_path)).process("azure", filters=["collection"])


def test_arbitrary_filter_values_passthrough(tmp_path):
    """通用 filters：键集合不设白名单，值（标量/列表）原样透传给检索器。"""
    filters = {"collection": "docs", "tags": ["a", "b"], "page": 3}
    processed = QueryProcessor(_settings(tmp_path)).process("azure", filters=filters)
    assert processed.filters == filters


# ---------------------------------------------------------------------------
# ProcessedQuery 契约
# ---------------------------------------------------------------------------


def test_processed_query_dict_roundtrip(tmp_path):
    processed = QueryProcessor(_settings(tmp_path)).process(
        "配置 Azure", filters={"collection": "docs"}
    )
    data = processed.to_dict()
    assert set(data) == {"query", "keywords", "filters"}
    restored = ProcessedQuery.from_dict(data)
    assert restored == processed


def test_processed_query_from_dict_tolerates_unknown_keys():
    restored = ProcessedQuery.from_dict(
        {"query": "q", "keywords": ["a"], "filters": {"k": 1}, "future_field": True}
    )
    assert restored.query == "q"
    assert restored.keywords == ["a"]
    assert restored.filters == {"k": 1}


def test_processed_query_json_roundtrip(tmp_path):
    processed = QueryProcessor(_settings(tmp_path)).process("配置 Azure")
    assert ProcessedQuery.from_json(processed.to_json()) == processed


def test_processed_query_rejects_empty_query():
    with pytest.raises(ValueError, match="non-empty"):
        ProcessedQuery(query="", keywords=["a"])


# ---------------------------------------------------------------------------
# 注入点
# ---------------------------------------------------------------------------


def test_custom_stopwords_replace_defaults(tmp_path):
    processor = QueryProcessor(_settings(tmp_path), stopwords=set())
    processed = processor.process("the and vector")
    assert processed.keywords == ["the", "and", "vector"]
    assert processor.stopwords == frozenset()


def test_custom_tokenizer_injected(tmp_path):
    # 同时注入空停用词，隔离验证 tokenizer 行为（"a" 是默认停用词）。
    processor = QueryProcessor(
        _settings(tmp_path), stopwords=set(), tokenizer=lambda text: text.split(",")
    )
    processed = processor.process("a,b,a")
    assert processed.keywords == ["a", "b"]
    assert processor.tokenizer("x,y") == ["x", "y"]


# ---------------------------------------------------------------------------
# trace 打点
# ---------------------------------------------------------------------------


def test_process_records_trace(tmp_path):
    trace = TraceContext()
    QueryProcessor(_settings(tmp_path)).process(
        "如何配置 Azure？", filters={"collection": "docs"}, trace=trace
    )
    record = trace.get_stage("query_processing")
    assert record["method"] == "query_processor"
    assert record["details"]["keywords"] == ["azure", "配", "置"]
    assert record["details"]["keyword_count"] == 3
    assert record["details"]["filter_keys"] == ["collection"]
    assert record["details"]["elapsed_ms"] >= 0


def test_no_trace_no_error(tmp_path):
    processed = QueryProcessor(_settings(tmp_path)).process("azure")
    assert processed.keywords == ["azure"]


# ---------------------------------------------------------------------------
# 与 C9 SparseEncoder 的分词对齐（drift guard）
# ---------------------------------------------------------------------------


_ALIGN_SAMPLES = [
    "How do I configure Azure OpenAI?",
    "如何配置向量检索？",
    "RAG 检索增强 GPT-4o 3.5-turbo",
    "Mixed 混合查询 chroma-0.5.0",
    "!!! ??? ###",
    "",
]


@pytest.mark.parametrize("sample", _ALIGN_SAMPLES)
def test_tokenizer_alignment_with_sparse_encoder(sample):
    """core 本地分词必须与 C9 索引侧分词逐 token 一致（BM25 查询词=索引词）。"""
    assert qp_tokenize(sample) == sparse_tokenize(sample)


# ---------------------------------------------------------------------------
# C9→C11 链路：keywords 可直接命中 BM25 索引
# ---------------------------------------------------------------------------


def test_keywords_hit_bm25_index(tmp_path):
    settings = _settings(tmp_path)
    chunks = [
        Chunk(id="doc_0000_aaaa1111", text="Azure OpenAI 配置指南 部署步骤", metadata={"source_path": "a.pdf"}),
        Chunk(id="doc_0001_bbbb2222", text="本地 Ollama 模型安装", metadata={"source_path": "b.pdf"}),
    ]
    encoder = SparseEncoder(settings)
    indexer = BM25Indexer(settings)
    indexer.add(encoder.encode_chunks(chunks))
    processor = QueryProcessor(settings)
    keywords = processor.process("如何配置 Azure？").keywords
    assert keywords == ["azure", "配", "置"]
    hits = indexer.query(keywords, top_k=5)
    assert hits, "keywords 应能命中 BM25 索引"
    assert hits[0]["chunk_id"] == "doc_0000_aaaa1111"
