"""SearchService：Dashboard「在线检索」页的三模式分发服务（S4.1）。

协议无关地复用两条既有检索链路，页面不重写检索逻辑：

- ``normal`` → :class:`~mcp_server.tools.query_knowledge_hub.QueryKnowledgeHubTool`
  （HybridSearch + Rerank + 引用 + trace 持久化）；
- ``fast`` / ``deep`` → :class:`~mcp_server.tools.agentic_search.AgenticSearchTool`
  （sirchmunk sidecar，PathGuard 双层防线 + ``allow_deep`` 门控 +
  sidecar 不可用时友好 ``isError``）。

接口冻结（S4 并行开发前置，见 SIRCHMUNK_INTEGRATION_SPEC 3.3）：

- :class:`SearchOutcome` —— 归一化结果（mode/markdown/citations/
  agentic_meta/is_error/error/elapsed_ms）；
- :class:`SearchService.availability(settings)` —— 各模式可用状态与原因；
- :meth:`SearchService.run` —— ``run(mode, query, top_k, collection)``。

SX5 追加 :meth:`SearchService.run_stream`：Deep 模式经后端
``search_stream`` 回调进度日志，后端不支持流式时回退 :meth:`run`。

工具实例懒构建并缓存在实例上（页面把 SearchService 存入
``st.session_state``，避免每次点击都重建 HybridSearch）。不改动检索核心，
不新增依赖；默认配置（``agentic_search.enabled: false``）下仅 Normal 可用。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from mcp_server.protocol_handler import ToolInvalidParams

__all__ = ["ModeAvailability", "SearchOutcome", "SearchService", "VALID_MODES"]

# normal=主链路 HybridSearch；fast/deep=sirchmunk AgenticSearchTool。
VALID_MODES: tuple[str, ...] = ("normal", "fast", "deep")

_FAST_REASON = (
    "Fast 模式需要在 config/settings.yaml 开启 agentic_search.enabled，"
    "并启动 sidecar（scripts/sirchmunk/）"
)
_DEEP_EXTRA_REASON = "Deep 模式还需要在 config/settings.yaml 开启 agentic_search.allow_deep"


@dataclass(frozen=True)
class ModeAvailability:
    """单个检索模式的可用状态（不可用时 ``reason`` 为悬停提示文案）。"""

    available: bool
    reason: str | None = None


@dataclass(frozen=True)
class SearchOutcome:
    """一次在线检索的归一化结果（成功 / isError / 参数非法 / 空结果统一形态）。

    Attributes:
        mode: 触发模式（``normal`` | ``fast`` | ``deep``）。
        markdown: 答案 Markdown（isError 时为原始错误文案）。
        citations: 结构化引用列表（source/page/chunk_id/text/score）。
        agentic_meta: Fast/Deep 模式附加元数据（模式/Token/LLM 调用次数/
            耗时/证据文件等）；Normal 模式恒为 ``None``。
        is_error: 是否失败（工具 ``isError`` 或参数非法/门控拒绝）。
        error: 失败原因（成功时 ``None``）。
        elapsed_ms: 调用方实测耗时（perf_counter 口径）。
    """

    mode: str
    markdown: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    agentic_meta: dict[str, Any] | None = None
    is_error: bool = False
    error: str | None = None
    elapsed_ms: float = 0.0


class SearchService:
    """三模式在线检索服务：门控 + 分发 + 结果归一化。"""

    def __init__(
        self,
        settings: Any | None = None,
        *,
        query_tool: Any | None = None,
        agentic_tool: Any | None = None,
    ) -> None:
        """Args:
            settings: 全局配置（缺省 ``load_settings()``；测试注入 fake）。
            query_tool: 注入 :class:`QueryKnowledgeHubTool` 实例（测试用）。
            agentic_tool: 注入 :class:`AgenticSearchTool` 实例（测试用）。
        """
        self._settings = settings
        self._query_tool = query_tool
        self._agentic_tool = agentic_tool

    # ------------------------------------------------------------------ #
    # 可用性矩阵（enabled × allow_deep 四种组合）
    # ------------------------------------------------------------------ #
    def availability(self, settings: Any | None = None) -> dict[str, ModeAvailability]:
        """返回 normal/fast/deep 的可用状态与不可用原因。

        Args:
            settings: 显式传入配置；缺省用实例 settings（再缺省
                ``load_settings()``）。只读 ``settings.agentic_search``。

        Returns:
            ``{"normal": ModeAvailability, "fast": ..., "deep": ...}``。
            Normal 恒可用；Fast 需 ``enabled=true``；Deep 需
            ``enabled`` 与 ``allow_deep`` 同时为 true。
        """
        config = getattr(settings if settings is not None else self._get_settings(), "agentic_search")
        enabled = bool(getattr(config, "enabled", False))
        allow_deep = bool(getattr(config, "allow_deep", False))
        return {
            "normal": ModeAvailability(available=True),
            "fast": ModeAvailability(available=enabled, reason=None if enabled else _FAST_REASON),
            "deep": ModeAvailability(
                available=enabled and allow_deep,
                reason=None if (enabled and allow_deep) else (_FAST_REASON if not enabled else _DEEP_EXTRA_REASON),
            ),
        }

    # ------------------------------------------------------------------ #
    # 分发入口
    # ------------------------------------------------------------------ #
    def run(
        self,
        mode: str,
        query: str,
        top_k: int | None = None,
        collection: str | None = None,
    ) -> SearchOutcome:
        """按模式执行检索并归一化为 :class:`SearchOutcome`。

        门控不可用 / 参数非法 / 工具 ``isError`` / 意外异常一律归一化为
        ``is_error=True`` 的结果，页面层据此 ``st.error``，不抛异常。

        Args:
            mode: ``normal`` | ``fast`` | ``deep``。
            query: 查询文本（非空）。
            top_k: 返回条数上限（正整数；``None`` 用工具缺省）。
            collection: 仅 Normal 模式生效的 collection 过滤。

        Returns:
            归一化的 :class:`SearchOutcome`。
        """
        mode = (mode or "").strip().lower()
        if mode not in VALID_MODES:
            return self._error_outcome(mode or "normal", f"未知检索模式：{mode!r}（可选：{list(VALID_MODES)}）")

        config = getattr(self._get_settings(), "agentic_search", None)
        if mode in ("fast", "deep") and config is not None:
            availability = self.availability()
            if not availability[mode].available:
                return self._error_outcome(mode, availability[mode].reason or "该模式当前不可用")

        started = time.perf_counter()
        try:
            if mode == "normal":
                result = self._run_normal(query, top_k=top_k, collection=collection)
            else:
                result = self._run_agentic(query, mode=mode, top_k=top_k)
        except ToolInvalidParams as exc:
            return self._error_outcome(mode, f"参数非法：{exc}", started)
        except (TypeError, ValueError) as exc:
            return self._error_outcome(mode, str(exc), started)
        except Exception as exc:  # noqa: BLE001 - 页面稳定性优先，友好兜底
            return self._error_outcome(mode, f"{type(exc).__name__}: {exc}", started)

        return self._parse(mode, result, (time.perf_counter() - started) * 1000.0)

    def run_stream(
        self,
        mode: str,
        query: str,
        top_k: int | None = None,
        collection: str | None = None,
        *,
        on_event: Callable[[str, str, float | None], None],
    ) -> SearchOutcome:
        """带进度回调的检索（SX5）：仅 Deep 走后端 ``search_stream``。

        其余模式、工具/后端不支持流式（``NotImplementedError``）时回退
        :meth:`run`，结果形态与 :meth:`run` 完全一致。流式请求同样经
        ``AgenticSearchTool`` 的参数校验、PathGuard 双层防线、trace 与
        isError 降级——只把后端调用换成 ``search_stream``。

        Args:
            on_event: ``(level, message, timestamp)`` 进度回调。
        """
        normalized = (mode or "").strip().lower()
        if normalized != "deep":
            return self.run(mode, query, top_k=top_k, collection=collection)

        config = getattr(self._get_settings(), "agentic_search", None)
        if config is not None:
            availability = self.availability()
            if not availability["deep"].available:
                return self._error_outcome("deep", availability["deep"].reason or "该模式当前不可用")

        started = time.perf_counter()
        try:
            stream_tool = self._build_stream_tool(on_event)
            if stream_tool is None:
                return self.run(mode, query, top_k=top_k, collection=collection)
            arguments: dict[str, Any] = {"query": query, "mode": "DEEP"}
            if top_k is not None:
                arguments["top_k"] = top_k
            result = stream_tool(arguments)
        except NotImplementedError:
            return self.run(mode, query, top_k=top_k, collection=collection)
        except ToolInvalidParams as exc:
            return self._error_outcome("deep", f"参数非法：{exc}", started)
        except (TypeError, ValueError) as exc:
            return self._error_outcome("deep", str(exc), started)
        except Exception as exc:  # noqa: BLE001 - 页面稳定性优先，友好兜底
            return self._error_outcome("deep", f"{type(exc).__name__}: {exc}", started)

        return self._parse("deep", result, (time.perf_counter() - started) * 1000.0)

    # ------------------------------------------------------------------ #
    # 内部：分发与解析
    # ------------------------------------------------------------------ #
    def _run_normal(self, query: str, *, top_k: int | None, collection: str | None) -> dict[str, Any]:
        arguments: dict[str, Any] = {"query": query}
        if top_k is not None:
            arguments["top_k"] = top_k
        if collection:
            arguments["collection"] = collection
        return self._get_query_tool()(arguments)

    def _run_agentic(self, query: str, *, mode: str, top_k: int | None) -> dict[str, Any]:
        arguments: dict[str, Any] = {"query": query, "mode": "FAST" if mode == "fast" else "DEEP"}
        if top_k is not None:
            arguments["top_k"] = top_k
        return self._get_agentic_tool()(arguments)

    def _parse(self, mode: str, result: Any, elapsed_ms: float) -> SearchOutcome:
        if not isinstance(result, dict):
            return self._error_outcome(mode, f"工具返回形态非法：{type(result).__name__}", elapsed_ms)
        structured = result.get("structuredContent") or {}
        markdown = str(structured.get("answer") or self._content_text(result) or "")
        is_error = bool(result.get("isError"))
        return SearchOutcome(
            mode=mode,
            markdown=markdown,
            citations=list(structured.get("citations") or []),
            agentic_meta=structured.get("agentic"),
            is_error=is_error,
            error=markdown if is_error else None,
            elapsed_ms=elapsed_ms,
        )

    @staticmethod
    def _content_text(result: dict[str, Any]) -> str:
        content = result.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                return str(first.get("text") or "")
            return str(first)
        return ""

    @staticmethod
    def _error_outcome(mode: str, message: str, started: float | None = None) -> SearchOutcome:
        elapsed = (time.perf_counter() - started) * 1000.0 if started is not None else 0.0
        return SearchOutcome(mode=mode, markdown=message, is_error=True, error=message, elapsed_ms=elapsed)

    # ------------------------------------------------------------------ #
    # 懒初始化（会话内缓存：实例持有工具，页面持有服务）
    # ------------------------------------------------------------------ #
    def _get_settings(self) -> Any:
        if self._settings is None:
            from core.settings import load_settings

            self._settings = load_settings()
        return self._settings

    def _get_query_tool(self) -> Any:
        if self._query_tool is None:
            self._query_tool = self._build_query_tool()
        return self._query_tool

    def _get_agentic_tool(self) -> Any:
        if self._agentic_tool is None:
            self._agentic_tool = self._build_agentic_tool()
        return self._agentic_tool

    def _build_query_tool(self) -> Any:
        from mcp_server.tools.query_knowledge_hub import QueryKnowledgeHubTool

        return QueryKnowledgeHubTool(settings=self._get_settings())

    def _build_agentic_tool(self) -> Any:
        from mcp_server.tools.agentic_search import AgenticSearchTool

        return AgenticSearchTool(settings=self._get_settings())

    def _build_stream_tool(self, on_event: Callable[[str, str, float | None], None]) -> Any | None:
        """构建一次性流式工具：复用会话工具的后端/PathGuard/settings。

        MCP tool 本身保持同步不动：这里另起一个 ``AgenticSearchTool``，把
        后端包成 ``search = backend.search_stream(request, on_event)`` 的
        适配器，tool 的校验/防线/trace 逻辑原样复用。注入的非
        ``AgenticSearchTool`` 工具（测试 fake）无从取后端，返回 None 走同步。
        """
        from mcp_server.tools.agentic_search import AgenticSearchTool

        tool = self._get_agentic_tool()
        if not isinstance(tool, AgenticSearchTool):
            return None
        backend = tool._get_search()
        if not callable(getattr(backend, "search_stream", None)):
            return None
        return AgenticSearchTool(
            search=_StreamingSearchAdapter(backend, on_event),
            settings=tool._get_settings(),
            path_guard=tool._get_path_guard(),
        )


class _StreamingSearchAdapter:
    """把 ``search_stream`` 伪装成 ``search``，供同步 tool 原样调用。"""

    def __init__(self, backend: Any, on_event: Callable[[str, str, float | None], None]) -> None:
        self._backend = backend
        self._on_event = on_event

    @property
    def backend_name(self) -> str:
        return getattr(self._backend, "backend_name", "unknown")

    def search(self, request: Any) -> Any:
        return self._backend.search_stream(request, self._on_event)
