"""Fake Agentic 检索后端 — 阶段 S1.2。

确定性实现，供 MCP tool / 下游模块离线单测注入（零网络零子进程）。
输出由构造参数完全决定：同输入重复调用结果逐字节一致，对齐本项目
「离线确定性测试」纪律（SIRCHMUNK_INTEGRATION_SPEC 第 5 章）。
"""

from __future__ import annotations

import sys
from typing import Sequence

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from libs.agentic_search.agentic_search_factory import AgenticSearchFactory
from libs.agentic_search.base_agentic_search import (
    AgenticEvidence,
    AgenticSearchRequest,
    AgenticSearchResponse,
    BaseAgenticSearch,
)

__all__ = ["FakeAgenticSearch"]


@AgenticSearchFactory.register("fake")
class FakeAgenticSearch(BaseAgenticSearch):
    """确定性 Fake 后端（测试注入用，不作生产 backend）。"""

    def __init__(
        self,
        *,
        answer: str = "fake agentic answer",
        evidences: Sequence[AgenticEvidence] | None = None,
        source_files: Sequence[str] | None = None,
        llm_call_count: int | None = 2,
        total_llm_tokens: int | None = 1024,
        error: Exception | None = None,
    ) -> None:
        """Args:
            error: 非 None 时 ``search`` 抛出该异常（测试降级路径）。
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
        self._error = error
        self.last_request: AgenticSearchRequest | None = None

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
        )
