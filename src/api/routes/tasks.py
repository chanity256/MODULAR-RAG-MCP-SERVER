"""后台任务查询（REST 轮询）与任务事件 WS 通道（WC4.2 / WC4.3）。

WS 路径选择 ``WS /api/v1/tasks/{task_id}/events``（任务 id 在路径里，无需订阅帧）：
连接后先回放当前快照，随后推送变更，任务进入终态后服务端主动关闭。帧沿用
WEBUI_CHAT_SPEC §4.7 的信封::

    {"type": "task_event", "task_id": ..., "status": ..., "detail": {...},
     "timestamp": <updated_at>, "version": <int>}

未知任务：发一帧 ``{"type": "error", "code": "not_found", "message": ...}`` 后以 4404 关闭；
鉴权失败：握手前以 4401 关闭。WS 断线后客户端用 ``GET /api/v1/tasks/{id}`` 取回状态即可恢复。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from api.errors import ApiError
from api.schemas.common import ErrorResponse, Page
from api.schemas.tasks import TaskModel
from api.security import verify_ws_token
from api.tasks import TaskManager, TaskRecord

__all__ = ["get_task_manager", "router", "task_event_frame", "ws_router"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])
ws_router = APIRouter(prefix="/tasks", tags=["tasks"])

_VALID_STATUSES = ("pending", "running", "succeeded", "failed")
WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_NOT_FOUND = 4404


def get_task_manager(request: Request) -> TaskManager:
    manager = getattr(request.app.state, "task_manager", None)
    if manager is None:
        raise ApiError(503, "Task manager is not available")
    return manager


def task_event_frame(record: TaskRecord) -> dict[str, Any]:
    return {
        "type": "task_event",
        "task_id": record.task_id,
        "status": record.status,
        "detail": record.detail,
        "timestamp": record.updated_at,
        "version": record.version,
    }


@router.get("", response_model=Page[TaskModel], summary="List background tasks (newest first)")
def list_tasks(
    status: str | None = Query(default=None, description="pending | running | succeeded | failed"),
    kind: str | None = Query(default=None, description="e.g. ingestion | evaluation"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    manager: TaskManager = Depends(get_task_manager),
) -> Page[TaskModel]:
    if status is not None and status not in _VALID_STATUSES:
        raise ApiError(422, f"status must be one of {', '.join(_VALID_STATUSES)}")
    records, total = manager.list(
        kind=kind or None, statuses=[status] if status else None, limit=limit, offset=offset
    )
    return Page[TaskModel](
        items=[TaskModel(**record.to_dict()) for record in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{task_id}",
    response_model=TaskModel,
    responses={404: {"model": ErrorResponse}},
    summary="Get a task snapshot (also the recovery path after a WS disconnect)",
)
def get_task(task_id: str, manager: TaskManager = Depends(get_task_manager)) -> TaskModel:
    record = manager.get(task_id)
    if record is None:
        raise ApiError(404, f"Task {task_id!r} not found")
    return TaskModel(**record.to_dict())


@ws_router.websocket("/{task_id}/events")
async def task_events(websocket: WebSocket, task_id: str) -> None:
    app = websocket.app
    if not verify_ws_token(
        app.state.api_token,
        query_params=websocket.query_params,
        headers=websocket.headers,
    ):
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return
    manager: TaskManager | None = getattr(app.state, "task_manager", None)
    await websocket.accept()
    if manager is None:
        await websocket.send_json(
            {"type": "error", "code": "unavailable", "message": "Task manager is not available"}
        )
        await websocket.close(code=1011)
        return

    # 先订阅再读快照：二者之间产生的事件不会丢，重复帧靠 version 去重。
    subscription = manager.subscribe(task_id, asyncio.get_running_loop())
    receiver: asyncio.Task[Any] | None = None
    try:
        record = await run_in_threadpool(manager.get, task_id)
        if record is None:
            await websocket.send_json(
                {"type": "error", "code": "not_found", "message": f"Task {task_id!r} not found"}
            )
            await websocket.close(code=WS_CLOSE_NOT_FOUND)
            return
        await websocket.send_json(task_event_frame(record))
        last_version = record.version
        # receive() 仅用于及时感知客户端断开；客户端发来的帧一律忽略。
        receiver = asyncio.ensure_future(websocket.receive())
        while not record.terminal:
            getter = asyncio.ensure_future(subscription.queue.get())
            done, _ = await asyncio.wait({getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                if receiver.result().get("type") == "websocket.disconnect":
                    getter.cancel()
                    return
                receiver = asyncio.ensure_future(websocket.receive())
            if getter not in done:
                getter.cancel()
                continue
            record = getter.result()
            if record.version <= last_version:
                continue
            last_version = record.version
            await websocket.send_json(task_event_frame(record))
        await websocket.close(code=1000)
    except (WebSocketDisconnect, RuntimeError):
        logger.debug("task events client disconnected: %s", task_id)
    finally:
        subscription.close()
        if receiver is not None and not receiver.done():
            receiver.cancel()
