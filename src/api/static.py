"""单端口托管静态导出前端（WEBUI_CHAT_SPEC §4.2 / WC3.4）。

Next 静态导出（``trailingSlash: true``）为每个路由生成 ``<route>/index.html``；
``StaticFiles(html=True)`` 对无斜杠形式会 307 重定向，对客户端路由则直接 404。
这里用 :class:`SPAStaticFiles` 统一处理：

1. 精确文件（``/_next/...``、``/favicon.ico``）；
2. ``<path>/index.html`` 与 ``<path>.html``（带/不带斜杠都直接 200，不重定向）；
3. 带扩展名的缺失资源 → 404（不能把 index.html 当作 JS/CSS 返回）；
4. 其余视为客户端路由 → 根 ``index.html``。

``/api`` 前缀永远不回退到 HTML：未匹配的 API 路径返回统一 JSON 404 信封。
静态挂载必须在所有 API 路由注册之后调用 :func:`install_static_ui`。
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from core.settings import ApiSettings, ConfigError

__all__ = ["SPAStaticFiles", "install_static_ui", "resolve_static_dir"]

logger = logging.getLogger(__name__)


def _is_api_path(path: str) -> bool:
    head = path.strip("/").split("/", 1)[0]
    return head == "api"


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        normalized = path.strip("/")
        if normalized == ".":
            normalized = ""
        if _is_api_path(normalized):
            raise HTTPException(status_code=404)

        candidates = [normalized] if normalized else ["index.html"]
        if normalized:
            candidates += [f"{normalized}/index.html", f"{normalized}.html"]
        for candidate in candidates:
            try:
                return await super().get_response(candidate, scope)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise

        suffix = PurePosixPath(normalized).suffix
        if suffix and suffix != ".html":
            raise HTTPException(status_code=404)
        return await super().get_response("index.html", scope)


def resolve_static_dir(api_settings: ApiSettings) -> Path:
    """解析并校验静态目录；``serve_ui`` 开启时缺目录/缺 index.html 即 fail-fast。"""
    directory = Path(api_settings.static_dir).expanduser()
    if not directory.is_absolute():
        directory = Path.cwd() / directory
    if not (directory / "index.html").is_file():
        raise ConfigError(
            f"api.serve_ui is true but static_dir {str(directory)!r} has no index.html; "
            "build the frontend (cd frontend && npm run build) or set api.serve_ui: false"
        )
    return directory


def install_static_ui(app: FastAPI, api_settings: ApiSettings) -> None:
    """``serve_ui`` 为 false 时无操作；否则在已注册的 API 路由之后挂载 ``/``。"""
    if not api_settings.serve_ui:
        return
    directory = resolve_static_dir(api_settings)
    app.mount("/", SPAStaticFiles(directory=str(directory)), name="ui")
    logger.info("serving UI from %s", directory)
