"""MCP Tool：agentic_search — 阶段 S1.6。

调用 Sirchmunk HTTP sidecar（无索引 Agentic 检索）的独立 MCP tool
（SIRCHMUNK_INTEGRATION_SPEC 第 3/7 章）。与主检索工具
``query_knowledge_hub`` 互补：面向精确标识符 / 非 PDF 格式 / 多跳
跨文档查询，不进入默认热路径。

设计要点（对齐 ``query_knowledge_hub`` 先例）：
- **懒初始化**：backend / PathGuard 首次调用才构建；server 启动零网络。
- **可注入**：``search``（BaseAgenticSearch）/``path_guard``/``settings``
  均可注入，测试离线零网络。
- **参数校验**：``query`` 必填非空；``mode`` ∈ {FAST, DEEP} 且 DEEP 受
  ``allow_deep`` 配置门控；``paths`` 必须全部过 PathGuard 白名单
  （越权抛 ``ToolInvalidParams`` → -32602，fail-closed 不发请求）。
- **引用口径**：citations 的 ``page`` 恒为 None（sirchmunk 无页码/行号，
  字符偏移入 metadata，spec 4.6）。
- **降级语义**：sidecar 不可用/超时 → ``isError: true`` 友好响应
  （资源性失败，不误报 -32603，对齐 E5 ``get_document_summary`` 语义）。
"""

from __future__ import annotations

import sys
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from core.response.response_builder import ResponseBuilder
from core.trace.persist import persist_trace
from core.trace.trace_context import TRACE_TYPE_QUERY, TraceContext
from libs.agentic_search.base_agentic_search import (
    VALID_MODES,
    AgenticSearchError,
    AgenticSearchRequest,
)
from libs.agentic_search.converter import evidence_to_retrieval_results
from libs.agentic_search.path_guard import PathGuard, PathGuardError
from mcp_server.protocol_handler import ToolDefinition, ToolInvalidParams

__all__ = [
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "TOOL_INPUT_SCHEMA",
    "AgenticSearchTool",
    "create_agentic_search_tool",
    "create_agentic_search_definition",
]

TOOL_NAME = "agentic_search"
TOOL_DESCRIPTION = (
    "Indexless agentic search over live files via a local Sirchmunk sidecar: "
    "grep-based lexical recall (rg/rga) with LLM-driven query analysis and "
    "iterative multi-hop exploration. Best for exact identifiers (error "
    "codes, function names), non-PDF formats (md/docx/xlsx/pptx), and "
    "cross-file multi-hop questions. Citations carry character offsets but "
    "no page numbers. Slower (seconds) and costs LLM tokens per call."
)
TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The standalone (context-complete) user query.",
        },
        "mode": {
            "type": "string",
            "enum": ["FAST", "DEEP"],
            "description": (
                "FAST: greedy keyword search (~seconds). DEEP: iterative "
                "multi-hop exploration (tens of seconds; may be disabled by "
                "server config)."
            ),
        },
        "paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Absolute paths (files or directories) to search. Must be "
                "within the server's allowed roots; defaults to them."
            ),
        },
        "top_k": {
            "type": "integer",
            "minimum": 1,
            "description": "Maximum number of evidence citations to return.",
        },
    },
    "required": ["query"],
}


# Note: 调用前 -32602 / 调用后静默过滤证据、DEEP 门控、isError 降级 — 见 .agents/notes/implemented/feature/2026-10-05-agentic-search-path-guard.md
class AgenticSearchTool:
    """agentic_search 的可调用实现（协议无关，直接吃 arguments dict）。"""

    def __init__(
        self,
        search: Any | None = None,
        *,
        settings: Any | None = None,
        path_guard: PathGuard | None = None,
        response_builder: ResponseBuilder | None = None,
    ) -> None:
        """Args:
            search: 具备 ``search(request) -> AgenticSearchResponse`` 签法的
                后端（缺省首次调用时经 ``AgenticSearchFactory`` 按 settings
                创建）。
            path_guard: 路径白名单守卫（缺省按 settings.agentic_search 构建）。
            settings: 全局配置（缺省 ``load_settings()``）。
        """
        self._search = search
        self._path_guard = path_guard
        self._settings = settings
        self._response_builder = response_builder or ResponseBuilder()

    # ------------------------------------------------------------------ #
    # 懒初始化
    # ------------------------------------------------------------------ #
    def _get_settings(self) -> Any:
        if self._settings is None:
            from core.settings import load_settings

            self._settings = load_settings()
        return self._settings

    def _get_config(self) -> Any:
        return self._get_settings().agentic_search

    def _get_search(self) -> Any:
        if self._search is None:
            import libs.agentic_search  # noqa: F401  # 触发后端注册

            from libs.agentic_search.agentic_search_factory import AgenticSearchFactory

            self._search = AgenticSearchFactory.create(self._get_settings())
        return self._search

    def _get_path_guard(self) -> PathGuard:
        if self._path_guard is None:
            config = self._get_config()
            self._path_guard = PathGuard(
                config.allowed_roots,
                config.denied_substrings,
            )
        return self._path_guard

    # ------------------------------------------------------------------ #
    # Tool 入口
    # ------------------------------------------------------------------ #
    def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """执行 Agentic 检索并返回 MCP tool result。

        Raises:
            TypeError: arguments 非 dict。
            ToolInvalidParams: 参数非法（query/mode/paths/top_k 校验失败，
                含 PathGuard 越权——fail-closed，不发请求）。
        """
        query, mode, paths, top_k = self._validate_arguments(arguments)
        trace = TraceContext(
            trace_type=TRACE_TYPE_QUERY,
            metadata={"query": query, "mode": mode, "routes": "agentic"},
        )
        request = AgenticSearchRequest(
            query=query,
            mode=mode,
            paths=paths,
            top_k_files=self._get_config().top_k_files,
            max_loops=self._get_config().max_loops,
            max_token_budget=self._get_config().max_token_budget,
        )
        try:
            response = self._get_search().search(request)
        except AgenticSearchError as exc:
            self._record_error(trace, exc)
            return self._unavailable_result(exc)
        self._record_trace(trace, response)

        settings = self._settings
        if settings is not None:
            persist_trace(trace, log_file=settings.observability.log_file)
        return self._build_result(response, query, top_k)

    # ------------------------------------------------------------------ #
    # 校验与构建
    # ------------------------------------------------------------------ #
    def _validate_arguments(
        self, arguments: dict[str, Any]
    ) -> tuple[str, str, list[str], int | None]:
        if not isinstance(arguments, dict):
            raise TypeError(f"arguments must be an object, got {type(arguments).__name__}")
        config = self._get_config()

        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolInvalidParams("'query' must be a non-empty string")

        mode = arguments.get("mode")
        if mode is None:
            mode = config.mode
        if not isinstance(mode, str) or mode.upper() not in VALID_MODES:
            raise ToolInvalidParams(f"'mode' must be one of {list(VALID_MODES)}, got {mode!r}")
        mode = mode.upper()
        if mode == "DEEP" and not config.allow_deep:
            raise ToolInvalidParams(
                "'mode: DEEP' is disabled by server config (agentic_search.allow_deep=false)"
            )

        raw_paths = arguments.get("paths")
        if raw_paths is None:
            raw_paths = list(config.allowed_roots)
        if not isinstance(raw_paths, list) or any(not isinstance(p, str) for p in raw_paths):
            raise ToolInvalidParams("'paths' must be a list of strings")
        try:
            paths = self._get_path_guard().validate_paths(raw_paths)
        except PathGuardError as exc:
            raise ToolInvalidParams(f"'paths' rejected by allowlist: {exc}") from exc

        top_k = arguments.get("top_k")
        if top_k is not None and (
            isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1
        ):
            raise ToolInvalidParams(f"'top_k' must be a positive integer, got {top_k!r}")
        return query, mode, paths, top_k

    def _build_result(
        self, response: Any, query: str, top_k: int | None
    ) -> dict[str, Any]:
        guard = self._get_path_guard()
        # 调用后第二层防线：剔除越权来源的证据与文件引用（spec 4.4）。
        # 证据路径可能为相对形式（sidecar 按其 CWD 回显，spec 4.6）——
        # 先归一化为绝对路径再比对，保留路径同时让 citations 可定位。
        normalized_files = guard.filter_evidence_paths(response.source_files)
        kept_evidences = []
        for evidence in response.evidences:
            normalized = guard.filter_evidence_paths([evidence.source_path])
            if normalized:
                evidence.source_path = normalized[0]
                kept_evidences.append(evidence)
        filtered = type(response)(
            answer=response.answer,
            evidences=kept_evidences,
            mode=response.mode,
            source_files=normalized_files,
            confidence=response.confidence,
            llm_call_count=response.llm_call_count,
            total_llm_tokens=response.total_llm_tokens,
            elapsed_ms=response.elapsed_ms,
        )
        results = evidence_to_retrieval_results(filtered, top_k=top_k)
        tool_result = self._response_builder.build(results, query)

        agentic_meta = {
            "mode": response.mode,
            "agentic_answer": response.answer,
            "llm_call_count": response.llm_call_count,
            "total_llm_tokens": response.total_llm_tokens,
            "confidence": response.confidence,
            "elapsed_ms": response.elapsed_ms,
            "evidence_files": filtered.source_files,
        }
        structured = tool_result.setdefault("structuredContent", {})
        structured["agentic"] = agentic_meta
        if not results and response.answer:
            # 无证据但有答案（如 greeting 短路）：给友好提示而非
            # EMPTY_RESULT_MESSAGE（其文案指向摄取，对 agentic 有误导）。
            note = (
                f"{response.answer}\n\n"
                "(Agentic search returned no citable evidence for this query.)"
            )
            tool_result["content"] = [{"type": "text", "text": note}]
            structured["answer"] = note
        return tool_result

    def _record_trace(self, trace: TraceContext, response: Any) -> None:
        trace.record_stage(
            "agentic_search",
            # method 标识具体实现（backend 名），与 F3 阶段打点约定一致。
            method=getattr(self._search, "backend_name", "unknown"),
            details={
                "backend": getattr(self._search, "backend_name", "unknown"),
                "mode": response.mode,
                "evidences": len(response.evidences),
                "evidence_files": len(response.source_files),
                "llm_call_count": response.llm_call_count,
                "total_llm_tokens": response.total_llm_tokens,
                "elapsed_ms": response.elapsed_ms,
            },
        )
        trace.finish()

    def _record_error(self, trace: TraceContext, exc: AgenticSearchError) -> None:
        trace.record_stage(
            "agentic_search",
            method="error",
            details={"error_type": type(exc).__name__, "error": str(exc)},
        )
        trace.finish()

    @staticmethod
    def _unavailable_result(exc: AgenticSearchError) -> dict[str, Any]:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Agentic search is currently unavailable: "
                        f"{type(exc).__name__}: {exc}. "
                        "Check that the sirchmunk sidecar is running "
                        "(see scripts/sirchmunk/ in the repo)."
                    ),
                }
            ],
            "isError": True,
            "structuredContent": {"error_type": type(exc).__name__},
        }


def create_agentic_search_tool(
    search: Any | None = None,
    **kwargs: Any,
) -> AgenticSearchTool:
    """构建 tool 实例（测试注入便捷入口）。"""
    return AgenticSearchTool(search=search, **kwargs)


def create_agentic_search_definition(
    search: Any | None = None,
    **kwargs: Any,
) -> ToolDefinition:
    """构建可注册到 ``ProtocolHandler`` 的 ``ToolDefinition``（懒初始化栈）。"""
    return ToolDefinition(
        name=TOOL_NAME,
        description=TOOL_DESCRIPTION,
        input_schema=TOOL_INPUT_SCHEMA,
        handler=create_agentic_search_tool(search=search, **kwargs),
    )
