"""Web API 鉴权与绑定守卫（WEBUI_CHAT_SPEC §4.2 / D9）。

纯函数、无 FastAPI 路由依赖，HTTP 依赖（``deps.require_auth``）与 WS 端点（WC7）共用。
"""

from __future__ import annotations

import hmac
import ipaddress
import re
from typing import Mapping

from core.settings import ConfigError

__all__ = [
    "extract_bearer",
    "is_loopback_host",
    "normalize_token",
    "token_matches",
    "validate_api_binding",
    "verify_ws_token",
]

# settings.py 的 ${ENV} 解析在环境变量缺失时保留原值；若不拦截，
# 字面量 "${RAG_API_TOKEN}" 会成为一个公开已知的 token。
_UNRESOLVED_PLACEHOLDER = re.compile(r"^\$\{.+\}$")


def normalize_token(raw: str | None) -> str | None:
    """把配置中的 token 规整为有效值；空串 / 空白 / 未解析占位符一律视为未配置。"""
    if raw is None or not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value or _UNRESOLVED_PLACEHOLDER.match(value):
        return None
    return value


def is_loopback_host(host: str) -> bool:
    """``host`` 是否只监听本机（``0.0.0.0`` / ``::`` / 空串都不是）。"""
    value = (host or "").strip().strip("[]").lower()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def validate_api_binding(host: str, token: str | None) -> str | None:
    """校验绑定地址与 token 的组合；返回规整后的 token。

    未配置 token 时只允许 loopback，否则抛 ``ConfigError``（启动即 fail-fast）。
    """
    normalized = normalize_token(token)
    if normalized is None and not is_loopback_host(host):
        raise ConfigError(
            f"api.host={host!r} is not a loopback address but no API token is configured; "
            "set the RAG_API_TOKEN environment variable (api.api_token) or bind to 127.0.0.1"
        )
    return normalized


def token_matches(expected: str, provided: str | None) -> bool:
    """常数时间比较；按 UTF-8 字节比较以免非 ASCII 输入触发 TypeError。"""
    if not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def extract_bearer(authorization: str | None) -> str | None:
    """从 ``Authorization`` 头取 Bearer token；其它 scheme 返回 None。"""
    if not authorization:
        return None
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return credentials.strip() or None


def verify_ws_token(
    expected: str | None,
    *,
    query_params: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
) -> bool:
    """WS 握手鉴权：``?token=`` 查询参数或 ``Authorization: Bearer`` 头任一匹配即通过。

    ``expected`` 为 None（未配置 token，仅 loopback）时放行。
    """
    if expected is None:
        return True
    if query_params is not None and token_matches(expected, query_params.get("token")):
        return True
    if headers is not None:
        auth = headers.get("authorization") or headers.get("Authorization")
        return token_matches(expected, extract_bearer(auth))
    return False
