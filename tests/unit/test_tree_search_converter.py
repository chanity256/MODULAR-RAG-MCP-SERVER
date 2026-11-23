"""PI3: converter 解析与 TreeEvidence → RetrievalResult 映射单测（离线）。"""

from __future__ import annotations

import pytest

from libs.tree_search.base_tree_search import (
    TreeEvidence,
    TreeSearchResponse,
    TreeSearchResponseError,
)
from libs.tree_search.converter import (
    make_tree_chunk_id,
    parse_search_data,
    tree_evidence_to_retrieval_results,
)


class TestParseSearchData:
    def test_full_contract(self):
        response = parse_search_data(
            {
                "evidences": [
                    {
                        "source_path": "/a.md",
                        "doc_id": "pi-1",
                        "node_id": "0002",
                        "text": "正文",
                        "score": 1.0,
                        "section": "A > B",
                        "page_start": None,
                        "page_end": None,
                        "line_start": 3,
                        "line_end": 9,
                    }
                ],
                "llm_call_count": 2,
                "total_llm_tokens": 512,
                "telemetry": {"invalid_output": 0},
            }
        )
        assert response.evidences[0].line_start == 3
        assert response.llm_call_count == 2
        assert response.total_llm_tokens == 512
        assert response.telemetry == {"invalid_output": 0}

    def test_float_ints_and_missing_optionals(self):
        response = parse_search_data(
            {
                "evidences": [
                    {
                        "source_path": "/a.pdf",
                        "doc_id": "d",
                        "node_id": "n",
                        "text": "t",
                        "score": 0.5,
                        "page_start": 2.0,
                        "page_end": 3,
                        "line_start": "x",
                        "section": None,
                    }
                ]
            }
        )
        evidence = response.evidences[0]
        assert evidence.page_start == 2
        assert evidence.line_start is None
        assert evidence.section is None
        assert response.llm_call_count is None
        assert response.total_llm_tokens is None
        assert response.telemetry is None

    @pytest.mark.parametrize(
        "data",
        [
            None,
            "str",
            42,
            {},
            {"evidences": None},
            {"evidences": "x"},
            {"telemetry": ["x"]},
        ],
    )
    def test_malformed_data_raises(self, data):
        with pytest.raises(TreeSearchResponseError):
            parse_search_data(data)

    def test_malformed_single_evidence_skipped(self):
        """单条畸形降级跳过，不阻塞整体响应（对齐 agentic converter 先例）。"""
        response = parse_search_data(
            {
                "evidences": [
                    "not-a-dict",
                    {"source_path": "", "doc_id": "d", "node_id": "n", "text": "t"},
                    {"source_path": "a.md", "doc_id": "d", "node_id": "n"},
                    {"source_path": "a.md", "doc_id": "d", "node_id": "n", "text": 3},
                    {"source_path": "a.md", "doc_id": "d", "node_id": "n", "text": "ok"},
                ]
            }
        )
        assert [e.text for e in response.evidences] == ["ok"]

    def test_empty_evidences_list_is_valid(self):
        response = parse_search_data({"evidences": []})
        assert response.evidences == []


class TestChunkId:
    def test_chunk_id_rule(self):
        evidence = TreeEvidence(source_path="a.md", doc_id="pi-abc", node_id="0007", text="t")
        assert make_tree_chunk_id(evidence) == "pageindex:pi-abc:0007"


class TestToRetrievalResults:
    def _response(self, **overrides) -> TreeSearchResponse:
        md = TreeEvidence(
            source_path="/a.md",
            doc_id="pi-1",
            node_id="0007",
            text="MD 正文",
            score=1.0,
            section="3 模块化RAG项目",
            page_start=None,
            page_end=None,
            line_start=210,
            line_end=342,
        )
        pdf = TreeEvidence(
            source_path="/a.pdf",
            doc_id="pi-2",
            node_id="0012",
            text="PDF 正文",
            score=0.8,
            section="Report",
            page_start=14,
            page_end=16,
        )
        return TreeSearchResponse(evidences=[md, pdf], **overrides)

    def test_md_evidence_metadata(self):
        result = tree_evidence_to_retrieval_results(self._response())[0]
        assert result.chunk_id == "pageindex:pi-1:0007"
        assert result.score == 1.0
        assert result.text == "MD 正文"
        assert result.metadata == {
            "retrieval_route": "longdoc",
            "source_path": "/a.md",
            "page": None,
            "page_end": None,
            "section": "3 模块化RAG项目",
            "line_start": 210,
            "line_end": 342,
            "node_id": "0007",
        }

    def test_pdf_evidence_metadata(self):
        result = tree_evidence_to_retrieval_results(self._response())[1]
        assert result.metadata["page"] == 14
        assert result.metadata["page_end"] == 16
        assert result.metadata["line_start"] is None
        assert result.metadata["line_end"] is None

    def test_score_fallback_to_zero(self):
        response = TreeSearchResponse(
            evidences=[TreeEvidence(source_path="a.md", doc_id="d", node_id="n", text="t")]
        )
        result = tree_evidence_to_retrieval_results(response)[0]
        assert result.score == 0.0

    def test_top_k_truncates(self):
        results = tree_evidence_to_retrieval_results(self._response(), top_k=1)
        assert len(results) == 1
        assert results[0].chunk_id == "pageindex:pi-1:0007"

    def test_top_k_validation(self):
        with pytest.raises(ValueError, match="top_k"):
            tree_evidence_to_retrieval_results(self._response(), top_k=0)
