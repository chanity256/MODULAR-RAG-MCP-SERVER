"""Fake Agentic 检索后端 — 阶段 S1.2。

确定性实现，供 MCP tool / 下游模块离线单测注入（零网络零子进程）。
输出由构造参数完全决定：同输入重复调用结果逐字节一致，对齐本项目
「离线确定性测试」纪律（SIRCHMUNK_INTEGRATION_SPEC 第 5 章）。
"""

from __future__ import annotations

import sys
from typing import Any, Sequence

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from libs.agentic_search.agentic_search_factory import AgenticSearchFactory
from libs.agentic_search.base_agentic_search import (
    AgenticEvidence,
    AgenticLogCallback,
    AgenticSearchRequest,
    AgenticSearchResponse,
    BaseAgenticSearch,
)

__all__ = ["FakeAgenticSearch", "DEFAULT_STREAM_EVENTS"]

#: ``search_stream`` 缺省回放的罐头进度日志 ``(level, message, timestamp)``。
DEFAULT_STREAM_EVENTS: tuple[tuple[str, str, float | None], ...] = (
    ("info", "fake: analyzing query", 1.0),
    ("info", "fake: searching files", 2.0),
    ("info", "fake: synthesizing answer", 3.0),
)


@AgenticSearchFactory.register("fake")
class FakeAgenticSearch(BaseAgenticSearch):
    """确定性 Fake 后端（测试注入用，不作生产 backend）。"""

    def __init__(
        self,
        agentic_config: Any | None = None,
        *,
        answer: str = "fake agentic answer",
        evidences: Sequence[AgenticEvidence] | None = None,
        source_files: Sequence[str] | None = None,
        llm_call_count: int | None = 2,
        total_llm_tokens: int | None = 1024,
        telemetry: dict[str, Any] | None = None,
        error: Exception | None = None,
        stream_events: Sequence[tuple[str, str, float | None]] | None = None,
        supports_stream: bool = True,
    ) -> None:
        """Args:
            agentic_config: 工厂统一签名首参（``AgenticSearchFactory.create``
                以 ``ctor(config)`` 调用）；fake 忽略配置，输出由构造参数
                完全决定。
            error: 非 None 时 ``search`` 抛出该异常（测试降级路径）；
                ``search_stream`` 先回放日志再抛出。
            stream_events: ``search_stream`` 回放的进度日志（缺省
                :data:`DEFAULT_STREAM_EVENTS`）。
            supports_stream: False 时 ``search_stream`` 走基类默认实现
                （抛 ``NotImplementedError``），测试回退同步路径。
        """
        self._answer = answer
        self._evidences = (
            list(evidences)
            if evidences is not None
            else [
                AgenticEvidence(
                    source_path="fake/doc.md",
                    snippet="fake evidence snippet",
                    score=0.9,
                    char_start=0,
                    char_end=22,
                )
            ]
        )
        self._source_files = list(source_files) if source_files is not None else ["fake/doc.md"]
        self._llm_call_count = llm_call_count
        self._total_llm_tokens = total_llm_tokens
        self._telemetry = dict(telemetry) if telemetry is not None else None
        self._error = error
        self._stream_events = list(stream_events) if stream_events is not None else list(DEFAULT_STREAM_EVENTS)
        self._supports_stream = supports_stream
        self.last_request: AgenticSearchRequest | None = None
        self.stream_calls = 0

    @property
    def backend_name(self) -> str:
        return "fake"

    def search(self, request: AgenticSearchRequest) -> AgenticSearchResponse:
        self.last_request = request
        if self._error is not None:
            raise self._error
        return AgenticSearchResponse(
            answer=self._answer,
            evidences=list(self._evidences),
            mode=request.mode,
            source_files=list(self._source_files),
            confidence=0.85,
            llm_call_count=self._llm_call_count,
            total_llm_tokens=self._total_llm_tokens,
            telemetry=dict(self._telemetry) if self._telemetry is not None else None,
        )

    def search_stream(
        self,
        request: AgenticSearchRequest,
        on_event: AgenticLogCallback,
    ) -> AgenticSearchResponse:
        if not self._supports_stream:
            return super().search_stream(request, on_event)
        self.stream_calls += 1
        for level, message, timestamp in self._stream_events:
            on_event(level, message, timestamp)
        return self.search(request)
