"""响应构建器 — 阶段 E3。

把检索结果组装为 MCP Tool 的标准返回格式（DEV_SPEC 3.2.5 / E3 验收）::

    {
        "content": [{"type": "text", "text": "<Markdown，含 [1]/[2] 引用标注>"}],
        "structuredContent": {
            "query": "...",
            "answer": "<同 content[0].text>",
            "citations": [{"id": 1, "source": ..., "page": ..., "chunk_id": ..., "score": ...}, ...]
        }
    }

设计要点：
- **兼容性优先**：``content[0]`` 恒为纯 Markdown 文本，任何 MCP Client
  都能直接展示；结构化数据放 ``structuredContent`` 供高级 Client 解析。
- **无结果友好提示**：空结果返回一段说明文字（提示摄取数据或换个问法），
  而非空数组/空字符串。
- 图片等多模态内容由 E6 ``multimodal_assembler`` 追加到 ``content`` 后续项。
"""

from __future__ import annotations

import sys
from typing import Any, Sequence

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from core.response.citation_generator import CitationGenerator
from core.types import RetrievalResult

__all__ = ["ResponseBuilder", "EMPTY_RESULT_MESSAGE"]

#: 无命中时的友好提示（E3 验收：友好提示而非空数组）。
EMPTY_RESULT_MESSAGE = (
    "No relevant content found in the knowledge hub for this query.\n\n"
    "Suggestions:\n"
    "- Try rephrasing the query or using more specific keywords;\n"
    "- Ingest documents first with `python scripts/ingest.py`;\n"
    "- Check that the target collection exists (see `list_collections`)."
)

#: 每条结果正文摘要的最大字符数（Markdown 中控制篇幅；全文在 citations.text）。
EXCERPT_CHARS = 400


class ResponseBuilder:
    """构建 MCP Tool 响应（Markdown content + structuredContent）。

    Args:
        citation_generator: 引用生成器（可注入替换，缺省
            :class:`CitationGenerator`）。
    """

    def __init__(self, citation_generator: CitationGenerator | None = None) -> None:
        self._citation_generator = citation_generator or CitationGenerator()

    def build(
        self,
        retrieval_results: Sequence[RetrievalResult],
        query: str | None = None,
    ) -> dict[str, Any]:
        """把检索结果组装为 MCP tool result dict。

        Args:
            retrieval_results: 检索/精排后的最终结果（顺序即引用顺序）。
            query: 原始查询（记入 structuredContent，便于 Client 回显）。

        Returns:
            MCP tool result：``content[0]`` 为 Markdown 文本；
            ``structuredContent.citations`` 为结构化引用列表。

        Raises:
            TypeError: 输入类型不符（透传 CitationGenerator 校验）。
        """
        citations = self._citation_generator.generate(retrieval_results)
        if not citations:
            return {
                "content": [{"type": "text", "text": EMPTY_RESULT_MESSAGE}],
                "structuredContent": {
                    "query": query,
                    "answer": EMPTY_RESULT_MESSAGE,
                    "citations": [],
                },
            }
        markdown = self._render_markdown(query, citations)
        return {
            "content": [{"type": "text", "text": markdown}],
            "structuredContent": {
                "query": query,
                "answer": markdown,
                "citations": [c.to_dict() for c in citations],
            },
        }

    @staticmethod
    def _render_markdown(query: str | None, citations: Sequence[Any]) -> str:
        lines: list[str] = []
        if query:
            lines.append(f"Top results for query: **{query}**")
            lines.append("")
        for citation in citations:
            excerpt = citation.text.strip().replace("\n", " ")
            if len(excerpt) > EXCERPT_CHARS:
                excerpt = excerpt[:EXCERPT_CHARS].rstrip() + "…"
            location = f", p.{citation.page}" if citation.page is not None else ""
            lines.append(f"[{citation.id}] {citation.source}{location} (score: {citation.score})")
            lines.append("")
            lines.append(f"> {excerpt}")
            lines.append("")
        return "\n".join(lines).rstrip()
