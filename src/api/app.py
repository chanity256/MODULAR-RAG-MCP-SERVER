"""FastAPI 应用工厂（WEBUI_CHAT_SPEC §4.2）。

``create_app`` 零 IO：服务由 ``ServiceContainer`` 懒构建，测试注入 fake 即可。
本层只做协议适配（鉴权、CORS、错误格式、序列化）；业务逻辑留在应用服务。
日志一律走 logging（stderr），不向 stdout 输出。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from api.deps import ServiceContainer
from api.errors import install_error_handlers
from api.routes import build_v1_router
from api.routes import health as health_routes
from api.security import validate_api_binding
from core.settings import ApiSettings, Settings, load_settings

__all__ = ["SecurityHeadersMiddleware", "create_app"]

_DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response


def create_app(
    settings: Settings | None = None,
    *,
    services: ServiceContainer | None = None,
    api_settings: ApiSettings | None = None,
) -> FastAPI:
    """构建应用。

    Args:
        settings: 全局配置；缺省且未给 ``api_settings`` 时读取默认 ``config/settings.yaml``。
        services: 服务容器（测试注入 fake）；缺省按 ``settings`` 懒构建。
        api_settings: 覆盖 ``settings.api``（测试用，免构造完整 Settings）。

    Raises:
        ConfigError: 未配置 token 却绑定非 loopback 地址（启动即 fail-fast）。
    """
    if api_settings is None:
        if settings is None:
            settings = load_settings(_DEFAULT_CONFIG)
        api_settings = settings.api
    token = validate_api_binding(api_settings.host, api_settings.api_token)
    container = services or ServiceContainer(settings)

    app = FastAPI(
        title="Modular RAG API",
        version="0.1.0",
        description="Web API for the Modular RAG knowledge hub (read-only resources).",
    )
    app.state.container = container
    app.state.api_token = token
    app.state.api_settings = api_settings

    # add_middleware 后加者在外层：错误转换最内，CORS 最外，使所有响应（含 500）都带头。
    install_error_handlers(app)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(api_settings.allowed_origins),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )

    app.include_router(health_routes.router)
    app.include_router(build_v1_router())
    return app
