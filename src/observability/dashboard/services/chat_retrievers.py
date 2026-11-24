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
    RETRIEVAL_LONGDOC,
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
from libs.agentic_search.collection_paths import CollectionPathResolver
from libs.agentic_search.path_guard import PathGuard
from libs.tree_search.base_tree_search import (
    TreeSearchError,
    TreeSearchRequest,
    TreeSearchResponse,
)
from libs.tree_search.converter import tree_evidence_to_retrieval_results
from mcp_server.protocol_handler import ToolInvalidParams
from observability.dashboard.services.query_service import QueryService
from observability.dashboard.services.search_service import _StreamingSearchAdapter

__all__ = [
    "AgenticChatRetriever",
    "HybridChatRetriever",
    "TreeChatRetriever",
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
        if self._mode == "DEEP" and not self._allow_deep():
            raise DeepDisabledError("agentic_search.allow_deep is false")

        arguments: dict[str, Any] = {"query": query, "mode": self._mode}
        if top_k is not None:
            arguments["top_k"] = top_k
        if collection and collection.strip():
            # AC3：collection 非空时透传给工具，由工具按摄取历史推导定界
            # 路径集（AGENTIC_COLLECTION_ISOLATION_SPEC；空定界集由工具按
            # D3 零证据短路）。同步与流式共用同一 arguments。
            arguments["collection"] = collection.strip()

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
            history=getattr(tool, "_history", None),  # AC3：流式工具共享历史句柄
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
    tree_backend: Any | None = None,
) -> dict[str, Any]:
    """按配置装配检索器字典；agentic 两个模式仅在 ``agentic_search.enabled`` 时注册。

    DEEP 在 enabled 时也注册，由 ``allow_deep`` 在调用时门控（关闭时 ``DEEP_DISABLED``）。
    longdoc 仅在 ``tree_search.enabled`` 时注册（PAGEINDEX_TREE_RETRIEVAL_SPEC PI5）；
    ``tree_backend`` 可注入 fake（测试），缺省经 ``TreeSearchFactory`` 按 backend 构建。
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
    if getattr(getattr(settings, "tree_search", None), "enabled", False):
        if tree_backend is None:
            import libs.tree_search  # noqa: F401  # 触发后端注册
            from libs.tree_search.tree_search_factory import TreeSearchFactory

            tree_backend = TreeSearchFactory.create(settings)
        retrievers[RETRIEVAL_LONGDOC] = TreeChatRetriever(tree_backend, settings=settings)
    return retrievers


class TreeChatRetriever:
    """Longdoc（PageIndex sidecar 树检索）适配器（PAGEINDEX_TREE_RETRIEVAL_SPEC PI5）。

    走 AnswerGenerator 路径（``answer=None``）；不声明 ``on_log``（sidecar
    ``/search`` 为同步端点，无进度流）。证据在主 venv 侧仍做 PathGuard 过滤
    与 collection 定界集二次过滤（不信任 sidecar 的过滤结果，D12 / AC2 同构）。

    Args:
        backend: ``BaseTreeSearch``（或具备 ``search(TreeSearchRequest)`` 的 fake）。
        settings: 全局配置（读 ``tree_search`` 的 allowed_roots / denied_substrings）。
        history: 摄取历史句柄（collection 定界用；缺省懒建 ``SQLiteIntegrityChecker``）。
    """

    def __init__(self, backend: Any, *, settings: Any | None = None, history: Any | None = None) -> None:
        self._backend = backend
        self._settings = settings
        self._history = history
        self._path_guard: PathGuard | None = None

    def retrieve(
        self,
        query: str,
        *,
        collection: str | None = None,
        top_k: int | None = None,
        trace: Any | None = None,
    ) -> ChatRetrievalResult:
        scoped: list[str] | None = None
        if collection and collection.strip():
            scoped = self._resolve_collection_paths(collection.strip())
        if scoped is not None and not scoped:
            # D3 先例：定界集为空 fail-closed，不调用 sidecar，直接零证据。
            if trace is not None:
                trace.record_stage(
                    "tree_search",
                    method="scoped_empty",
                    details={
                        "backend": self._backend_name(),
                        "collection": collection,
                        "scoped_paths": 0,
                        "evidences": 0,
                        "filtered_out": 0,
                    },
                )
            return ChatRetrievalResult(metadata={"retrieval_route": RETRIEVAL_LONGDOC, "collection": collection})

        request = TreeSearchRequest(query=query, source_paths=scoped)
        try:
            response = self._backend.search(request)
        except TreeSearchError as exc:
            if trace is not None:
                trace.record_stage(
                    "tree_search",
                    method="error",
                    details={
                        "backend": self._backend_name(),
                        "collection": collection,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
            raise
        return self._to_result(response, collection=collection, scoped=scoped, top_k=top_k, trace=trace)

    # ------------------------------------------------------------------ #
    def _backend_name(self) -> str:
        return str(getattr(self._backend, "backend_name", type(self._backend).__name__))

    def _to_result(
        self,
        response: TreeSearchResponse,
        *,
        collection: str | None,
        scoped: list[str] | None,
        top_k: int | None,
        trace: Any | None,
    ) -> ChatRetrievalResult:
        """PathGuard 过滤 + 定界集二次过滤 → converter → trace（不 finish，D7）。"""
        kept = set(self._get_path_guard().filter_evidence_paths([e.source_path for e in response.evidences]))
        survivors = [e for e in response.evidences if e.source_path in kept]
        if scoped is not None:
            scoped_set = set(scoped)
            survivors = [e for e in survivors if e.source_path in scoped_set]
        filtered_out = len(response.evidences) - len(survivors)
        results = tree_evidence_to_retrieval_results(
            TreeSearchResponse(
                evidences=survivors,
                llm_call_count=response.llm_call_count,
                total_llm_tokens=response.total_llm_tokens,
                telemetry=response.telemetry,
            ),
            top_k=top_k,
        )
        telemetry = response.telemetry if isinstance(response.telemetry, dict) else {}
        if trace is not None:
            trace.record_stage(
                "tree_search",
                method=self._backend_name(),
                details={
                    "backend": self._backend_name(),
                    "collection": collection,
                    "scoped_paths": len(scoped) if scoped is not None else None,
                    "evidences": len(survivors),
                    "filtered_out": filtered_out,
                    "unindexed": telemetry.get("unindexed"),
                    "llm_call_count": response.llm_call_count,
                    "total_llm_tokens": response.total_llm_tokens,
                    "elapsed_ms": response.elapsed_ms,
                    "tree_telemetry": response.telemetry,
                },
            )
        metadata = {
            "retrieval_route": RETRIEVAL_LONGDOC,
            "collection": collection,
            "filtered_out": filtered_out,
            "llm_call_count": response.llm_call_count,
            "total_llm_tokens": response.total_llm_tokens,
            "elapsed_ms": response.elapsed_ms,
        }
        return ChatRetrievalResult(results=results, metadata=metadata)

    def _get_path_guard(self) -> PathGuard:
        if self._path_guard is None:
            config = self._get_config()
            self._path_guard = PathGuard(config.allowed_roots, config.denied_substrings)
        return self._path_guard

    def _get_config(self) -> Any:
        settings = self._settings
        if settings is None:
            from core.settings import load_settings

            settings = load_settings()
        return settings.tree_search

    def _get_history(self) -> Any:
        if self._history is None:
            from libs.loader.file_integrity import SQLiteIntegrityChecker

            self._history = SQLiteIntegrityChecker()
        return self._history

    def _resolve_collection_paths(self, collection: str) -> list[str]:
        """collection → 定界绝对路径集（AC1 组件复用；空集由调用方按 D3 短路）。"""
        resolver = CollectionPathResolver(self._get_history(), self._get_path_guard())
        return resolver.resolve(collection)
