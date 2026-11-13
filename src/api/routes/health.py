"""健康检查：免鉴权，只报告可达性，不暴露路径/配置/异常详情。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import ServiceContainer, get_container
from api.schemas.health import HealthResponse, SidecarHealth, StorageHealth

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness and dependency reachability")
def health(container: ServiceContainer = Depends(get_container)) -> HealthResponse:
    sidecar = SidecarHealth(**container.sidecar_health())
    storage = StorageHealth(**container.storage_health())
    degraded = storage.status == "degraded" or sidecar.status == "unreachable"
    return HealthResponse(status="degraded" if degraded else "ok", sidecar=sidecar, storage=storage)
