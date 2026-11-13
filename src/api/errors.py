"""统一错误格式：``{"success": false, "error": {code, message, details}}``。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

__all__ = [
    "ApiError",
    "UnhandledErrorMiddleware",
    "error_body",
    "install_error_handlers",
]

logger = logging.getLogger(__name__)

_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "too_many_requests",
    500: "internal_error",
    503: "service_unavailable",
}


class ApiError(Exception):
    """路由层主动抛出的业务错误（映射为统一错误体）。"""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str | None = None,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code or _STATUS_CODES.get(status_code, "error")
        self.details = details
        self.headers = headers


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"success": False, "error": {"code": code, "message": message, "details": details}}


def _json_error(
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(error_body(code, message, details), status_code=status_code, headers=headers)


async def _handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
    return _json_error(exc.status_code, exc.code, exc.message, exc.details, exc.headers)


async def _handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _STATUS_CODES.get(exc.status_code, "error")
    message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ")
    details = None if isinstance(exc.detail, str) else exc.detail
    return _json_error(exc.status_code, code, message, details, getattr(exc, "headers", None))


async def _handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # exc.errors() 的 ctx 可能含不可序列化对象（如异常实例），只取稳定字段。
    details = [
        {"loc": list(item.get("loc", ())), "msg": item.get("msg", ""), "type": item.get("type", "")}
        for item in exc.errors()
    ]
    return _json_error(422, "validation_error", "Request validation failed", details)


class UnhandledErrorMiddleware(BaseHTTPMiddleware):
    """把未捕获异常转为统一 500 响应。

    必须是最内层用户中间件：这样外层的 CORS / 安全头中间件仍会给 500 响应补头
    （框架自带的 ``Exception`` handler 位于所有中间件之外，响应拿不到这些头）。
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            return await call_next(request)
        except Exception:
            logger.exception("unhandled error on %s %s", request.method, request.url.path)
            return _json_error(500, "internal_error", "Internal server error")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_api_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_validation_error)  # type: ignore[arg-type]
    app.add_middleware(UnhandledErrorMiddleware)
