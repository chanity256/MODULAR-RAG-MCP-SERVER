"""``/api/v1`` 路由聚合；新增资源路由只需在此登记。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import require_auth
from api.routes import collections, documents, images, overview, traces
from api.schemas.common import ErrorResponse

__all__ = ["API_PREFIX", "build_v1_router"]

API_PREFIX = "/api/v1"

_ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Missing or invalid API token"},
    422: {"model": ErrorResponse, "description": "Request validation failed"},
}


def build_v1_router() -> APIRouter:
    router = APIRouter(
        prefix=API_PREFIX,
        dependencies=[Depends(require_auth)],
        responses=_ERROR_RESPONSES,  # type: ignore[arg-type]
    )
    for module in (overview, collections, documents, images, traces):
        router.include_router(module.router)
    return router
