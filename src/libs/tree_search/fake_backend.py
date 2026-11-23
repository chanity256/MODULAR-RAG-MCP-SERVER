"""Fake 树检索后端 — 阶段 PI3。

确定性实现，供下游模块离线单测注入（零网络零子进程）。输出由构造
参数完全决定：同输入重复调用结果逐字节一致，对齐本项目「离线确定性
测试」纪律。默认证据为 MD 形（page_* 为 None、line_* 给行号），覆盖
D8 双形态之一；PDF 形证据经构造参数注入。
"""

from __future__ import annotations

import sys
from typing import Any, Sequence

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from libs.tree_search.base_tree_search import (
    TreeEvidence,
    TreeSearchRequest,
    TreeSearchResponse,
    BaseTreeSearch,
)
from libs.tree_search.tree_search_factory import TreeSearchFactory

__all__ = ["FakeTreeSearch"]


@TreeSearchFactory.register("fake")
class FakeTreeSearch(BaseTreeSearch):
    """确定性 Fake 后端（测试注入用，不作生产 backend）。"""

    def __init__(
        self,
        tree_config: Any | None = None,
        *,
        evidences: Sequence[TreeEvidence] | None = None,
        llm_call_count: int | None = 2,
        total_llm_tokens: int | None = 1024,
        telemetry: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        """Args:
            tree_config: 工厂统一签名首参（``TreeSearchFactory.create``
                以 ``ctor(config)`` 调用）；fake 忽略配置，输出由构造
                参数完全决定。
            error: 非 None 时 ``search`` 抛出该异常（测试降级路径）。
        """
        self._evidences = (
            list(evidences)
            if evidences is not None
            else [
                TreeEvidence(
                    source_path="fake/doc.md",
                    doc_id="pi-fake",
                    node_id="0000",
                    text="fake tree evidence",
                    score=0.9,
                    section="第一章 > 1.1 背景",
                    page_start=None,
                    page_end=None,
                    line_start=12,
                    line_end=45,
                )
            ]
        )
        self._llm_call_count = llm_call_count
        self._total_llm_tokens = total_llm_tokens
        self._telemetry = dict(telemetry) if telemetry is not None else None
        self._error = error
        self.last_request: TreeSearchRequest | None = None

    @property
    def backend_name(self) -> str:
        return "fake"

    def search(self, request: TreeSearchRequest) -> TreeSearchResponse:
        self.last_request = request
        if self._error is not None:
            raise self._error
        return TreeSearchResponse(
            evidences=list(self._evidences),
            llm_call_count=self._llm_call_count,
            total_llm_tokens=self._total_llm_tokens,
            telemetry=dict(self._telemetry) if self._telemetry is not None else None,
        )
