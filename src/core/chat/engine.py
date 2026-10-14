"""ChatEngine（WC6.4）：一轮对话的编排、错误分类、重试与降级。

``ChatEngine.run_turn`` 返回帧字典的生成器，帧顺序见 WEBUI_CHAT_SPEC §4.7::

    session? -> status(rewriting)? -> status(retrieving) -> search_complete -> sources
             -> status(generating) -> stream* -> result            （成功）
    ... -> error                                                    （失败，终帧）

要点：
- 重试窗口止于首个 ``stream`` 帧：此后异常不重试，直接 ``error`` 终帧并保存已生成部分。
- 每轮恰好一条 ``query`` trace（D7），由本类创建/收尾/落盘；检索器只能向其中 ``record_stage``。
- 取消：调用方 ``close()`` 生成器或置位 ``cancel_event``。已生成内容落库并标
  ``metadata.cancelled: true``；``close()`` 之后无法再 yield，故不发终帧。
- 本模块自成一体：不 import ``mcp_server`` / ``observability``，也不自行装配检索组件。
"""

from __future__ import annotations

import inspect
import logging
import queue
import threading
import time
import urllib.error
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping

import httpx

from core.chat.answer_generator import AnswerGenerator
from core.chat.context import ContextProcessor
from core.chat.history import build_history
from core.chat.retriever import (
    RETRIEVAL_HYBRID,
    RETRIEVAL_MODES,
    ChatRetrievalResult,
    ChatRetriever,
    DeepDisabledError,
)
from core.chat.session_store import SessionNotFoundError, SessionStore
from core.settings import ChatSettings
from libs.agentic_search.base_agentic_search import (
    AgenticSearchBackendError,
    AgenticSearchError,
    AgenticSearchResponseError,
    AgenticSearchTimeoutError,
)
from libs.llm.base_llm import ChatMessage
from libs.llm.openai_llm import LLMRequestError

if TYPE_CHECKING:
    from core.trace.trace_context import TraceContext

__all__ = [
    "ChatEngine",
    "ERROR_AUTH",
    "ERROR_DEEP_DISABLED",
    "ERROR_INTERNAL",
    "ERROR_RATE_LIMITED",
    "ERROR_UPSTREAM",
    "classify_error",
]

logger = logging.getLogger(__name__)

ERROR_AUTH = "AUTH"
ERROR_RATE_LIMITED = "RATE_LIMITED"
ERROR_UPSTREAM = "UPSTREAM"
ERROR_DEEP_DISABLED = "DEEP_DISABLED"
ERROR_INTERNAL = "INTERNAL"

METHOD_LLM_STREAM = "llm_stream"
METHOD_SIDECAR_PASSTHROUGH = "sidecar_passthrough"
METHOD_PURE_CHAT = "pure_chat"

_TITLE_CHARS = 40
# search_log 去重窗口与取消轮询间隔（对齐上游 chat.py:424-456 的 0.5s 去重）。
_LOG_DEDUP_WINDOW_S = 0.5
_LOG_POLL_S = 0.05
_PURE_CHAT_SYSTEM = (
    "You are a helpful assistant. Answer the user's question using the conversation so far. "
    "你是一个乐于助人的助手，请结合对话上下文回答用户问题。"
)

_MESSAGES = {
    ERROR_AUTH: "模型或检索服务认证失败，请检查 API Key / Token 配置。",
    ERROR_RATE_LIMITED: "请求过于频繁，已被服务端限流，请稍后重试。",
    ERROR_UPSTREAM: "上游服务暂时不可用（连接失败、超时或服务端错误），请稍后重试。",
    ERROR_DEEP_DISABLED: "DEEP 检索未启用（agentic_search.allow_deep=false），请改用 hybrid 或 agentic_fast。",
    ERROR_INTERNAL: "服务内部错误，请稍后重试。",
}


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str
    transient: bool


def classify_error(exc: BaseException) -> ErrorInfo:
    """把异常归类为 WS ``error`` 帧的 code + 中文文案，并标明是否值得重试。"""
    if isinstance(exc, DeepDisabledError):
        return ErrorInfo(ERROR_DEEP_DISABLED, _MESSAGES[ERROR_DEEP_DISABLED], False)
    if isinstance(exc, LLMRequestError):
        status = exc.status
        if status in (401, 403):
            return ErrorInfo(ERROR_AUTH, _MESSAGES[ERROR_AUTH], False)
        if status == 429:
            return ErrorInfo(ERROR_RATE_LIMITED, _MESSAGES[ERROR_RATE_LIMITED], True)
        if status is None or status >= 500:
            return ErrorInfo(ERROR_UPSTREAM, _MESSAGES[ERROR_UPSTREAM], True)
        return ErrorInfo(ERROR_INTERNAL, f"模型服务拒绝了请求（HTTP {status}）。", False)
    if isinstance(exc, (AgenticSearchTimeoutError, AgenticSearchBackendError)):
        return ErrorInfo(ERROR_UPSTREAM, _MESSAGES[ERROR_UPSTREAM], True)
    if isinstance(exc, AgenticSearchResponseError):
        return ErrorInfo(ERROR_UPSTREAM, "检索服务返回了无法解析的响应。", False)
    if isinstance(exc, AgenticSearchError):
        return ErrorInfo(ERROR_UPSTREAM, _MESSAGES[ERROR_UPSTREAM], False)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return ErrorInfo(ERROR_AUTH, _MESSAGES[ERROR_AUTH], False)
        if status == 429:
            return ErrorInfo(ERROR_RATE_LIMITED, _MESSAGES[ERROR_RATE_LIMITED], True)
        if status >= 500:
            return ErrorInfo(ERROR_UPSTREAM, _MESSAGES[ERROR_UPSTREAM], True)
    if isinstance(exc, (httpx.TransportError, ConnectionError, TimeoutError, urllib.error.URLError)):
        return ErrorInfo(ERROR_UPSTREAM, _MESSAGES[ERROR_UPSTREAM], True)
    return ErrorInfo(ERROR_INTERNAL, _MESSAGES[ERROR_INTERNAL], False)


def _accepts_on_log(retriever: Any) -> bool:
    """检索器是否声明了可选的 ``on_log`` 形参（或 ``**kwargs``）。"""
    try:
        params = inspect.signature(retriever.retrieve).parameters
    except (TypeError, ValueError, AttributeError):
        return False
    return "on_log" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _default_trace_factory(metadata: dict[str, Any]) -> TraceContext:
    # 延迟导入：``core.trace`` 包初始化会连带加载 ``observability.logger``，
    # 顶层导入会让 ``import core.chat`` 触碰上层（分层守卫禁止）。
    from core.trace.trace_context import TRACE_TYPE_QUERY, TraceContext

    return TraceContext(trace_type=TRACE_TYPE_QUERY, metadata=metadata)


@dataclass
class _TurnState:
    session_id: str
    question: str
    retrieval: str
    trace: TraceContext
    parts: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    grounded: bool = True
    method: str | None = None
    fallback: bool = False
    rewritten_query: str | None = None
    usage: dict[str, Any] | None = None
    chunk_count: int = 0
    cancelled: bool = False
    finalized: bool = False
    terminal: dict[str, Any] | None = None


class ChatEngine:
    """编排一整轮对话。

    Args:
        retrievers: 检索模式（``hybrid`` / ``agentic_fast`` / ``agentic_deep``）-> ``ChatRetriever``。
        llm: 提供 ``chat`` / ``chat_stream`` 的 LLM。
        session_store: 会话存储。
        settings: ``ChatSettings``（缺省全默认）。
        answer_generator / context_processor: 可注入覆盖（缺省按 ``llm`` 与 ``settings`` 构造）。
        trace_factory: ``metadata -> TraceContext``；缺省直接构造 query trace。
        persist_trace: 落盘回调；``None`` 表示不落盘（离线/测试）。失败只记日志，不影响本轮。
        sleep / retry_interval_s: 重试间隔（``sleep`` 可注入，测试免等待）。
    """

    def __init__(
        self,
        *,
        retrievers: Mapping[str, ChatRetriever],
        llm: Any,
        session_store: SessionStore,
        settings: ChatSettings | None = None,
        answer_generator: AnswerGenerator | None = None,
        context_processor: ContextProcessor | None = None,
        trace_factory: Callable[[dict[str, Any]], TraceContext] | None = None,
        persist_trace: Callable[[TraceContext], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        retry_interval_s: float = 2.0,
    ) -> None:
        self._settings = settings or ChatSettings()
        self._retrievers = dict(retrievers)
        unknown = set(self._retrievers) - set(RETRIEVAL_MODES)
        if unknown:
            raise ValueError(f"unknown retrieval modes: {sorted(unknown)}")
        self._llm = llm
        self._store = session_store
        self._answers = answer_generator or AnswerGenerator(
            llm, max_context_chunks=self._settings.max_context_chunks
        )
        self._context = context_processor or ContextProcessor(llm)
        self._trace_factory = trace_factory or _default_trace_factory
        self._persist_trace = persist_trace
        self._sleep = sleep
        self._retry_interval_s = retry_interval_s

    @property
    def available_retrievals(self) -> tuple[str, ...]:
        return tuple(m for m in RETRIEVAL_MODES if m in self._retrievers)

    # ------------------------------------------------------------------ #
    # 入口
    # ------------------------------------------------------------------ #
    def run_turn(
        self,
        message: str,
        *,
        session_id: str | None = None,
        retrieval: str | None = None,
        collection: str | None = None,
        top_k: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[dict[str, Any]]:
        """执行一轮对话，返回帧字典的迭代器（每帧含 ``type``）。

        参数在调用时即校验（而不是首次迭代时），便于 API 层在开始推流前返回协议错误。

        Raises:
            ValueError: ``message`` 为空 / ``retrieval`` 未知或未注册 / ``top_k`` 非正整数。
            SessionNotFoundError: 给定的 ``session_id`` 不存在。
        """
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string")
        mode = retrieval or self._settings.default_retrieval
        if mode not in RETRIEVAL_MODES:
            raise ValueError(f"retrieval must be one of {RETRIEVAL_MODES}, got {mode!r}")
        if mode not in self._retrievers:
            raise ValueError(f"retrieval mode {mode!r} is not available")
        if top_k is not None and (isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1):
            raise ValueError(f"top_k must be a positive integer, got {top_k!r}")
        if session_id is not None and not self._store.session_exists(session_id):
            raise SessionNotFoundError(session_id)
        return self._run(message, session_id, mode, collection, top_k, cancel_event)

    # ------------------------------------------------------------------ #
    # 编排
    # ------------------------------------------------------------------ #
    def _run(
        self,
        message: str,
        session_id: str | None,
        mode: str,
        collection: str | None,
        top_k: int | None,
        cancel_event: threading.Event | None,
    ) -> Iterator[dict[str, Any]]:
        is_new = session_id is None
        if is_new:
            session = self._store.create_session(
                title=message.strip()[:_TITLE_CHARS],
                settings={"retrieval": mode, "collection": collection},
            )
            session_id = session["session_id"]
            yield {"type": "session", "session_id": session_id}

        prior = self._store.get_messages(session_id)
        if not is_new and not prior:
            self._store.update_title(session_id, message.strip()[:_TITLE_CHARS])
        self._store.append_message(session_id, "user", message)

        trace = self._trace_factory(
            {"query": message, "routes": "chat", "retrieval": mode, "session_id": session_id}
        )
        state = _TurnState(session_id=session_id, question=message, retrieval=mode, trace=trace)

        def cancelled() -> bool:
            return cancel_event is not None and cancel_event.is_set()

        try:
            history_msgs, history_info = self._prepare_history(prior, message)
            search_query = yield from self._rewrite(state, message, history_msgs, history_info)

            if cancelled():
                state.cancelled = True
                yield self._finalize(state)
                return

            retrieved: ChatRetrievalResult | None = None
            yield {"type": "status", "stage": "retrieving", "message": "正在检索知识库"}
            try:
                retrieved = yield from self._retrieve(
                    state, self._retrievers[mode], search_query, collection, top_k, mode, cancel_event
                )
            except Exception as exc:  # noqa: BLE001
                info = classify_error(exc)
                if info.code == ERROR_DEEP_DISABLED or not self._settings.fallback_to_chat_only:
                    raise
                logger.warning("chat retrieval failed, falling back to pure chat: %s", exc)
                state.fallback = True
                state.grounded = False
                yield {"type": "search_error", "message": info.message}

            if cancelled():
                state.cancelled = True
                yield self._finalize(state)
                return

            if retrieved is not None:
                passthrough = retrieved.answer is not None and retrieved.answer.strip() != ""
                if passthrough:
                    state.citations = [dict(c) for c in retrieved.citations]
                    state.method = METHOD_SIDECAR_PASSTHROUGH
                else:
                    state.citations = [c.to_dict() for c in self._answers.citations(retrieved.results)]
                    state.method = METHOD_LLM_STREAM
                state.chunk_count = len(state.citations)
                yield {"type": "search_complete", "message": f"检索完成，命中 {state.chunk_count} 条"}
                yield {"type": "sources", "citations": state.citations}
                yield {"type": "status", "stage": "generating", "message": "正在生成回答"}
                if passthrough:
                    state.parts.append(retrieved.answer)
                    yield {"type": "stream", "content": retrieved.answer}
                else:
                    yield from self._stream(
                        state,
                        lambda: self._answers.stream(message, retrieved.results, history=history_msgs),
                        cancel_event,
                    )
            else:
                state.method = METHOD_PURE_CHAT
                yield {"type": "status", "stage": "generating", "message": "正在生成回答"}
                chat_messages = [ChatMessage("system", _PURE_CHAT_SYSTEM), *history_msgs, ChatMessage("user", message)]
                yield from self._stream(state, lambda: self._llm.chat_stream(chat_messages), cancel_event)

            # 空检索短路时 LLM 未被调用，其 last_usage 属于上一轮，不能带入本轮。
            llm_called = state.method == METHOD_PURE_CHAT or (
                state.method == METHOD_LLM_STREAM and state.chunk_count > 0
            )
            if llm_called:
                state.usage = getattr(self._llm, "last_usage", None)
            yield self._finalize(state)
        except GeneratorExit:
            self._finalize(state, cancelled_by_close=True)
            raise
        except Exception as exc:  # noqa: BLE001 - 一律转 error 终帧，不让异常击穿到传输层
            info = classify_error(exc)
            if info.code == ERROR_INTERNAL:
                logger.exception("chat turn failed")
            else:
                logger.warning("chat turn failed (%s): %s", info.code, exc)
            yield self._finalize(state, error=info)

    # ------------------------------------------------------------------ #
    # 阶段实现
    # ------------------------------------------------------------------ #
    def _prepare_history(
        self, prior: list[dict[str, Any]], question: str
    ) -> tuple[list[ChatMessage], dict[str, Any]]:
        """裁剪 + 过滤；返回 (历史, trace 数据)。过滤的 LLM 调用 fail-open。"""
        trimmed = build_history(
            prior,
            max_turns=self._settings.history_max_turns,
            max_tokens=self._settings.history_max_tokens,
        )
        info: dict[str, Any] = {
            "messages_before": len(prior),
            "messages_after_trim": len(trimmed),
            "filter_enabled": self._settings.relevance_filter,
            "filter_called": False,
            "filter_failed": False,
            "turns_dropped": 0,
        }
        history = trimmed
        if self._settings.relevance_filter and trimmed:
            outcome = self._context.filter_history(trimmed, question)
            history = outcome.history
            info.update(
                filter_called=outcome.called,
                filter_failed=outcome.failed,
                turns_dropped=outcome.dropped_turns,
            )
        info["messages_after_filter"] = len(history)
        return history, info

    def _rewrite(
        self,
        state: _TurnState,
        question: str,
        history: list[ChatMessage],
        history_info: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        """记录 chat_history 并（仅 RAG 检索前）执行改写；生成器返回检索用查询。"""
        state.trace.record_stage("chat_history", **history_info)
        query = question
        rewrite_info: dict[str, Any] = {"enabled": self._settings.query_rewrite, "called": False, "rewritten": False}
        if self._settings.query_rewrite and history:
            yield {"type": "status", "stage": "rewriting", "message": "正在改写查询"}
            outcome = self._context.rewrite_query(question, history)
            query = outcome.query
            rewrite_info.update(
                called=outcome.called,
                rewritten=outcome.rewritten,
                failed=outcome.failed,
                rewritten_query=outcome.query if outcome.rewritten else None,
            )
            if outcome.rewritten:
                state.rewritten_query = outcome.query
        state.trace.record_stage("chat_rewrite", **rewrite_info)
        return query

    def _retrieve(
        self,
        state: _TurnState,
        retriever: ChatRetriever,
        query: str,
        collection: str | None,
        top_k: int | None,
        mode: str,
        cancel_event: threading.Event | None,
    ) -> Iterator[dict[str, Any]]:
        attempts = 0
        while True:
            try:
                return (
                    yield from self._call_retriever(
                        state, retriever, query, collection, top_k, mode, cancel_event
                    )
                )
            except Exception as exc:  # noqa: BLE001
                info = classify_error(exc)
                if info.transient and attempts < self._settings.pipeline_max_retries:
                    attempts += 1
                    logger.warning("chat retrieval transient failure (%s), retry %d: %s", info.code, attempts, exc)
                    yield {"type": "status", "stage": "retrying", "message": "检索失败，正在重试"}
                    self._sleep(self._retry_interval_s)
                    continue
                raise

    def _call_retriever(
        self,
        state: _TurnState,
        retriever: ChatRetriever,
        query: str,
        collection: str | None,
        top_k: int | None,
        mode: str,
        cancel_event: threading.Event | None,
    ) -> Iterator[dict[str, Any]]:
        """调用检索器；开启进度流时放到工作线程，并把 sidecar 日志转成 ``search_log`` 帧。

        取消时不再等待检索线程（无法中断阻塞中的 HTTP 调用），返回空结果由调用方的
        取消检查收尾；遗留线程是 daemon，结果被丢弃。
        """
        kwargs: dict[str, Any] = {"collection": collection, "top_k": top_k, "trace": state.trace}
        if not (
            self._settings.agentic_progress_stream
            and mode != RETRIEVAL_HYBRID
            and _accepts_on_log(retriever)
        ):
            return retriever.retrieve(query, **kwargs)

        events: queue.Queue[tuple[Any, ...]] = queue.Queue()
        outcome: dict[str, Any] = {}

        def on_log(level: str, message: str, timestamp: float | None = None) -> None:
            events.put(("log", level, message, timestamp))

        def work() -> None:
            try:
                outcome["value"] = retriever.retrieve(query, on_log=on_log, **kwargs)
            except BaseException as exc:  # noqa: BLE001 - 原样交回引擎线程分类
                outcome["error"] = exc
            finally:
                events.put(("done",))

        threading.Thread(target=work, name="chat-retrieve", daemon=True).start()
        last_seen: dict[tuple[str, str], float] = {}
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return ChatRetrievalResult()
            try:
                item = events.get(timeout=_LOG_POLL_S)
            except queue.Empty:
                continue
            if item[0] == "done":
                break
            _, level, message, timestamp = item
            now = time.monotonic()
            key = (str(level), str(message))
            previous = last_seen.get(key)
            last_seen[key] = now
            if previous is not None and now - previous < _LOG_DEDUP_WINDOW_S:
                continue
            frame: dict[str, Any] = {
                "type": "search_log",
                "task_id": state.trace.trace_id,
                "level": str(level),
                "message": str(message),
            }
            if timestamp is not None:
                frame["timestamp"] = timestamp
            yield frame
        if "error" in outcome:
            raise outcome["error"]
        return outcome["value"]

    def _stream(
        self,
        state: _TurnState,
        make_chunks: Callable[[], Iterator[str]],
        cancel_event: threading.Event | None,
    ) -> Iterator[dict[str, Any]]:
        """流式生成：首个增量之前可重试，之后不重试。"""
        attempts = 0
        while True:
            chunks = iter(make_chunks())
            try:
                first = next(chunks)
            except StopIteration:
                return
            except Exception as exc:  # noqa: BLE001
                info = classify_error(exc)
                if info.transient and attempts < self._settings.pipeline_max_retries:
                    attempts += 1
                    logger.warning("chat generation transient failure (%s), retry %d: %s", info.code, attempts, exc)
                    yield {"type": "status", "stage": "retrying", "message": "生成失败，正在重试"}
                    self._sleep(self._retry_interval_s)
                    continue
                raise
            break

        try:
            pending: str | None = first
            while pending is not None:
                if pending:
                    state.parts.append(pending)
                    yield {"type": "stream", "content": pending}
                if cancel_event is not None and cancel_event.is_set():
                    state.cancelled = True
                    return
                pending = next(chunks, None)
        finally:
            close = getattr(chunks, "close", None)
            if close is not None:
                close()

    # ------------------------------------------------------------------ #
    # 收尾：落库助手消息 + trace（每轮恰好一次）
    # ------------------------------------------------------------------ #
    def _finalize(
        self,
        state: _TurnState,
        *,
        error: ErrorInfo | None = None,
        cancelled_by_close: bool = False,
    ) -> dict[str, Any]:
        if state.finalized:
            return state.terminal or {}
        state.finalized = True

        content = "".join(state.parts)
        if cancelled_by_close:
            state.cancelled = True
        # 流中途失败也算中断：保留已生成部分并标 cancelled。
        if error is not None and content:
            state.cancelled = True

        trace = state.trace
        metadata: dict[str, Any] = {
            "grounded": state.grounded,
            "retrieval": state.retrieval,
            "trace_id": trace.trace_id,
        }
        if state.rewritten_query is not None:
            metadata["rewritten_query"] = state.rewritten_query
        if state.usage is not None:
            metadata["usage"] = state.usage
        if state.cancelled:
            metadata["cancelled"] = True
        if error is not None:
            metadata["error"] = {"code": error.code, "message": error.message}

        message_id: str | None = None
        try:
            saved = self._store.append_message(
                state.session_id, "assistant", content, sources=state.citations, metadata=metadata
            )
            message_id = saved["id"]
        except Exception:  # noqa: BLE001 - 落库失败不应吞掉终帧
            logger.exception("failed to persist assistant message for session %s", state.session_id)

        generation: dict[str, Any] = {
            "method": state.method or "none",
            "grounded": state.grounded,
            "chunks": state.chunk_count,
            "chars": len(content),
            "fallback": state.fallback,
            "cancelled": state.cancelled,
        }
        if state.usage is not None:
            generation["usage"] = state.usage
        if error is not None:
            generation["error_code"] = error.code
        trace.record_stage("chat_generation", **generation)
        # HybridSearch.search 会对注入的 trace 提前 finish() 并固化总耗时（早于改写后的生成阶段）；
        # 复位后重新结算，使 total_elapsed_ms 覆盖整轮。
        trace.total_elapsed_ms = None
        trace.finished_at = None
        trace.finish()
        if self._persist_trace is not None:
            try:
                self._persist_trace(trace)
            except Exception:  # noqa: BLE001
                logger.exception("failed to persist chat trace %s", trace.trace_id)

        if error is not None:
            terminal: dict[str, Any] = {"type": "error", "code": error.code, "message": error.message}
        else:
            terminal = {
                "type": "result",
                "content": content,
                "session_id": state.session_id,
                "message_id": message_id,
                "grounded": state.grounded,
                "trace_id": trace.trace_id,
            }
            if state.rewritten_query is not None:
                terminal["rewritten_query"] = state.rewritten_query
            if state.usage is not None:
                terminal["usage"] = state.usage
            if state.cancelled:
                terminal["cancelled"] = True
        state.terminal = terminal
        return terminal
