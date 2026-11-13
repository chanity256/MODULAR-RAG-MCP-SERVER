"""QueryService：HybridSearch + Reranker 装配，返回结构化 ``RetrievalResult``（WC1.6）。

与 :class:`~observability.dashboard.services.search_service.SearchService` 的
normal 模式使用同一条检索链路，区别在产物与 trace 归属：

- 返回 ``list[RetrievalResult]``（供有据回答生成，而不是 MCP Markdown）；
- trace 由调用方创建、传入并负责结束/落盘；本服务只把它透传给
  ``HybridSearch.search`` 与 ``Reranker.rerank``，**从不**创建、finish 或持久化
  trace。未传 trace 时由下游各自内部新建（不落盘）。

不经过、不改动 MCP tool。检索栈懒构建；``search`` / ``reranker`` 可注入 fake，
离线单测零网络、零真实模型。
"""

from __future__ import annotations

from typing import Any

from core.types import RetrievalResult

__all__ = ["QueryService"]


class QueryService:
    """混合检索 + 可选精排。

    Args:
        settings: 全局配置（构建默认检索栈用；缺省首次调用时 ``load_settings()``）。
        search: 具备 ``search(query, top_k, filters, trace=...)`` 的检索器
            （缺省首次调用时构建 ``HybridSearch(settings)``）。
        reranker: 具备 ``rerank(query, results, trace=...)`` 的精排器
            （缺省且 ``rerank_enabled`` 时首次需要精排才构建 ``Reranker(settings)``）。
        rerank_enabled: False 时跳过精排，直接返回融合排名。
    """

    def __init__(
        self,
        settings: Any | None = None,
        *,
        search: Any | None = None,
        reranker: Any | None = None,
        rerank_enabled: bool = True,
    ) -> None:
        self._settings = settings
        self._search = search
        self._reranker = reranker
        self._rerank_enabled = rerank_enabled

    @property
    def rerank_enabled(self) -> bool:
        return self._rerank_enabled

    def search(
        self,
        query: str,
        *,
        collection: str | None = None,
        top_k: int | None = None,
        trace: Any | None = None,
    ) -> list[RetrievalResult]:
        """执行检索（+ 精排）。

        Args:
            query: 独立完整的用户查询。
            collection: 限定集合；``None`` 或空白不过滤。
            top_k: 返回数量；``None`` 取 ``retrieval.top_k_final``。
            trace: 调用方持有的 ``TraceContext``，原样透传；服务不 finish、不落盘。

        Returns:
            ``RetrievalResult`` 列表（无命中时为空列表，不调用精排）。

        Raises:
            TypeError / ValueError: ``query`` 非字符串或为空白、``top_k`` 非正整数。
            Exception: 检索/精排下游异常原样传播（如 ``HybridSearchError``）。
        """
        if not isinstance(query, str):
            raise TypeError(f"query must be a string, got {type(query).__name__}")
        if not query.strip():
            raise ValueError("query must be a non-empty, non-whitespace string")
        if top_k is not None and (isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1):
            raise ValueError(f"top_k must be a positive integer or None, got {top_k!r}")

        collection = collection.strip() if isinstance(collection, str) else collection
        filters = {"collection": collection} if collection else None

        results = self._get_search().search(query, top_k=top_k, filters=filters, trace=trace)
        if self._rerank_enabled and results:
            results = self._get_reranker().rerank(query, results, trace=trace)
        return list(results)

    def _get_settings(self) -> Any:
        if self._settings is None:
            from core.settings import load_settings

            self._settings = load_settings()
        return self._settings

    def _get_search(self) -> Any:
        if self._search is None:
            from core.query_engine import HybridSearch

            self._search = HybridSearch(self._get_settings())
        return self._search

    def _get_reranker(self) -> Any:
        if self._reranker is None:
            from core.query_engine import Reranker

            self._reranker = Reranker(self._get_settings())
        return self._reranker
