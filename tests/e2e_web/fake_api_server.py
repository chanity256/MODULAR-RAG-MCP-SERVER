"""Offline, deterministic API server for the Playwright web-UI flows (``frontend/e2e``).

It builds the *real* app through ``api.app.create_app`` and serves the real static export
(``frontend/out``) on the same port, but every backend dependency is a fake: no network, no
real model, no sidecar, and no write outside a throw-away temp directory.

    .venv/bin/python tests/e2e_web/fake_api_server.py --port 18765 [--token SECRET]

Chat runs through the genuine ``ChatEngine`` + ``SessionStore`` + trace persistence with a
scripted LLM; the prompt text selects the behaviour:

* ``SLOW``         streams ~10 s of chunks (cancellable)
* ``FORCE_ERROR``  the LLM raises a 503 -> ``error{UPSTREAM}`` frame
* anything else    a short markdown answer carrying a ``[1]`` citation marker

``POST /__e2e__/reset`` restores the seeded documents so specs stay independent and retryable.
"""

from __future__ import annotations

import argparse
import atexit
import json
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from fastapi import FastAPI  # noqa: E402
from starlette.routing import Route  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

from api.app import create_app  # noqa: E402
from api.chat_wiring import ChatServices  # noqa: E402
from api.deps import ServiceContainer  # noqa: E402
from api.tasks import TaskManager  # noqa: E402
from core.chat import SessionStore  # noqa: E402
from core.chat.retriever import ChatRetrievalResult, DeepDisabledError  # noqa: E402
from core.settings import ApiSettings, ChatSettings, load_settings  # noqa: E402
from core.types import RetrievalResult  # noqa: E402
from ingestion.document_manager import CollectionStats, DeleteResult, DocumentDetail, DocumentInfo  # noqa: E402
from libs.llm.base_llm import BaseLLM, ChatResponse  # noqa: E402
from libs.llm.openai_llm import LLMRequestError  # noqa: E402
from observability.dashboard.services.chat_retrievers import HybridChatRetriever  # noqa: E402
from observability.dashboard.services.config_service import ConfigService  # noqa: E402
from observability.dashboard.services.ingestion_service import IngestionService, IngestionSummary  # noqa: E402
from observability.dashboard.services.search_service import ModeAvailability, SearchOutcome  # noqa: E402
from observability.dashboard.services.trace_service import TraceService  # noqa: E402

__all__ = ["FakeData", "build_app", "main"]

DEFAULT_STATIC_DIR = _REPO_ROOT / "frontend" / "out"
_CONFIG = _REPO_ROOT / "config" / "settings.yaml"

# 1x1 transparent PNG.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000001e221bc330000000049454e44ae426082"
)

ANSWER_CHUNKS = ("**RAG** grounds answers", " in your documents", " [1].")
SLOW_CHUNK_COUNT = 50
SLOW_CHUNK_DELAY_S = 0.2


# --------------------------------------------------------------------------- chat fakes
class ScriptedLLM(BaseLLM):
    def __init__(self) -> None:
        self.last_usage: dict[str, int] | None = None

    def chat(self, messages, *, trace=None, **kwargs):
        return ChatResponse(content="[1]")

    def chat_stream(self, messages, *, trace=None, **kwargs) -> Iterator[str]:
        self.last_usage = None
        prompt = messages[-1].content
        if "FORCE_ERROR" in prompt:
            raise LLMRequestError("fake", "scripted upstream failure", status=503)
        if "SLOW" in prompt:
            for index in range(SLOW_CHUNK_COUNT):
                time.sleep(SLOW_CHUNK_DELAY_S)
                yield f"slow-{index} "
        else:
            for chunk in ANSWER_CHUNKS:
                time.sleep(0.03)
                yield chunk
        self.last_usage = {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}


class FakeQueryService:
    def search(self, query, *, collection=None, top_k=None, trace=None):
        if trace is not None:
            trace.record_stage("dense_retrieval", count=1)
            trace.finish()
        return [
            RetrievalResult(
                "chunk-guide-1",
                0.8731,
                "RAG retrieves relevant passages and conditions the model on them.",
                {"source_path": "guide.md", "page": 2},
            )
        ]


class FakeAgenticRetriever:
    def __init__(self, label: str, *, deep: bool = False) -> None:
        self._label = label
        self._deep = deep

    def retrieve(self, query, *, collection=None, top_k=None, trace=None, on_log=None):
        if self._deep:
            raise DeepDisabledError("deep search disabled")
        return ChatRetrievalResult(
            citations=[
                {"id": 1, "source": "agentic-notes.md", "page": None, "chunk_id": "agentic-1",
                 "text": "Sidecar evidence passage.", "score": 0.5}
            ],
            answer=f"{self._label} answer from the sidecar [1].",
        )


# --------------------------------------------------------------------------- data fakes
def _doc(source: str, collection: str, chunks: int, images: int = 0, title: str | None = None) -> DocumentInfo:
    return DocumentInfo(
        source_path=source,
        collection=collection,
        chunk_count=chunks,
        image_count=images,
        ingested_at="2026-01-05T09:30:00",
        doc_hash=f"hash-{source}",
        title=title or source,
    )


def _seed_documents() -> list[DocumentInfo]:
    return [
        _doc("guide.md", "e2e-docs", 3, images=1, title="Getting Started Guide"),
        _doc("faq.md", "e2e-docs", 2, title="Product FAQ"),
        _doc("delete-me.md", "e2e-docs", 1, title="Disposable Note"),
        _doc("old-notes.md", "archive", 4, title="Old Notes"),
    ]


class FakeData:
    """In-memory stand-in for ``DataService`` (same read/delete surface the routes use)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.documents: list[DocumentInfo] = _seed_documents()

    def reset(self) -> None:
        with self._lock:
            self.documents = _seed_documents()

    def add_document(self, source: str, collection: str, chunks: int) -> None:
        with self._lock:
            self.documents = [d for d in self.documents if (d.source_path, d.collection) != (source, collection)]
            self.documents.append(_doc(source, collection, chunks, title=source))

    def list_documents(self, collection=None):
        with self._lock:
            return [d for d in self.documents if collection in (None, d.collection)]

    def get_document_detail(self, doc_id, collection=None):
        for d in self.list_documents(collection):
            if d.source_path == doc_id:
                images = (
                    [{"image_id": "img-guide-1", "file_path": "/hidden/server/path.png", "collection": d.collection,
                      "doc_hash": d.doc_hash, "page_num": 1, "created_at": "2026-01-05"}]
                    if d.image_count
                    else []
                )
                return DocumentDetail(
                    source_path=d.source_path,
                    collection=d.collection,
                    chunk_count=d.chunk_count,
                    chunks=[
                        {"id": f"{d.source_path}-c{i}", "text": f"Chunk {i + 1} text of {d.title}.",
                         "metadata": {"title": d.title, "page": i + 1}}
                        for i in range(d.chunk_count)
                    ],
                    images=images,
                    ingested_at=d.ingested_at,
                    doc_hash=d.doc_hash,
                    title=d.title,
                )
        return None

    def get_collection_stats(self, collection=None):
        buckets: dict[str, CollectionStats] = {}
        for d in self.list_documents():
            bucket = buckets.setdefault(d.collection, CollectionStats(name=d.collection))
            bucket.document_count += 1
            bucket.chunk_count += d.chunk_count
            bucket.image_count += d.image_count
        if collection is not None:
            return [buckets.get(collection, CollectionStats(name=collection))]
        return [buckets[name] for name in sorted(buckets)]

    def list_images(self, collection=None, doc_hash=None):
        return []

    def image_bytes(self, image_id):
        return (_PNG, "image/png") if image_id == "img-guide-1" else None

    def delete_document(self, source_path, collection):
        with self._lock:
            before = len(self.documents)
            self.documents = [d for d in self.documents if (d.source_path, d.collection) != (source_path, collection)]
            removed = before - len(self.documents)
        return DeleteResult(
            source_path, collection, chroma_deleted=removed * 2, bm25_deleted=removed * 2, integrity_removed=bool(removed)
        )


class FakeIngestion(IngestionService):
    """Real upload-to-disk behaviour; ``ingest`` is a short scripted run that registers the documents."""

    def __init__(self, upload_dir: Path, data: FakeData) -> None:
        super().__init__(upload_dir=upload_dir)
        self._data = data

    def ingest(self, files, *, collection, force=False, on_progress=None):
        paths = [Path(p) for p in files]
        if on_progress:
            on_progress("load", 1, 2)
        time.sleep(0.8)
        if on_progress:
            on_progress("embed", 2, 2)
        for path in paths:
            self._data.add_document(path.name, collection, 3)
        return IngestionSummary(files=len(paths), chunks=3 * len(paths), images=0, failed=0)


class _Report:
    def to_dict(self) -> dict[str, Any]:
        return {
            "total": 2,
            "evaluated": 2,
            "failed": 0,
            "metrics": {"hit_rate": 0.8, "mrr": 0.6667},
            "per_query": [
                {"query": "What is RAG?", "retrieved_ids": ["chunk-guide-1"], "expected_chunk_ids": ["chunk-guide-1"],
                 "metrics": {"hit_rate": 1.0}},
                {"query": "How do I reset my password?", "retrieved_ids": ["chunk-faq-2"],
                 "expected_chunk_ids": ["chunk-faq-1"], "metrics": {"hit_rate": 0.0}},
            ],
        }


class FakeEvaluation:
    backends = ("composite", "custom", "ragas")

    def default_test_set_path(self) -> Path:
        return Path("/golden/e2e-default.json")

    def test_set_exists(self, test_set_path) -> bool:
        # Real filesystem check: the WP2 e2e submits a bogus explicit path and expects the 422
        # precheck; default-path submissions never reach this (route skips the precheck).
        return Path(test_set_path).exists()

    def run(self, test_set_path, *, backend="composite", top_k=None, filters=None):
        time.sleep(0.8)
        return _Report()


class FakeSearch:
    """Query Playground backend: normal + fast available, deep gated off."""

    def availability(self, settings=None):
        return {
            "normal": ModeAvailability(True),
            "fast": ModeAvailability(True),
            "deep": ModeAvailability(False, "Deep search is disabled (agentic_search.allow_deep = false)"),
        }

    def run(self, mode, query, top_k=None, collection=None):
        meta = (
            {"mode": mode.upper(), "llm_call_count": 2, "total_llm_tokens": 1024, "telemetry": {"zero_llm": False}}
            if mode != "normal"
            else None
        )
        return SearchOutcome(
            mode=mode,
            markdown=f"Playground answer for: {query}",
            citations=[{"id": 1, "source": "guide.md", "page": 2, "chunk_id": "chunk-guide-1",
                        "text": "RAG retrieves relevant passages.", "score": 0.8731}],
            agentic_meta=meta,
            elapsed_ms=12.5,
        )


# --------------------------------------------------------------------------- traces
def _stage(name: str, ms: float, method: str = "pipeline", **details: Any) -> dict[str, Any]:
    return {"stage": name, "elapsed_ms": ms, "method": method, "details": details}


def _seed_traces() -> list[dict[str, Any]]:
    return [
        {
            "trace_id": "seed-ing-guide",
            "trace_type": "ingestion",
            "started_at": "2026-01-05T09:30:00+00:00",
            "finished_at": "2026-01-05T09:30:01+00:00",
            "total_elapsed_ms": 120.5,
            "metadata": {"source_path": "/docs/guide.md", "collection": "e2e-docs"},
            "stages": [
                _stage("integrity", 1.0, skipped=False),
                _stage("load", 10.0, source_path="/docs/guide.md"),
                _stage("split", 5.0, chunks=3),
                _stage("embed", 60.0, chunks=3),
                _stage("store", 40.0, chunks=3, images=1),
            ],
        },
        {
            "trace_id": "seed-ing-faq-skipped",
            "trace_type": "ingestion",
            "started_at": "2026-01-04T09:30:00+00:00",
            "total_elapsed_ms": 3.0,
            "metadata": {"collection": "e2e-docs"},
            "stages": [_stage("integrity", 1.0, skipped=True), _stage("load", 1.0, source_path="/docs/faq.md")],
        },
        {
            "trace_id": "seed-query-rrf",
            "trace_type": "query",
            "started_at": "2026-01-06T10:00:00+00:00",
            "finished_at": "2026-01-06T10:00:01+00:00",
            "total_elapsed_ms": 55.0,
            "metadata": {"query": "How does RRF fuse rankings?", "collection": None},
            "stages": [
                _stage("dense_retrieval", 12.0, method="dense", preview=[{"chunk_id": "chunk-guide-1", "score": 0.9}]),
                _stage("sparse_retrieval", 8.0, method="bm25", preview=[{"chunk_id": "chunk-faq-1", "score": 3.2}]),
                _stage("fusion", 3.0, method="rrf", preview=[{"chunk_id": "chunk-guide-1", "score": 0.032}]),
            ],
        },
        {
            "trace_id": "seed-query-chat",
            "trace_type": "query",
            "started_at": "2026-01-07T10:00:00+00:00",
            "finished_at": "2026-01-07T10:00:02+00:00",
            "total_elapsed_ms": 1500.0,
            "metadata": {
                "query": "Seeded chat question about citations",
                "routes": "chat",
                "retrieval": "hybrid",
                "session_id": "seed-session",
            },
            # The real ChatEngine records chat_* fields at the stage's top level (not under `details`).
            "stages": [
                {"stage": "chat_history", "elapsed_ms": 1.0, "messages_before": 4, "messages_after_filter": 2},
                {
                    "stage": "chat_rewrite",
                    "elapsed_ms": 1.0,
                    "enabled": True,
                    "called": True,
                    "rewritten": True,
                    "rewritten_query": "Seeded rewritten query",
                },
                _stage("dense_retrieval", 12.0, method="dense", preview=[{"chunk_id": "chunk-guide-1", "score": 0.9}]),
                {
                    "stage": "chat_generation",
                    "elapsed_ms": 1200.0,
                    "method": "llm_stream",
                    "grounded": True,
                    "chunks": 1,
                    "chars": 42,
                },
            ],
        },
    ]


# --------------------------------------------------------------------------- assembly
def build_app(
    workdir: Path,
    *,
    static_dir: Path = DEFAULT_STATIC_DIR,
    token: str | None = None,
) -> FastAPI:
    """Assemble the fake-backed app; every writable path lives under ``workdir``."""
    workdir.mkdir(parents=True, exist_ok=True)
    traces_path = workdir / "traces.jsonl"
    traces_path.write_text("".join(json.dumps(r) + "\n" for r in _seed_traces()), encoding="utf-8")

    loaded = load_settings(_CONFIG)
    chat_settings = ChatSettings(
        relevance_filter=False,
        query_rewrite=False,
        pipeline_max_retries=0,
        session_db=str(workdir / "chat.db"),
    )
    settings = replace(
        loaded,
        chat=chat_settings,
        agentic_search=replace(loaded.agentic_search, enabled=True, allow_deep=False),
        observability=replace(loaded.observability, log_file=str(traces_path)),
    )
    api_settings = ApiSettings(
        enabled=True,
        host="127.0.0.1",
        api_token=token,
        serve_ui=True,
        static_dir=str(static_dir),
        tasks_db=str(workdir / "tasks.db"),
        max_upload_mb=5,
    )

    data = FakeData()
    store = SessionStore(workdir / "chat.db")
    chat = ChatServices(
        settings,
        session_store=store,
        retrievers={
            "hybrid": HybridChatRetriever(FakeQueryService()),  # type: ignore[arg-type]
            "agentic_fast": FakeAgenticRetriever("Agentic fast"),
            "agentic_deep": FakeAgenticRetriever("Agentic deep", deep=True),
        },
        llm_factory=lambda _settings: ScriptedLLM(),
    )
    container = ServiceContainer(
        settings,
        config_service=ConfigService(settings),
        data_service=data,  # type: ignore[arg-type]
        trace_service=TraceService(traces_path),
        ingestion_service=FakeIngestion(workdir / "uploads", data),
        evaluation_service=FakeEvaluation(),
        task_manager=TaskManager(workdir / "tasks.db", max_workers=2),
        chat_services=chat,
        search_service=FakeSearch(),  # type: ignore[arg-type]
        sidecar_probe=lambda: True,
        storage_probe=lambda: {"vector_store": True, "traces": True},
    )
    app = create_app(api_settings=api_settings, services=container)

    async def reset(_request) -> JSONResponse:
        data.reset()
        return JSONResponse({"ok": True})

    # The SPA mount at "/" swallows later routes, so the hook goes in front of it.
    app.router.routes.insert(0, Route("/__e2e__/reset", reset, methods=["POST"]))
    app.state.e2e_cleanup = lambda: (container.task_manager.shutdown(wait=False), store.close())
    return app


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", default=None, help="require this API token (auth-flow server)")
    parser.add_argument("--static-dir", default=str(DEFAULT_STATIC_DIR))
    args = parser.parse_args(argv)

    workdir = Path(tempfile.mkdtemp(prefix="rag-e2e-web-"))
    atexit.register(shutil.rmtree, workdir, ignore_errors=True)
    app = build_app(workdir, static_dir=Path(args.static_dir), token=args.token)
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        app.state.e2e_cleanup()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
