"""IR4：错误分类、指数退避重试与 store 回滚（INGESTION_ROBUSTNESS_SPEC M4）。

全离线：fake 时钟（注入 ``sleep``）+ 固定随机源（注入 ``random_fn``），
不发生真实等待；外部边界（embedding / 向量库）用可控失败的 fake。
"""

from __future__ import annotations

import hashlib
import sqlite3
import urllib.error
from pathlib import Path

import httpx
import pytest

from core.settings import ConfigError, load_settings
from core.trace.trace_context import TraceContext
from core.types import make_image_id
from ingestion import IngestionPipeline, IngestionPipelineError
from ingestion.document_manager import DocumentManager
from ingestion.embedding import BatchProcessor, DenseEncoder, SparseEncoder
from ingestion.errors import MAX_BACKOFF_S, compute_backoff, is_retryable
from ingestion.storage import BM25Indexer, ImageStorage, VectorUpserter
from ingestion.storage.vector_upserter import VectorUpsertError
from libs.embedding.openai_embedding import EmbeddingRequestError
from libs.loader.file_integrity import SQLiteIntegrityChecker
from tests.unit.test_ingestion_pipeline import (
    _YAML,
    FakeEmbedding,
    FakeLoader,
    FakeVectorStore,
    _write_source,
)

_RETRY_YAML = (
    "  retry:\n"
    "    max_attempts: {max_attempts}\n"
    "    initial_backoff_s: 2.0\n"
    "    backoff_multiplier: 2.0\n"
    "    backoff_jitter: 0.2\n"
)


# ---------------------------------------------------------------------------
# fakes / builders
# ---------------------------------------------------------------------------


class FlakyEmbedding(FakeEmbedding):
    """前 ``failures`` 次调用抛出 ``error``，之后正常返回。"""

    def __init__(self, error: BaseException, failures: int) -> None:
        super().__init__()
        self._error = error
        self._failures = failures
        self.attempted = 0

    def embed(self, texts, *, trace=None, **kwargs):
        self.attempted += 1
        if self.attempted <= self._failures:
            raise self._error
        return super().embed(texts, trace=trace, **kwargs)


class FlakyVectorStore(FakeVectorStore):
    """前 ``failures`` 次 upsert 抛出 ``error``（``failures=None`` 表示永远失败）。"""

    def __init__(self, error: BaseException | None = None, failures: int | None = 0) -> None:
        super().__init__()
        self.error = error
        self.failures = failures
        self.attempted = 0

    def upsert(self, records, *, trace=None, **kwargs) -> int:
        self.attempted += 1
        if self.error is not None and (self.failures is None or self.attempted <= self.failures):
            raise self.error
        return super().upsert(records, trace=trace, **kwargs)


def _build(tmp_path: Path, *, max_attempts: int | None, embedding=None, store=None):
    yaml_text = _YAML
    if max_attempts is not None:
        yaml_text += _RETRY_YAML.format(max_attempts=max_attempts)
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(yaml_text, encoding="utf-8")
    settings = load_settings(settings_path)

    embedding = embedding if embedding is not None else FakeEmbedding()
    store = store if store is not None else FakeVectorStore()
    sleeps: list[float] = []
    integrity = SQLiteIntegrityChecker(db_path=tmp_path / "db" / "ingestion_history.db")
    bm25 = BM25Indexer(settings, index_path=tmp_path / "db" / "bm25" / "index.json")
    image_storage = ImageStorage(
        images_root=str(tmp_path / "images"),
        db_path=str(tmp_path / "db" / "image_index.db"),
    )
    pipeline = IngestionPipeline(
        settings,
        integrity_checker=integrity,
        loader=FakeLoader(tmp_path),
        dense_encoder=DenseEncoder(settings, embedding),
        sparse_encoder=SparseEncoder(settings),
        batch_processor=BatchProcessor(settings),
        bm25_indexer=bm25,
        vector_upserter=VectorUpserter(settings, store=store),
        image_storage=image_storage,
        sleep=sleeps.append,
        random_fn=lambda: 0.5,  # 抖动因子恰为 1.0
    )
    return pipeline, {
        "settings": settings,
        "embedding": embedding,
        "store": store,
        "sleeps": sleeps,
        "integrity": integrity,
        "bm25": bm25,
        "image_storage": image_storage,
        "index_file": tmp_path / "db" / "bm25" / "index.json",
    }


def _bm25_count(index_file: Path, doc_hash: str) -> int:
    if not index_file.is_file():
        return 0
    return BM25Indexer(index_path=index_file).load().remove_document(doc_hash)


def _doc_hash(source: Path) -> str:
    return hashlib.sha256(source.read_bytes()).hexdigest()


def _stages(trace: TraceContext, name: str) -> list[dict]:
    return [s for s in trace.stages if s["stage"] == name and s.get("method") == "pipeline"]


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.invalid/embeddings")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"status {status}", request=request, response=response)


def _chained(outer: BaseException, cause: BaseException) -> BaseException:
    outer.__cause__ = cause
    return outer


# ---------------------------------------------------------------------------
# 错误分类表
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("read timed out"),
        ConnectionRefusedError("connection refused"),
        ConnectionResetError("reset by peer"),
        httpx.ConnectTimeout("connect timeout"),
        httpx.ConnectError("connect failed"),
        _http_status_error(503),
        _http_status_error(429),
        urllib.error.URLError("temporary failure in name resolution"),
        urllib.error.HTTPError("https://x", 502, "bad gateway", {}, None),
        EmbeddingRequestError("openai", "server error", status=500),
        _chained(EmbeddingRequestError("openai", "network error"), urllib.error.URLError("refused")),
        sqlite3.OperationalError("database is locked"),
        _chained(IngestionPipelineError("embed failed", stage="embed"), TimeoutError("slow")),
        _chained(IngestionPipelineError("store failed", stage="store"), sqlite3.OperationalError("database is busy")),
    ],
    ids=lambda e: f"{type(e).__name__}:{e}"[:60],
)
def test_classifier_retryable(exc):
    assert is_retryable(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        FileNotFoundError("missing.pdf"),
        PermissionError("denied"),
        ValueError("bad value"),
        ConfigError("invalid config"),
        RuntimeError("unknown failure"),
        _http_status_error(400),
        urllib.error.HTTPError("https://x", 401, "unauthorized", {}, None),
        EmbeddingRequestError("openai", "invalid api key", status=401),
        EmbeddingRequestError("openai", "texts must be non-empty"),
        sqlite3.OperationalError("no such table: chunks"),
        VectorUpsertError("dimension mismatch: expected 8, got 4"),
        _chained(IngestionPipelineError("load failed", stage="load"), TimeoutError("slow disk")),
        _chained(IngestionPipelineError("split failed", stage="split"), ConnectionError("odd")),
        _chained(IngestionPipelineError("store failed", stage="store"), VectorUpsertError("dimension mismatch")),
        IngestionPipelineError("no loader registered", stage="load"),
    ],
    ids=lambda e: f"{type(e).__name__}:{e}"[:60],
)
def test_classifier_not_retryable(exc):
    assert is_retryable(exc) is False


# ---------------------------------------------------------------------------
# 退避序列
# ---------------------------------------------------------------------------


def _backoff(attempt: int, rnd: float, **overrides) -> float:
    params = {"initial_backoff_s": 2.0, "backoff_multiplier": 2.0, "backoff_jitter": 0.2}
    params.update(overrides)
    return compute_backoff(attempt, random_fn=lambda: rnd, **params)


def test_backoff_sequence_without_jitter_offset():
    assert [_backoff(n, 0.5) for n in (1, 2, 3)] == [2.0, 4.0, 8.0]


@pytest.mark.parametrize("attempt,base", [(1, 2.0), (2, 4.0), (3, 8.0)])
def test_backoff_jitter_bounds(attempt, base):
    low = _backoff(attempt, 0.0)
    high = _backoff(attempt, 0.999999)
    assert low == pytest.approx(base * 0.8)
    assert high == pytest.approx(base * 1.2, rel=1e-5)
    assert base * 0.8 <= _backoff(attempt, 0.37) <= base * 1.2


def test_backoff_is_capped():
    assert _backoff(10, 0.999999) == MAX_BACKOFF_S
    assert _backoff(10, 0.0) == MAX_BACKOFF_S


def test_backoff_rejects_attempt_zero():
    with pytest.raises(ValueError):
        _backoff(0, 0.5)


# ---------------------------------------------------------------------------
# 管线重试语义
# ---------------------------------------------------------------------------


def test_default_max_attempts_one_never_retries(tmp_path):
    embedding = FlakyEmbedding(ConnectionError("refused"), failures=1)
    pipeline, parts = _build(tmp_path, max_attempts=None, embedding=embedding)
    assert parts["settings"].ingestion.retry.max_attempts == 1
    source = _write_source(tmp_path)
    trace = TraceContext()

    with pytest.raises(IngestionPipelineError) as exc_info:
        pipeline.run(str(source), collection="a", trace=trace)

    assert exc_info.value.stage == "embed"
    assert exc_info.value.attempts == 1
    assert embedding.attempted == 1
    assert parts["sleeps"] == []
    assert _stages(trace, "retry") == []


def test_default_success_trace_has_no_attempt_details(tmp_path):
    pipeline, _ = _build(tmp_path, max_attempts=None)
    source = _write_source(tmp_path)
    trace = TraceContext()

    pipeline.run(str(source), collection="a", trace=trace)

    for name in ("embed", "store"):
        assert "attempts" not in _stages(trace, name)[0]["details"]


def test_embed_retry_succeeds_on_third_attempt(tmp_path):
    embedding = FlakyEmbedding(TimeoutError("read timed out"), failures=2)
    pipeline, parts = _build(tmp_path, max_attempts=3, embedding=embedding)
    source = _write_source(tmp_path)
    trace = TraceContext()

    result = pipeline.run(str(source), collection="a", trace=trace)

    assert result.status == "success"
    assert parts["sleeps"] == [2.0, 4.0]
    retries = _stages(trace, "retry")
    assert [r["details"]["attempt"] for r in retries] == [1, 2]
    assert all(r["details"]["stage"] == "embed" for r in retries)
    assert all(r["details"]["max_attempts"] == 3 for r in retries)
    assert "TimeoutError" in retries[0]["details"]["error"]
    assert _stages(trace, "embed")[0]["details"]["attempts"] == 3
    record = parts["integrity"].get_record(str(source), "a")
    assert record["status"] == "success"


def test_permanent_error_is_not_retried(tmp_path):
    embedding = FlakyEmbedding(EmbeddingRequestError("openai", "invalid api key", status=401), failures=5)
    pipeline, parts = _build(tmp_path, max_attempts=3, embedding=embedding)
    source = _write_source(tmp_path)

    with pytest.raises(IngestionPipelineError) as exc_info:
        pipeline.run(str(source), collection="a")

    assert exc_info.value.stage == "embed"
    assert exc_info.value.attempts == 1
    assert embedding.attempted == 1
    assert parts["sleeps"] == []


def test_load_failure_is_not_retried(tmp_path):
    pipeline, parts = _build(tmp_path, max_attempts=3)
    pipeline._injected["loader"] = FakeLoader(tmp_path, fail=True)
    source = _write_source(tmp_path)

    with pytest.raises(IngestionPipelineError) as exc_info:
        pipeline.run(str(source), collection="a")

    assert exc_info.value.stage == "load"
    assert parts["sleeps"] == []


def test_retries_exhausted_records_failed_stage(tmp_path):
    embedding = FlakyEmbedding(ConnectionError("refused"), failures=99)
    pipeline, parts = _build(tmp_path, max_attempts=3, embedding=embedding)
    source = _write_source(tmp_path)

    with pytest.raises(IngestionPipelineError) as exc_info:
        pipeline.run(str(source), collection="a")

    assert exc_info.value.stage == "embed"
    assert exc_info.value.attempts == 3
    assert embedding.attempted == 3
    assert parts["sleeps"] == [2.0, 4.0]
    record = parts["integrity"].get_record(str(source), "a")
    assert record["status"] == "failed"
    assert record["failed_stage"] == "embed"
    assert "refused" in record["error_msg"]


def test_store_retry_succeeds_without_duplicates(tmp_path):
    store = FlakyVectorStore(TimeoutError("chroma busy"), failures=1)
    pipeline, parts = _build(tmp_path, max_attempts=3, store=store)
    source = _write_source(tmp_path)
    doc_hash = _doc_hash(source)
    trace = TraceContext()

    result = pipeline.run(str(source), collection="a", trace=trace)

    assert result.status == "success"
    assert parts["sleeps"] == [2.0]
    assert _bm25_count(parts["index_file"], doc_hash) == result.chunks
    assert len(store.records) == result.chunks
    assert parts["image_storage"].count(doc_hash=doc_hash) == 1
    assert _stages(trace, "store")[0]["details"]["attempts"] == 2
    assert _stages(trace, "rollback") == []


# ---------------------------------------------------------------------------
# store 回滚
# ---------------------------------------------------------------------------


def test_store_exhausted_rolls_back_all_stores(tmp_path):
    store = FlakyVectorStore(TimeoutError("chroma timeout"), failures=None)
    pipeline, parts = _build(tmp_path, max_attempts=2, store=store)
    source = _write_source(tmp_path)
    doc_hash = _doc_hash(source)
    trace = TraceContext()

    with pytest.raises(IngestionPipelineError) as exc_info:
        pipeline.run(str(source), collection="a", trace=trace)

    assert exc_info.value.stage == "store"
    assert exc_info.value.attempts == 2
    assert exc_info.value.rollback_error is None
    assert store.attempted == 2
    assert _bm25_count(parts["index_file"], doc_hash) == 0
    assert store.records == {}
    assert parts["image_storage"].count(doc_hash=doc_hash) == 0
    rollback = _stages(trace, "rollback")
    assert len(rollback) == 1
    assert rollback[0]["details"]["ok"] is True
    assert rollback[0]["details"]["bm25_deleted"] > 0
    assert parts["integrity"].get_record(str(source), "a")["failed_stage"] == "store"


def test_image_failure_rolls_back_bm25_and_vectors(tmp_path, monkeypatch):
    pipeline, parts = _build(tmp_path, max_attempts=None)
    source = _write_source(tmp_path)
    doc_hash = _doc_hash(source)

    def _boom(*args, **kwargs):
        raise RuntimeError("injected image put failure")

    monkeypatch.setattr(parts["image_storage"], "put", _boom)

    with pytest.raises(IngestionPipelineError, match="injected image put failure"):
        pipeline.run(str(source), collection="a")

    assert _bm25_count(parts["index_file"], doc_hash) == 0
    assert parts["store"].records == {}
    assert parts["image_storage"].get(make_image_id(doc_hash, 0, 0)) is None


def test_rollback_failure_does_not_mask_original_error(tmp_path, monkeypatch):
    store = FlakyVectorStore(RuntimeError("injected upsert failure"), failures=None)
    pipeline, parts = _build(tmp_path, max_attempts=None, store=store)
    source = _write_source(tmp_path)
    trace = TraceContext()

    def _delete_boom(*args, **kwargs):
        raise RuntimeError("injected rollback failure")

    monkeypatch.setattr(store, "delete_by_metadata", _delete_boom)

    with pytest.raises(IngestionPipelineError) as exc_info:
        pipeline.run(str(source), collection="a", trace=trace)

    exc = exc_info.value
    assert exc.stage == "store"
    assert "injected upsert failure" in str(exc)
    assert "injected rollback failure" not in str(exc)
    assert "injected rollback failure" in exc.rollback_error
    rollback = _stages(trace, "rollback")
    assert rollback[0]["details"]["ok"] is False
    record = parts["integrity"].get_record(str(source), "a")
    assert record["failed_stage"] == "store"
    assert "injected upsert failure" in record["error_msg"]
    assert "rollback failed" in record["error_msg"]


def test_bm25_load_failure_skips_rollback(tmp_path, monkeypatch):
    pipeline, parts = _build(tmp_path, max_attempts=None)
    source = _write_source(tmp_path)
    trace = TraceContext()

    def _load_boom(*args, **kwargs):
        raise RuntimeError("corrupt index")

    monkeypatch.setattr(parts["bm25"], "load", _load_boom)

    with pytest.raises(IngestionPipelineError, match="corrupt index"):
        pipeline.run(str(source), collection="a", trace=trace)

    assert _stages(trace, "rollback") == []
    assert not parts["index_file"].exists()


def test_rollback_keeps_hash_shared_with_other_collection(tmp_path):
    store = FlakyVectorStore()
    pipeline, parts = _build(tmp_path, max_attempts=None, store=store)
    source = _write_source(tmp_path)
    doc_hash = _doc_hash(source)
    first = pipeline.run(str(source), collection="a")

    store.error, store.failures = RuntimeError("injected upsert failure"), None
    with pytest.raises(IngestionPipelineError):
        pipeline.run(str(source), collection="b")

    assert _bm25_count(parts["index_file"], doc_hash) == first.chunks
    assert len(store.get_by_metadata({"collection": "a"})) == first.chunks
    assert parts["image_storage"].count(doc_hash=doc_hash) == 1
    assert parts["integrity"].get_record(str(source), "a")["status"] == "success"


def test_rollback_is_idempotent(tmp_path):
    store = FlakyVectorStore(RuntimeError("injected upsert failure"), failures=None)
    pipeline, parts = _build(tmp_path, max_attempts=None, store=store)
    source = _write_source(tmp_path)
    doc_hash = _doc_hash(source)
    with pytest.raises(IngestionPipelineError):
        pipeline.run(str(source), collection="a")

    manager = DocumentManager(
        chroma_store=store,
        bm25_indexer=parts["bm25"],
        image_storage=parts["image_storage"],
        file_integrity=parts["integrity"],
        settings=parts["settings"],
    )
    first = manager.rollback_ingestion(str(source), "a", doc_hash)
    second = manager.rollback_ingestion(str(source), "a", doc_hash)

    assert (first.bm25_deleted, first.chroma_deleted, first.images_deleted) == (0, 0, 0)
    assert first.to_dict() == second.to_dict()
    assert _bm25_count(parts["index_file"], doc_hash) == 0
    assert parts["image_storage"].count(doc_hash=doc_hash) == 0
