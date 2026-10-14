"""Trace API 增量：全阶段 timings、``routes`` 来源过滤、``source`` 字段、解析缓存。"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.deps import ServiceContainer
from core.settings import ApiSettings
from observability.dashboard.services.trace_service import TraceService


def _stage(name, ms):
    return {"stage": name, "elapsed_ms": ms, "method": "m", "details": {}}


def _trace(trace_id, started, stages, routes=None, trace_type="query"):
    metadata = {"query": f"q-{trace_id}"}
    if routes is not None:
        metadata["routes"] = routes
    return {"trace_id": trace_id, "trace_type": trace_type, "started_at": started, "metadata": metadata, "stages": stages}


def _write(path: Path, records) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def _client(path: Path) -> TestClient:
    container = ServiceContainer(None, trace_service=TraceService(path))
    return TestClient(create_app(api_settings=ApiSettings(), services=container), raise_server_exceptions=False)


class TestTimingsKeepAllStages:
    def test_known_first_then_unknown_in_recorded_order(self, tmp_path):
        trace = _trace(
            "t1",
            "2026-10-01T00:00:00",
            [
                _stage("chat_history", 3.0),
                _stage("rerank", 8.0),
                _stage("chat_rewrite", 40.0),
                _stage("dense_retrieval", 12.0),
                _stage("agentic_search", 500.0),
                _stage("chat_generation", 900.0),
                _stage("chat_rewrite", 10.0),
                _stage("zero_stage", 0.0),
            ],
        )
        body = _client(_write(tmp_path / "t.jsonl", [trace])).get("/api/v1/traces/t1").json()
        assert body["timings"] == [
            {"stage": "dense_retrieval", "elapsed_ms": 12.0},
            {"stage": "rerank", "elapsed_ms": 8.0},
            {"stage": "chat_history", "elapsed_ms": 3.0},
            {"stage": "chat_rewrite", "elapsed_ms": 50.0},
            {"stage": "agentic_search", "elapsed_ms": 500.0},
            {"stage": "chat_generation", "elapsed_ms": 900.0},
        ]

    def test_known_only_trace_unchanged(self, tmp_path):
        trace = _trace("t2", "2026-10-01T00:00:00", [_stage("rerank", 2.0), _stage("dense_retrieval", 1.0)])
        body = _client(_write(tmp_path / "t.jsonl", [trace])).get("/api/v1/traces/t2").json()
        assert [t["stage"] for t in body["timings"]] == ["dense_retrieval", "rerank"]

    def test_ingestion_keeps_pipeline_stage_scope(self, tmp_path):
        trace = _trace(
            "t3", "2026-10-01T00:00:00", [_stage("integrity", 1.0), _stage("load", 2.0)], trace_type="ingestion"
        )
        body = _client(_write(tmp_path / "t.jsonl", [trace])).get("/api/v1/traces/t3").json()
        assert body["timings"] == [{"stage": "load", "elapsed_ms": 2.0}]


class TestRoutesFilter:
    def _records(self):
        return [
            _trace("t-default", "2026-10-01T00:00:01", [_stage("rerank", 1.0)]),
            _trace("t-chat", "2026-10-01T00:00:02", [_stage("rerank", 1.0)], routes="chat"),
            _trace("t-agentic", "2026-10-01T00:00:03", [_stage("rerank", 1.0)], routes="agentic"),
            _trace("t-chat2", "2026-10-01T00:00:04", [_stage("rerank", 1.0)], routes="chat"),
            _trace("t-ing", "2026-10-01T00:00:05", [_stage("load", 1.0)], trace_type="ingestion"),
        ]

    def test_filter_and_source_field(self, tmp_path):
        client = _client(_write(tmp_path / "t.jsonl", self._records()))
        chat = client.get("/api/v1/traces", params={"routes": "chat"}).json()
        assert [t["trace_id"] for t in chat["items"]] == ["t-chat2", "t-chat"]
        assert chat["total"] == 2
        assert {t["source"] for t in chat["items"]} == {"chat"}
        default = client.get("/api/v1/traces", params={"routes": "default", "trace_type": "query"}).json()
        assert [t["trace_id"] for t in default["items"]] == ["t-default"]
        assert default["items"][0]["source"] == "default" and default["items"][0]["routes"] is None

    def test_combines_with_keyword_and_pagination(self, tmp_path):
        client = _client(_write(tmp_path / "t.jsonl", self._records()))
        body = client.get("/api/v1/traces", params={"routes": "chat", "keyword": "chat2"}).json()
        assert [t["trace_id"] for t in body["items"]] == ["t-chat2"]
        page = client.get("/api/v1/traces", params={"routes": "chat", "limit": 1, "offset": 1}).json()
        assert page["total"] == 2 and [t["trace_id"] for t in page["items"]] == ["t-chat"]

    def test_unknown_or_blank_routes(self, tmp_path):
        client = _client(_write(tmp_path / "t.jsonl", self._records()))
        assert client.get("/api/v1/traces", params={"routes": "nope"}).json()["total"] == 0
        assert client.get("/api/v1/traces", params={"routes": "  "}).json()["total"] == 5

    def test_source_in_unfiltered_list_and_detail(self, tmp_path):
        client = _client(_write(tmp_path / "t.jsonl", self._records()))
        items = {t["trace_id"]: t["source"] for t in client.get("/api/v1/traces").json()["items"]}
        assert items == {
            "t-default": "default",
            "t-chat": "chat",
            "t-agentic": "agentic",
            "t-chat2": "chat",
            "t-ing": "default",
        }
        assert client.get("/api/v1/traces/t-agentic").json()["source"] == "agentic"


class TestParseCache:
    def test_reuses_parse_until_file_changes(self, tmp_path, monkeypatch):
        path = _write(tmp_path / "t.jsonl", [_trace("a", "2026-10-01T00:00:01", [])])
        service = TraceService(path)
        parses = []
        original = TraceService._parse
        monkeypatch.setattr(TraceService, "_parse", staticmethod(lambda text: parses.append(1) or original(text)))

        assert [t["trace_id"] for t in service.list_traces()] == ["a"]
        service.list_traces()
        service.get_trace("a")
        assert len(parses) == 1

        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_trace("b", "2026-10-01T00:00:02", [])) + "\n")
        assert [t["trace_id"] for t in service.list_traces()] == ["b", "a"]
        assert len(parses) == 2

    def test_same_size_rewrite_detected_via_mtime(self, tmp_path):
        path = _write(tmp_path / "t.jsonl", [_trace("a", "2026-10-01T00:00:01", [])])
        service = TraceService(path)
        assert service.get_trace("a") is not None
        _write(path, [_trace("z", "2026-10-01T00:00:01", [])])
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        assert service.get_trace("a") is None and service.get_trace("z") is not None

    def test_caller_sorting_does_not_corrupt_cache(self, tmp_path):
        path = _write(
            tmp_path / "t.jsonl",
            [_trace("old", "2026-10-01T00:00:01", []), _trace("new", "2026-10-01T00:00:09", [])],
        )
        service = TraceService(path)
        first = service.list_traces()
        first.reverse()
        first.clear()
        assert [t["trace_id"] for t in service.list_traces()] == ["new", "old"]

    def test_deleted_file_returns_empty(self, tmp_path):
        path = _write(tmp_path / "t.jsonl", [_trace("a", "2026-10-01T00:00:01", [])])
        service = TraceService(path)
        assert service.list_traces()
        path.unlink()
        assert service.list_traces() == []

    def test_concurrent_reads(self, tmp_path):
        path = _write(tmp_path / "t.jsonl", [_trace(f"t{i}", f"2026-10-01T00:00:{i:02d}", []) for i in range(30)])
        service = TraceService(path)
        results: list[int] = []
        threads = [threading.Thread(target=lambda: results.append(len(service.list_traces()))) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert results == [30] * 8
