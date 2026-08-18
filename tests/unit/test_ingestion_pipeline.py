"""``ingestion.pipeline.IngestionPipeline`` 单元测试（阶段 C14）。

纯离线端到端：仅在外部边界注入 fake（Loader / Embedding / VectorStore），
其余 C2-C13 组件全部使用真实实现（SQLite / JSON 落在 ``tmp_path``），
验证编排顺序、幂等性、跳过语义、降级标记与失败处理。

覆盖：
- happy path：三路产物落盘（BM25 JSON / fake 向量库 / ImageStorage SQLite）；
- 未变更跳过（C2 should_skip）与 force 重摄取（替换而非重复）；
- LLM/Vision 关闭时的降级标记仍完成摄取（C5-C7）；
- 空文档与加载失败（清晰异常 + mark_failed）；
- trace 阶段顺序与 on_progress 事件序列；
- collection 元数据标记与扩展名路由。
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from core.trace.trace_context import TraceContext
from core.types import Document, image_placeholder, make_image_id
from ingestion import IngestionPipeline, IngestionPipelineError, IngestionResult
from ingestion.embedding import BatchProcessor, DenseEncoder, SparseEncoder
from ingestion.storage import BM25Indexer, ImageStorage, VectorUpserter
from libs.embedding.base_embedding import BaseEmbedding
from libs.loader.base_loader import BaseLoader
from libs.loader.file_integrity import SQLiteIntegrityChecker
from libs.vector_store.base_vector_store import BaseVectorStore, VectorRecord

# ---------------------------------------------------------------------------
# 测试语料与 fake 组件
# ---------------------------------------------------------------------------

_PARAGRAPHS = [
    "Alpha section introduces the ingestion pipeline stages and strict ordering.",
    "Beta section describes integrity checking with sha256 hashing and skip.",
    "Gamma section covers loading documents and extracting embedded images.",
    "Delta section explains chunk splitting with deterministic identifiers.",
    "Epsilon section details dense and sparse encoding in stable batches.",
    "Zeta section stores vectors, bm25 postings and image files atomically.",
]

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00image-payload\x00" * 4

_YAML = dedent(
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
    splitter:
      provider: recursive
      chunk_size: 90
      chunk_overlap: 10
    ingestion:
      chunk_refiner:
        use_llm: false
      metadata_enricher:
        use_llm: false
      image_captioner:
        enabled: false
      batch_size: 2
    """
)


class FakeEmbedding(BaseEmbedding):
    """确定性哈希向量（无网络）：dim=8，同文本同向量。"""

    def __init__(self, dimension: int = 8) -> None:
        self._dimension = dimension
        self.calls: list[list[str]] = []

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts, *, trace=None, **kwargs):
        texts = list(texts)
        self.calls.append(texts)
        return [
            [
                round(int(hashlib.sha256(t.encode()).hexdigest()[i * 2 : i * 2 + 2], 16) % 97 / 97.0, 6)
                for i in range(self._dimension)
            ]
            for t in texts
        ]


class FakeVectorStore(BaseVectorStore):
    """内存向量库：按 id 覆盖（幂等），记录调用批次。"""

    def __init__(self) -> None:
        self.records: dict[str, VectorRecord] = {}
        self.upsert_calls: list[list[str]] = []

    def upsert(self, records, *, trace=None, **kwargs) -> int:
        records = list(records)
        self.upsert_calls.append([r.id for r in records])
        for record in records:
            self.records[record.id] = record
        return len(records)

    def query(self, vector, top_k=10, *, filters=None, trace=None, **kwargs):
        results = sorted(self.records.values(), key=lambda r: r.id)[:top_k]
        from libs.vector_store.base_vector_store import QueryResult

        return [QueryResult(id=r.id, score=1.0, text=r.text, metadata=r.metadata) for r in results]

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


class FakeLoader(BaseLoader):
    """可控 fake loader：产线内容确定性、可选图片/空文本/失败。"""

    def __init__(
        self,
        images_root: Path,
        *,
        with_image: bool = True,
        empty_text: bool = False,
        fail: bool = False,
    ) -> None:
        self._images_root = images_root
        self._with_image = with_image
        self._empty_text = empty_text
        self._fail = fail
        self.load_calls: list[str] = []

    def load(self, path: str) -> Document:
        self.load_calls.append(path)
        if self._fail:
            raise RuntimeError("fake loader exploded")
        data = Path(path).read_bytes()
        doc_hash = hashlib.sha256(data).hexdigest()
        if self._empty_text:
            return Document(
                id=doc_hash,
                text="",
                metadata={"source_path": path, "doc_type": "fake", "sha256": doc_hash},
            )
        paragraphs = list(_PARAGRAPHS)
        images_meta: list[dict] = []
        if self._with_image:
            image_id = make_image_id(doc_hash, page=0, seq=0)
            image_dir = self._images_root / "loader_extract"
            image_dir.mkdir(parents=True, exist_ok=True)
            image_path = image_dir / f"{image_id}.png"
            image_path.write_bytes(_PNG_BYTES)
            images_meta.append(
                {
                    "id": image_id,
                    "path": str(image_path),
                    "page": 0,
                    "text_offset": 0,
                    "text_length": len(image_placeholder(image_id)),
                    "position": {},
                }
            )
            # 把占位符插到第 3 段之后，保证与说明文字同 chunk 语义。
            paragraphs = paragraphs[:3] + [image_placeholder(image_id)] + paragraphs[3:]
        text = "\n\n".join(paragraphs)
        metadata = {"source_path": path, "doc_type": "fake", "sha256": doc_hash}
        if images_meta:
            metadata["images"] = images_meta
        return Document(id=doc_hash, text=text, metadata=metadata)


# ---------------------------------------------------------------------------
# 组装工具
# ---------------------------------------------------------------------------


def _build_pipeline(tmp_path: Path, loader: BaseLoader, store: BaseVectorStore):
    """全真实组件 + 边界 fake 的 pipeline（所有持久化落在 tmp_path）。"""
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(_YAML, encoding="utf-8")
    settings = load_settings(settings_path)

    embedding = FakeEmbedding()
    bm25 = BM25Indexer(settings, index_path=tmp_path / "db" / "bm25" / "index.json")
    integrity = SQLiteIntegrityChecker(db_path=tmp_path / "db" / "ingestion_history.db")
    image_storage = ImageStorage(
        images_root=str(tmp_path / "images"),
        db_path=str(tmp_path / "db" / "image_index.db"),
    )
    pipeline = IngestionPipeline(
        settings,
        integrity_checker=integrity,
        loader=loader,
        dense_encoder=DenseEncoder(settings, embedding),
        sparse_encoder=SparseEncoder(settings),
        batch_processor=BatchProcessor(settings),
        bm25_indexer=bm25,
        vector_upserter=VectorUpserter(settings, store=store),
        image_storage=image_storage,
    )
    return pipeline, {
        "settings": settings,
        "embedding": embedding,
        "bm25": bm25,
        "integrity": integrity,
        "image_storage": image_storage,
    }


def _write_source(tmp_path: Path, content: bytes = b"source-document-bytes-v1") -> Path:
    source = tmp_path / "sample.pdf"
    source.write_bytes(content)
    return source


# ---------------------------------------------------------------------------
# happy path：三路产物 + 元数据标记
# ---------------------------------------------------------------------------


def test_happy_path_end_to_end_artifacts(tmp_path):
    loader = FakeLoader(tmp_path)
    store = FakeVectorStore()
    pipeline, parts = _build_pipeline(tmp_path, loader, store)
    source = _write_source(tmp_path)

    result = pipeline.run(str(source), collection="docs")

    assert isinstance(result, IngestionResult)
    assert result.status == "success"
    assert result.chunks > 0
    assert result.images == 1
    assert result.written == result.chunks
    assert result.doc_hash == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result.trace_id
    assert result.elapsed_ms >= 0

    # 向量库：等量记录、collection 标记、text/metadata 载荷完整。
    assert len(store.records) == result.chunks
    assert all(r.metadata["collection"] == "docs" for r in store.records.values())
    assert all(r.text for r in store.records.values())
    assert all(r.metadata["source_path"] == str(source) for r in store.records.values())

    # BM25：索引文件落盘，重载后可查询且命中本批 chunk。
    index_file = tmp_path / "db" / "bm25" / "index.json"
    assert index_file.is_file()
    reloaded = BM25Indexer(index_path=index_file).load()
    assert len(reloaded) == result.chunks
    hits = reloaded.query(["section"], top_k=5)
    assert hits and all(h["chunk_id"] in store.records for h in hits)

    # 图片：登记 SQLite 索引 + 文件落在 {doc_hash}/ 目录（C3/C13 布局）。
    image_id = make_image_id(result.doc_hash, 0, 0)
    record = parts["image_storage"].get(image_id)
    assert record is not None
    assert record.doc_hash == result.doc_hash
    assert Path(record.file_path).read_bytes() == _PNG_BYTES
    assert (tmp_path / "images" / result.doc_hash / f"{image_id}.png").is_file()

    # 成功后完整性记录生效：同 hash 可跳过。
    assert parts["integrity"].should_skip(result.doc_hash) is True


def test_llm_disabled_degradation_markers_still_ingest(tmp_path):
    """LLM/Vision 全关（默认配置）：降级标记存在，摄取照常完成。"""
    loader = FakeLoader(tmp_path)
    store = FakeVectorStore()
    pipeline, parts = _build_pipeline(tmp_path, loader, store)
    source = _write_source(tmp_path)

    result = pipeline.run(str(source))

    assert result.status == "success"
    records = store.records.values()
    # C5/C6 规则模式标记。
    assert all(r.metadata["refined_by"] == "rule" for r in records)
    assert all(r.metadata["enriched_by"] == "rule" for r in records)
    assert all(r.metadata.get("title") and r.metadata.get("tags") for r in records)
    # C7 禁用模式：含图 chunk 保留引用并打降级标记，无 caption。
    with_image = [r for r in records if r.metadata.get("image_refs")]
    assert with_image, "占位符所在 chunk 应携带 image_refs"
    for record in with_image:
        assert record.metadata["has_unprocessed_images"] is True
        assert "image_captions" not in record.metadata
        assert record.metadata["images"][0]["id"] in record.metadata["image_refs"]
    # 图片本体仍持久化（图片登记不依赖 Vision LLM）。
    assert parts["image_storage"].count(doc_hash=result.doc_hash) == 1


# ---------------------------------------------------------------------------
# 跳过 / 幂等
# ---------------------------------------------------------------------------


def test_unchanged_file_is_skipped_without_touching_stores(tmp_path):
    loader = FakeLoader(tmp_path)
    store = FakeVectorStore()
    pipeline, parts = _build_pipeline(tmp_path, loader, store)
    source = _write_source(tmp_path)

    first = pipeline.run(str(source))
    assert first.status == "success"
    store_calls_after_first = len(store.upsert_calls)
    loader_calls_after_first = len(loader.load_calls)
    bm25_docs_after_first = len(parts["bm25"])

    second = pipeline.run(str(source))

    assert second.status == "skipped"
    assert second.chunks == 0
    assert second.doc_hash == first.doc_hash
    assert len(loader.load_calls) == loader_calls_after_first  # 零成本跳过：不再 load
    assert len(store.upsert_calls) == store_calls_after_first
    assert len(parts["bm25"]) == bm25_docs_after_first


def test_force_reingest_replaces_instead_of_duplicating(tmp_path):
    loader = FakeLoader(tmp_path)
    store = FakeVectorStore()
    pipeline, parts = _build_pipeline(tmp_path, loader, store)
    source = _write_source(tmp_path)

    first = pipeline.run(str(source))
    second = pipeline.run(str(source), force=True)

    assert second.status == "success"
    # 确定性 ID：两次产出完全一致的 chunk 集合。
    assert set(store.records) == {r.id for r in store.records.values()}
    assert len(store.records) == first.chunks == second.chunks
    # BM25：同 chunk_id 替换而非追加。
    assert len(parts["bm25"]) == first.chunks
    # 图片：重复 put 覆盖文件 + 替换行，不产生重复行。
    assert parts["image_storage"].count(doc_hash=first.doc_hash) == 1


def test_changed_file_reingests_as_new_version(tmp_path):
    loader = FakeLoader(tmp_path)
    store = FakeVectorStore()
    pipeline, parts = _build_pipeline(tmp_path, loader, store)
    source = _write_source(tmp_path)

    first = pipeline.run(str(source))
    source.write_bytes(b"source-document-bytes-v2")
    second = pipeline.run(str(source))

    assert second.status == "success"
    assert second.doc_hash != first.doc_hash
    # 新版本 chunk 追加，旧版本保留（BM25 增量、向量库按 id 并存）。
    assert len(store.records) == first.chunks + second.chunks
    assert len(parts["bm25"]) == first.chunks + second.chunks


# ---------------------------------------------------------------------------
# 失败与空文档
# ---------------------------------------------------------------------------


def test_failed_load_raises_stage_error_and_marks_failed(tmp_path):
    loader = FakeLoader(tmp_path, fail=True)
    store = FakeVectorStore()
    pipeline, parts = _build_pipeline(tmp_path, loader, store)
    source = _write_source(tmp_path)
    file_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    with pytest.raises(IngestionPipelineError) as exc_info:
        pipeline.run(str(source))

    assert exc_info.value.stage == "load"
    assert "stage 'load' failed" in str(exc_info.value)
    assert "fake loader exploded" in str(exc_info.value)
    # 失败落账：status=failed → 不阻塞下次重跑。
    assert parts["integrity"].should_skip(file_hash) is False
    assert store.upsert_calls == []


def test_missing_file_fails_at_integrity_stage(tmp_path):
    loader = FakeLoader(tmp_path)
    store = FakeVectorStore()
    pipeline, _ = _build_pipeline(tmp_path, loader, store)

    with pytest.raises(IngestionPipelineError) as exc_info:
        pipeline.run(str(tmp_path / "nope.pdf"))

    assert exc_info.value.stage == "integrity"
    assert "integrity" in str(exc_info.value)


def test_unknown_extension_without_injected_loader(tmp_path):
    """未注入 loader 时按扩展名路由；未知扩展给出清晰错误。"""
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(_YAML, encoding="utf-8")
    settings = load_settings(settings_path)
    # 不注入 loader / 向量库走默认构造：vector_store 用 fake 以免真实 Chroma。
    pipeline = IngestionPipeline(
        settings,
        vector_upserter=VectorUpserter(settings, store=FakeVectorStore()),
        image_storage=ImageStorage(
            images_root=str(tmp_path / "images"),
            db_path=str(tmp_path / "db" / "image_index.db"),
        ),
        integrity_checker=SQLiteIntegrityChecker(db_path=tmp_path / "db" / "ih.db"),
        bm25_indexer=BM25Indexer(settings, index_path=tmp_path / "db" / "bm25" / "index.json"),
    )
    source = tmp_path / "notes.docx"
    source.write_bytes(b"not a pdf")

    with pytest.raises(IngestionPipelineError) as exc_info:
        pipeline.run(str(source))

    assert exc_info.value.stage == "load"
    assert "no loader registered" in str(exc_info.value)
    assert "'.docx'" in str(exc_info.value)


def test_empty_document_succeeds_with_zero_chunks(tmp_path):
    loader = FakeLoader(tmp_path, empty_text=True)
    store = FakeVectorStore()
    pipeline, parts = _build_pipeline(tmp_path, loader, store)
    source = _write_source(tmp_path)

    result = pipeline.run(str(source))

    assert result.status == "success"
    assert result.chunks == 0
    assert result.written == 0
    assert result.images == 0
    assert store.upsert_calls == []  # 空批次不触库
    assert len(parts["bm25"]) == 0
    assert parts["integrity"].should_skip(result.doc_hash) is True


# ---------------------------------------------------------------------------
# trace 顺序与进度回调
# ---------------------------------------------------------------------------


def test_trace_stage_order_and_pipeline_markers(tmp_path):
    loader = FakeLoader(tmp_path)
    store = FakeVectorStore()
    pipeline, _ = _build_pipeline(tmp_path, loader, store)
    source = _write_source(tmp_path)
    trace = TraceContext()

    pipeline.run(str(source), trace=trace)

    stage_names = [s["stage"] for s in trace.stages]
    # Pipeline 级标记按执行顺序完整出现。
    pipeline_markers = [s["stage"] for s in trace.stages if s.get("method") == "pipeline"]
    assert pipeline_markers[:6] == [
        "integrity", "load", "split", "transform", "embed", "store",
    ]
    # 组件打点（transform/embedding/storage）相对顺序正确。
    assert stage_names.index("transform") < stage_names.index("embedding")
    assert stage_names.index("embedding") < stage_names.index("storage")


def test_skip_path_records_integrity_marker_only(tmp_path):
    loader = FakeLoader(tmp_path)
    store = FakeVectorStore()
    pipeline, _ = _build_pipeline(tmp_path, loader, store)
    source = _write_source(tmp_path)
    trace = TraceContext()

    pipeline.run(str(source), trace=trace)
    trace.stages.clear()
    result = pipeline.run(str(source), trace=trace)

    assert result.status == "skipped"
    markers = [s for s in trace.stages if s.get("method") == "pipeline"]
    assert [s["stage"] for s in markers] == ["integrity"]
    assert markers[0]["details"]["skipped"] is True


def test_on_progress_event_sequence(tmp_path):
    loader = FakeLoader(tmp_path)
    store = FakeVectorStore()
    pipeline, _ = _build_pipeline(tmp_path, loader, store)
    source = _write_source(tmp_path)
    events: list[tuple[str, int, int]] = []

    result = pipeline.run(str(source), on_progress=lambda s, c, t: events.append((s, c, t)))

    assert result.status == "success"
    assert events[0] == ("load", 1, 1)
    assert events[1] == ("split", 1, 1)
    assert events[2:5] == [("transform", 1, 3), ("transform", 2, 3), ("transform", 3, 3)]

    embed_events = [e for e in events if e[0] == "embed"]
    expected_batches = 2 * math.ceil(result.chunks / 2)  # dense 一趟 + sparse 一趟
    assert embed_events == [("embed", i, expected_batches) for i in range(1, expected_batches + 1)]

    upsert_events = [e for e in events if e[0] == "upsert"]
    assert upsert_events == [("upsert", 1, 3), ("upsert", 2, 3), ("upsert", 3, 3)]
    assert events[-1] == ("upsert", 3, 3)


def test_result_to_dict_roundtrip(tmp_path):
    loader = FakeLoader(tmp_path)
    store = FakeVectorStore()
    pipeline, _ = _build_pipeline(tmp_path, loader, store)
    source = _write_source(tmp_path)

    result = pipeline.run(str(source))
    payload = result.to_dict()

    assert payload["status"] == "success"
    assert payload["chunks"] == result.chunks
    assert payload["doc_hash"] == result.doc_hash
    assert json.dumps(payload)  # 可 JSON 序列化
