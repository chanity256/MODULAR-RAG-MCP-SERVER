"""``ingestion.embedding.DenseEncoder`` 契约测试（阶段 C8）。

覆盖：
- 向量数量与 chunks 一致、维度一致且非零；
- 空 chunks → 空列表；顺序与输入对应（确定性 hash 向量可逐位验证）；
- 契约破坏（数量/维度不一致、空向量）→ ``DenseEncodingError``；
- 注入 fake 而非走工厂；settings 驱动工厂路径；trace 打点。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from core.trace.trace_context import TraceContext
from core.types import Chunk
from ingestion.embedding import DenseEncoder, DenseEncodingError
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


def _settings(tmp_path: Path):
    path = Path(tmp_path) / "settings.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_VALID_YAML, encoding="utf-8")
    return load_settings(path)


def _chunks(n: int) -> list[Chunk]:
    return [
        Chunk(
            id=f"doc_{i:04d}_ab12cd34",
            text=f"第 {i} 个 chunk 的正文内容，用于向量化。",
            metadata={"source_path": "d.pdf", "chunk_index": i},
            start_offset=i * 10,
            end_offset=i * 10 + 8,
            source_ref="doc" + "0" * 61,
        )
        for i in range(n)
    ]


class _HashEmbedding(BaseEmbedding):
    """确定性 fake：按文本 sha256 派生固定维度向量。"""

    def __init__(self, dimension: int = 8):
        self.dimension_value = dimension
        self.batches: list[list[str]] = []

    @property
    def dimension(self) -> int | None:
        return self.dimension_value

    def embed(self, texts, *, trace=None, **kwargs):
        self.batches.append(list(texts))
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vector = [float(digest[i % len(digest)]) for i in range(self.dimension_value)]
            vectors.append(vector)
        return vectors


# ---------------------------------------------------------------------------
# 核心契约
# ---------------------------------------------------------------------------


def test_vector_count_and_dimension_match_chunks(tmp_path):
    chunks = _chunks(5)
    fake = _HashEmbedding(dimension=8)
    vectors = DenseEncoder(_settings(tmp_path), embedding=fake).encode(chunks)
    assert len(vectors) == 5
    assert all(len(v) == 8 for v in vectors)
    assert all(vectors[i] for i in range(5))  # 非空向量


def test_vectors_deterministic_and_ordered(tmp_path):
    chunks = _chunks(3)
    fake = _HashEmbedding()
    encoder = DenseEncoder(_settings(tmp_path), embedding=fake)
    a = encoder.encode(chunks)
    b = encoder.encode(chunks)
    assert a == b  # 相同输入 → 相同向量
    # 向量与文本一一对应：单测第 2 个文本的向量应等于全量结果中的第 2 个。
    single = fake.embed([chunks[1].text])[0]
    assert a[1] == single


def test_texts_passed_in_order_single_batch(tmp_path):
    chunks = _chunks(4)
    fake = _HashEmbedding()
    DenseEncoder(_settings(tmp_path), embedding=fake).encode(chunks)
    assert fake.batches == [[c.text for c in chunks]]


def test_empty_chunks_returns_empty(tmp_path):
    fake = _HashEmbedding()
    assert DenseEncoder(_settings(tmp_path), embedding=fake).encode([]) == []
    assert fake.batches == []  # 不应触发底层调用


def test_embed_exception_propagates(tmp_path):
    class _Boom(BaseEmbedding):
        def embed(self, texts, *, trace=None, **kwargs):
            raise RuntimeError("api 429")

    with pytest.raises(RuntimeError, match="api 429"):
        DenseEncoder(_settings(tmp_path), embedding=_Boom()).encode(_chunks(2))


# ---------------------------------------------------------------------------
# 契约破坏 fail-fast
# ---------------------------------------------------------------------------


def test_count_mismatch_raises(tmp_path):
    class _Short(BaseEmbedding):
        def embed(self, texts, *, trace=None, **kwargs):
            return [[0.0, 0.1] for _ in texts[:-1]]  # 少返回一个

    with pytest.raises(DenseEncodingError, match="4 vectors for 5"):
        DenseEncoder(_settings(tmp_path), embedding=_Short()).encode(_chunks(5))


def test_dimension_mismatch_raises(tmp_path):
    class _Ragged(BaseEmbedding):
        def embed(self, texts, *, trace=None, **kwargs):
            return [[0.0] * (2 + i) for i, _ in enumerate(texts)]

    with pytest.raises(DenseEncodingError, match="inconsistent dimension"):
        DenseEncoder(_settings(tmp_path), embedding=_Ragged()).encode(_chunks(3))


def test_empty_vector_raises(tmp_path):
    class _EmptyVec(BaseEmbedding):
        def embed(self, texts, *, trace=None, **kwargs):
            return [[] for _ in texts]

    with pytest.raises(DenseEncodingError, match="empty vector"):
        DenseEncoder(_settings(tmp_path), embedding=_EmptyVec()).encode(_chunks(1))


# ---------------------------------------------------------------------------
# 工厂 / trace / 属性
# ---------------------------------------------------------------------------


def test_settings_driven_factory_used(tmp_path, monkeypatch):
    from libs.embedding import EmbeddingFactory

    created = _HashEmbedding(dimension=16)
    monkeypatch.setattr(
        EmbeddingFactory, "create", staticmethod(lambda settings, **kwargs: created)
    )
    encoder = DenseEncoder(_settings(tmp_path))
    assert encoder.dimension == 16  # 使用了工厂返回的实例


def test_dimension_property_from_embedding(tmp_path):
    fake = _HashEmbedding(dimension=32)
    assert DenseEncoder(_settings(tmp_path), embedding=fake).dimension == 32


def test_encode_records_trace_stage(tmp_path):
    trace = TraceContext()
    DenseEncoder(_settings(tmp_path), embedding=_HashEmbedding(dimension=4)).encode(
        _chunks(3), trace=trace
    )
    record = trace.get_stage("embedding")
    assert record["method"] == "dense_encoder"
    assert record["details"]["chunks"] == 3
    assert record["details"]["dimension"] == 4
    assert record["details"]["elapsed_ms"] >= 0
