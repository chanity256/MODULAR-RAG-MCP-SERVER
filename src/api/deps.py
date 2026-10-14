"""依赖注入：鉴权依赖与懒构建的服务单例。

``ServiceContainer`` 是 API 层唯一的服务来源；测试构造容器时注入 fake，生产路径
按 ``Settings`` 懒构建（首次访问才触碰存储，保证 ``create_app`` 本身零 IO）。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.errors import ApiError
from api.security import token_matches
from core.settings import Settings
from observability.dashboard.services.config_service import ConfigService
from observability.dashboard.services.data_service import DataService
from observability.dashboard.services.trace_service import TraceService

__all__ = [
    "ServiceContainer",
    "get_config_service",
    "get_container",
    "get_data_service",
    "get_trace_service",
    "probe_sidecar",
    "require_auth",
]

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False, description="RAG_API_TOKEN (api.api_token)")


def probe_sidecar(base_url: str, timeout: float = 2.0) -> bool:
    """探活 sidecar：任何 HTTP 响应（含 401）都算进程存活，与 start_dashboard 口径一致。"""
    try:
        httpx.get(f"{base_url.rstrip('/')}/api/v1/search/status", timeout=timeout)
        return True
    except httpx.HTTPError:
        return False


class ServiceContainer:
    """服务单例容器；所有构造参数可选，缺省按 ``settings`` 懒构建。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        config_service: ConfigService | None = None,
        data_service: DataService | None = None,
        trace_service: TraceService | None = None,
        sidecar_probe: Callable[[], bool] | None = None,
        storage_probe: Callable[[], dict[str, bool]] | None = None,
    ) -> None:
        self._settings = settings
        self._config = config_service
        self._data = data_service
        self._traces = trace_service
        self._sidecar_probe = sidecar_probe
        self._storage_probe = storage_probe
        self._lock = threading.Lock()

    @property
    def settings(self) -> Settings | None:
        return self._settings

    @property
    def config(self) -> ConfigService:
        with self._lock:
            if self._config is None:
                self._config = ConfigService(self._settings)
            return self._config

    @property
    def data(self) -> DataService:
        with self._lock:
            if self._data is None:
                self._data = DataService(settings=self._settings)
            return self._data

    @property
    def traces(self) -> TraceService:
        with self._lock:
            if self._traces is None:
                self._traces = TraceService(settings=self._settings)
            return self._traces

    def sidecar_health(self) -> dict[str, Any]:
        """探活失败不抛：未启用 → disabled；注入探针优先于配置。"""
        agentic = self._settings.agentic_search if self._settings is not None else None
        enabled = bool(agentic.enabled) if agentic is not None else False
        probe = self._sidecar_probe
        if probe is None:
            if agentic is None:
                return {"status": "unknown", "enabled": False}
            if not enabled:
                return {"status": "disabled", "enabled": False}
            base_url = agentic.base_url
            probe = lambda: probe_sidecar(base_url)  # noqa: E731
        try:
            alive = bool(probe())
        except Exception:
            logger.warning("sidecar probe raised", exc_info=True)
            alive = False
        return {"status": "ok" if alive else "unreachable", "enabled": enabled or self._sidecar_probe is not None}

    def storage_health(self) -> dict[str, Any]:
        """只做文件系统级检查，不实例化 Chroma/BM25，避免免鉴权端点触发重 IO 或建库。"""
        probe = self._storage_probe
        if probe is None:
            if self._settings is None:
                return {"status": "unknown", "components": {}}
            settings = self._settings
            probe = lambda: {  # noqa: E731
                "vector_store": Path(settings.vector_store.persist_path).exists(),
                "traces": Path(settings.observability.log_file).parent.exists(),
            }
        try:
            components = {str(k): bool(v) for k, v in probe().items()}
        except Exception:
            logger.warning("storage probe raised", exc_info=True)
            return {"status": "degraded", "components": {}}
        if not components:
            return {"status": "unknown", "components": {}}
        return {"status": "ok" if all(components.values()) else "degraded", "components": components}


def get_container(request: Request) -> ServiceContainer:
    return request.app.state.container


def get_data_service(container: ServiceContainer = Depends(get_container)) -> DataService:
    return container.data


def get_trace_service(container: ServiceContainer = Depends(get_container)) -> TraceService:
    return container.traces


def get_config_service(container: ServiceContainer = Depends(get_container)) -> ConfigService:
    return container.config


def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """未配置 token（仅 loopback）放行；配置后必须带正确的 Bearer。"""
    expected: str | None = request.app.state.api_token
    if expected is None:
        return
    if credentials is None or not token_matches(expected, credentials.credentials):
        raise ApiError(
            401,
            "Missing or invalid API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
