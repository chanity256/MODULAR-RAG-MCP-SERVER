"""libs.tree_search — PageIndex 树检索适配层（PAGEINDEX_TREE_RETRIEVAL_SPEC 阶段 PI3）。

导入本包即完成后端注册（``fake`` / ``pageindex_http``），与
``libs.agentic_search`` 等包的工厂注册先例一致。主 venv 永不
``import pageindex``（红线）：本包只经 HTTP 调用独立 sidecar 进程。
"""

from __future__ import annotations

from libs.tree_search.base_tree_search import (
    BaseTreeSearch,
    TreeEvidence,
    TreeSearchBackendError,
    TreeSearchError,
    TreeSearchRequest,
    TreeSearchResponse,
    TreeSearchResponseError,
    TreeSearchTimeoutError,
)
from libs.tree_search.converter import (
    make_tree_chunk_id,
    parse_search_data,
    tree_evidence_to_retrieval_results,
)
from libs.tree_search.fake_backend import FakeTreeSearch
from libs.tree_search.http_backend import PageIndexHttpBackend
from libs.tree_search.tree_search_factory import (
    TreeSearchBackendRouteError,
    TreeSearchFactory,
)

__all__ = [
    "BaseTreeSearch",
    "TreeEvidence",
    "TreeSearchRequest",
    "TreeSearchResponse",
    "TreeSearchError",
    "TreeSearchTimeoutError",
    "TreeSearchBackendError",
    "TreeSearchResponseError",
    "TreeSearchFactory",
    "TreeSearchBackendRouteError",
    "FakeTreeSearch",
    "PageIndexHttpBackend",
    "parse_search_data",
    "tree_evidence_to_retrieval_results",
    "make_tree_chunk_id",
]
