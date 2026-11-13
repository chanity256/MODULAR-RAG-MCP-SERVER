"""persist_trace 直接追加 JSONL，不走全局 logger 单例。"""

from __future__ import annotations

from pathlib import Path

from core.trace.persist import persist_trace
from core.trace.trace_context import TRACE_TYPE_QUERY, TraceContext


def test_persist_trace_appends_finished_jsonl(tmp_path: Path):
    path = tmp_path / "nested" / "traces.jsonl"
    first = TraceContext(trace_type=TRACE_TYPE_QUERY, metadata={"query": "one"})
    first.record_stage("dense_retrieval", method="dense_retriever")
    persist_trace(first, log_file=str(path))
    second = TraceContext(trace_type=TRACE_TYPE_QUERY, metadata={"query": "two"})
    persist_trace(second, log_file=str(path))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    payload = __import__("json").loads(lines[0])
    assert payload["trace_id"] == first.trace_id
    assert payload["trace_type"] == "query"
    assert payload["finished_at"]
    assert payload["metadata"]["query"] == "one"


def test_persist_trace_write_failure_does_not_raise(tmp_path: Path, monkeypatch):
    target = tmp_path / "traces.jsonl"

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", _boom)
    persist_trace(TraceContext(), log_file=str(target))
