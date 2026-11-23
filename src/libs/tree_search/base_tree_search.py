"""PageIndex 树检索抽象层 — 阶段 PI3（PAGEINDEX_TREE_RETRIEVAL_SPEC §3.2）。

与 ``libs/agentic_search/`` 同构但独立的可插拔抽象：sidecar 沿文档树选
节点返回证据（章节 / 页 / 行级定位），主 venv 永不 ``import pageindex``
（红线，与 Sirchmunk 同构）。证据形态是「章节路径 + 页码或行号」，与
agentic 的「抽取文本字符偏移」不同，故请求模型与证据契约独立成包。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "TreeSearchError",
    "TreeSearchTimeoutError",
    "TreeSearchBackendError",
    "TreeSearchResponseError",
    "TreeSearchRequest",
    "TreeEvidence",
    "TreeSearchResponse",
    "BaseTreeSearch",
]


class TreeSearchError(Exception):
    """树检索错误基类（ChatEngine classify_error 按子类分型映射）。"""


class TreeSearchTimeoutError(TreeSearchError):
    """sidecar 调用超时。"""


class TreeSearchBackendError(TreeSearchError):
    """sidecar 不可达 / 返回错误状态 / 上游报错。"""


class TreeSearchResponseError(TreeSearchError):
    """sidecar 响应结构畸形（缺字段/类型不符），无法解析。"""


@dataclass
class TreeSearchRequest:
    """一次树检索请求（Chat retriever 层组装并过 PathGuard）。

    ``source_paths``：已通过 PathGuard 校验的绝对路径列表；
    ``None`` 表示用 sidecar 全部已索引文档；**空列表**是合法取值
    （sidecar 契约 fail-closed 直接返回空证据，spec §3.4）。
    ``max_nodes`` / ``max_loops`` / ``max_token_budget``：请求级覆盖，
    ``None`` 表示沿用 ``TreeSearchConfig`` 同名配置。
    """

    query: str
    source_paths: list[str] | None = None
    max_nodes: int | None = None
    max_loops: int | None = None
    max_token_budget: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not self.query.strip():
            raise ValueError("query must be a non-empty string")
        if self.source_paths is not None:
            if not isinstance(self.source_paths, list) or any(
                not isinstance(p, str) or not p.strip() for p in self.source_paths
            ):
                raise ValueError("source_paths must be None or a list of non-empty strings")
        for name in ("max_nodes", "max_loops", "max_token_budget"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                raise ValueError(f"{name} must be a positive integer or None, got {value!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "source_paths": list(self.source_paths) if self.source_paths is not None else None,
            "max_nodes": self.max_nodes,
            "max_loops": self.max_loops,
            "max_token_budget": self.max_token_budget,
        }


@dataclass
class TreeEvidence:
    """单条树检索证据（sidecar ``TreeEvidence`` 的主 venv 投影）。

    ``section`` 为从根到该节点的标题路径（以 ``" > "`` 连接）。位置字段按
    文档格式二选一（D8）：MD 证据 ``page_start`` / ``page_end`` 为 ``None``、
    ``line_start`` / ``line_end`` 为原 MD 行号（1 起计、闭区间）；PDF 证据反之。
    回定位失败时 ``line_*`` 为 ``None``，只保留 ``section``。
    """

    source_path: str
    doc_id: str
    node_id: str
    text: str
    score: float | None = None
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    line_start: int | None = None
    line_end: int | None = None

    def __post_init__(self) -> None:
        for name in ("source_path", "doc_id", "node_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")
        if self.score is not None and (isinstance(self.score, bool) or not isinstance(self.score, (int, float))):
            raise ValueError("score must be a number or None")
        if self.section is not None and not isinstance(self.section, str):
            raise ValueError("section must be a string or None")
        for name in ("page_start", "page_end", "line_start", "line_end"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError(f"{name} must be an integer or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "doc_id": self.doc_id,
            "node_id": self.node_id,
            "text": self.text,
            "score": self.score,
            "section": self.section,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


@dataclass
class TreeSearchResponse:
    """树检索响应契约（sidecar ``/search`` ``data`` 的投影）。"""

    evidences: list[TreeEvidence] = field(default_factory=list)
    llm_call_count: int | None = None
    total_llm_tokens: int | None = None
    elapsed_ms: float | None = None
    telemetry: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        for e in self.evidences:
            if not isinstance(e, TreeEvidence):
                raise ValueError("evidences must contain TreeEvidence instances")
        if self.telemetry is not None and not isinstance(self.telemetry, dict):
            raise ValueError("telemetry must be a dict or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidences": [e.to_dict() for e in self.evidences],
            "llm_call_count": self.llm_call_count,
            "total_llm_tokens": self.total_llm_tokens,
            "elapsed_ms": self.elapsed_ms,
            "telemetry": dict(self.telemetry) if self.telemetry is not None else None,
        }


class BaseTreeSearch(ABC):
    """树检索抽象基类（同步接口）。"""

    @abstractmethod
    def search(self, request: TreeSearchRequest) -> TreeSearchResponse:
        """执行一次树检索。

        Raises:
            TreeSearchTimeoutError: 调用超时。
            TreeSearchBackendError: sidecar 不可达或上游报错。
            TreeSearchResponseError: 响应结构畸形。
        """
        raise NotImplementedError
