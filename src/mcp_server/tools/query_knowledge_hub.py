"""MCP Tool：query_knowledge_hub — 阶段 E3。

主检索入口（DEV_SPEC 3.2.4）：HybridSearch + Reranker → 带引用的
MCP 响应（Markdown content + structuredContent.citations）。

设计要点：
- **懒初始化**：检索栈（HybridSearch / Reranker）在首次调用时才按
  ``Settings`` 构建，避免 server 启动即触发网络/凭据依赖；后续调用
  复用同一实例。
- **可注入**：``QueryKnowledgeHubTool(search=..., reranker=...)``
  支持测试注入 fake 查询栈（离线单测零网络零持久化）。
- **参数校验**：query 必填非空字符串、top_k 为正整数、collection 为
  字符串；非法参数抛 ``ValueError``/``TypeError``（由 E2
  ProtocolHandler 统一转换为 JSON-RPC error）。
- 与 ``scripts/query.py``（D7 调试入口）共享 Core 层，各自独立装配。
"""

from __future__ import annotations

import sys
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from core.response import ResponseBuilder
from core.trace.trace_context import TraceContext
from mcp_server.protocol_handler import ToolDefinition, ToolInvalidParams

__all__ = [
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "TOOL_INPUT_SCHEMA",
    "QueryKnowledgeHubTool",
    "create_query_knowledge_hub_tool",
    "create_query_knowledge_hub_definition",
]

TOOL_NAME = "query_knowledge_hub"
TOOL_DESCRIPTION = (
    "Search the private knowledge hub with hybrid retrieval "
    "(dense embeddings + BM25 keywords, RRF fusion) followed by optional "
    "reranking. Returns readable Markdown with [1]/[2] citation markers and "
    "structured citations (source, page, chunk_id, score)."
)
TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The standalone (context-complete) user query.",
        },
        "top_k": {
            "type": "integer",
            "minimum": 1,
            "description": "Maximum number of results to return.",
        },
        "collection": {
            "type": "string",
            "description": "Restrict search to a specific collection.",
        },
    },
    "required": ["query"],
}


class QueryKnowledgeHubTool:
    """query_knowledge_hub 的可调用实现（协议无关，直接吃 arguments dict）。"""

    def __init__(
        self,
        search: Any | None = None,
        reranker: Any | None = None,
        *,
        settings: Any | None = None,
        response_builder: ResponseBuilder | None = None,
        rerank_enabled: bool = True,
    ) -> None:
        """Args:
            search: 具备 ``search(query, top_k, filters, trace=...)`` 签法的
                检索器（缺省首次调用时构建 ``HybridSearch(settings)``）。
            reranker: 具备 ``rerank(query, results, trace=...)`` 签法的精排器
                （缺省首次调用时构建 ``Reranker(settings)``）。
            settings: 全局配置（构建默认检索栈用；缺省 ``load_settings()``）。
            response_builder: 响应构建器（缺省 ``ResponseBuilder()``）。
            rerank_enabled: False 时跳过精排（直接返回融合排名）。
        """
        self._search = search
        self._reranker = reranker
        self._settings = settings
        self._response_builder = response_builder or ResponseBuilder()
        self._rerank_enabled = rerank_enabled

    # ------------------------------------------------------------------ #
    # 懒初始化
    # ------------------------------------------------------------------ #
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

    def _get_settings(self) -> Any:
        if self._settings is None:
            from core.settings import load_settings

            self._settings = load_settings()
        return self._settings

    # ------------------------------------------------------------------ #
    # Tool 入口
    # ------------------------------------------------------------------ #
    def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """执行检索并返回 MCP tool result（content + structuredContent）。

        Raises:
            TypeError: arguments 非 dict。
            ValueError: query 缺失/为空、top_k/collection 非法。
        """
        query, top_k, collection = self._validate_arguments(arguments)
        trace = TraceContext(metadata={"query": query, "collection": collection})
        filters = {"collection": collection} if collection else None

        results = self._get_search().search(query, top_k=top_k, filters=filters, trace=trace)
        if self._rerank_enabled and results:
            results = self._get_reranker().rerank(query, results, trace=trace)
        return self._response_builder.build(results, query)

    @staticmethod
    def _validate_arguments(arguments: dict[str, Any]) -> tuple[str, int | None, str | None]:
        if not isinstance(arguments, dict):
            raise TypeError(f"arguments must be an object, got {type(arguments).__name__}")
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolInvalidParams("'query' must be a non-empty string")
        top_k = arguments.get("top_k")
        if top_k is not None and (isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1):
            raise ToolInvalidParams(f"'top_k' must be a positive integer, got {top_k!r}")
        collection = arguments.get("collection")
        if collection is not None and (not isinstance(collection, str) or not collection.strip()):
            raise ToolInvalidParams(f"'collection' must be a non-empty string, got {collection!r}")
        return query, top_k, collection


def create_query_knowledge_hub_tool(
    search: Any | None = None,
    reranker: Any | None = None,
    **kwargs: Any,
) -> QueryKnowledgeHubTool:
    """构建 tool 实例（测试注入便捷入口）。"""
    return QueryKnowledgeHubTool(search=search, reranker=reranker, **kwargs)


def create_query_knowledge_hub_definition(
    search: Any | None = None,
    reranker: Any | None = None,
    **kwargs: Any,
) -> ToolDefinition:
    """构建可注册到 ``ProtocolHandler`` 的 ``ToolDefinition``（懒初始化栈）。"""
    return ToolDefinition(
        name=TOOL_NAME,
        description=TOOL_DESCRIPTION,
        input_schema=TOOL_INPUT_SCHEMA,
        handler=create_query_knowledge_hub_tool(search=search, reranker=reranker, **kwargs),
    )
