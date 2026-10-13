"""Agentic 检索抽象基类与契约类型 — 阶段 S1.2。

定义 ``BaseAgenticSearch`` 抽象接口与请求/响应契约，供 HTTP 后端
（S1.4）、Fake 后端（测试）与 MCP tool（S1.6）共用。与 ``libs/*``
其它组件同一纪律：上层只依赖抽象，工厂按配置路由（SIRCHMUNK_
INTEGRATION_SPEC 第 3.3 节）。

设计要点：
- **同步接口**：sidecar 方案下后端走同步 httpx，调用方（MCP tool /
  HybridSearch）无需处理 async。
- **契约类型可序列化**：``to_dict`` 输出 JSON 安全结构，供 trace 打点
  与单测断言；``from_dict`` 容忍未知字段（对齐 C1 契约风格）。
- **错误分型**：超时 / 后端不可达 / 响应畸形分型抛出，MCP tool 据此
  返回友好 ``isError``（S1.6），不误报内部错误。
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

__all__ = [
    "AgenticSearchRequest",
    "AgenticEvidence",
    "AgenticSearchResponse",
    "BaseAgenticSearch",
    "AgenticSearchError",
    "AgenticSearchTimeoutError",
    "AgenticSearchBackendError",
    "AgenticSearchResponseError",
    "VALID_MODES",
]

#: sirchmunk 合法检索模式（FILENAME_ONLY 不对外暴露，见 spec 2.2）。
VALID_MODES = ("FAST", "DEEP")


class AgenticSearchError(Exception):
    """Agentic 检索错误基类。"""


class AgenticSearchTimeoutError(AgenticSearchError):
    """sidecar 调用超时。"""


class AgenticSearchBackendError(AgenticSearchError):
    """sidecar 不可达 / 返回错误状态 / 上游报错。"""


class AgenticSearchResponseError(AgenticSearchError):
    """sidecar 响应结构畸形（缺字段/类型不符），无法解析。"""


@dataclass
class AgenticSearchRequest:
    """一次 Agentic 检索请求（由 MCP tool 层组装并过 PathGuard）。"""

    query: str
    mode: str = "FAST"
    # 已通过 PathGuard 校验的绝对路径列表；None 表示用 sidecar 缺省。
    paths: list[str] | None = None
    top_k_files: int | None = None
    max_loops: int | None = None
    max_token_budget: int | None = None
    include: list[str] | None = None
    exclude: list[str] | None = None
    # 请求级覆盖（SX2）；None 表示沿用 AgenticSearchConfig 同名配置。
    max_depth: int | None = None
    enable_dir_scan: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not self.query.strip():
            raise ValueError("query must be a non-empty string")
        mode = self.mode.upper() if isinstance(self.mode, str) else self.mode
        if mode not in VALID_MODES:
            raise ValueError(f"mode must be one of {VALID_MODES}, got {self.mode!r}")
        self.mode = mode
        if self.max_depth is not None and (
            isinstance(self.max_depth, bool) or not isinstance(self.max_depth, int) or self.max_depth < 1
        ):
            raise ValueError(f"max_depth must be a positive integer or None, got {self.max_depth!r}")
        if self.enable_dir_scan is not None and not isinstance(self.enable_dir_scan, bool):
            raise ValueError(f"enable_dir_scan must be a bool or None, got {self.enable_dir_scan!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "mode": self.mode,
            "paths": list(self.paths) if self.paths is not None else None,
            "top_k_files": self.top_k_files,
            "max_loops": self.max_loops,
            "max_token_budget": self.max_token_budget,
            "include": list(self.include) if self.include is not None else None,
            "exclude": list(self.exclude) if self.exclude is not None else None,
            "max_depth": self.max_depth,
            "enable_dir_scan": self.enable_dir_scan,
        }


@dataclass
class AgenticEvidence:
    """单条证据（来自 sidecar ``SearchContext.cluster.evidences``）。

    ``char_start``/``char_end`` 是 **抽取文本上的字符偏移**（不是原文
    页码/行号；sirchmunk 全链路无行号，spec 4.6）。``snippets`` 的
    dict/str 两形态由解析器统一为 ``snippet`` 字符串 + 可选偏移。
    """

    source_path: str
    snippet: str
    score: float | None = None
    char_start: int | None = None
    char_end: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_path, str) or not self.source_path:
            raise ValueError("source_path must be a non-empty string")
        if not isinstance(self.snippet, str):
            raise ValueError("snippet must be a string")
        if self.score is not None and (isinstance(self.score, bool) or not isinstance(self.score, (int, float))):
            raise ValueError("score must be a number or None")
        for name, value in (("char_start", self.char_start), ("char_end", self.char_end)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError(f"{name} must be an integer or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "snippet": self.snippet,
            "score": self.score,
            "char_start": self.char_start,
            "char_end": self.char_end,
        }


@dataclass
class AgenticSearchResponse:
    """Agentic 检索响应契约（sidecar ``SearchContext`` 的投影）。"""

    answer: str
    evidences: list[AgenticEvidence] = field(default_factory=list)
    mode: str = "FAST"
    # 供引用文件列表（cluster.search_results，供 PathGuard 调用后过滤）。
    source_files: list[str] = field(default_factory=list)
    confidence: float | None = None
    llm_call_count: int | None = None
    total_llm_tokens: int | None = None
    elapsed_ms: float | None = None
    # sidecar 顶层 telemetry 的白名单投影（SX1，见 converter._parse_telemetry）；
    # 上游未回传任何 telemetry 键时为 None。
    telemetry: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.answer, str):
            raise ValueError("answer must be a string")
        if self.telemetry is not None and not isinstance(self.telemetry, dict):
            raise ValueError("telemetry must be a dict or None")
        for e in self.evidences:
            if not isinstance(e, AgenticEvidence):
                raise ValueError("evidences must contain AgenticEvidence instances")

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "evidences": [e.to_dict() for e in self.evidences],
            "mode": self.mode,
            "source_files": list(self.source_files),
            "confidence": self.confidence,
            "llm_call_count": self.llm_call_count,
            "total_llm_tokens": self.total_llm_tokens,
            "elapsed_ms": self.elapsed_ms,
            "telemetry": dict(self.telemetry) if self.telemetry is not None else None,
        }


class BaseAgenticSearch(ABC):
    """Agentic 检索抽象基类（同步接口）。"""

    @abstractmethod
    def search(self, request: AgenticSearchRequest) -> AgenticSearchResponse:
        """执行一次 Agentic 检索。

        Raises:
            AgenticSearchTimeoutError: 调用超时。
            AgenticSearchBackendError: sidecar 不可达或上游报错。
            AgenticSearchResponseError: 响应结构畸形。
        """
        raise NotImplementedError
