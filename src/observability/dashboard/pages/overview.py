"""系统总览页（阶段 G1）。"""

from __future__ import annotations

from typing import Any

import streamlit as st

from observability.dashboard.services.config_service import ConfigService
from observability.dashboard.services.data_service import DataService
from observability.dashboard.services.trace_service import TraceService

__all__ = ["render"]


def render(
    *,
    config_service: ConfigService | None = None,
    data_service: DataService | None = None,
    trace_service: TraceService | None = None,
) -> None:
    config_service = config_service or ConfigService()
    data_service = data_service or DataService()
    trace_service = trace_service or TraceService()

    st.header("系统总览")
    st.caption("当前可插拔组件配置与知识库资产统计。")

    cards = config_service.component_cards()
    columns = st.columns(len(cards) or 1)
    for column, card in zip(columns, cards):
        with column:
            st.subheader(card.name)
            st.write(card.headline())
            if card.extras:
                extras = {key: value for key, value in card.extras.items() if value is not None}
                if extras:
                    st.caption(" · ".join(f"{key}={value}" for key, value in extras.items()))

    st.divider()
    st.subheader("数据资产")
    try:
        stats = data_service.get_collection_stats()
    except Exception as exc:
        st.warning(f"读取集合统计失败：{exc}")
        stats = []
    if not stats:
        st.info("暂无已摄入文档。请先运行 `python scripts/ingest.py` 或前往 Ingestion 管理页上传。")
    else:
        total_docs = sum(item.document_count for item in stats)
        total_chunks = sum(item.chunk_count for item in stats)
        total_images = sum(item.image_count for item in stats)
        metric_cols = st.columns(3)
        metric_cols[0].metric("文档数", total_docs)
        metric_cols[1].metric("Chunk 数", total_chunks)
        metric_cols[2].metric("图片数", total_images)
        st.table(
            [
                {
                    "集合": item.name,
                    "文档": item.document_count,
                    "Chunks": item.chunk_count,
                    "图片": item.image_count,
                }
                for item in stats
            ]
        )

    st.divider()
    st.subheader("系统健康")
    health_cols = st.columns(2)
    with health_cols[0]:
        _render_latest_trace(trace_service.latest("ingestion"), "最近一次 Ingestion")
    with health_cols[1]:
        _render_latest_trace(trace_service.latest("query"), "最近一次 Query")


def _render_latest_trace(trace: dict[str, Any] | None, title: str) -> None:
    st.markdown(f"**{title}**")
    if not trace:
        st.caption("暂无记录")
        return
    elapsed = trace.get("total_elapsed_ms")
    st.write(f"trace_id: `{trace.get('trace_id')}`")
    st.caption(f"started_at={trace.get('started_at')} · {elapsed} ms")


if __name__ == "__main__":
    render()
