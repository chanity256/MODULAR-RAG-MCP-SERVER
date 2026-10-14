"""ChatRetriever 协议的两个应用层实现（WC7.3，WEBUI_CHAT_SPEC D8 / §4.6）。

- :class:`HybridChatRetriever`：包 :class:`QueryService`，产出 ``RetrievalResult`` 列表，
  由 ChatEngine 交给 ``AnswerGenerator`` 生成有据回答；
- :class:`AgenticChatRetriever`：包 :class:`AgenticSearchTool`（复用其参数校验、PathGuard
  双层防线与 ``agentic_search`` trace 打点），从 MCP tool result 取 citations 与 sidecar 答案，
  agentic 模式不经 AnswerGenerator。

D7 纪律：两者都只向 ChatEngine 注入的 ``trace`` 里 ``record_stage``，**从不** ``finish()``
或落盘；每轮恰好一条 trace 由 ChatEngine 收尾。

这里是应用服务层，可以依赖 ``mcp_server``；``src/core/chat/`` 反向不得依赖本模块。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from core.chat.retriever import (
    RETRIEVAL_AGENTIC_DEEP,
    RETRIEVAL_AGENTIC_FAST,
    RETRIEVAL_HYBRID,
    ChatRetrievalResult,
    DeepDisabledError,
    SearchLogCallback,
)
from libs.agentic_search.base_agentic_search import (
    AgenticSearchBackendError,
    AgenticSearchError,
    AgenticSearchResponseError,
    AgenticSearchTimeoutError,
)
from mcp_server.protocol_handler import ToolInvalidParams
from observability.dashboard.services.query_service import QueryService
from observability.dashboard.services.search_service import _StreamingSearchAdapter

__all__ = [
    "AgenticChatRetriever",
    "HybridChatRetriever",
    "build_chat_retrievers",
]

_AGENTIC_ERRORS: dict[str, type[AgenticSearchError]] = {
    cls.__name__: cls
    for cls in (AgenticSearchTimeoutError, AgenticSearchBackendError, AgenticSearchResponseError)
}


class HybridChatRetriever:
    """Hybrid（Dense + BM25 + RRF + 可选 Rerank）检索适配器。"""

    def __init__(self, query_service: QueryService) -> None:
        self._service = query_service

    def retrieve(
        self,
        query: str,
        *,
        collection: str | None = None,
        top_k: int | None = None,
        trace: Any | None = None,
    ) -> ChatRetrievalResult:
        results = self._service.search(query, collection=collection, top_k=top_k, trace=trace)
        return ChatRetrievalResult(results=list(results))


class AgenticChatRetriever:
    """Agentic（sirchmunk sidecar）检索适配器；``mode`` 为 ``FAST`` 或 ``DEEP``。

    Args:
        tool: ``AgenticSearchTool``（或具备 ``run(arguments, trace=)`` / ``__call__`` 的 fake）。
        mode: ``"FAST"`` / ``"DEEP"``。
        settings: 读 ``agentic_search.allow_deep`` 的配置；缺省取 ``tool._get_settings()``。
        stream_tool_factory: ``on_log -> 工具``；缺省按 ``SearchService._build_stream_tool`` 的
            做法用后端 ``search_stream`` 包一个一次性 ``AgenticSearchTool``。返回 ``None``
            表示不支持流式，回退同步 ``search()``。
    """

    def __init__(
        self,
        tool: Any,
        *,
        mode: str = "FAST",
        settings: Any | None = None,
        stream_tool_factory: Callable[[SearchLogCallback], Any | None] | None = None,
    ) -> None:
        normalized = mode.upper()
        if normalized not in ("FAST", "DEEP"):
            raise ValueError(f"mode must be FAST or DEEP, got {mode!r}")
        self._tool = tool
        self._mode = normalized
        self._settings = settings
        self._stream_tool_factory = stream_tool_factory

    @property
    def mode(self) -> str:
        return self._mode

    def retrieve(
        self,
        query: str,
        *,
        collection: str | None = None,
        top_k: int | None = None,
        trace: Any | None = None,
        on_log: SearchLogCallback | None = None,
    ) -> ChatRetrievalResult:
        # agentic 按路径白名单检索，没有 collection 概念，该参数在此被有意忽略。
        if self._mode == "DEEP" and not self._allow_deep():
            raise DeepDisabledError("agentic_search.allow_deep is false")

        arguments: dict[str, Any] = {"query": query, "mode": self._mode}
        if top_k is not None:
            arguments["top_k"] = top_k

        try:
            result = self._invoke(arguments, trace, on_log)
        except ToolInvalidParams as exc:
            if "allow_deep" in str(exc):
                raise DeepDisabledError(str(exc)) from exc
            raise ValueError(f"agentic search rejected the request: {exc}") from exc

        if not isinstance(result, dict):
            raise AgenticSearchResponseError(f"unexpected tool result type: {type(result).__name__}")
        if result.get("isError"):
            raise self._map_error(result)
        return self._to_result(result)

    # ------------------------------------------------------------------ #
    def _allow_deep(self) -> bool:
        settings = self._settings
        if settings is None:
            getter = getattr(self._tool, "_get_settings", None)
            settings = getter() if callable(getter) else None
        config = getattr(settings, "agentic_search", None)
        return bool(getattr(config, "allow_deep", False))

    def _invoke(
        self, arguments: dict[str, Any], trace: Any | None, on_log: SearchLogCallback | None
    ) -> Any:
        if on_log is not None:
            stream_tool = self._build_stream_tool(on_log)
            if stream_tool is not None:
                try:
                    return self._call(stream_tool, arguments, trace)
                except NotImplementedError:
                    pass  # 后端不支持流式：回退同步 search()
        return self._call(self._tool, arguments, trace)

    @staticmethod
    def _call(tool: Any, arguments: dict[str, Any], trace: Any | None) -> Any:
        run = getattr(tool, "run", None)
        if callable(run):
            return run(arguments, trace=trace)
        return tool(arguments)

    def _build_stream_tool(self, on_log: SearchLogCallback) -> Any | None:
        if self._stream_tool_factory is not None:
            return self._stream_tool_factory(on_log)
        from mcp_server.tools.agentic_search import AgenticSearchTool

        tool = self._tool
        if not isinstance(tool, AgenticSearchTool):
            return None
        backend = tool._get_search()
        if not callable(getattr(backend, "search_stream", None)):
            return None
        return AgenticSearchTool(
            search=_StreamingSearchAdapter(backend, on_log),
            settings=tool._get_settings(),
            path_guard=tool._get_path_guard(),
        )

    @staticmethod
    def _map_error(result: Mapping[str, Any]) -> AgenticSearchError:
        """还原 ``AgenticSearchTool`` 吞掉的异常类型，使 ChatEngine 的分类/重试语义生效。"""
        structured = result.get("structuredContent") or {}
        content = result.get("content") or []
        text = ""
        if isinstance(content, list) and content and isinstance(content[0], dict):
            text = str(content[0].get("text") or "")
        cls = _AGENTIC_ERRORS.get(str(structured.get("error_type")), AgenticSearchError)
        return cls(text or str(structured.get("error_type") or "agentic search failed"))

    @staticmethod
    def _to_result(result: Mapping[str, Any]) -> ChatRetrievalResult:
        structured = result.get("structuredContent") or {}
        agentic = structured.get("agentic") or {}
        citations = [dict(c) for c in (structured.get("citations") or []) if isinstance(c, Mapping)]
        answer = agentic.get("agentic_answer")
        if not (isinstance(answer, str) and answer.strip()):
            # sidecar 未给答案但有可引用证据时，用 ResponseBuilder 的 Markdown 作为透传答案。
            fallback = structured.get("answer")
            answer = fallback if citations and isinstance(fallback, str) and fallback.strip() else None
        metadata = {
            key: agentic[key]
            for key in ("mode", "llm_call_count", "total_llm_tokens", "confidence", "elapsed_ms")
            if key in agentic
        }
        return ChatRetrievalResult(results=[], citations=citations, answer=answer, metadata=metadata)


def build_chat_retrievers(
    settings: Any,
    *,
    query_service: QueryService | None = None,
    agentic_tool: Any | None = None,
) -> dict[str, Any]:
    """按配置装配检索器字典；agentic 两个模式仅在 ``agentic_search.enabled`` 时注册。

    DEEP 在 enabled 时也注册，由 ``allow_deep`` 在调用时门控（关闭时 ``DEEP_DISABLED``）。
    """
    retrievers: dict[str, Any] = {
        RETRIEVAL_HYBRID: HybridChatRetriever(query_service or QueryService(settings)),
    }
    if getattr(getattr(settings, "agentic_search", None), "enabled", False):
        if agentic_tool is None:
            from mcp_server.tools.agentic_search import AgenticSearchTool

            agentic_tool = AgenticSearchTool(settings=settings)
        retrievers[RETRIEVAL_AGENTIC_FAST] = AgenticChatRetriever(agentic_tool, mode="FAST", settings=settings)
        retrievers[RETRIEVAL_AGENTIC_DEEP] = AgenticChatRetriever(agentic_tool, mode="DEEP", settings=settings)
    return retrievers
