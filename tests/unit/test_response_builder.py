"""E3: ResponseBuilder / CitationGenerator 单元测试（离线，零依赖）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from core.response import (
    EMPTY_RESULT_MESSAGE,
    Citation,
    CitationGenerator,
    ResponseBuilder,
)
from core.types import RetrievalResult


def _result(chunk_id: str = "c1", score: float = 0.9, text: str = "body", **metadata) -> RetrievalResult:
    return RetrievalResult(chunk_id=chunk_id, score=score, text=text, metadata=dict(metadata))


# --------------------------------------------------------------------- #
# CitationGenerator
# --------------------------------------------------------------------- #
class TestCitationGenerator:
    def test_generates_one_based_citations_in_order(self):
        citations = CitationGenerator().generate(
            [_result("a", 0.9, "first", source_path="a.pdf", page=1),
             _result("b", 0.8, "second", source_path="b.pdf", page=2)]
        )
        assert [c.id for c in citations] == [1, 2]
        assert citations[0].source == "a.pdf"
        assert citations[0].page == 1
        assert citations[1].chunk_id == "b"

    def test_source_fallback_and_missing_page(self):
        citation = CitationGenerator().generate([_result("x", 0.5, "t")])[0]
        assert citation.source == "unknown"
        assert citation.page is None

    def test_page_non_numeric_becomes_none(self):
        citation = CitationGenerator().generate([_result("x", 0.5, "t", page="5a")])[0]
        assert citation.page is None

    def test_score_rounded_to_4_digits(self):
        citation = CitationGenerator().generate([_result("x", 0.12345678, "t")])[0]
        assert citation.score == 0.1235

    def test_empty_input_returns_empty_list(self):
        assert CitationGenerator().generate([]) == []

    def test_non_list_input_raises_type_error(self):
        with pytest.raises(TypeError):
            CitationGenerator().generate("nope")

    def test_non_result_element_raises_type_error(self):
        with pytest.raises(TypeError):
            CitationGenerator().generate([{"chunk_id": "a"}])

    def test_citation_to_dict_fields(self):
        citation = Citation(id=1, source="s.pdf", page=3, chunk_id="c", text="t", score=0.5)
        assert citation.to_dict() == {
            "id": 1, "source": "s.pdf", "page": 3, "chunk_id": "c", "text": "t", "score": 0.5,
        }


# --------------------------------------------------------------------- #
# ResponseBuilder
# --------------------------------------------------------------------- #
class TestResponseBuilder:
    def test_content_zero_is_markdown_with_citation_markers(self):
        response = ResponseBuilder().build(
            [_result("a", 0.9, "first chunk", source_path="a.pdf", page=1),
             _result("b", 0.8, "second chunk", source_path="b.pdf", page=2)],
            query="bm25 scoring",
        )
        content = response["content"]
        assert content[0]["type"] == "text"
        text = content[0]["text"]
        assert "[1]" in text and "[2]" in text
        assert "a.pdf" in text and "b.pdf" in text

    def test_structured_content_citation_fields(self):
        response = ResponseBuilder().build(
            [_result("a", 0.91234, "text body", source_path="a.pdf", page=7)], query="q"
        )
        citations = response["structuredContent"]["citations"]
        assert len(citations) == 1
        assert citations[0]["source"] == "a.pdf"
        assert citations[0]["page"] == 7
        assert citations[0]["chunk_id"] == "a"
        assert citations[0]["score"] == 0.9123

    def test_empty_results_friendly_message_not_empty_array(self):
        response = ResponseBuilder().build([], query="nothing matches")
        assert response["content"][0]["text"] == EMPTY_RESULT_MESSAGE
        assert "No relevant content" in response["content"][0]["text"]
        assert response["structuredContent"]["citations"] == []

    def test_query_echoed_in_structured_content(self):
        response = ResponseBuilder().build([_result()], query="azure config")
        assert response["structuredContent"]["query"] == "azure config"

    def test_long_text_excerpted_in_markdown_full_text_in_citation(self):
        long_text = "x" * 600
        response = ResponseBuilder().build([_result("a", 0.9, long_text)])
        markdown = response["content"][0]["text"]
        assert "…" in markdown
        assert len(markdown) < len(long_text)
        assert response["structuredContent"]["citations"][0]["text"] == long_text

    def test_injected_citation_generator_is_used(self):
        class StubGenerator:
            def generate(self, results):
                return [Citation(id=1, source="stub.pdf", page=None, chunk_id="s", text="t", score=1.0)]

        response = ResponseBuilder(citation_generator=StubGenerator()).build([_result()])
        assert response["structuredContent"]["citations"][0]["source"] == "stub.pdf"
