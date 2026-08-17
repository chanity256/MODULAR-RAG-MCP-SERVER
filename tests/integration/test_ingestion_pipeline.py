"""F4：Ingestion 链路 trace 打点（集成，复用 unit 测试的边界 fake）。"""

from __future__ import annotations

from tests.unit.test_ingestion_pipeline import (
    FakeLoader,
    FakeVectorStore,
    _build_pipeline,
    _write_source,
)


def test_ingestion_link_trace_records_all_stages(tmp_path):
    pipeline, _ = _build_pipeline(tmp_path, FakeLoader(tmp_path), FakeVectorStore())
    source = _write_source(tmp_path)

    # 不传 trace：pipeline 缺省新建 trace_type="ingestion" 的上下文。
    result = pipeline.run(str(source))

    assert result.status == "success"
    assert result.trace_id

    # 显式传入 trace 再跑一次（force 覆盖幂等跳过），拿到完整上下文。
    from core.trace.trace_context import TraceContext

    trace = TraceContext(trace_type="ingestion")
    pipeline.run(str(source), force=True, trace=trace)

    data = trace.to_dict()
    assert data["trace_type"] == "ingestion"
    assert data["trace_id"] == trace.trace_id
    assert data["finished_at"] is not None
    assert data["total_elapsed_ms"] >= 0.0

    markers = [s for s in data["stages"] if s.get("method") == "pipeline"]
    stage_names = [s["stage"] for s in markers]
    # load/split/transform/embed + upsert（store 阶段承载 BM25/向量/图片三路写入）。
    for expected in ("load", "split", "transform", "embed", "store"):
        assert expected in stage_names, f"missing pipeline stage {expected}"
    for record in data["stages"]:
        assert "elapsed_ms" in record, f"stage {record['stage']} missing elapsed_ms"
        assert "method" in record, f"stage {record['stage']} missing method"
        if record.get("method") == "pipeline":
            assert "details" in record


def test_ingestion_trace_persists_to_jsonl(tmp_path):
    """F1+F2 联动：TraceCollector 把 ingestion trace 写入 JSONL。"""
    import json
    import logging

    from core.trace.trace_collector import TraceCollector
    from observability.logger import get_trace_logger, write_trace

    log_file = tmp_path / "traces.jsonl"
    for handler in list(logging.getLogger("modular-rag.traces").handlers):
        logging.getLogger("modular-rag.traces").removeHandler(handler)
        handler.close()

    def writer(data):
        write_trace(data, log_file=str(log_file))

    pipeline, _ = _build_pipeline(tmp_path, FakeLoader(tmp_path), FakeVectorStore())
    source = _write_source(tmp_path)

    from core.trace.trace_context import TraceContext

    trace = TraceContext(trace_type="ingestion")
    pipeline.run(str(source), trace=trace)
    TraceCollector(writer=writer).collect(trace)

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert lines
    payload = json.loads(lines[-1])
    assert payload["trace_type"] == "ingestion"
    assert payload["trace_id"] == trace.trace_id
    assert payload["total_elapsed_ms"] is not None
