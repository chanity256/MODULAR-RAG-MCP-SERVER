"""上传、摄取提交与文档删除（WC4.2）。

路由只做协议适配：文件名/大小校验、冲突检测、把摄取编排交给 ``IngestionService``
并作为后台任务运行；删除直接委托 ``DataService``（跨存储协调删除）。

摄取提交只接受 ``POST /uploads`` 返回的文件名（在上传目录内解析），不接受任意
服务器路径，避免持有 token 的客户端借此读取服务器上的任意文件。
"""

from __future__ import annotations

import dataclasses
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from api.deps import get_container, get_data_service, ServiceContainer
from api.errors import ApiError
from api.routes.tasks import get_task_manager
from api.schemas.common import ErrorResponse
from api.schemas.tasks import (
    DeleteDocumentResponse,
    IngestionRequest,
    TaskModel,
    TaskSubmitted,
    UploadedFile,
    UploadResponse,
)
from api.tasks import ACTIVE_STATUSES, TaskContext, TaskFailure, TaskManager
from observability.dashboard.services.data_service import DataService
from observability.dashboard.services.ingestion_service import IngestionService, sanitize_filename

__all__ = ["delete_router", "get_ingestion_service", "router", "summarize_ingestion"]

router = APIRouter(tags=["ingestion"])
delete_router = APIRouter(prefix="/documents", tags=["documents"])

INGESTION_KIND = "ingestion"
_READ_CHUNK = 1024 * 1024
# check-then-submit 的冲突检测需要串行化，否则并发提交同一文件会同时通过检查。
_submit_lock = threading.Lock()


def get_ingestion_service(container: ServiceContainer = Depends(get_container)) -> IngestionService:
    return container.ingestion


def _validate_upload_name(raw: str | None) -> str:
    name = (raw or "").strip()
    if not name or "/" in name or "\\" in name or name in (".", "..") or "\x00" in name:
        raise ApiError(400, f"Invalid file name: {raw!r}", code="invalid_filename")
    return name


async def _read_limited(upload: UploadFile, limit: int, name: str) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await upload.read(_READ_CHUNK)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise ApiError(
                413,
                f"File {name!r} exceeds the {limit // (1024 * 1024)} MB upload limit",
                details={"max_upload_mb": limit // (1024 * 1024)},
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/uploads",
    status_code=201,
    response_model=UploadResponse,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
    summary="Upload one or more files (multipart field name: files)",
)
async def upload_files(
    request: Request,
    files: list[UploadFile] = File(description="Files to store in the upload directory"),
    ingestion: IngestionService = Depends(get_ingestion_service),
) -> UploadResponse:
    limit = int(request.app.state.api_settings.max_upload_mb) * 1024 * 1024
    payloads: list[tuple[str, bytes]] = []
    for upload in files:
        name = _validate_upload_name(upload.filename)
        payloads.append((name, await _read_limited(upload, limit, name)))
    await run_in_threadpool(ingestion.save_uploads, payloads)
    return UploadResponse(files=[UploadedFile(name=name, size=len(data)) for name, data in payloads])


def summarize_ingestion(summary: Any) -> dict[str, Any]:
    """把 ``IngestionSummary``（或同形 dict/对象）规整为可 JSON 化的结果摘要。"""
    if dataclasses.is_dataclass(summary) and not isinstance(summary, type):
        data = dataclasses.asdict(summary)
    elif isinstance(summary, dict):
        data = dict(summary)
    else:
        data = {key: getattr(summary, key) for key in ("files", "chunks", "images", "failed", "errors") if hasattr(summary, key)}
    errors = data.get("errors") or []
    data["errors"] = [
        {"path": str(item[0]), "message": str(item[1])} if isinstance(item, (tuple, list)) and len(item) == 2 else item
        for item in errors
    ]
    return data


@router.post(
    "/ingestions",
    status_code=202,
    response_model=TaskSubmitted,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    summary="Submit an ingestion task for previously uploaded files",
)
def submit_ingestion(
    body: IngestionRequest,
    ingestion: IngestionService = Depends(get_ingestion_service),
    manager: TaskManager = Depends(get_task_manager),
) -> TaskSubmitted:
    names: list[str] = []
    for raw in body.files:
        name = _validate_upload_name(raw)
        if sanitize_filename(name) != name:
            raise ApiError(400, f"Invalid file name: {raw!r}", code="invalid_filename")
        if name not in names:
            names.append(name)
    paths: list[Path] = []
    for name in names:
        path = ingestion.upload_dir / name
        if not path.is_file():
            raise ApiError(404, f"Uploaded file {name!r} not found; upload it first")
        paths.append(path)

    params = {"files": names, "collection": body.collection, "force": body.force}

    def run(ctx: TaskContext) -> dict[str, Any]:
        summary = ingestion.ingest(paths, collection=body.collection, force=body.force, on_progress=ctx.progress)
        result = summarize_ingestion(summary)
        files, failed = int(result.get("files") or 0), int(result.get("failed") or 0)
        if files > 0 and failed >= files:
            raise TaskFailure(f"All {files} file(s) failed to ingest", result=result)
        return result

    with _submit_lock:
        active, _ = manager.list(kind=INGESTION_KIND, statuses=ACTIVE_STATUSES, limit=200)
        for task in active:
            other = task.detail.get("params") or {}
            overlap = set(other.get("files") or []) & set(names)
            if other.get("collection") == body.collection and overlap:
                raise ApiError(
                    409,
                    "An ingestion task for the same file(s) and collection is already active",
                    details={"task_id": task.task_id, "files": sorted(overlap)},
                )
        record = manager.submit(INGESTION_KIND, run, detail={"params": params})
    return TaskSubmitted(task_id=record.task_id, status=record.status, task=TaskModel(**record.to_dict()))


@delete_router.delete(
    "/{document_id:path}",
    response_model=DeleteDocumentResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Delete a document (Chroma + BM25 + images + ingestion history)",
)
def delete_document(
    document_id: str,
    collection: str = Query(min_length=1, description="Collection that owns the document"),
    data: DataService = Depends(get_data_service),
) -> DeleteDocumentResponse:
    """``document_id`` 即 ``source_path``（与 ``GET /documents/{id}`` 一致，客户端需 URL 编码）。"""
    try:
        result = data.delete_document(document_id, collection)
    except ValueError as exc:
        raise ApiError(422, str(exc)) from exc
    payload = result.to_dict()
    nothing_removed = not payload.get("integrity_removed") and not any(
        payload.get(key) for key in ("chroma_deleted", "bm25_deleted", "images_deleted")
    )
    if nothing_removed:
        raise ApiError(404, f"Document {document_id!r} not found in collection {collection!r}")
    return DeleteDocumentResponse(**payload)
