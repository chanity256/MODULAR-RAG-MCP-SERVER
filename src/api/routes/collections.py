"""Collection 统计。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_data_service
from api.errors import ApiError
from api.schemas.collections import CollectionsResponse, CollectionStatsModel
from api.schemas.common import ErrorResponse
from observability.dashboard.services.data_service import DataService

router = APIRouter(prefix="/collections", tags=["collections"])


@router.get("", response_model=CollectionsResponse, summary="List collections with stats")
def list_collections(data: DataService = Depends(get_data_service)) -> CollectionsResponse:
    return CollectionsResponse(items=[CollectionStatsModel(**item.to_dict()) for item in data.get_collection_stats()])


@router.get(
    "/{name}",
    response_model=CollectionStatsModel,
    responses={404: {"model": ErrorResponse}},
    summary="Get one collection's stats",
)
def get_collection(name: str, data: DataService = Depends(get_data_service)) -> CollectionStatsModel:
    # DataService 对不存在的集合会返回全零统计而不是 None，故在全集中查找以区分 404。
    for item in data.get_collection_stats():
        if item.name == name:
            return CollectionStatsModel(**item.to_dict())
    raise ApiError(404, f"Collection {name!r} not found")
