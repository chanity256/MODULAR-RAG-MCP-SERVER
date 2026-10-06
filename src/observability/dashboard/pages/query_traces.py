"""Query 追踪页面（阶段 G6）。"""

from __future__ import annotations

from typing import Any

import pandas as pd
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
    if not timings:
        timings = [item for item in trace_service.stage_timings(trace) if item["elapsed_ms"] > 0]
    if timings:
        st.subheader("耗时瀑布图")
        chart = pd.DataFrame(timings).set_index("stage")
        st.bar_chart(chart, horizontal=True)

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

    _render_agentic(trace)

    fusion = _preview(trace, "fusion") or _preview(trace, "hybrid")
    if fusion:
        st.subheader("最终 / 融合结果")
        _render_hits(fusion)


def _render_agentic(trace: dict[str, Any]) -> None:
    """渲染 agentic 相关阶段（S3.2）。

    - ``agentic_search`` 工具阶段：模式 / Token / LLM 调用次数 / 证据文件；
    - ``hybrid`` / ``fusion`` 阶段里的 agentic 路信息（routes 计数、
      ``agentic_trigger`` 触发状态与 reason）。

    均基于 trace details 动态渲染：无 agentic 阶段时自然隐藏。
    """
    _render_agentic_tool_stage(trace)
    _render_agentic_route(trace)


def _render_agentic_tool_stage(trace: dict[str, Any]) -> None:
    """渲染 ``agentic_search`` 工具阶段（字段与 trace details 一一对应）。"""
    record = _stage(trace, "agentic_search")
    if not record:
        return
    details = record.get("details") or {}
    st.subheader("Agentic 检索")
    if details.get("error"):
        # 失败阶段（_record_error）：details 只有 error_type / error。
        st.warning(
            f"agentic 检索失败：{details.get('error_type')}: {details.get('error')}"
        )
        return
    cols = st.columns(4)
    cols[0].metric("模式", str(details.get("mode") or "—"))
    cols[1].metric("LLM Tokens", details.get("total_llm_tokens", 0))
    cols[2].metric("LLM 调用次数", details.get("llm_call_count", 0))
    evidence_files = details.get("evidence_files") or []
    cols[3].metric("证据文件数", len(evidence_files) if isinstance(evidence_files, list) else evidence_files)
    st.caption(
        f"backend={details.get('backend')} · evidences={details.get('evidences')}"
        f" · elapsed_ms={details.get('elapsed_ms')}"
    )
    if isinstance(evidence_files, list) and evidence_files:
        st.write("**证据文件列表**")
        st.table([{"rank": idx, "file": path} for idx, path in enumerate(evidence_files, start=1)])


def _render_agentic_route(trace: dict[str, Any]) -> None:
    """渲染 hybrid / fusion 阶段中的 agentic 路信息（routes 计数与触发状态）。"""
    hybrid_details = (_stage(trace, "hybrid") or {}).get("details") or {}
    fusion_details = (_stage(trace, "fusion") or {}).get("details") or {}
    trigger = hybrid_details.get("agentic_trigger")
    route_hits = [
        (name, details)
        for name, details in (("hybrid", hybrid_details), ("fusion", fusion_details))
        if isinstance(details.get("routes"), dict) and "agentic" in details["routes"]
    ]
    if not trigger and not route_hits:
        return
    st.subheader("Agentic 路由")
    rows = []
    for name, details in route_hits:
        routes = details["routes"]
        rows.append(
            {
                "stage": name,
                "dense": routes.get("dense"),
                "sparse": routes.get("sparse"),
                "agentic": routes.get("agentic"),
            }
        )
    if rows:
        st.table(rows)
    if trigger:
        st.caption(
            f"触发策略={trigger.get('strategy')} · triggered={trigger.get('triggered')}"
            f" · reason={trigger.get('reason')}"
        )


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


if __name__ == "__main__":
    render()
