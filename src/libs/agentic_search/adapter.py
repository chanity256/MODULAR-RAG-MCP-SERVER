"""Agentic 第三路检索适配器 — 阶段 S3.1（自 S2.5 scripts/query.py 平移）。

把 ``BaseAgenticSearch`` 包装成第三路 ``retrieve`` 形态。

``HybridSearch`` 的 ``agentic_retriever`` 注入契约（S2.3）是鸭子类型
``retrieve(query, top_k, trace=...) -> list[RetrievalResult]``；本适配器
在其下调用 ``backend.search(AgenticSearchRequest)`` 并经
``evidence_to_retrieval_results`` 转换证据。``last_response`` /
``last_elapsed_ms`` 供调用方展示/采集 Token 与耗时。

落点说明：S2.5 时仅 CLI 需要此包装，故收敛在脚本内；S3.1 评估入口
（``scripts/evaluate.py``）成为第二个消费者，按原注释预案平移到
``libs/agentic_search/``（``scripts/query.py`` 改为 re-import，对外行为
零变化）。
"""

from __future__ import annotations

import sys
import time
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from core.types import RetrievalResult
from libs.agentic_search.agentic_search_factory import AgenticSearchFactory
from libs.agentic_search.base_agentic_search import AgenticSearchRequest
from libs.agentic_search.converter import evidence_to_retrieval_results

__all__ = ["AgenticRouteAdapter", "build_agentic_retriever"]


class AgenticRouteAdapter:
    """把 ``BaseAgenticSearch`` 包装成第三路 ``retrieve`` 形态。"""

    def __init__(self, backend: Any) -> None:
        self._backend = backend
        self.last_response: Any | None = None
        self.last_elapsed_ms: float | None = None

    @property
    def backend_name(self) -> str:
        return getattr(self._backend, "backend_name", type(self._backend).__name__)

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        trace: Any = None,
    ) -> list[RetrievalResult]:
        """执行一次 Agentic 检索并转换为 ``RetrievalResult`` 列表。"""
        started = time.perf_counter()
        response = self._backend.search(AgenticSearchRequest(query=query))
        self.last_response = response
        self.last_elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        return evidence_to_retrieval_results(response, top_k=top_k)


def build_agentic_retriever(settings: Any) -> AgenticRouteAdapter:
    """按 ``settings.agentic_search.backend`` 构建第三路检索适配器。"""
    return AgenticRouteAdapter(AgenticSearchFactory.create(settings))
