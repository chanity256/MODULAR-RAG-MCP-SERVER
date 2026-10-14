"""系统总览：组件配置卡片 + 资产统计 + 最近一次 trace。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from api.deps import get_config_service, get_data_service, get_trace_service
from api.schemas.collections import CollectionStatsModel
from api.schemas.overview import ComponentCardModel, OverviewResponse, OverviewTotals
from api.schemas.traces import TraceSummary
from observability.dashboard.services.config_service import ConfigService
from observability.dashboard.services.data_service import DataService
from observability.dashboard.services.trace_service import TraceService

router = APIRouter(tags=["overview"])
logger = logging.getLogger(__name__)


@router.get("/overview", response_model=OverviewResponse, summary="System overview")
def get_overview(
    config: ConfigService = Depends(get_config_service),
    data: DataService = Depends(get_data_service),
    traces: TraceService = Depends(get_trace_service),
) -> OverviewResponse:
    warnings: list[str] = []

    cards = [
        ComponentCardModel(
            name=card.name,
            provider=card.provider,
            model=card.model,
            headline=card.headline(),
            extras={k: v for k, v in (card.extras or {}).items() if v is not None},
        )
        for card in config.component_cards()
    ]

    # 与 Streamlit 总览一致：统计读取失败只告警，页面其余部分照常展示。
    try:
        stats = [CollectionStatsModel(**item.to_dict()) for item in data.get_collection_stats()]
    except Exception:
        logger.warning("collection stats unavailable", exc_info=True)
        warnings.append("collection stats unavailable")
        stats = []

    def _latest(trace_type: str) -> TraceSummary | None:
        record = traces.latest(trace_type)
        return TraceSummary.from_record(record) if record else None

    return OverviewResponse(
        components=cards,
        totals=OverviewTotals(
            collections=len(stats),
            documents=sum(s.document_count for s in stats),
            chunks=sum(s.chunk_count for s in stats),
            images=sum(s.image_count for s in stats),
        ),
        collections=stats,
        latest_ingestion=_latest("ingestion"),
        latest_query=_latest("query"),
        warnings=warnings,
    )
