"""Ingestion 追踪页面（阶段 G5）。"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from observability.dashboard.services.trace_service import TraceService

__all__ = ["render"]

_INGEST_STAGES = ["load", "split", "transform", "embed", "store", "upsert"]


def render(*, trace_service: TraceService | None = None) -> None:
    trace_service = trace_service or TraceService()
    st.header("Ingestion 追踪")
    st.caption("摄取历史与阶段耗时瀑布图。")

    traces = trace_service.list_traces("ingestion")
    if not traces:
        st.info("暂无 ingestion trace。先运行一次摄取后再回来查看。")
        return

    labels = [_label(item) for item in traces]
    selected = st.selectbox("摄取记录", labels)
    trace = traces[labels.index(selected)]
    _render_summary(trace)
    timings = [item for item in trace_service.stage_timings(trace, _INGEST_STAGES) if item["elapsed_ms"] > 0]
    if not timings:
        timings = [item for item in trace_service.stage_timings(trace) if item["elapsed_ms"] > 0]
    if timings:
        st.subheader("阶段耗时瀑布图")
        chart = pd.DataFrame(timings).set_index("stage")
        st.bar_chart(chart, horizontal=True)
    st.subheader("阶段详情")
    for record in trace.get("stages") or []:
        with st.expander(f"{record.get('stage')} · {record.get('method')} · {record.get('elapsed_ms')} ms"):
            st.json(record.get("details") or record)


def _source_of(trace: dict[str, Any]) -> str | None:
    metadata = trace.get("metadata") or {}
    if metadata.get("source_path"):
        return str(metadata["source_path"])
    load = _stage(trace, "load") or {}
    return (load.get("details") or {}).get("source_path")


def _label(trace: dict[str, Any]) -> str:
    source = _source_of(trace) or trace.get("trace_id")
    elapsed = trace.get("total_elapsed_ms")
    return f"{source} · {elapsed} ms"


def _render_summary(trace: dict[str, Any]) -> None:
    metadata = trace.get("metadata") or {}
    store = _stage(trace, "store") or {}
    details = store.get("details") or {}
    cols = st.columns(4)
    cols[0].metric("状态", "skipped" if ( _stage(trace, "integrity") or {}).get("details", {}).get("skipped") else "success")
    cols[1].metric("Chunks", details.get("chunks", "-"))
    cols[2].metric("Images", details.get("images", "-"))
    cols[3].metric("总耗时 ms", trace.get("total_elapsed_ms") or 0)
    source = _source_of(trace)
    collection = metadata.get("collection")
    st.caption(f"collection={collection} · source={source}")


def _stage(trace: dict[str, Any], name: str) -> dict[str, Any] | None:
    for record in reversed(trace.get("stages") or []):
        if record.get("stage") == name:
            return record
    return None


if __name__ == "__main__":
    render()
