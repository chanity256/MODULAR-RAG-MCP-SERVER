"""``ingestion.storage.VectorUpserter`` 契约测试（阶段 C12）。

覆盖：
- happy path：记录数 / 顺序 / text + 完整 metadata 载荷 / metadata 拷贝语义；
- 幂等性：同 chunk 重复 upsert → 相同 id、覆盖不重复；内容变更 → id 变更；
- 批量切分：``ingestion.batch_size`` 分批（[2,2,1]），显式参数优先，保序；
- 空输入：零统计、不触库、不打点；
- 契约破坏 fail-fast（数量/维度/空向量/非数值/重复 id/非法返回计数）；
- 工厂路径：``settings.vector_store.backend`` 路由创建 store；
- trace 打点（storage / vector_upserter）；C8 DenseEncoder → C12 链路。
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from core.trace.trace_context import TraceContext
from core.types import Chunk
from ingestion.embedding import DenseEncoder
from ingestion.storage import UpsertSummary, VectorUpsertError, VectorUpserter
from libs.embedding import BaseEmbedding
from libs.vector_store.base_vector_store import BaseVectorStore, QueryResult, VectorRecord
from libs.vector_store.vector_store_factory import VectorStoreFactory

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


def _settings(tmp_path: Path, batch_size: int | None = None):
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    settings = load_settings(path)
    if batch_size is not None:
        settings = replace(settings, ingestion=replace(settings.ingestion, batch_size=batch_size))
    return settings


def _chunk_id(index: int, text: str, doc_id: str = "d0" + "0" * 61) -> str:
    """复刻 C4 DocumentChunker 的确定性 ID：``{doc_id}_{index:04d}_{content_hash[:8]}``。"""
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{doc_id}_{index:04d}_{content_hash}"


def _chunks(n: int, text_prefix: str = "第 {i} 个 chunk 的正文内容。") -> list[Chunk]:
    chunks: list[Chunk] = []
    for i in range(n):
        text = text_prefix.format(i=i)
        chunks.append(
            Chunk(
                id=_chunk_id(i, text),
                text=text,
                metadata={"source_path": "doc.pdf", "chunk_index": i, "page": i + 1},
                start_offset=i * 10,
                end_offset=i * 10 + 8,
                source_ref="d0" + "0" * 61,
            )
        )
    return chunks


def _vectors(n: int, dimension: int = 4, seed: float = 0.0) -> list[list[float]]:
    return [[seed + i + j / 10 for j in range(dimension)] for i in range(n)]


class _FakeVectorStore(BaseVectorStore):
    """内存 fake：dict 按 id 覆盖（模拟幂等），记录每批大小 / id 顺序 / trace。"""

    def __init__(self):
        self.records: dict[str, VectorRecord] = {}
        self.batch_sizes: list[int] = []
        self.id_batches: list[list[str]] = []
        self.trace_seen: list[bool] = []

    def upsert(self, records, *, trace=None, **kwargs):
        batch = list(records)
        self.batch_sizes.append(len(batch))
        self.id_batches.append([r.id for r in batch])
        self.trace_seen.append(trace is not None)
        for record in batch:
            if not isinstance(record, VectorRecord):
                raise ValueError("upsert requires VectorRecord instances")
            self.records[record.id] = record  # 同 id 覆盖 = 幂等
        return len(batch)

    def query(self, vector, top_k=10, *, filters=None, trace=None, **kwargs):
        return []

    def get_by_ids(self, ids, *, trace=None, **kwargs):
        return [
            {"id": r.id, "text": r.text, "metadata": dict(r.metadata)}
            for r in (self.records.get(i) for i in dict.fromkeys(ids))
            if r is not None
        ]

    def delete_by_metadata(self, filters, *, trace=None, **kwargs):
        if not isinstance(filters, dict) or not filters:
            raise ValueError("filters must be a non-empty dict (refuse to wipe all)")
        deleted = 0
        for rid, rec in list(self.records.items()):
            if all(rec.metadata.get(k) == v for k, v in filters.items()):
                del self.records[rid]
                deleted += 1
        return deleted

    def get_by_metadata(self, filters=None, *, trace=None, **kwargs):
        records = []
        for rec in self.records.values():
            if filters and not all(rec.metadata.get(k) == v for k, v in filters.items()):
                continue
            records.append({"id": rec.id, "text": rec.text, "metadata": dict(rec.metadata)})
        records.sort(key=lambda r: r["id"])
        return records

    def get_collection_stats(self, *, trace=None, **kwargs):
        records = self.get_by_metadata()
        return {"name": "fake", "chunk_count": len(records), "document_count": 0, "collections": []}


class _HashEmbedding(BaseEmbedding):
    """确定性 fake：按文本 sha256 派生固定维度向量（C8 链路测试用）。"""

    def __init__(self, dimension: int = 8):
        self.dimension_value = dimension

    @property
    def dimension(self) -> int | None:
        return self.dimension_value

    def embed(self, texts, *, trace=None, **kwargs):
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vectors.append([float(digest[i % len(digest)]) for i in range(self.dimension_value)])
        return vectors


@pytest.fixture
def clean_registry():
    saved = dict(VectorStoreFactory._registry)
    VectorStoreFactory._registry.clear()
    yield VectorStoreFactory
    VectorStoreFactory._registry.clear()
    VectorStoreFactory._registry.update(saved)


# ---------------------------------------------------------------------------
# happy path 与载荷契约
# ---------------------------------------------------------------------------


def test_upsert_happy_path(tmp_path):
    store = _FakeVectorStore()
    chunks = _chunks(3)
    summary = VectorUpserter(_settings(tmp_path), store=store).upsert(chunks, _vectors(3))
    assert isinstance(summary, UpsertSummary)
    assert summary.chunks == 3
    assert summary.batches == 1
    assert summary.written == 3
    assert summary.ids == [c.id for c in chunks]  # 顺序与输入一致
    assert store.batch_sizes == [3]
    assert len(store.records) == 3
    for chunk in chunks:
        record = store.records[chunk.id]
        assert record.id == chunk.id
        assert record.text == chunk.text
        assert record.metadata == chunk.metadata  # 完整 metadata 载荷


def test_metadata_is_copied_not_aliased(tmp_path):
    store = _FakeVectorStore()
    chunks = _chunks(1)
    VectorUpserter(_settings(tmp_path), store=store).upsert(chunks, _vectors(1))
    chunks[0].metadata["injected"] = "late-mutation"
    assert "injected" not in store.records[chunks[0].id].metadata


def test_vectors_passed_through_unchanged(tmp_path):
    store = _FakeVectorStore()
    chunks = _chunks(2)
    vectors = _vectors(2, dimension=6)
    VectorUpserter(_settings(tmp_path), store=store).upsert(chunks, vectors)
    assert store.records[chunks[0].id].vector == vectors[0]
    assert store.records[chunks[1].id].vector == vectors[1]


# ---------------------------------------------------------------------------
# 幂等性与确定性 id
# ---------------------------------------------------------------------------


def test_reupsert_same_chunks_is_idempotent(tmp_path):
    store = _FakeVectorStore()
    chunks = _chunks(3)
    upserter = VectorUpserter(_settings(tmp_path), store=store)
    first = upserter.upsert(chunks, _vectors(3))
    second = upserter.upsert(chunks, _vectors(3))
    assert first.ids == second.ids  # 同一 chunk 两次 upsert 产生相同 id
    assert second.written == 3  # 后端按 id 覆盖，再次"写入"3 条
    assert len(store.records) == 3  # 无重复记录
    assert store.batch_sizes == [3, 3]


def test_reupsert_same_id_replaces_record(tmp_path):
    store = _FakeVectorStore()
    chunk_v1 = Chunk(id=_chunk_id(0, "旧内容"), text="旧内容", metadata={"source_path": "a.pdf"})
    chunk_v2 = Chunk(id=_chunk_id(0, "旧内容"), text="旧内容（更新后）", metadata={"source_path": "a.pdf"})
    upserter = VectorUpserter(_settings(tmp_path), store=store)
    upserter.upsert([chunk_v1], [[1.0, 2.0]])
    upserter.upsert([chunk_v2], [[3.0, 4.0]])
    assert len(store.records) == 1  # 同 id 替换而非追加
    assert store.records[chunk_v1.id].text == "旧内容（更新后）"
    assert store.records[chunk_v1.id].vector == [3.0, 4.0]


def test_content_change_changes_id(tmp_path):
    text_a = "完全相同的位置与序号"
    text_b = "完全相同的位置与序号，但内容变了"
    id_a = _chunk_id(0, text_a)
    id_b = _chunk_id(0, text_b)
    assert id_a != id_b  # 内容变更 → content_hash 变化 → id 变更
    store = _FakeVectorStore()
    chunks = [
        Chunk(id=id_a, text=text_a, metadata={"source_path": "a.pdf", "chunk_index": 0}),
        Chunk(id=id_b, text=text_b, metadata={"source_path": "a.pdf", "chunk_index": 0}),
    ]
    summary = VectorUpserter(_settings(tmp_path), store=store).upsert(chunks, _vectors(2))
    assert summary.ids == [id_a, id_b]
    assert len(store.records) == 2


def test_same_content_yields_same_id_across_runs(tmp_path):
    text = "跨进程重复摄取的同一段内容"
    assert _chunk_id(3, text) == _chunk_id(3, text)  # 确定性
    assert _chunk_id(3, text) != _chunk_id(4, text)  # 序号参与 id


# ---------------------------------------------------------------------------
# 批量切分与顺序
# ---------------------------------------------------------------------------


def test_batch_splitting_follows_ingestion_batch_size(tmp_path):
    store = _FakeVectorStore()
    chunks = _chunks(5)
    upserter = VectorUpserter(_settings(tmp_path, batch_size=2), store=store)
    assert upserter.batch_size == 2
    summary = upserter.upsert(chunks, _vectors(5))
    assert store.batch_sizes == [2, 2, 1]
    assert summary.batches == 3
    assert summary.chunks == 5
    # 各批 id 顺序拼接后与输入一致（保序、不丢不重）。
    assert [i for batch in store.id_batches for i in batch] == [c.id for c in chunks]


def test_explicit_batch_size_overrides_settings(tmp_path):
    store = _FakeVectorStore()
    chunks = _chunks(5)
    upserter = VectorUpserter(_settings(tmp_path, batch_size=2), store=store, batch_size=3)
    assert upserter.batch_size == 3
    upserter.upsert(chunks, _vectors(5))
    assert store.batch_sizes == [3, 2]


def test_default_batch_size_from_settings(tmp_path):
    store = _FakeVectorStore()
    upserter = VectorUpserter(_settings(tmp_path, batch_size=7), store=store)
    assert upserter.batch_size == 7
    upserter.upsert(_chunks(8), _vectors(8))
    assert store.batch_sizes == [7, 1]


def test_single_batch_when_under_batch_size(tmp_path):
    store = _FakeVectorStore()
    VectorUpserter(_settings(tmp_path), store=store).upsert(_chunks(4), _vectors(4))
    assert store.batch_sizes == [4]  # 默认 batch_size=32


# ---------------------------------------------------------------------------
# 空输入
# ---------------------------------------------------------------------------


def test_empty_input_is_noop(tmp_path):
    store = _FakeVectorStore()
    trace = TraceContext()
    summary = VectorUpserter(_settings(tmp_path), store=store).upsert([], [], trace=trace)
    assert summary == UpsertSummary()
    assert store.batch_sizes == []  # 不触库
    assert trace.stages == []  # 空跑不打点


# ---------------------------------------------------------------------------
# 契约破坏 fail-fast
# ---------------------------------------------------------------------------


def test_count_mismatch_raises(tmp_path):
    store = _FakeVectorStore()
    upserter = VectorUpserter(_settings(tmp_path), store=store)
    with pytest.raises(VectorUpsertError, match="2 vectors for 3 chunks"):
        upserter.upsert(_chunks(3), _vectors(2))


def test_empty_chunks_with_vectors_raises(tmp_path):
    store = _FakeVectorStore()
    upserter = VectorUpserter(_settings(tmp_path), store=store)
    with pytest.raises(VectorUpsertError, match="1 vectors for 0 chunks"):
        upserter.upsert([], _vectors(1))


def test_empty_vector_raises(tmp_path):
    store = _FakeVectorStore()
    chunks = _chunks(2)
    upserter = VectorUpserter(_settings(tmp_path), store=store)
    with pytest.raises(VectorUpsertError, match="empty vector"):
        upserter.upsert(chunks, [[1.0, 2.0], []])


def test_dimension_mismatch_raises(tmp_path):
    store = _FakeVectorStore()
    chunks = _chunks(2)
    upserter = VectorUpserter(_settings(tmp_path), store=store)
    with pytest.raises(VectorUpsertError, match="inconsistent dimension"):
        upserter.upsert(chunks, [[1.0, 2.0], [1.0, 2.0, 3.0]])


def test_non_numeric_vector_raises(tmp_path):
    store = _FakeVectorStore()
    chunks = _chunks(1)
    upserter = VectorUpserter(_settings(tmp_path), store=store)
    with pytest.raises(VectorUpsertError, match="must be numbers"):
        upserter.upsert(chunks, [["x", 1.0]])


def test_bool_vector_component_raises(tmp_path):
    store = _FakeVectorStore()
    chunks = _chunks(1)
    upserter = VectorUpserter(_settings(tmp_path), store=store)
    with pytest.raises(VectorUpsertError, match="must be numbers"):
        upserter.upsert(chunks, [[True, 1.0]])


def test_non_list_vector_raises(tmp_path):
    store = _FakeVectorStore()
    chunks = _chunks(1)
    upserter = VectorUpserter(_settings(tmp_path), store=store)
    with pytest.raises(VectorUpsertError, match="must be a list"):
        upserter.upsert(chunks, [42])


def test_duplicate_chunk_id_in_one_call_raises(tmp_path):
    store = _FakeVectorStore()
    chunk = _chunks(1)[0]
    upserter = VectorUpserter(_settings(tmp_path), store=store)
    with pytest.raises(VectorUpsertError, match="duplicate chunk_id"):
        upserter.upsert([chunk, chunk], _vectors(2))


def test_single_chunk_instance_raises(tmp_path):
    store = _FakeVectorStore()
    chunk = _chunks(1)[0]
    upserter = VectorUpserter(_settings(tmp_path), store=store)
    with pytest.raises(VectorUpsertError, match="single Chunk instance"):
        upserter.upsert(chunk, _vectors(1))


def test_invalid_chunk_id_raises(tmp_path):
    store = _FakeVectorStore()
    bad = Chunk(id="valid_id", text="t", metadata={})
    bad.id = ""  # 模拟上游契约破坏（绕过构造期校验）
    upserter = VectorUpserter(_settings(tmp_path), store=store)
    with pytest.raises(VectorUpsertError, match="chunk_id must be a non-empty string"):
        upserter.upsert([bad], [[1.0]])


def test_store_returning_invalid_count_raises(tmp_path):
    class _BadCountStore(_FakeVectorStore):
        def upsert(self, records, *, trace=None, **kwargs):
            super().upsert(records, trace=trace, **kwargs)
            return "3"  # 非 int

    store = _BadCountStore()
    upserter = VectorUpserter(_settings(tmp_path), store=store)
    with pytest.raises(VectorUpsertError, match="invalid count"):
        upserter.upsert(_chunks(3), _vectors(3))


def test_store_returning_out_of_range_count_raises(tmp_path):
    class _OverflowStore(_FakeVectorStore):
        def upsert(self, records, *, trace=None, **kwargs):
            super().upsert(records, trace=trace, **kwargs)
            return len(list(records)) + 1  # 超过批大小

    store = _OverflowStore()
    upserter = VectorUpserter(_settings(tmp_path), store=store)
    with pytest.raises(VectorUpsertError, match="invalid count"):
        upserter.upsert(_chunks(2), _vectors(2))


def test_store_exception_propagates(tmp_path):
    class _BoomStore(_FakeVectorStore):
        def upsert(self, records, *, trace=None, **kwargs):
            raise RuntimeError("backend down")

    upserter = VectorUpserter(_settings(tmp_path), store=_BoomStore())
    with pytest.raises(RuntimeError, match="backend down"):
        upserter.upsert(_chunks(2), _vectors(2))


# ---------------------------------------------------------------------------
# 构造参数校验
# ---------------------------------------------------------------------------


def test_invalid_batch_size_raises(tmp_path):
    store = _FakeVectorStore()
    with pytest.raises(ValueError, match="positive integer"):
        VectorUpserter(_settings(tmp_path), store=store, batch_size=0)
    with pytest.raises(ValueError, match="positive integer"):
        VectorUpserter(_settings(tmp_path), store=store, batch_size="2")


def test_settings_required_without_store():
    with pytest.raises(ValueError, match="settings is required"):
        VectorUpserter()


# ---------------------------------------------------------------------------
# 工厂路径（settings.vector_store.backend 路由）
# ---------------------------------------------------------------------------


def test_settings_driven_factory_store(tmp_path, clean_registry):
    created = _FakeVectorStore()
    clean_registry.register("chroma")(lambda vs_config, **kwargs: created)
    settings = _settings(tmp_path)
    upserter = VectorUpserter(settings)  # store 未注入 → 走工厂
    assert upserter.store is created
    summary = upserter.upsert(_chunks(2), _vectors(2))
    assert summary.written == 2
    assert created.batch_sizes == [2]


# ---------------------------------------------------------------------------
# trace 打点
# ---------------------------------------------------------------------------


def test_trace_records_storage_stage(tmp_path):
    store = _FakeVectorStore()
    trace = TraceContext()
    VectorUpserter(_settings(tmp_path, batch_size=2), store=store).upsert(
        _chunks(3), _vectors(3), trace=trace
    )
    record = trace.get_stage("storage")
    assert record["method"] == "vector_upserter"
    assert record["details"]["chunks"] == 3
    assert record["details"]["batches"] == 2
    assert record["details"]["batch_size"] == 2
    assert record["details"]["written"] == 3
    assert record["details"]["elapsed_ms"] >= 0


def test_trace_passed_through_to_store(tmp_path):
    store = _FakeVectorStore()
    trace = TraceContext()
    VectorUpserter(_settings(tmp_path), store=store).upsert(_chunks(1), _vectors(1), trace=trace)
    assert store.trace_seen == [True]


def test_no_trace_no_stage(tmp_path):
    store = _FakeVectorStore()
    VectorUpserter(_settings(tmp_path), store=store).upsert(_chunks(2), _vectors(2))
    assert store.trace_seen == [False]


# ---------------------------------------------------------------------------
# C8 → C12 链路
# ---------------------------------------------------------------------------


def test_dense_encoder_output_upserts_end_to_end(tmp_path):
    settings = _settings(tmp_path)
    chunks = _chunks(4)
    vectors = DenseEncoder(settings, embedding=_HashEmbedding(dimension=8)).encode(chunks)
    store = _FakeVectorStore()
    summary = VectorUpserter(settings, store=store).upsert(chunks, vectors)
    assert summary.written == 4
    assert summary.ids == [c.id for c in chunks]
    assert all(len(store.records[c.id].vector) == 8 for c in chunks)
    assert store.records[chunks[2].id].text == chunks[2].text


def test_dense_encoder_chain_idempotent_rerun(tmp_path):
    settings = _settings(tmp_path)
    chunks = _chunks(3)
    encoder = DenseEncoder(settings, embedding=_HashEmbedding(dimension=4))
    store = _FakeVectorStore()
    upserter = VectorUpserter(settings, store=store)
    first = upserter.upsert(chunks, encoder.encode(chunks))
    second = upserter.upsert(chunks, encoder.encode(chunks))
    assert first.ids == second.ids
    assert len(store.records) == 3
    # 向量确定性：重跑后存储中的向量与首次一致。
    assert store.records[first.ids[0]].vector == store.records[second.ids[0]].vector
