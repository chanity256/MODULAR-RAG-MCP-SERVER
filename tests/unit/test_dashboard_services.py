"""Dashboard 服务层单测（阶段 G1/G3/G5）。

ConfigService / DataService / TraceService 均可注入，不启动 Streamlit。
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from core.trace.persist import persist_trace
from core.trace.trace_context import TRACE_TYPE_INGESTION, TRACE_TYPE_QUERY, TraceContext
from ingestion.document_manager import CollectionStats, DeleteResult, DocumentDetail, DocumentInfo
from observability.dashboard.services.config_service import ComponentCard, ConfigService
from observability.dashboard.services.data_service import DataService
from observability.dashboard.services.trace_service import TraceService

_VALID_YAML = dedent(
    """\
    llm:
      provider: azure
      model: gpt-4o
    embedding:
      provider: openai
      model: text-embedding-3-small
      dimensions: 1536
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
      chunk_size: 512
      chunk_overlap: 64
    dashboard:
      port: 8502
    observability:
      log_file: ./logs/custom-traces.jsonl
    """
)


def _settings(tmp_path: Path):
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    return load_settings(path), path


def test_config_service_formats_component_cards(tmp_path):
    settings, path = _settings(tmp_path)
    service = ConfigService(settings=settings, config_path=path)
    cards = service.component_cards()
    names = [card.name for card in cards]
    assert names == ["LLM", "Embedding", "Splitter", "Reranker", "Evaluator", "Agentic Search"]
    by_name = {card.name: card for card in cards}
    assert by_name["LLM"].headline() == "azure / gpt-4o"
    assert by_name["Embedding"].extras["dimensions"] == 1536
    assert by_name["Reranker"].provider == "none"
    assert by_name["Reranker"].model == "None"
    assert by_name["Evaluator"].provider == "custom"
    assert isinstance(cards[0], ComponentCard)


def test_config_service_agentic_card_disabled_by_default(tmp_path):
    """S3.2：默认 enabled=false 时 Agentic 卡片显示 disabled 关闭状态。"""
    settings, path = _settings(tmp_path)
    service = ConfigService(settings=settings, config_path=path)
    card = {item.name: item for item in service.component_cards()}["Agentic Search"]
    assert card.provider == "disabled"
    assert card.extras == {"enabled": False}


def test_config_service_agentic_card_enabled(tmp_path):
    """S3.2：enabled=true 时 Agentic 卡片展示 backend 与 trigger/mode。"""
    settings, path = _settings(tmp_path)
    settings.agentic_search.enabled = True
    settings.agentic_search.backend = "sirchmunk_http"
    settings.agentic_search.trigger = "fallback"
    service = ConfigService(settings=settings, config_path=path)
    card = {item.name: item for item in service.component_cards()}["Agentic Search"]
    assert card.provider == "sirchmunk_http"
    assert card.extras == {"enabled": True, "trigger": "fallback", "mode": "FAST"}


def test_config_service_loads_from_explicit_path(tmp_path):
    _settings_obj, path = _settings(tmp_path)
    service = ConfigService(config_path=path)
    assert service.get_settings().dashboard.port == 8502


class FakeManager:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []
        self._docs = [
            DocumentInfo("a.pdf", "alpha", 2, image_count=1, title="A"),
            DocumentInfo("b.pdf", "beta", 1, title="B"),
        ]

    def list_documents(self, collection=None):
        if collection:
            return [item for item in self._docs if item.collection == collection]
        return list(self._docs)

    def get_document_detail(self, doc_id, collection=None):
        if doc_id != "a.pdf":
            return None
        return DocumentDetail(doc_id, collection or "alpha", 1, chunks=[{"id": "c1", "text": "hi"}])

    def delete_document(self, source_path, collection):
        self.deleted.append((source_path, collection))
        return DeleteResult(source_path, collection, chroma_deleted=2)

    def get_collection_stats(self, collection=None):
        if collection == "ghost":
            return [CollectionStats(name="ghost")]
        return [CollectionStats(name="alpha", document_count=1, chunk_count=2, image_count=1)]


class FakeImages:
    def __init__(self, tmp_path: Path) -> None:
        self.path = tmp_path / "pic.png"
        self.path.write_bytes(b"png-bytes")

    def list_images(self, collection=None, doc_hash=None):
        from ingestion.storage import ImageRecord

        return [ImageRecord("img1", str(self.path), collection=collection, doc_hash=doc_hash)]

    def get(self, image_id):
        from ingestion.storage import ImageRecord

        if image_id != "img1":
            return None
        return ImageRecord("img1", str(self.path))


def test_data_service_delegates_to_manager(tmp_path):
    manager = FakeManager()
    images = FakeImages(tmp_path)
    service = DataService(manager=manager, image_storage=images)
    assert [item.source_path for item in service.list_documents("alpha")] == ["a.pdf"]
    assert service.get_document_detail("a.pdf").chunk_count == 1
    deleted = service.delete_document("a.pdf", "alpha")
    assert deleted.chroma_deleted == 2
    assert manager.deleted == [("a.pdf", "alpha")]
    assert service.get_collection_stats()[0].chunk_count == 2
    listed = service.list_images(doc_hash="abc")
    assert listed[0]["image_id"] == "img1"
    payload = service.image_bytes("img1")
    assert payload == (b"png-bytes", "image/png")
    assert service.image_bytes("missing") is None


def test_trace_service_filters_and_searches(tmp_path):
    log_file = tmp_path / "traces.jsonl"
    ingest = TraceContext(trace_type=TRACE_TYPE_INGESTION, metadata={"source_path": "a.pdf"})
    ingest.record_stage("load", method="pipeline", details={"elapsed_ms": 4}, elapsed_ms=4)
    ingest.record_stage("store", method="pipeline", details={"chunks": 2}, elapsed_ms=8)
    persist_trace(ingest, log_file=str(log_file))
    query = TraceContext(trace_type=TRACE_TYPE_QUERY, metadata={"query": "azure 配置"})
    query.record_stage("dense_retrieval", method="dense_retriever", elapsed_ms=3)
    persist_trace(query, log_file=str(log_file))
    log_file.write_text(log_file.read_text(encoding="utf-8") + "{not json}\n", encoding="utf-8")

    service = TraceService(log_file)
    ingestions = service.list_traces("ingestion")
    queries = service.list_traces("query")
    assert len(ingestions) == 1 and ingestions[0]["trace_id"] == ingest.trace_id
    assert service.get_trace(query.trace_id)["trace_type"] == "query"
    assert service.latest("ingestion")["trace_id"] == ingest.trace_id
    assert [item["trace_id"] for item in service.search_query_traces("azure")] == [query.trace_id]
    assert service.search_query_traces("nope") == []
    timings = service.stage_timings(ingestions[0], ["load", "split", "store"])
    assert timings == [
        {"stage": "load", "elapsed_ms": 4.0},
        {"stage": "split", "elapsed_ms": 0.0},
        {"stage": "store", "elapsed_ms": 8.0},
    ]


def test_trace_service_missing_file_is_empty(tmp_path):
    service = TraceService(tmp_path / "missing.jsonl")
    assert service.list_traces() == []
    assert service.get_trace("x") is None
    assert service.latest("query") is None


def test_trace_service_reads_settings_log_file(tmp_path):
    settings, _path = _settings(tmp_path)
    settings.observability.log_file = str(tmp_path / "from-settings.jsonl")
    Path(settings.observability.log_file).write_text(
        json.dumps({"trace_id": "abc", "trace_type": "query", "started_at": "z"}) + "\n",
        encoding="utf-8",
    )
    service = TraceService(settings=settings)
    assert service.path == Path(settings.observability.log_file)
    assert service.list_traces("query")[0]["trace_id"] == "abc"
