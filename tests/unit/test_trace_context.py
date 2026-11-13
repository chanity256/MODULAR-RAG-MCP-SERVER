"""F1：TraceContext 增强测试（trace_type / finish / elapsed_ms / to_dict / TraceCollector）。"""

from __future__ import annotations

import json
import time

import pytest

from core.trace.trace_collector import TraceCollector
from core.trace.trace_context import TraceContext


class TestTraceType:
    def test_default_trace_type_is_query(self):
        assert TraceContext().trace_type == "query"

    def test_ingestion_trace_type(self):
        assert TraceContext(trace_type="ingestion").trace_type == "ingestion"

    def test_invalid_trace_type_raises(self):
        with pytest.raises(ValueError, match="trace_type"):
            TraceContext(trace_type="unknown")


class TestRecordStage:
    def test_record_stage_appends_with_elapsed(self):
        trace = TraceContext()
        trace.record_stage("query_processing", method="query_processor")
        assert len(trace.stages) == 1
        record = trace.get_stage("query_processing")
        assert record["method"] == "query_processor"
        assert "elapsed_ms" in record
        assert isinstance(record["elapsed_ms"], float)

    def test_explicit_elapsed_ms_wins(self):
        trace = TraceContext()
        trace.record_stage("load", elapsed_ms=12.5)
        assert trace.get_stage("load")["elapsed_ms"] == 12.5

    def test_get_stage_missing_returns_none(self):
        assert TraceContext().get_stage("nope") is None


class TestFinish:
    def test_finish_sets_fields(self):
        trace = TraceContext()
        assert trace.finished_at is None and trace.total_elapsed_ms is None
        trace.finish()
        assert trace.finished_at is not None
        assert isinstance(trace.total_elapsed_ms, float)

    def test_finish_is_idempotent(self):
        trace = TraceContext()
        trace.finish()
        first = trace.total_elapsed_ms
        time.sleep(0.002)
        trace.finish()
        assert trace.total_elapsed_ms == first


class TestElapsedMs:
    def test_total_elapsed_before_finish(self):
        trace = TraceContext()
        time.sleep(0.002)
        assert trace.elapsed_ms() >= 1.0

    def test_total_elapsed_frozen_after_finish(self):
        trace = TraceContext()
        trace.finish()
        frozen = trace.total_elapsed_ms
        time.sleep(0.002)
        assert trace.elapsed_ms() == frozen

    def test_stage_elapsed(self):
        trace = TraceContext()
        trace.record_stage("dense_retrieval")
        assert trace.elapsed_ms("dense_retrieval") >= 0.0

    def test_stage_elapsed_missing_raises(self):
        with pytest.raises(KeyError):
            TraceContext().elapsed_ms("missing")


class TestToDict:
    def test_dict_contains_required_keys_and_serializable(self):
        trace = TraceContext(trace_type="query", metadata={"query": "azure"})
        trace.record_stage("dense_retrieval", method="dense")
        trace.finish()
        data = trace.to_dict()
        for key in (
            "trace_id",
            "trace_type",
            "started_at",
            "finished_at",
            "total_elapsed_ms",
            "stages",
        ):
            assert key in data
        assert data["trace_type"] == "query"
        assert data["stages"][0]["stage"] == "dense_retrieval"
        json.dumps(data)  # 必须可直接序列化

    def test_unfinished_trace_serializes_with_nulls(self):
        data = TraceContext(trace_type="ingestion").to_dict()
        assert data["trace_type"] == "ingestion"
        assert data["finished_at"] is None and data["total_elapsed_ms"] is None
        json.dumps(data)


class TestTraceCollector:
    def test_collect_finishes_and_persists(self):
        persisted: list[dict] = []

        def writer(data):
            persisted.append(dict(data))

        collector = TraceCollector(writer=writer)
        trace = TraceContext(trace_type="query")
        collector.collect(trace)
        assert trace.finished_at is not None
        assert len(collector) == 1
        assert persisted and persisted[0]["trace_id"] == trace.trace_id
        assert persisted[0]["trace_type"] == "query"

    def test_writer_failure_does_not_raise(self):
        collector = TraceCollector(writer=lambda data: (_ for _ in ()).throw(RuntimeError("boom")))
        collector.collect(TraceContext())  # 不抛
        assert len(collector) == 1
