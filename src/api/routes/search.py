"""Query Playground：``POST /search``（normal / fast / deep）与 ``GET /search/capabilities``。

薄包装既有 :class:`SearchService`（D8：Playground 原样复用，不改其检索链路）。

错误映射：
- 模式被配置门控拒绝（``agentic_search.enabled`` / ``allow_deep``）→ 403 统一错误体，
  code ``AGENTIC_DISABLED`` / ``DEEP_DISABLED``，请求不会触达 sidecar；
- 检索本身失败（sidecar 不可达、工具 ``isError``、参数被工具拒绝）→ 200 + ``is_error: true``，
  与 Streamlit 页同口径：这是一次「执行了但失败」的结果，而非协议错误。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from api.deps import get_search_service
from api.errors import ApiError
from api.schemas.common import ErrorResponse
from api.schemas.search import ModeStatus, SearchCapabilities, SearchRequest, SearchResponse
from observability.dashboard.services.search_service import SearchService

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/capabilities", response_model=SearchCapabilities, summary="Query Playground mode availability")
def get_capabilities(service: SearchService = Depends(get_search_service)) -> SearchCapabilities:
    availability = service.availability()
    modes = [mode for mode in ("normal", "fast", "deep") if availability[mode].available]
    return SearchCapabilities(
        modes=modes,  # type: ignore[arg-type]
        agentic_enabled=availability["fast"].available,
        allow_deep=availability["deep"].available,
        availability={
            mode: ModeStatus(available=status.available, reason=status.reason)
            for mode, status in availability.items()
        },
    )


@router.post(
    "",
    response_model=SearchResponse,
    responses={403: {"model": ErrorResponse, "description": "Mode disabled by agentic_search config"}},
    summary="Run a retrieval in normal / fast / deep mode",
)
def search(body: SearchRequest, service: SearchService = Depends(get_search_service)) -> SearchResponse:
    if body.mode != "normal":
        availability = service.availability()
        if not availability["fast"].available:
            raise ApiError(
                403,
                availability["fast"].reason or "agentic search is disabled",
                code="AGENTIC_DISABLED",
                details={"mode": body.mode},
            )
        if body.mode == "deep" and not availability["deep"].available:
            raise ApiError(
                403,
                availability["deep"].reason or "deep search is disabled",
                code="DEEP_DISABLED",
                details={"mode": body.mode},
            )

    outcome = service.run(body.mode, body.query, top_k=body.top_k, collection=body.collection)
    structured: dict[str, Any] = {"citations": list(outcome.citations)}
    if outcome.agentic_meta is not None:
        structured["agentic"] = outcome.agentic_meta
    return SearchResponse(
        mode=body.mode,
        query=body.query,
        elapsed_ms=outcome.elapsed_ms,
        markdown=outcome.markdown,
        structured=structured,
        is_error=outcome.is_error,
        error=outcome.error if outcome.is_error else None,
    )
