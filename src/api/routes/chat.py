"""Chat：``WS /api/v1/chat`` 端点 + 会话 REST + capabilities（WC7.1 / WC7.2 / WC7.3）。

WS 协议（WEBUI_CHAT_SPEC §4.7）沿用「每条消息一个连接」：握手后客户端发一帧
``user_message``，服务端流式回写下行帧直到终帧（``result`` 或 ``error``）后关闭。

- 鉴权：``?token=`` 或 ``Authorization`` 头（``verify_ws_token``）；未配置 token（仅 loopback）放行。
  握手一律先 accept 再以 ``error`` 帧拒绝（auth/超限/校验失败都能被浏览器端看到原因），
  随后以 1008 关闭。
- ``cancel`` 帧在轮进行中到达：置位引擎的 ``cancel_event``，引擎收尾后照常发终帧
  ``result{cancelled: true}``（已生成部分落库）；客户端断开等价于取消，但不再有终帧可发。
- 除 ``cancel`` 外，轮进行中再收到任何帧都是协议违例：终止当前轮、发 ``error{BAD_REQUEST}``、
  丢弃引擎后续帧、以 1008 关闭。
- 引擎是同步的：放到工作线程里跑，经 ``asyncio.Queue`` 把帧桥接回事件循环。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, WebSocket
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect, WebSocketState

from api.chat_wiring import ChatServices
from api.deps import ServiceContainer, get_container
from api.errors import ApiError
from api.schemas.chat import (
    CancelFrame,
    ChatCapabilities,
    ChatSessionDetail,
    ChatSessionSummary,
    UserMessageFrame,
    parse_upstream_frame,
)
from api.schemas.common import ErrorResponse, Page
from api.security import verify_ws_token
from core.chat import SessionNotFoundError

__all__ = ["WS_PATH", "router", "ws_router"]

logger = logging.getLogger(__name__)

WS_PATH = "/api/v1/chat"

# 在 spec §4.7 的 AUTH / RATE_LIMITED / UPSTREAM / DEEP_DISABLED / INTERNAL 之外，
# 协议层自己的拒绝原因：帧/参数非法（含未知会话、未启用的检索模式），以及 chat 被配置关闭。
CODE_BAD_REQUEST = "BAD_REQUEST"
CODE_UNAVAILABLE = "UNAVAILABLE"

CLOSE_NORMAL = 1000
CLOSE_POLICY = 1008

# 握手后迟迟不发首帧的连接会长期占用 max_connections 名额。
FIRST_FRAME_TIMEOUT_S = 30.0
# 客户端已断开时，最多等工作线程收尾这么久（阻塞中的检索无法中断，超时即放弃）。
WORKER_JOIN_TIMEOUT_S = 5.0

router = APIRouter(prefix="/chat", tags=["chat"])
ws_router = APIRouter(tags=["chat"])

_DONE = object()


# --------------------------------------------------------------------------- #
# REST
# --------------------------------------------------------------------------- #
def get_chat_services(container: ServiceContainer = Depends(get_container)) -> ChatServices:
    return container.chat


@router.get("/capabilities", response_model=ChatCapabilities, summary="Chat retrieval capabilities")
def get_capabilities(chat: ChatServices = Depends(get_chat_services)) -> ChatCapabilities:
    return ChatCapabilities(**chat.capabilities())


@router.get("/sessions", response_model=Page[ChatSessionSummary], summary="List chat sessions (recent first)")
def list_sessions(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    chat: ChatServices = Depends(get_chat_services),
) -> Page[ChatSessionSummary]:
    store = chat.session_store
    return Page[ChatSessionSummary](
        items=[ChatSessionSummary(**item) for item in store.list_sessions(limit=limit, offset=offset)],
        total=store.count_sessions(),
        limit=limit,
        offset=offset,
    )


@router.get(
    "/sessions/{session_id}",
    response_model=ChatSessionDetail,
    responses={404: {"model": ErrorResponse}},
    summary="Get one chat session with its messages",
)
def get_session(session_id: str, chat: ChatServices = Depends(get_chat_services)) -> ChatSessionDetail:
    session = chat.session_store.get_session(session_id, with_messages=True)
    if session is None:
        raise ApiError(404, f"Chat session {session_id!r} not found")
    return ChatSessionDetail(**session)


@router.delete(
    "/sessions/{session_id}",
    responses={404: {"model": ErrorResponse}},
    summary="Delete a chat session and its messages",
)
def delete_session(session_id: str, chat: ChatServices = Depends(get_chat_services)) -> dict[str, Any]:
    if not chat.session_store.delete_session(session_id):
        raise ApiError(404, f"Chat session {session_id!r} not found")
    return {"deleted": True, "session_id": session_id}


# --------------------------------------------------------------------------- #
# WS
# --------------------------------------------------------------------------- #
class _ConnectionLimiter:
    def __init__(self) -> None:
        self._active = 0
        self._lock = threading.Lock()

    def acquire(self, limit: int) -> bool:
        with self._lock:
            if self._active >= limit:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)


_limiter_lock = threading.Lock()


def _limiter(app: Any) -> _ConnectionLimiter:
    with _limiter_lock:
        limiter = getattr(app.state, "chat_limiter", None)
        if limiter is None:
            limiter = _ConnectionLimiter()
            app.state.chat_limiter = limiter
        return limiter


def _error_frame(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message}


def _origin_allowed(websocket: WebSocket, allowed_origins: list[str]) -> bool:
    """浏览器发起的 WS 不受 CORS 约束；无 token 的 loopback 部署下必须自行校验 Origin，
    否则任意网页都能连上本机 chat 并读取知识库内容。非浏览器客户端不带 Origin，放行。
    """
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    normalized = origin.rstrip("/")
    if "*" in allowed_origins or normalized in {o.rstrip("/") for o in allowed_origins}:
        return True
    host = websocket.headers.get("host")
    return bool(host) and urlsplit(normalized).netloc == host


def _describe_validation(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:3]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg', 'invalid')}" if loc else str(err.get("msg", "invalid")))
    return "Invalid frame: " + "; ".join(parts)


def _decode_frame(message: dict[str, Any]) -> UserMessageFrame | CancelFrame | str:
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
        return parse_upstream_frame(payload)
    except ValidationError as exc:
        return _describe_validation(exc)


async def _send(websocket: WebSocket, frame: dict[str, Any]) -> bool:
    """发送一帧；连接已不可用时返回 False（不抛）。"""
    if websocket.application_state != WebSocketState.CONNECTED:
        return False
    try:
        await websocket.send_text(json.dumps(frame, ensure_ascii=False, default=str))
        return True
    except (WebSocketDisconnect, RuntimeError):
        return False


async def _close(websocket: WebSocket, code: int = CLOSE_NORMAL) -> None:
    if websocket.application_state == WebSocketState.CONNECTED:
        try:
            await websocket.close(code=code)
        except (WebSocketDisconnect, RuntimeError):
            pass


async def _reject(websocket: WebSocket, code: str, message: str) -> None:
    await _send(websocket, _error_frame(code, message))
    await _close(websocket, CLOSE_POLICY)


@ws_router.websocket("/chat")  # 挂在 build_v1_ws_router 的 /api/v1 前缀下 = WS_PATH
async def chat_websocket(websocket: WebSocket) -> None:
    app = websocket.app
    container: ServiceContainer = app.state.container
    api_settings = app.state.api_settings
    await websocket.accept()

    if not _origin_allowed(websocket, list(api_settings.allowed_origins)):
        await _reject(websocket, CODE_BAD_REQUEST, "Origin not allowed")
        return
    if not verify_ws_token(
        app.state.api_token, query_params=websocket.query_params, headers=websocket.headers
    ):
        await _reject(websocket, "AUTH", "Missing or invalid API token")
        return

    chat = container.chat
    chat_settings = chat.chat_settings
    if not chat_settings.enabled:
        await _reject(websocket, CODE_UNAVAILABLE, "Chat is disabled (chat.enabled=false)")
        return

    limiter = _limiter(app)
    if not limiter.acquire(chat_settings.max_connections):
        await _reject(websocket, "RATE_LIMITED", "Too many concurrent chat connections")
        return
    try:
        await _serve(websocket, chat)
    finally:
        limiter.release()
        await _close(websocket)


async def _serve(websocket: WebSocket, chat: ChatServices) -> None:
    try:
        first = await asyncio.wait_for(websocket.receive(), timeout=FIRST_FRAME_TIMEOUT_S)
    except asyncio.TimeoutError:
        await _reject(websocket, CODE_BAD_REQUEST, "Timed out waiting for a user_message frame")
        return
    if first["type"] == "websocket.disconnect":
        return
    frame = _decode_frame(first)
    if isinstance(frame, str):
        await _reject(websocket, CODE_BAD_REQUEST, frame)
        return
    if isinstance(frame, CancelFrame):
        return  # 还没有进行中的轮，无需取消

    try:
        engine = await asyncio.to_thread(chat.new_engine)
    except Exception:  # noqa: BLE001 - 装配失败（LLM/配置）不向客户端泄露细节
        logger.exception("failed to build chat engine")
        await _reject(websocket, "INTERNAL", "Chat engine is unavailable")
        return
    await _run_turn(websocket, engine, frame)


async def _run_turn(websocket: WebSocket, engine: Any, frame: UserMessageFrame) -> None:
    loop = asyncio.get_running_loop()
    out: asyncio.Queue[Any] = asyncio.Queue()
    cancel_event = threading.Event()  # 引擎可见：收尾并发 result{cancelled}
    abort = threading.Event()  # 客户端已走：工作线程直接 close() 生成器

    def put(item: Any) -> None:
        try:
            loop.call_soon_threadsafe(out.put_nowait, item)
        except RuntimeError:  # 事件循环已关闭
            pass

    def worker() -> None:
        generator = None
        try:
            try:
                generator = engine.run_turn(
                    frame.message,
                    session_id=frame.session_id,
                    retrieval=frame.retrieval,
                    collection=frame.collection,
                    top_k=frame.top_k,
                    cancel_event=cancel_event,
                )
            except SessionNotFoundError as exc:
                missing = exc.args[0] if exc.args else frame.session_id
                put(_error_frame(CODE_BAD_REQUEST, f"Chat session not found: {missing}"))
                return
            except ValueError as exc:
                put(_error_frame(CODE_BAD_REQUEST, str(exc)))
                return
            for item in generator:
                if abort.is_set():
                    break
                put(item)
        except Exception:  # noqa: BLE001 - 引擎自身已把异常转 error 帧，这里兜底传输层意外
            logger.exception("chat worker crashed")
            put(_error_frame("INTERNAL", "Internal server error"))
        finally:
            if generator is not None:
                generator.close()
            put(_DONE)

    worker_task = asyncio.ensure_future(asyncio.to_thread(worker))
    get_task: asyncio.Future[Any] = asyncio.ensure_future(out.get())
    recv_task: asyncio.Future[Any] | None = asyncio.ensure_future(websocket.receive())
    client_gone = False
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
                    cancel_event.set()
                    recv_task = asyncio.ensure_future(websocket.receive())
                else:
                    violation = True
                    cancel_event.set()
                    abort.set()
                    reason = decoded if isinstance(decoded, str) else (
                        "Only 'cancel' is accepted while a turn is running"
                    )
                    await _send(websocket, _error_frame(CODE_BAD_REQUEST, reason))

            if get_task in done:
                item = get_task.result()
                if item is _DONE:
                    break
                get_task = asyncio.ensure_future(out.get())
                if violation:
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
        if client_gone or violation:
            try:
                await asyncio.wait_for(asyncio.shield(worker_task), timeout=WORKER_JOIN_TIMEOUT_S)
            except Exception:  # noqa: BLE001 - 含超时；放弃等待
                pass
    if violation:
        await _close(websocket, CLOSE_POLICY)
