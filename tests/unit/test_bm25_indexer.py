"""``ingestion.storage.BM25Indexer`` 契约测试（阶段 C11）。

覆盖：
- 从多个 ``SparseDocStats`` 构建：postings / df / doc_length / avgdl 正确；
- IDF 精确性：3 文档小语料手工对数验证、``df = N/2`` 时 IDF 恰为 0；
- 空输入 / 空 ``term_freqs``：不抛错、不入索引、不计入 N；
- 增量 ``add`` 与全量 ``build`` 等价；同 chunk_id 替换（死词清理）；
- ``build`` 重建（旧索引被清空）；重复 chunk_id fail-fast；
- save/load JSON 往返（原子写、自动建目录、采纳文件 k1/b、损坏文件报错）；
- BM25 打分：手工推导分数、排序稳定（同分按 chunk_id 升序）、
  build→save→load 后同一语料查询返回稳定 top ids（D3 契约）。
"""

from __future__ import annotations

import json
import math

import pytest

from core.trace.trace_context import TraceContext
from ingestion.embedding import SparseDocStats, SparseEncoder
from ingestion.storage import BM25Index, BM25IndexError, BM25Indexer


def _stats(chunk_id: str, term_freqs: dict[str, int], length: int) -> SparseDocStats:
    return SparseDocStats(chunk_id=chunk_id, term_freqs=term_freqs, length=length)


#: 4 文档语料：N=4, total_length=9, avgdl=2.25。
DOCS = [
    _stats("d1", {"search": 2, "engine": 1}, 3),
    _stats("d2", {"bm25": 1, "engine": 1}, 2),
    _stats("d3", {"bm25": 1, "ranking": 1}, 2),
    _stats("d4", {"vector": 1, "store": 1}, 2),
]

#: 3 文档语料：N=3, total_length=7。
DOCS3 = [
    _stats("c1", {"bm25": 2, "vector": 1}, 3),
    _stats("c2", {"bm25": 1, "search": 1}, 2),
    _stats("c3", {"vector": 1, "store": 1}, 2),
]


# ---------------------------------------------------------------------------
# 构建：postings / df / avgdl
# ---------------------------------------------------------------------------


def test_build_from_multiple_stats_postings_and_df():
    indexer = BM25Indexer().build(DOCS)
    assert indexer.num_docs == 4
    assert set(indexer.terms) == {"search", "engine", "bm25", "ranking", "vector", "store"}
    # spec 结构：{term: {idf, postings: [{chunk_id, tf, doc_length}]}}
    entry = indexer.terms["bm25"]
    assert {p.chunk_id for p in entry.postings} == {"d2", "d3"}
    assert all(p.tf == 1 and p.doc_length == 2 for p in entry.postings)
    assert indexer.index.doc_frequency("engine") == 2
    assert indexer.index.doc_frequency("ranking") == 1
    assert indexer.index.doc_frequency("nonexistent") == 0
    assert indexer.index.get_postings("search") == [
        p for p in indexer.terms["search"].postings
    ]
    assert indexer.index.get_postings("nonexistent") == []
    assert len(indexer) == 4


def test_avgdl_matches_total_over_docs():
    indexer = BM25Indexer().build(DOCS3)
    assert indexer.avgdl == pytest.approx(7 / 3)
    assert indexer.index.total_length == 7


def test_empty_input_builds_empty_index():
    indexer = BM25Indexer().build([])
    assert indexer.num_docs == 0
    assert indexer.terms == {}
    assert indexer.avgdl == 0.0
    assert indexer.query(["bm25"], 3) == []


def test_empty_term_freqs_docs_are_skipped():
    indexer = BM25Indexer().build([_stats("c0", {}, 0), DOCS3[0]])
    assert indexer.num_docs == 1  # 空文档不计入 N
    assert indexer.terms.keys() == {"bm25", "vector"}
    assert indexer.index.total_length == 3


# ---------------------------------------------------------------------------
# IDF 精确性（spec 公式：log((N - df + 0.5) / (df + 0.5))，自然对数）
# ---------------------------------------------------------------------------


def test_idf_hand_computed_small_corpus():
    indexer = BM25Indexer().build(DOCS3)  # N=3
    # df=2: log((3 - 2 + 0.5) / (2 + 0.5)) = log(0.6)
    assert indexer.terms["bm25"].idf == pytest.approx(math.log(0.6))
    assert indexer.terms["vector"].idf == pytest.approx(math.log(0.6))
    # df=1: log((3 - 1 + 0.5) / (1 + 0.5)) = log(5/3)
    assert indexer.terms["search"].idf == pytest.approx(math.log(5 / 3))
    assert indexer.terms["store"].idf == pytest.approx(math.log(5 / 3))
    assert indexer.index.idf("search") == pytest.approx(math.log(5 / 3))
    assert indexer.index.idf("nonexistent") is None


def test_idf_zero_when_df_is_half_of_n():
    indexer = BM25Indexer().build(DOCS)  # N=4, df(bm25)=2
    assert indexer.terms["bm25"].idf == pytest.approx(math.log((4 - 2 + 0.5) / (2 + 0.5)))
    assert indexer.terms["bm25"].idf == pytest.approx(0.0)


def test_idf_negative_for_terms_in_more_than_half_docs():
    # spec 公式不加 1 不截断：df=2, N=3 → log(0.6) < 0
    indexer = BM25Indexer().build(DOCS3)
    assert indexer.terms["bm25"].idf < 0


# ---------------------------------------------------------------------------
# 更新：增量 / 替换 / 重建 / fail-fast
# ---------------------------------------------------------------------------


def test_incremental_add_equals_full_build():
    incremental = BM25Indexer().build(DOCS[:2])
    incremental.add(DOCS[2:])
    full = BM25Indexer().build(DOCS)
    assert incremental.index.to_dict() == full.index.to_dict()
    assert incremental.query(["bm25", "ranking"], 4) == full.query(["bm25", "ranking"], 4)


def test_add_replaces_same_chunk_id_and_cleans_dead_terms():
    indexer = BM25Indexer().build(DOCS)  # d1: {search:2, engine:1}
    indexer.add([_stats("d1", {"vector": 3}, 3)])
    assert indexer.num_docs == 4  # 替换不增加 N
    assert indexer.index.total_length == 9  # 3+2+2+2 不变
    assert "search" not in indexer.terms  # df 归零的死词被清理
    assert indexer.terms["vector"].idf == pytest.approx(math.log((4 - 2 + 0.5) / 2.5))  # df 重算
    assert indexer.query(["search"], 3) == []  # 旧内容不再可命中


def test_build_resets_previous_index():
    indexer = BM25Indexer().build(DOCS)
    indexer.build(DOCS3)
    fresh = BM25Indexer().build(DOCS3)
    assert indexer.index.to_dict() == fresh.index.to_dict()
    indexed_ids = {p.chunk_id for entry in indexer.terms.values() for p in entry.postings}
    assert indexed_ids == {"c1", "c2", "c3"}  # 旧语料的 chunk 全部清除


def test_duplicate_chunk_id_in_single_batch_raises():
    indexer = BM25Indexer()
    with pytest.raises(BM25IndexError, match="duplicate chunk_id"):
        indexer.build([_stats("d1", {"a": 1}, 1), _stats("d1", {"b": 1}, 1)])
    assert indexer.num_docs == 0  # 失败前未污染索引
    # 跨批次重复是合法替换路径
    indexer.add([_stats("d1", {"a": 1}, 1)])
    indexer.add([_stats("d1", {"b": 1}, 1)])
    assert indexer.num_docs == 1
    assert set(indexer.terms) == {"b"}


@pytest.mark.parametrize(
    "bad",
    [
        _stats("", {"a": 1}, 1),
        _stats("d1", {"a": 0}, 1),
        _stats("d1", {"": 1}, 1),
        _stats("d1", {"a": -2}, 1),
        _stats("d1", {"a": 1}, -1),
    ],
)
def test_invalid_stats_fail_fast(bad):
    with pytest.raises(BM25IndexError):
        BM25Indexer().add([bad])


def test_single_stats_instance_rejected():
    with pytest.raises(BM25IndexError, match="sequence"):
        BM25Indexer().add(_stats("d1", {"a": 1}, 1))  # type: ignore[arg-type]


def test_invalid_bm25_params_rejected():
    with pytest.raises(ValueError):
        BM25Indexer(k1=-0.5)
    with pytest.raises(ValueError):
        BM25Indexer(b=1.5)


# ---------------------------------------------------------------------------
# 持久化：save / load 往返
# ---------------------------------------------------------------------------


def test_save_load_roundtrip(tmp_path):
    path = tmp_path / "bm25" / "index.json"
    indexer = BM25Indexer(index_path=path).build(DOCS)
    indexer.save()
    assert path.is_file()

    loaded = BM25Indexer(index_path=path).load()
    assert loaded.index.to_dict() == indexer.index.to_dict()
    assert loaded.num_docs == 4
    assert loaded.avgdl == pytest.approx(2.25)
    assert loaded.terms["bm25"].idf == pytest.approx(0.0)
    # 验收标准：build 后 load，同一语料查询返回稳定 top ids
    assert loaded.query(["bm25", "engine"], 4) == indexer.query(["bm25", "engine"], 4)
    # 加载后仍可继续增量更新：与全量重建等价（含负 IDF 词项的排序也一致）
    loaded.add([_stats("d5", {"bm25": 2}, 2)])
    assert loaded.num_docs == 5
    rebuilt = BM25Indexer().build([*DOCS, _stats("d5", {"bm25": 2}, 2)])
    assert loaded.query(["bm25", "engine", "vector"], 5) == rebuilt.query(["bm25", "engine", "vector"], 5)
    # 正 IDF 词项（df=1 < N/2）的 top-1 稳定命中唯一包含它的 chunk
    assert loaded.query(["vector"], 1)[0]["chunk_id"] == "d4"


def test_load_adopts_file_params(tmp_path):
    path = tmp_path / "index.json"
    BM25Indexer(k1=1.2, b=0.9, index_path=path).build(DOCS).save()
    loaded = BM25Indexer(k1=1.5, b=0.75, index_path=path).load()
    assert loaded.index.k1 == pytest.approx(1.2)
    assert loaded.index.b == pytest.approx(0.9)


def test_save_explicit_path_and_missing_dir_created(tmp_path):
    target = tmp_path / "nested" / "deep" / "idx.json"
    returned = BM25Indexer().build(DOCS).save(target)
    assert returned == target and target.is_file()


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="bm25 index file not found"):
        BM25Indexer(index_path=tmp_path / "nope.json").load()


def test_load_malformed_json_raises(tmp_path):
    path = tmp_path / "index.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(BM25IndexError, match="failed to parse"):
        BM25Indexer(index_path=path).load()


def test_load_corrupt_stats_mismatch_raises(tmp_path):
    path = tmp_path / "index.json"
    payload = BM25Indexer().build(DOCS).index.to_dict()
    payload["num_docs"] = 99  # 与 postings 覆盖的 chunk 数不一致
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BM25IndexError, match="num_docs"):
        BM25Indexer(index_path=path).load()
    payload["num_docs"] = 4
    payload["total_length"] = 123
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BM25IndexError, match="total_length"):
        BM25Indexer(index_path=path).load()


def test_from_dict_tolerates_extra_keys_and_defaults():
    payload = BM25Indexer().build(DOCS).index.to_dict()
    payload["unknown_future_field"] = {"x": 1}
    index = BM25Index.from_dict(payload)
    assert index.num_docs == 4
    minimal = {"version": 1, "terms": {"a": {"idf": 0.5, "postings": [{"chunk_id": "c", "tf": 1, "doc_length": 1}]}}}
    index2 = BM25Index.from_dict(minimal)
    assert index2.k1 == pytest.approx(1.5) and index2.b == pytest.approx(0.75)
    with pytest.raises(BM25IndexError, match="unsupported index version"):
        BM25Index.from_dict({"version": 99})


def test_to_dict_structure_matches_spec(tmp_path):
    payload = BM25Indexer().build(DOCS).index.to_dict()
    assert payload["version"] == 1
    term = payload["terms"]["bm25"]
    assert set(term) == {"idf", "postings"}
    assert all(set(p) == {"chunk_id", "tf", "doc_length"} for p in term["postings"])


# ---------------------------------------------------------------------------
# 查询：打分 / 排序稳定性
# ---------------------------------------------------------------------------


def test_query_hand_computed_score():
    indexer = BM25Indexer(k1=1.5, b=0.75).build(DOCS)  # N=4, avgdl=2.25
    results = indexer.query(["search"], 4)
    # 只有 d1 含 "search"：df=1 → idf = log(3.5/1.5)
    idf = math.log((4 - 1 + 0.5) / (1 + 0.5))
    tf, dl = 2, 3
    expected = idf * (tf * 2.5) / (tf + 1.5 * (1 - 0.75 + 0.75 * dl / 2.25))
    assert len(results) == 1
    assert results[0]["chunk_id"] == "d1"
    assert results[0]["score"] == pytest.approx(expected)


def test_query_ranks_relevant_doc_first_and_truncates():
    indexer = BM25Indexer().build(DOCS)
    results = indexer.query(["search", "engine"], 4)
    assert [r["chunk_id"] for r in results] == ["d1", "d2"]  # engine 命中 d1/d2，d1 叠加 search 分更高
    assert [r["chunk_id"] for r in indexer.query(["search"], 1)] == ["d1"]  # top_k 截断
    scores = [r["score"] for r in indexer.query(["bm25", "ranking", "engine"], 2)]
    assert len(scores) == 2 and scores == sorted(scores, reverse=True)  # 降序


def test_query_tie_broken_by_chunk_id_ascending():
    indexer = BM25Indexer().build(DOCS)
    # "ranking" 仅在 d3，"store" 仅在 d4：单独查同分词验证并列排序
    r1 = indexer.query(["ranking"], 4)
    r2 = indexer.query(["store"], 4)
    # d2/d3 对 "bm25" tf/dl 完全一致 → 同分，按 chunk_id 升序
    tied = indexer.query(["bm25"], 4)
    assert [r["chunk_id"] for r in tied] == ["d2", "d3"]
    assert tied[0]["score"] == pytest.approx(tied[1]["score"])
    assert r1[0]["chunk_id"] == "d3" and r2[0]["chunk_id"] == "d4"


def test_query_deterministic_across_calls():
    indexer = BM25Indexer().build(DOCS)
    first = indexer.query(["bm25", "engine", "vector"], 4)
    for _ in range(3):
        assert indexer.query(["bm25", "engine", "vector"], 4) == first


def test_query_keyword_repetition_multiplies_score():
    indexer = BM25Indexer().build(DOCS)
    single = indexer.query(["search"], 4)[0]["score"]
    double = indexer.query(["search", "search"], 4)[0]["score"]
    assert double == pytest.approx(2 * single)


def test_query_empty_or_unknown_keywords_return_empty():
    indexer = BM25Indexer().build(DOCS)
    assert indexer.query([], 3) == []
    assert indexer.query(["nonexistent"], 3) == []


def test_query_invalid_top_k_raises():
    indexer = BM25Indexer().build(DOCS)
    with pytest.raises(ValueError, match="top_k"):
        indexer.query(["bm25"], 0)
    with pytest.raises(ValueError, match="top_k"):
        indexer.query(["bm25"], -1)


# ---------------------------------------------------------------------------
# 与 C9 SparseEncoder 的链路契约 + trace 打点
# ---------------------------------------------------------------------------


def test_consume_sparse_encoder_output_end_to_end(tmp_path):
    from textwrap import dedent
    from pathlib import Path

    from core.settings import load_settings
    from core.types import Chunk

    yaml_path = Path(tmp_path) / "settings.yaml"
    yaml_path.write_text(
        dedent(
            """\
            llm: {provider: azure, model: gpt-4o}
            embedding: {provider: openai, model: text-embedding-3-small}
            vision_llm: {provider: azure, model: gpt-4o}
            vector_store: {backend: chroma}
            retrieval: {sparse_backend: bm25, fusion_algorithm: rrf}
            rerank: {backend: none}
            evaluation: {backends: [custom]}
            """
        ),
        encoding="utf-8",
    )
    settings = load_settings(yaml_path)
    chunks = [
        Chunk(
            id=f"doc_000{i}_ab12cd34",
            text=text,
            metadata={},
            start_offset=0,
            end_offset=len(text),
            source_ref="doc" + "0" * 61,
        )
        for i, text in enumerate(["BM25 sparse keyword ranking", "dense vector embedding store", ""])
    ]
    stats = SparseEncoder(settings).encode_chunks(chunks)
    indexer = BM25Indexer().build(stats)
    assert indexer.num_docs == 2  # 第三个 chunk 为空文本 → 不入索引
    top = indexer.query(["bm25", "sparse"], 1)
    assert top[0]["chunk_id"] == "doc_0000_ab12cd34"


def test_remove_document_by_doc_hash_prefix():
    indexer = BM25Indexer().build([
        _stats("abc123_0000_deadbeef", {"search": 1}, 1),
        _stats("abc123_0001_feedface", {"engine": 1}, 1),
        _stats("zzz999_0000_cafebabe", {"vector": 1}, 1),
    ])
    removed = indexer.remove_document("abc123")
    assert removed == 2
    assert indexer.num_docs == 1
    assert indexer.query(["search"], 3) == []
    assert indexer.query(["vector"], 1)[0]["chunk_id"] == "zzz999_0000_cafebabe"
    assert indexer.remove_document("missing") == 0


def test_remove_document_rejects_empty_source():
    with pytest.raises(ValueError):
        BM25Indexer().remove_document("")


def test_build_and_add_record_storage_trace():
    trace = TraceContext()
    indexer = BM25Indexer()
    indexer.build(DOCS, trace=trace)
    indexer.add(DOCS3, trace=trace)
    stages = [s for s in trace.stages if s["stage"] == "storage"]
    assert [s["method"] for s in stages] == ["bm25_indexer", "bm25_indexer"]
    assert stages[0]["details"]["mode"] == "build"
    assert stages[0]["details"]["num_docs"] == 4
    assert stages[1]["details"]["mode"] == "add"
    assert stages[1]["details"]["docs"] == 3
    empty_trace = TraceContext()
    BM25Indexer().build([], trace=empty_trace)  # 空跑不打点
    assert empty_trace.stages == []
