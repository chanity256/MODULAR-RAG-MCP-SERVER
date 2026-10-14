"""图片索引与图片内容。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response

from api.deps import get_data_service
from api.errors import ApiError
from api.schemas.common import ErrorResponse
from api.schemas.images import ImageInfo
from observability.dashboard.services.data_service import DataService

router = APIRouter(prefix="/images", tags=["images"])


@router.get("", response_model=list[ImageInfo], summary="List image index records")
def list_images(
    collection: str | None = Query(default=None),
    doc_hash: str | None = Query(default=None),
    data: DataService = Depends(get_data_service),
) -> list[ImageInfo]:
    return [ImageInfo(**record) for record in data.list_images(collection or None, doc_hash or None)]


@router.get(
    "/{image_id}",
    response_class=Response,
    responses={200: {"content": {"image/*": {}}}, 404: {"model": ErrorResponse}},
    summary="Get image bytes",
)
def get_image(image_id: str, data: DataService = Depends(get_data_service)) -> Response:
    payload = data.image_bytes(image_id)
    if payload is None:
        raise ApiError(404, f"Image {image_id!r} not found")
    content, mime = payload
    return Response(content=content, media_type=mime)
