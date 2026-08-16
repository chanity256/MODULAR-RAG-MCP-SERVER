"""``ingestion.embedding.SparseEncoder`` 契约测试（阶段 C9）。

覆盖：
- 词频/长度统计正确（拉丁词小写化、CJK 逐字）；
- 输出结构可直接用于 bm25_indexer（chunk_id 对齐、term_freqs 可比较）；
- 空文本明确行为：``term_freqs={}`` 且 ``length=0``；
- 自定义 tokenizer 注入、确定性、to_dict/from_dict 往返、trace 打点。
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from core.settings import load_settings
from core.trace.trace_context import TraceContext
from core.types import Chunk
from ingestion.embedding import SparseDocStats, SparseEncoder, default_tokenize

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


def _chunks(texts: list[str]) -> list[Chunk]:
    return [
        Chunk(
            id=f"doc_{i:04d}_ab12cd34",
            text=text,
            metadata={"source_path": "d.pdf", "chunk_index": i},
            start_offset=0,
            end_offset=len(text),
            source_ref="doc" + "0" * 61,
        )
        for i, text in enumerate(texts)
    ]


# ---------------------------------------------------------------------------
# 分词与统计
# ---------------------------------------------------------------------------


def test_latin_words_lowercased_and_counted(tmp_path):
    encoder = SparseEncoder(_settings(tmp_path))
    freqs, length = encoder.encode_text("Vector Store store the Query")
    assert freqs == {"vector": 1, "store": 2, "the": 1, "query": 1}
    assert length == 5


def test_cjk_char_by_char(tmp_path):
    freqs, length = SparseEncoder(_settings(tmp_path)).encode_text("向量检索")
    assert freqs == {"向": 1, "量": 1, "检": 1, "索": 1}
    assert length == 4


def test_mixed_text_tokens(tmp_path):
    freqs, length = SparseEncoder(_settings(tmp_path)).encode_text("RAG 检索增强")
    assert "rag" in freqs
    assert freqs["检"] == 1
    assert length == 1 + 4  # rag + 检索增强（4 个 CJK 字）


def test_default_tokenize_is_pure_function():
    assert default_tokenize("Hello World, 世界") == ["hello", "world", "世", "界"]
    assert default_tokenize("") == []


def test_stats_align_with_chunk_ids_in_order(tmp_path):
    chunks = _chunks(["甲乙", "one two", ""])
    stats = SparseEncoder(_settings(tmp_path)).encode_chunks(chunks)
    assert [s.chunk_id for s in stats] == [c.id for c in chunks]
    assert stats[0].term_freqs == {"甲": 1, "乙": 1}
    assert stats[1].term_freqs == {"one": 1, "two": 1}


# ---------------------------------------------------------------------------
# 空文本契约
# ---------------------------------------------------------------------------


def test_empty_text_explicit_behavior(tmp_path):
    encoder = SparseEncoder(_settings(tmp_path))
    freqs, length = encoder.encode_text("")
    assert freqs == {}
    assert length == 0
    stats = encoder.encode_chunks(_chunks(["", "   "]))
    assert all(s.term_freqs == {} and s.length == 0 for s in stats)


def test_whitespace_only_text_behaves_like_empty(tmp_path):
    freqs, length = SparseEncoder(_settings(tmp_path)).encode_text("  \n\t ")
    assert freqs == {} and length == 0


# ---------------------------------------------------------------------------
# 可用于 bm25_indexer 的结构
# ---------------------------------------------------------------------------


def test_stats_usable_for_idf_and_posting(tmp_path):
    """ indexer 视角：可从 stats 集合推导 df（文档频率）与总长度。"""
    stats = SparseEncoder(_settings(tmp_path)).encode_chunks(
        _chunks(["apple banana", "banana cherry", "banana"])
    )
    df = {}
    for s in stats:
        for term in s.term_freqs:
            df[term] = df.get(term, 0) + 1
    assert df == {"apple": 1, "banana": 3, "cherry": 1}
    assert sum(s.length for s in stats) == 5


def test_deterministic(tmp_path):
    encoder = SparseEncoder(_settings(tmp_path))
    a = encoder.encode_chunks(_chunks(["稳定统计 term term"]))
    b = encoder.encode_chunks(_chunks(["稳定统计 term term"]))
    assert a == b


def test_custom_tokenizer_injected(tmp_path):
    encoder = SparseEncoder(_settings(tmp_path), tokenizer=lambda text: text.split("|"))
    freqs, length = encoder.encode_text("a|b|a")
    assert freqs == {"a": 2, "b": 1}
    assert length == 3
    assert encoder.tokenizer("x|y") == ["x", "y"]


def test_stats_dict_roundtrip(tmp_path):
    stats = SparseEncoder(_settings(tmp_path)).encode_chunks(_chunks(["Round Trip 往返"]))[0]
    data = stats.to_dict()
    assert data == {
        "chunk_id": stats.chunk_id,
        "term_freqs": stats.term_freqs,
        "length": stats.length,
    }
    assert SparseDocStats.from_dict(data) == stats
    # 容忍多余键 / 缺失可选键
    assert SparseDocStats.from_dict({"chunk_id": "x", "extra": 1}) == SparseDocStats(chunk_id="x")


def test_empty_chunks_returns_empty(tmp_path):
    assert SparseEncoder(_settings(tmp_path)).encode_chunks([]) == []


def test_encode_chunks_records_trace(tmp_path):
    trace = TraceContext()
    SparseEncoder(_settings(tmp_path)).encode_chunks(_chunks(["甲 乙", "丙"]), trace=trace)
    record = trace.get_stage("embedding")
    assert record["method"] == "sparse_encoder"
    assert record["details"]["chunks"] == 2
    assert record["details"]["total_terms"] == 3  # 甲+乙 + 丙
    assert record["details"]["unique_terms"] == 3
