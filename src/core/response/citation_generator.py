"""Citation 生成器 — 阶段 E3。

把查询链路的 ``RetrievalResult`` 列表转换为统一的结构化引用格式
（DEV_SPEC 3.2.5 ``structuredContent.citations`` 约定）::

    [
        {"id": 1, "source": "xxx.pdf", "page": 5,
         "chunk_id": "...", "text": "原文片段...", "score": 0.92},
        ...
    ]

设计要点：
- 引用序号（``id``，1-based）与 ``ResponseBuilder`` Markdown 中的
  ``[1]``/``[2]`` 标注共用同一顺序（结果列表顺序即引用顺序）。
- ``page`` 缺失时输出 ``None``（保持字段稳定，Client 可据此省略页码）。
- ``score`` 保留原始数值语义，序列化时四舍五入到 4 位小数（足够区分
  排序且避免浮点噪声）。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from core.types import RetrievalResult

__all__ = ["Citation", "CitationGenerator"]


@dataclass
class Citation:
    """单条结构化引用（与 Markdown ``[n]`` 标注一一对应）。

    ``section`` / ``line_start`` / ``line_end`` 为 longdoc 路径新增可选字段
    （PAGEINDEX_TREE_RETRIEVAL_SPEC D8）：``None`` 时 ``to_dict()`` 不输出该键，
    既有 hybrid / agentic 引用的键集合与值逐字节不变。
    """

    id: int
    source: str
    page: int | None
    chunk_id: str
    text: str
    score: float
    section: str | None = None
    line_start: int | None = None
    line_end: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "source": self.source,
            "page": self.page,
            "chunk_id": self.chunk_id,
            "text": self.text,
            "score": self.score,
        }
        if self.section is not None:
            result["section"] = self.section
        if self.line_start is not None:
            result["line_start"] = self.line_start
        if self.line_end is not None:
            result["line_end"] = self.line_end
        return result


class CitationGenerator:
    """从 ``RetrievalResult`` 列表生成结构化引用。"""

    def generate(self, retrieval_results: Sequence[RetrievalResult]) -> list[Citation]:
        """按结果顺序生成 1-based 引用列表。

        Raises:
            TypeError: 输入非列表/元组或元素非 ``RetrievalResult``。
        """
        if not isinstance(retrieval_results, (list, tuple)):
            raise TypeError(
                "retrieval_results must be a list of RetrievalResult, "
                f"got {type(retrieval_results).__name__}"
            )
        citations: list[Citation] = []
        for index, result in enumerate(retrieval_results, start=1):
            if not isinstance(result, RetrievalResult):
                raise TypeError(
                    f"retrieval_results[{index - 1}] must be RetrievalResult, "
                    f"got {type(result).__name__}"
                )
            citations.append(self._to_citation(index, result))
        return citations

    @staticmethod
    def _to_citation(index: int, result: RetrievalResult) -> Citation:
        metadata = result.metadata or {}
        source = metadata.get("source_path") or metadata.get("source") or "unknown"
        page = metadata.get("page")
        if isinstance(page, bool) or not isinstance(page, (int, float)):
            page = None
        # D8：位置可选字段来自 longdoc 证据 metadata；类型不符视为缺失。
        section = metadata.get("section")
        if not isinstance(section, str) or not section:
            section = None
        line_start = metadata.get("line_start")
        if isinstance(line_start, bool) or not isinstance(line_start, int):
            line_start = None
        line_end = metadata.get("line_end")
        if isinstance(line_end, bool) or not isinstance(line_end, int):
            line_end = None
        return Citation(
            id=index,
            source=str(source),
            page=int(page) if page is not None else None,
            chunk_id=result.chunk_id,
            text=result.text,
            score=round(float(result.score), 4),
            section=section,
            line_start=line_start,
            line_end=line_end,
        )
