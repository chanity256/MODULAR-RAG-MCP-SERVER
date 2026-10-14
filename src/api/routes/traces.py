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


def _query_timings(traces: TraceService, record: dict[str, Any]) -> list[dict[str, Any]]:
    """已知检索阶段按规范顺序在前，其余阶段（chat_* / agentic_search ...）按记录顺序追加；同名累加。

    Ingestion 不走此路径：其 ``integrity`` 等前置检查阶段不属于瀑布图口径。
    """
    timings = [item for item in traces.stage_timings(record, _QUERY_STAGES) if item["elapsed_ms"] > 0]
    extras: dict[str, float] = {}
    known = set(_QUERY_STAGES)
    for item in traces.stage_timings(record):
        if item["stage"] not in known and item["elapsed_ms"] > 0:
            extras[item["stage"]] = extras.get(item["stage"], 0.0) + item["elapsed_ms"]
    return [*timings, *({"stage": name, "elapsed_ms": ms} for name, ms in extras.items())]


@router.get("", response_model=Page[TraceSummary], summary="List traces (newest first)")
def list_traces(
    trace_type: Literal["ingestion", "query"] | None = Query(default=None),
    keyword: str | None = Query(default=None, description="Match on trace_id / query / source_path"),
    routes: str | None = Query(
        default=None,
        description="Keep only traces whose source tag equals this (`chat` / `agentic` / `default` / ...); "
        "source is metadata.routes, `default` when absent",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    traces: TraceService = Depends(get_trace_service),
) -> Page[TraceSummary]:
    records = traces.list_traces(trace_type)
    source = (routes or "").strip()
    if source:
        records = [item for item in records if TraceService.trace_source(item) == source]
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
    if record.get("trace_type") == "ingestion":
        timings = [item for item in traces.stage_timings(record, _INGEST_STAGES) if item["elapsed_ms"] > 0]
        if not timings:
            timings = [item for item in traces.stage_timings(record) if item["elapsed_ms"] > 0]
    else:
        timings = _query_timings(traces, record)
    summary = TraceSummary.from_record(record)
    return TraceDetail(
        **summary.model_dump(),
        stages=[StageRecord(**stage) for stage in record.get("stages") or []],
        timings=[StageTiming(stage=item["stage"], elapsed_ms=item["elapsed_ms"]) for item in timings],
    )
