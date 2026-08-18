"""Query 追踪页面（阶段 G6）。"""

from __future__ import annotations

from typing import Any

import streamlit as st

from observability.dashboard.services.trace_service import TraceService

__all__ = ["render"]

_QUERY_STAGES = [
    "query_processing",
    "dense_retrieval",
    "sparse_retrieval",
    "fusion",
    "hybrid",
    "rerank",
]


def render(*, trace_service: TraceService | None = None) -> None:
    trace_service = trace_service or TraceService()
    st.header("Query 追踪")
    st.caption("查询历史、阶段耗时、Dense/Sparse 对比与 Rerank 变化。")

    keyword = st.text_input("按 Query 关键词搜索", value="")
    traces = trace_service.search_query_traces(keyword)
    if not traces:
        st.info("暂无 query trace。先运行一次 `python scripts/query.py` 后再回来查看。")
        return

    labels = [_label(item) for item in traces]
    selected = st.selectbox("查询记录", labels)
    trace = traces[labels.index(selected)]

    metadata = trace.get("metadata") or {}
    st.write(f"**Query**: {metadata.get('query') or '(unknown)'}")
    st.caption(f"trace_id=`{trace.get('trace_id')}` · {trace.get('total_elapsed_ms')} ms")

    timings = [item for item in trace_service.stage_timings(trace, _QUERY_STAGES) if item["elapsed_ms"] > 0]
    if timings:
        st.subheader("耗时瀑布图")
        st.bar_chart({item["stage"]: item["elapsed_ms"] for item in timings})

    dense = _preview(trace, "dense_retrieval")
    sparse = _preview(trace, "sparse_retrieval")
    compare_cols = st.columns(2)
    with compare_cols[0]:
        st.subheader("Dense")
        _render_hits(dense)
    with compare_cols[1]:
        st.subheader("Sparse")
        _render_hits(sparse)

    rerank = _stage(trace, "rerank") or {}
    details = rerank.get("details") or {}
    if details:
        st.subheader("Rerank 前后对比")
        st.caption(
            f"backend={details.get('backend')} · fallback={details.get('fallback')}"
        )
        before = {item["chunk_id"]: idx for idx, item in enumerate(details.get("before") or [], start=1)}
        after = details.get("after") or []
        rows = []
        for idx, item in enumerate(after, start=1):
            previous = before.get(item["chunk_id"])
            delta = "" if previous is None else previous - idx
            marker = "—" if delta == "" else ("↑" if delta > 0 else ("↓" if delta < 0 else "·"))
            rows.append(
                {
                    "after": idx,
                    "before": previous,
                    "change": f"{marker}{delta}" if delta != "" else marker,
                    "chunk_id": item["chunk_id"],
                    "score": item.get("score"),
                }
            )
        if rows:
            st.table(rows)

    fusion = _preview(trace, "fusion") or _preview(trace, "hybrid")
    if fusion:
        st.subheader("最终 / 融合结果")
        _render_hits(fusion)


def _label(trace: dict[str, Any]) -> str:
    query = (trace.get("metadata") or {}).get("query") or trace.get("trace_id")
    return f"{query} · {trace.get('total_elapsed_ms')} ms"


def _stage(trace: dict[str, Any], name: str) -> dict[str, Any] | None:
    for record in reversed(trace.get("stages") or []):
        if record.get("stage") == name:
            return record
    return None


def _preview(trace: dict[str, Any], name: str) -> list[dict[str, Any]]:
    record = _stage(trace, name) or {}
    details = record.get("details") or {}
    return list(details.get("preview") or details.get("after") or [])


def _render_hits(hits: list[dict[str, Any]]) -> None:
    if not hits:
        st.caption("无命中")
        return
    st.table(
        [
            {"rank": index, "chunk_id": item.get("chunk_id"), "score": item.get("score")}
            for index, item in enumerate(hits, start=1)
        ]
    )
