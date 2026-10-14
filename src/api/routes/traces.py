"""Ingestion / Query trace 列表与详情。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from api.deps import get_trace_service
from api.errors import ApiError
from api.schemas.common import ErrorResponse, Page
from api.schemas.traces import StageRecord, StageTiming, TraceDetail, TraceSummary
from observability.dashboard.services.trace_service import TraceService

router = APIRouter(prefix="/traces", tags=["traces"])

# 与 Streamlit 追踪页的瀑布图阶段口径一致。
_INGEST_STAGES = ["load", "split", "transform", "embed", "store", "upsert"]
_QUERY_STAGES = ["query_processing", "dense_retrieval", "sparse_retrieval", "fusion", "hybrid", "rerank"]


def _matches(record: dict[str, Any], needle: str) -> bool:
    summary = TraceSummary.from_record(record)
    haystacks = (summary.trace_id, summary.query, summary.source_path)
    return any(needle in str(value).lower() for value in haystacks if value)


@router.get("", response_model=Page[TraceSummary], summary="List traces (newest first)")
def list_traces(
    trace_type: Literal["ingestion", "query"] | None = Query(default=None),
    keyword: str | None = Query(default=None, description="Match on trace_id / query / source_path"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    traces: TraceService = Depends(get_trace_service),
) -> Page[TraceSummary]:
    records = traces.list_traces(trace_type)
    needle = (keyword or "").strip().lower()
    if needle:
        records = [item for item in records if _matches(item, needle)]
    page = records[offset : offset + limit]
    return Page[TraceSummary](
        items=[TraceSummary.from_record(item) for item in page],
        total=len(records),
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{trace_id}",
    response_model=TraceDetail,
    responses={404: {"model": ErrorResponse}},
    summary="Get one trace with stages and timings",
)
def get_trace(trace_id: str, traces: TraceService = Depends(get_trace_service)) -> TraceDetail:
    record = traces.get_trace(trace_id)
    if record is None:
        raise ApiError(404, f"Trace {trace_id!r} not found")
    wanted = _INGEST_STAGES if record.get("trace_type") == "ingestion" else _QUERY_STAGES
    timings = [item for item in traces.stage_timings(record, wanted) if item["elapsed_ms"] > 0]
    if not timings:
        timings = [item for item in traces.stage_timings(record) if item["elapsed_ms"] > 0]
    summary = TraceSummary.from_record(record)
    return TraceDetail(
        **summary.model_dump(),
        stages=[StageRecord(**stage) for stage in record.get("stages") or []],
        timings=[StageTiming(stage=item["stage"], elapsed_ms=item["elapsed_ms"]) for item in timings],
    )
