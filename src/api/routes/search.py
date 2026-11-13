"""Query Playground：``POST /search``（normal / fast / deep）、``GET /search/capabilities``
与 ``WS /search/ws``（Deep 进度日志流式，WEBUI_SIDECAR_SPEC WS2）。

薄包装既有 :class:`SearchService`（D8：Playground 原样复用，不改其检索链路）。

错误映射：
- 模式被配置门控拒绝（``agentic_search.enabled`` / ``allow_deep``）→ 403 统一错误体，
  code ``AGENTIC_DISABLED`` / ``DEEP_DISABLED``，请求不会触达 sidecar；
- 检索本身失败（sidecar 不可达、工具 ``isError``、参数被工具拒绝）→ 200 + ``is_error: true``，
  这是一次「执行了但失败」的结果，而非协议错误。

WS 协议（对齐 chat WS 先例）：握手先 accept，Origin 校验 / WS token / 门控
（``api.deep_progress_stream``，默认 false，SX5.3 上游线程泄漏）失败都以
``error`` 帧 + 1008 关闭。每条连接一次检索：上行 ``{mode, query, top_k?,
collection?}``，下行 ``search_log`` 进度帧（复用 WS1 的 framer，0.5s 去重）→
``search_result``（形态对齐 ``SearchResponse``，``is_error`` 口径一致）→ ``done``；
检索中的阻塞 HTTP 调用不可中断，``cancel`` 帧与断连等价于丢弃剩余帧。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, WebSocket
from pydantic import ValidationError

from api.deps import get_search_service
from api.errors import ApiError
from api.schemas.chat import CancelFrame
from api.schemas.common import ErrorResponse
from api.schemas.search import (
    ModeStatus,
    SearchCapabilities,
    SearchRequest,
    SearchResponse,
    SearchStartFrame,
)
from api.security import verify_ws_token
from api.routes.chat import (
    CODE_BAD_REQUEST,
    CODE_UNAVAILABLE,
    CLOSE_NORMAL,
    CLOSE_POLICY,
    FIRST_FRAME_TIMEOUT_S,
    WORKER_JOIN_TIMEOUT_S,
    _ConnectionLimiter,
    _close,
    _describe_validation,
    _error_frame,
    _origin_allowed,
    _reject,
    _send,
)
from core.chat.progress import SearchLogFramer
from observability.dashboard.services.search_service import SearchOutcome, SearchService

router = APIRouter(prefix="/search", tags=["search"])
ws_router = APIRouter(tags=["search"])

logger = logging.getLogger(__name__)

# 搜索流式连接上限（chat 用 chat.max_connections 配置；此处无对应配置节，取保守常量）。
_MAX_SEARCH_WS_CONNECTIONS = 4

_DONE = object()


@router.get("/capabilities", response_model=SearchCapabilities, summary="Query Playground mode availability")
def get_capabilities(request: Request, service: SearchService = Depends(get_search_service)) -> SearchCapabilities:
    availability = service.availability()
    modes = [mode for mode in ("normal", "fast", "deep") if availability[mode].available]
    return SearchCapabilities(
        modes=modes,  # type: ignore[arg-type]
        agentic_enabled=availability["fast"].available,
        allow_deep=availability["deep"].available,
        availability={
            mode: ModeStatus(available=status.available, reason=status.reason)
            for mode, status in availability.items()
        },
        progress_stream=bool(getattr(request.app.state.api_settings, "deep_progress_stream", False)),
    )


@router.post(
    "",
    response_model=SearchResponse,
    responses={403: {"model": ErrorResponse, "description": "Mode disabled by agentic_search config"}},
    summary="Run a retrieval in normal / fast / deep mode",
)
def search(body: SearchRequest, service: SearchService = Depends(get_search_service)) -> SearchResponse:
    if body.mode != "normal":
        availability = service.availability()
        if not availability["fast"].available:
            raise ApiError(
                403,
                availability["fast"].reason or "agentic search is disabled",
                code="AGENTIC_DISABLED",
                details={"mode": body.mode},
            )
        if body.mode == "deep" and not availability["deep"].available:
            raise ApiError(
                403,
                availability["deep"].reason or "deep search is disabled",
                code="DEEP_DISABLED",
                details={"mode": body.mode},
            )

    outcome = service.run(body.mode, body.query, top_k=body.top_k, collection=body.collection)
    structured: dict[str, Any] = {"citations": list(outcome.citations)}
    if outcome.agentic_meta is not None:
        structured["agentic"] = outcome.agentic_meta
    return SearchResponse(
        mode=body.mode,
        query=body.query,
        elapsed_ms=outcome.elapsed_ms,
        markdown=outcome.markdown,
        structured=structured,
        is_error=outcome.is_error,
        error=outcome.error if outcome.is_error else None,
    )


# --------------------------------------------------------------------------- #
# WS /search/ws（WS2）：Deep 进度日志流式
# --------------------------------------------------------------------------- #
_limiter_lock = threading.Lock()


def _limiter(app: Any) -> _ConnectionLimiter:
    with _limiter_lock:
        limiter = getattr(app.state, "search_limiter", None)
        if limiter is None:
            limiter = _ConnectionLimiter()
            app.state.search_limiter = limiter
        return limiter


def _decode_frame(message: dict[str, Any]) -> SearchStartFrame | CancelFrame | str:
    """解码一条 ASGI receive 消息；失败返回人类可读的错误文案（str）。"""
    text = message.get("text")
    if text is None:
        return "Invalid frame: only JSON text frames are supported"
    try:
        payload = json.loads(text)
    except ValueError:
        return "Invalid frame: not valid JSON"
    if not isinstance(payload, dict):
        return "Invalid frame: expected a JSON object"
    try:
        if payload.get("type") == "cancel":
            return CancelFrame.model_validate(payload)
        return SearchStartFrame.model_validate(payload)
    except ValidationError as exc:
        return _describe_validation(exc)


def _result_frame(outcome: SearchOutcome, frame: SearchStartFrame) -> dict[str, Any]:
    """``search_result`` 帧：形态对齐 ``POST /search`` 的 ``SearchResponse``。"""
    structured: dict[str, Any] = {"citations": list(outcome.citations)}
    if outcome.agentic_meta is not None:
        structured["agentic"] = outcome.agentic_meta
    response = SearchResponse(
        mode=outcome.mode,  # type: ignore[arg-type]
        query=frame.query,
        elapsed_ms=outcome.elapsed_ms,
        markdown=outcome.markdown,
        structured=structured,
        is_error=outcome.is_error,
        error=outcome.error if outcome.is_error else None,
    )
    return {"type": "search_result", **response.model_dump()}


@ws_router.websocket("/search/ws")
async def search_websocket(websocket: WebSocket) -> None:
    app = websocket.app
    api_settings = app.state.api_settings
    await websocket.accept()

    if not _origin_allowed(websocket, list(api_settings.allowed_origins)):
        await _reject(websocket, CODE_BAD_REQUEST, "Origin not allowed")
        return
    if not verify_ws_token(app.state.api_token, query_params=websocket.query_params, headers=websocket.headers):
        await _reject(websocket, "AUTH", "Missing or invalid API token")
        return
    if not api_settings.deep_progress_stream:
        await _reject(
            websocket,
            CODE_UNAVAILABLE,
            "Deep progress stream is disabled (api.deep_progress_stream=false)",
        )
        return

    limiter = _limiter(app)
    if not limiter.acquire(_MAX_SEARCH_WS_CONNECTIONS):
        await _reject(websocket, "RATE_LIMITED", "Too many concurrent search stream connections")
        return
    try:
        await _serve_search(websocket, app.state.container.search)
    finally:
        limiter.release()
        await _close(websocket)


async def _serve_search(websocket: WebSocket, service: SearchService) -> None:
    try:
        first = await asyncio.wait_for(websocket.receive(), timeout=FIRST_FRAME_TIMEOUT_S)
    except asyncio.TimeoutError:
        await _reject(websocket, CODE_BAD_REQUEST, "Timed out waiting for a search frame")
        return
    if first["type"] == "websocket.disconnect":
        return
    frame = _decode_frame(first)
    if isinstance(frame, str):
        await _reject(websocket, CODE_BAD_REQUEST, frame)
        return
    if isinstance(frame, CancelFrame):
        return  # 还没有进行中的检索，无需取消

    # 与 REST 同款的门控预检（REST 侧是 403）：被门控拒绝的请求不触达 sidecar。
    if frame.mode != "normal":
        availability = service.availability()
        if not availability["fast"].available:
            await _reject(websocket, "AGENTIC_DISABLED", availability["fast"].reason or "agentic search is disabled")
            return
        if frame.mode == "deep" and not availability["deep"].available:
            await _reject(websocket, "DEEP_DISABLED", availability["deep"].reason or "deep search is disabled")
            return

    await _run_search(websocket, service, frame)


async def _run_search(websocket: WebSocket, service: SearchService, frame: SearchStartFrame) -> None:
    loop = asyncio.get_running_loop()
    out: asyncio.Queue[Any] = asyncio.Queue()
    cancel_event = threading.Event()  # on_event 可见：取消后不再产生进度帧
    abort = threading.Event()  # 客户端已走：不再向队列放帧
    task_id = uuid.uuid4().hex

    def put(item: Any) -> None:
        try:
            loop.call_soon_threadsafe(out.put_nowait, item)
        except RuntimeError:  # 事件循环已关闭
            pass

    def worker() -> None:
        framer = SearchLogFramer(task_id)

        def on_event(level: str, message: str, timestamp: float | None = None) -> None:
            if cancel_event.is_set():
                return
            log_frame = framer.to_frame(level, message, timestamp)
            if log_frame is not None:
                put(log_frame)

        try:
            outcome = service.run_stream(
                frame.mode,
                frame.query,
                top_k=frame.top_k,
                collection=frame.collection,
                on_event=on_event,
            )
            if not abort.is_set():
                put(_result_frame(outcome, frame))
        except Exception:  # noqa: BLE001 - run_stream 自身已把失败归一化为 is_error，这里兜底传输层意外
            logger.exception("search worker crashed")
            put(_error_frame("INTERNAL", "Internal server error"))
        finally:
            put(_DONE)

    worker_task = asyncio.ensure_future(asyncio.to_thread(worker))
    get_task: asyncio.Future[Any] = asyncio.ensure_future(out.get())
    recv_task: asyncio.Future[Any] | None = asyncio.ensure_future(websocket.receive())
    client_gone = False
    cancelled = False
    violation = False
    try:
        while True:
            waiting: set[asyncio.Future[Any]] = {get_task}
            if recv_task is not None:
                waiting.add(recv_task)
            done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)

            if recv_task is not None and recv_task in done:
                message = recv_task.result()
                recv_task = None
                if message["type"] == "websocket.disconnect":
                    client_gone = True
                    abort.set()
                    cancel_event.set()
                    break
                decoded = _decode_frame(message)
                if isinstance(decoded, CancelFrame):
                    cancelled = True
                    cancel_event.set()
                    recv_task = asyncio.ensure_future(websocket.receive())
                else:
                    violation = True
                    cancel_event.set()
                    abort.set()
                    reason = decoded if isinstance(decoded, str) else (
                        "Only 'cancel' is accepted while a search is running"
                    )
                    await _send(websocket, _error_frame(CODE_BAD_REQUEST, reason))

            if get_task in done:
                item = get_task.result()
                if item is _DONE:
                    break
                get_task = asyncio.ensure_future(out.get())
                if violation or cancelled:
                    continue
                if not await _send(websocket, item):
                    client_gone = True
                    abort.set()
                    cancel_event.set()
                    break
    finally:
        for task in (get_task, recv_task):
            if task is not None and not task.done():
                task.cancel()
        if client_gone or violation or cancelled:
            try:
                await asyncio.wait_for(asyncio.shield(worker_task), timeout=WORKER_JOIN_TIMEOUT_S)
            except Exception:  # noqa: BLE001 - 含超时；放弃等待
                pass
    if violation:
        await _close(websocket, CLOSE_POLICY)
