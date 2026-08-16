"""``ingestion.embedding.BatchProcessor`` 契约测试（阶段 C10）。

覆盖：
- ``batch_size=2`` 对 5 chunks 分成 3 批（[2,2,1]）且顺序稳定；
- ``run`` 结果拼接顺序与输入一致、长度守恒；编码函数收到的就是批次切分；
- 每批耗时记录进 trace；默认批大小来自 ``ingestion.batch_size``；
- 非法批大小（0/负数/bool）抛 ``ValueError``；空输入返回空；
- 编码函数抛错原样传播；run_dense/run_sparse 便捷路径。
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from core.trace.trace_context import TraceContext
from core.types import Chunk
from ingestion.embedding import BatchProcessor, DenseEncoder, SparseEncoder
from libs.embedding import BaseEmbedding

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


def _settings(tmp_path: Path, ingestion_yaml: str = ""):
    path = Path(tmp_path) / "settings.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_VALID_YAML + ingestion_yaml, encoding="utf-8")
    return load_settings(path)


def _chunks(n: int) -> list[Chunk]:
    return [
        Chunk(
            id=f"doc_{i:04d}_ab12cd34",
            text=f"chunk-{i}",
            metadata={"chunk_index": i},
            start_offset=i * 8,
            end_offset=i * 8 + 7,
            source_ref="doc" + "0" * 61,
        )
        for i in range(n)
    ]


class _SeqEmbedding(BaseEmbedding):
    """返回文本长度的单维向量，记录调用批次。"""

    def __init__(self):
        self.batches: list[list[str]] = []

    def embed(self, texts, *, trace=None, **kwargs):
        self.batches.append(list(texts))
        return [[float(len(t))] for t in texts]


# ---------------------------------------------------------------------------
# split 契约
# ---------------------------------------------------------------------------


def test_batch_size_2_splits_5_into_3_batches(tmp_path):
    bp = BatchProcessor(batch_size=2)
    batches = bp.split(_chunks(5))
    assert len(batches) == 3
    assert [len(b) for b in batches] == [2, 2, 1]


def test_split_order_stable_no_loss_no_dup(tmp_path):
    chunks = _chunks(7)
    batches = BatchProcessor(batch_size=3).split(chunks)
    flat = [c.id for b in batches for c in b]
    assert flat == [c.id for c in chunks]  # 顺序稳定
    assert len(flat) == len(set(flat)) == 7  # 不丢不重


def test_split_exact_multiple(tmp_path):
    assert [len(b) for b in BatchProcessor(batch_size=4).split(_chunks(8))] == [4, 4]


def test_split_empty_returns_empty_list(tmp_path):
    assert BatchProcessor(batch_size=2).split([]) == []


def test_invalid_batch_size_raises():
    for bad in (0, -1, True, 2.5, "3"):
        with pytest.raises(ValueError, match="positive integer"):
            BatchProcessor(batch_size=bad)


def test_default_batch_size_32_without_settings():
    bp = BatchProcessor()
    assert bp.batch_size == 32


# ---------------------------------------------------------------------------
# run 契约
# ---------------------------------------------------------------------------


def test_run_concatenates_in_input_order(tmp_path):
    bp = BatchProcessor(batch_size=2)
    out = bp.run([1, 2, 3, 4, 5], lambda batch: [x * 10 for x in batch])
    assert out == [10, 20, 30, 40, 50]


def test_run_passes_batches_to_encode_fn(tmp_path):
    seen: list[list[int]] = []

    def record(batch):
        seen.append(batch)
        return batch

    BatchProcessor(batch_size=2).run([1, 2, 3, 4, 5], record)
    assert seen == [[1, 2], [3, 4], [5]]


def test_run_records_per_batch_timing_in_trace(tmp_path):
    trace = TraceContext()
    BatchProcessor(batch_size=2).run(_chunks(5), lambda b: [None] * len(b), trace=trace)
    record = trace.get_stage("embedding")
    assert record["method"] == "batch_processor"
    assert record["details"]["batches"] == 3
    assert record["details"]["items"] == 5
    assert record["details"]["batch_size"] == 2
    assert len(record["details"]["batch_timings_ms"]) == 3
    assert all(t >= 0 for t in record["details"]["batch_timings_ms"])


def test_run_count_mismatch_raises(tmp_path):
    # 每个双元素批次各丢一个：[1],[3],[5] = 3 个结果对 5 个输入。
    with pytest.raises(RuntimeError, match="produced 3 results for 5"):
        BatchProcessor(batch_size=2).run([1, 2, 3, 4, 5], lambda b: b[:-1] if len(b) > 1 else b)


def test_run_encode_fn_exception_propagates(tmp_path):
    def boom(batch):
        raise RuntimeError("rate limited")

    with pytest.raises(RuntimeError, match="rate limited"):
        BatchProcessor(batch_size=2).run([1, 2, 3], boom)


def test_run_empty_returns_empty_without_trace_record(tmp_path):
    trace = TraceContext()
    assert BatchProcessor(batch_size=2).run([], lambda b: b, trace=trace) == []
    assert trace.get_stage("embedding") is None  # 空跑不打点


# ---------------------------------------------------------------------------
# settings 驱动 + 便捷路径
# ---------------------------------------------------------------------------


def test_settings_driven_batch_size(tmp_path):
    assert BatchProcessor(_settings(tmp_path)).batch_size == 32  # 默认
    yaml = "\ningestion:\n  batch_size: 3\n"
    assert BatchProcessor(_settings(tmp_path, yaml)).batch_size == 3
    # 显式参数优先于 settings
    assert BatchProcessor(_settings(tmp_path, yaml), batch_size=7).batch_size == 7


def test_invalid_batch_size_in_settings_rejected(tmp_path):
    yaml = "\ningestion:\n  batch_size: 0\n"
    with pytest.raises(Exception, match="batch_size"):
        _settings(tmp_path, yaml)


def test_run_dense_end_to_end(tmp_path):
    fake = _SeqEmbedding()
    encoder = DenseEncoder(_settings(tmp_path), embedding=fake)
    vectors = BatchProcessor(batch_size=2).run_dense(_chunks(5), encoder)
    assert vectors == [[7.0]] * 5
    assert fake.batches == [["chunk-0", "chunk-1"], ["chunk-2", "chunk-3"], ["chunk-4"]]


def test_run_sparse_end_to_end(tmp_path):
    encoder = SparseEncoder(_settings(tmp_path))
    stats = BatchProcessor(batch_size=3).run_sparse(_chunks(5), encoder)
    assert len(stats) == 5
    assert stats[0].term_freqs["chunk"] == 1
    assert stats[0].term_freqs["0"] == 1
