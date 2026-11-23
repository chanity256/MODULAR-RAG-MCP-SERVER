"""PageIndex HTTP 后端 — 阶段 PI3（PAGEINDEX_TREE_RETRIEVAL_SPEC §3.2 / §3.4）。

通过 HTTP 调用 PageIndex sidecar 的同步后端（工厂键 ``pageindex_http``，
唯一生产后端）。错误映射与 Sirchmunk 后端一致：超时 →
``TreeSearchTimeoutError``；连接失败 / 4xx / 5xx / ``success=false`` →
``TreeSearchBackendError``；非 JSON / 结构畸形 → ``TreeSearchResponseError``。
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from libs.tree_search.base_tree_search import (
    BaseTreeSearch,
    TreeSearchBackendError,
    TreeSearchRequest,
    TreeSearchResponse,
    TreeSearchResponseError,
    TreeSearchTimeoutError,
)
from libs.tree_search.converter import parse_search_data
from libs.tree_search.tree_search_factory import TreeSearchFactory

__all__ = ["PageIndexHttpBackend"]


@TreeSearchFactory.register("pageindex_http")
class PageIndexHttpBackend(BaseTreeSearch):
    """通过 HTTP 调用 PageIndex sidecar 的同步后端。"""

    def __init__(self, config: Any, *, transport: Any | None = None) -> None:
        """Args:
            config: ``TreeSearchConfig``（base_url/api_token/timeout_s/
                max_nodes/max_loops/max_token_budget 驱动请求）。
            transport: 可注入 httpx transport（测试 MockTransport 用）。
        """
        self._config = config

        def _transport_factory() -> Any:
            import httpx

            return httpx.HTTPTransport() if transport is None else transport

        self._transport_factory = _transport_factory

    @property
    def backend_name(self) -> str:
        return "pageindex_http"

    def search(self, request: TreeSearchRequest) -> TreeSearchResponse:
        """执行一次 sidecar 树检索（同步、可超时、错误分型）。"""
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
                raise TreeSearchTimeoutError(
                    f"sidecar timeout after {self._config.timeout_s}s: {exc}"
                ) from exc
            except httpx.HTTPError as exc:
                raise TreeSearchBackendError(f"sidecar unreachable: {exc}") from exc

        if resp.status_code >= 400:
            raise TreeSearchBackendError(
                f"sidecar returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            raise TreeSearchResponseError(f"sidecar returned non-JSON body: {exc}") from exc
        if not isinstance(body, dict) or body.get("success") is not True:
            error = body.get("error", "unknown error") if isinstance(body, dict) else body
            raise TreeSearchBackendError(f"sidecar reported failure: {error}")

        response = parse_search_data(body.get("data"))
        response.elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        return response

    def _build_payload(self, request: TreeSearchRequest) -> dict[str, Any]:
        """请求级覆盖缺省回填 config 同名值（对齐 agentic payload 口径）。"""
        return {
            "query": request.query,
            "source_paths": (
                list(request.source_paths) if request.source_paths is not None else None
            ),
            "max_nodes": (
                request.max_nodes if request.max_nodes is not None else self._config.max_nodes
            ),
            "max_loops": (
                request.max_loops if request.max_loops is not None else self._config.max_loops
            ),
            "max_token_budget": (
                request.max_token_budget
                if request.max_token_budget is not None
                else self._config.max_token_budget
            ),
        }
