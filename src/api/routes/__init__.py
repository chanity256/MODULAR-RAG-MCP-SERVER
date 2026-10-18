"""``/api/v1`` 路由聚合；新增资源路由只需在此登记。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import require_auth
from api.routes import collections, documents, images, overview, traces
from api.routes import chat, evaluations, ingestion, search, tasks
from api.schemas.common import ErrorResponse

__all__ = ["API_PREFIX", "build_v1_router", "build_v1_ws_router"]

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
    for module in (ingestion, evaluations, tasks, chat, search):
        router.include_router(module.router)
    router.include_router(ingestion.delete_router)
    return router


def build_v1_ws_router() -> APIRouter:
    """WS 路由不能挂在 ``build_v1_router`` 下：其 ``require_auth`` 依赖需要 HTTP ``Request``，
    WS 端点改用 ``verify_ws_token`` 在握手阶段自行鉴权。"""
    router = APIRouter(prefix=API_PREFIX)
    router.include_router(tasks.ws_router)
    router.include_router(chat.ws_router)
    router.include_router(search.ws_router)
    return router
