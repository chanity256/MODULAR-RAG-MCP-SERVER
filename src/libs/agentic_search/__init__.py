"""libs.agentic_search — Sirchmunk HTTP sidecar 适配层（SIRCHMUNK_INTEGRATION_SPEC 阶段 S1）。

导入本包即完成后端注册（``fake`` / ``sirchmunk_http``），与
``libs.reranker`` 等包的工厂注册先例一致。
"""

from __future__ import annotations

from libs.agentic_search.base_agentic_search import (
    VALID_MODES,
    AgenticEvidence,
    AgenticSearchBackendError,
    AgenticSearchError,
    AgenticSearchRequest,
    AgenticSearchResponse,
    AgenticSearchResponseError,
    AgenticSearchTimeoutError,
    BaseAgenticSearch,
)
from libs.agentic_search.agentic_search_factory import (
    AgenticSearchBackendRouteError,
    AgenticSearchFactory,
)
from libs.agentic_search.converter import (
    evidence_to_retrieval_results,
    make_agentic_id,
    parse_search_context,
)
from libs.agentic_search.fake_backend import FakeAgenticSearch
from libs.agentic_search.http_backend import SirchmunkHttpBackend
from libs.agentic_search.path_guard import PathGuard, PathGuardError

__all__ = [
    "VALID_MODES",
    "AgenticEvidence",
    "AgenticSearchRequest",
    "AgenticSearchResponse",
    "AgenticSearchError",
    "AgenticSearchTimeoutError",
    "AgenticSearchBackendError",
    "AgenticSearchResponseError",
    "BaseAgenticSearch",
    "AgenticSearchFactory",
    "AgenticSearchBackendRouteError",
    "FakeAgenticSearch",
    "SirchmunkHttpBackend",
    "PathGuard",
    "PathGuardError",
    "parse_search_context",
    "evidence_to_retrieval_results",
    "make_agentic_id",
]
