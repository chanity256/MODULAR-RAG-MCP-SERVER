"""已摄入文档列表与详情。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.deps import get_data_service
from api.errors import ApiError
from api.schemas.common import ErrorResponse, Page
from api.schemas.documents import ChunkModel, DocumentDetailModel, DocumentSummary
from observability.dashboard.services.data_service import DataService

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=Page[DocumentSummary], summary="List ingested documents")
def list_documents(
    collection: str | None = Query(default=None, description="Filter by collection"),
    keyword: str | None = Query(default=None, description="Case-insensitive match on source_path / title"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    data: DataService = Depends(get_data_service),
) -> Page[DocumentSummary]:
    documents = data.list_documents(collection or None)
    needle = (keyword or "").strip().lower()
    if needle:
        documents = [
            item
            for item in documents
            if needle in item.source_path.lower() or needle in str(item.title or "").lower()
        ]
    page = documents[offset : offset + limit]
    return Page[DocumentSummary](
        items=[DocumentSummary(**item.to_dict()) for item in page],
        total=len(documents),
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{document_id:path}",
    response_model=DocumentDetailModel,
    responses={404: {"model": ErrorResponse}},
    summary="Get a document with its chunks and images",
)
def get_document(
    document_id: str,
    collection: str | None = Query(default=None),
    data: DataService = Depends(get_data_service),
) -> DocumentDetailModel:
    """``document_id`` 即文档的 ``source_path``（可能含 ``/``，客户端需 URL 编码）。"""
    detail = data.get_document_detail(document_id, collection or None)
    if detail is None:
        raise ApiError(404, f"Document {document_id!r} not found")
    payload = detail.to_dict()
    payload["chunks"] = [ChunkModel(**chunk) for chunk in payload.get("chunks") or []]
    return DocumentDetailModel(**payload)
