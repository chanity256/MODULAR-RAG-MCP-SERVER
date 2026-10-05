"""Sirchmunk HTTP sidecar 后端 — 阶段 S1.4。

同步 httpx 调用独立进程 ``sirchmunk serve`` 的 ``POST /api/v1/search``
（Bearer 鉴权 + ``response_format=context``，spec 3.2）。主 venv 永不
import sirchmunk——所有上游耦合收敛在本文件。

错误映射（spec S1.4）：
- ``httpx.TimeoutException`` → :class:`AgenticSearchTimeoutError`
- ``httpx.ConnectError`` 等传输错误 → :class:`AgenticSearchBackendError`
- HTTP 4xx/5xx / ``success=false`` → :class:`AgenticSearchBackendError`
- 响应非 JSON / 结构畸形 → :class:`AgenticSearchResponseError`

测试：``transport`` 可注入（``httpx.MockTransport`` 离线单测）；真实
sidecar 冒烟走 ``RAG_AGENTIC_INTEGRATION=1`` 门控（S1.7）。
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from libs.agentic_search.base_agentic_search import (
    AgenticSearchBackendError,
    AgenticSearchRequest,
    AgenticSearchResponse,
    AgenticSearchResponseError,
    AgenticSearchTimeoutError,
    BaseAgenticSearch,
)
from libs.agentic_search.agentic_search_factory import AgenticSearchFactory
from libs.agentic_search.converter import parse_search_context

__all__ = ["SirchmunkHttpBackend"]


@AgenticSearchFactory.register("sirchmunk_http")
class SirchmunkHttpBackend(BaseAgenticSearch):
    """通过 HTTP 调用 sirchmunk sidecar 的同步后端。"""

    def __init__(self, config: Any, *, transport: Any | None = None) -> None:
        """Args:
            config: ``AgenticSearchConfig``（base_url/api_token/timeout_s/
                top_k_files/max_loops/max_token_budget 驱动请求）。
            transport: 可注入 httpx transport（测试 MockTransport 用）。
        """
        self._config = config

        def _transport_factory() -> Any:
            import httpx

            return httpx.HTTPTransport() if transport is None else transport

        self._transport_factory = _transport_factory

    @property
    def backend_name(self) -> str:
        return "sirchmunk_http"

    def search(self, request: AgenticSearchRequest) -> AgenticSearchResponse:
        """执行一次 sidecar 检索（同步、可超时、错误分型）。"""
        import httpx

        started = time.perf_counter()
        payload = self._build_payload(request)
        headers = {"Authorization": f"Bearer {self._config.api_token}"} if self._config.api_token else {}
        with httpx.Client(
            transport=self._transport_factory(),
            timeout=self._config.timeout_s,
        ) as client:
            try:
                resp = client.post(
                    f"{self._config.base_url.rstrip('/')}/api/v1/search",
                    json=payload,
                    headers=headers,
                )
            except httpx.TimeoutException as exc:
                raise AgenticSearchTimeoutError(
                    f"sidecar timeout after {self._config.timeout_s}s: {exc}"
                ) from exc
            except httpx.HTTPError as exc:
                raise AgenticSearchBackendError(f"sidecar unreachable: {exc}") from exc

        if resp.status_code >= 400:
            raise AgenticSearchBackendError(
                f"sidecar returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            raise AgenticSearchResponseError(f"sidecar returned non-JSON body: {exc}") from exc
        if not isinstance(body, dict) or body.get("success") is not True:
            error = body.get("error", "unknown error") if isinstance(body, dict) else body
            raise AgenticSearchBackendError(f"sidecar reported failure: {error}")

        response = parse_search_context(body.get("data"), mode=request.mode)
        response.elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        return response

    def _build_payload(self, request: AgenticSearchRequest) -> dict[str, Any]:
        cfg = self._config
        payload: dict[str, Any] = {
            "query": request.query,
            "mode": request.mode,
            "response_format": "context",
            # 恒发送绝对路径（PathGuard 已校验）；sidecar 相对路径按其
            # CWD 解析，绝不依赖（spec 4.6）。
            "paths": list(request.paths) if request.paths is not None else None,
            "top_k_files": request.top_k_files if request.top_k_files is not None else cfg.top_k_files,
            "max_loops": request.max_loops if request.max_loops is not None else cfg.max_loops,
            "max_token_budget": (
                request.max_token_budget
                if request.max_token_budget is not None
                else cfg.max_token_budget
            ),
        }
        if request.include is not None:
            payload["include_patterns"] = list(request.include)
        if request.exclude is not None:
            payload["exclude_patterns"] = list(request.exclude)
        return payload
