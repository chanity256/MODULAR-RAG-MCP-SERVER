"""摄取失败的错误分类与退避计算（INGESTION_ROBUSTNESS_SPEC M4 / IR4.1）。

分类口径：「同一输入重跑可能成功」的瞬时错误可重试，其余一律不可重试。

- **可重试**：超时（``TimeoutError`` / ``httpx.TimeoutException``）、连接类错误
  （``ConnectionError`` 及其子类、``urllib.error.URLError``、``httpx.NetworkError``
  / ``httpx.RemoteProtocolError``）、HTTP 5xx 与 429（``httpx.HTTPStatusError``、
  ``urllib.error.HTTPError``，以及携带 ``status`` 属性的 provider 包装异常如
  ``EmbeddingRequestError``）、SQLite ``database is locked`` / ``busy``。
- **不可重试**：确定性阶段（integrity / load / split）的 ``IngestionPipelineError``、
  文件不存在/无权限、配置非法（``ConfigError``）、HTTP 4xx（429 除外），以及
  任何未识别的异常（保守默认，避免对确定性故障反复重打外部服务）。

判定沿异常链（``__cause__`` 优先，其次 ``__context__``）由外向内进行，第一个
能给出结论的异常决定结果：包装异常（如 ``status=None`` 的
``EmbeddingRequestError``）本身不下结论时，交给其底层原因判定。
"""

from __future__ import annotations

import random
import sqlite3
import urllib.error
from typing import Any, Callable

__all__ = [
    "MAX_BACKOFF_S",
    "NON_RETRYABLE_STAGES",
    "RETRYABLE_STAGES",
    "compute_backoff",
    "is_retryable",
]

#: 退避上限（秒）：``initial × multiplier^n`` 无论如何不超过该值。
MAX_BACKOFF_S = 30.0

#: 输入确定、重跑结果必然一致的阶段：其失败直接判为不可重试。
NON_RETRYABLE_STAGES = frozenset({"integrity", "load", "split"})

#: pipeline 中启用退避重试的阶段（transform 的 LLM 失败在组件内部降级，不经此通道）。
RETRYABLE_STAGES = frozenset({"embed", "store"})

_RETRYABLE_STATUS = frozenset({429})
_MAX_CHAIN_DEPTH = 16

try:  # httpx 是可选依赖：缺失时只是少一组可识别类型
    import httpx

    _HTTPX_TRANSIENT: tuple[type[BaseException], ...] = (
        httpx.TimeoutException,
        httpx.NetworkError,
        httpx.RemoteProtocolError,
    )
    _HTTPX_STATUS_ERROR: type[BaseException] | None = httpx.HTTPStatusError
except ImportError:  # pragma: no cover - 依赖缺失分支
    _HTTPX_TRANSIENT = ()
    _HTTPX_STATUS_ERROR = None


def is_retryable(exc: BaseException) -> bool:
    """判定异常是否值得退避重试（见模块文档的分类表）。"""
    seen: set[int] = set()
    current: BaseException | None = exc
    depth = 0
    while current is not None and id(current) not in seen and depth < _MAX_CHAIN_DEPTH:
        seen.add(id(current))
        verdict = _classify_one(current)
        if verdict is not None:
            return verdict
        current = current.__cause__ or current.__context__
        depth += 1
    return False


def _classify_one(exc: BaseException) -> bool | None:
    """单个异常的判定：True/False 为结论，None 表示交给底层原因。"""
    from core.settings import ConfigError
    from ingestion.pipeline import IngestionPipelineError

    if isinstance(exc, IngestionPipelineError):
        if exc.stage in NON_RETRYABLE_STAGES:
            return False
        return None
    if isinstance(exc, ConfigError):
        return False
    if isinstance(exc, (FileNotFoundError, IsADirectoryError, NotADirectoryError, PermissionError)):
        return False
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if _HTTPX_TRANSIENT and isinstance(exc, _HTTPX_TRANSIENT):
        return True
    if isinstance(exc, sqlite3.OperationalError):
        message = str(exc).lower()
        return "locked" in message or "busy" in message
    status = _http_status(exc)
    if status is not None:
        return status >= 500 or status in _RETRYABLE_STATUS
    if isinstance(exc, urllib.error.URLError):
        return True
    return None


def _http_status(exc: BaseException) -> int | None:
    if _HTTPX_STATUS_ERROR is not None and isinstance(exc, _HTTPX_STATUS_ERROR):
        response = getattr(exc, "response", None)
        return getattr(response, "status_code", None)
    for attr in ("status", "status_code", "code"):
        value: Any = getattr(exc, attr, None)
        if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
            return value
    return None


def compute_backoff(
    attempt: int,
    *,
    initial_backoff_s: float,
    backoff_multiplier: float,
    backoff_jitter: float,
    random_fn: Callable[[], float] = random.random,
    max_backoff_s: float = MAX_BACKOFF_S,
) -> float:
    """第 ``attempt`` 次失败后的等待秒数（``attempt`` 从 1 起）。

    基准 ``initial × multiplier^(attempt-1)``，乘以 ``1 ± jitter`` 的均匀抖动
    （``random_fn`` 返回 ``[0, 1)``，可注入以获得确定性），最终封顶
    ``max_backoff_s``。
    """
    if attempt < 1:
        raise ValueError(f"attempt must be >= 1, got {attempt!r}")
    base = initial_backoff_s * (backoff_multiplier ** (attempt - 1))
    factor = 1.0 + backoff_jitter * (2.0 * random_fn() - 1.0)
    return max(0.0, min(max_backoff_s, base * factor))
