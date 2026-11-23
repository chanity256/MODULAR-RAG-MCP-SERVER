"""PI3: TreeSearchRequest / TreeEvidence / TreeSearchResponse 契约单测（离线）。"""

from __future__ import annotations

import pytest

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


class TestTreeSearchRequest:
    def test_defaults_and_to_dict(self):
        request = TreeSearchRequest(query="章节定位")
        assert request.source_paths is None
        assert request.max_nodes is None
        assert request.max_loops is None
        assert request.max_token_budget is None
        assert request.to_dict() == {
            "query": "章节定位",
            "source_paths": None,
            "max_nodes": None,
            "max_loops": None,
            "max_token_budget": None,
        }

    def test_empty_source_paths_is_legal_fail_closed_value(self):
        """空列表是合法取值（sidecar 契约 fail-closed，spec §3.4）。"""
        request = TreeSearchRequest(query="q", source_paths=[])
        assert request.source_paths == []

    @pytest.mark.parametrize(
        "kwargs, fragment",
        [
            ({"query": "  "}, "query"),
            ({"query": ""}, "query"),
            ({"query": 3}, "query"),
            ({"query": "q", "source_paths": "x"}, "source_paths"),
            ({"query": "q", "source_paths": ["", "a"]}, "source_paths"),
            ({"query": "q", "source_paths": [3]}, "source_paths"),
            ({"query": "q", "max_nodes": 0}, "max_nodes"),
            ({"query": "q", "max_nodes": True}, "max_nodes"),
            ({"query": "q", "max_loops": -1}, "max_loops"),
            ({"query": "q", "max_token_budget": "32k"}, "max_token_budget"),
        ],
    )
    def test_invalid_values_raise(self, kwargs, fragment):
        with pytest.raises(ValueError, match=fragment):
            TreeSearchRequest(**kwargs)


class TestTreeEvidence:
    def test_md_shaped_evidence(self):
        evidence = TreeEvidence(
            source_path="/repo/data/documents/a.md",
            doc_id="pi-abc",
            node_id="0007",
            text="章节正文",
            score=0.9,
            section="3 模块化RAG项目 > 3.2 模块划分",
            page_start=None,
            page_end=None,
            line_start=210,
            line_end=342,
        )
        assert evidence.to_dict() == {
            "source_path": "/repo/data/documents/a.md",
            "doc_id": "pi-abc",
            "node_id": "0007",
            "text": "章节正文",
            "score": 0.9,
            "section": "3 模块化RAG项目 > 3.2 模块划分",
            "page_start": None,
            "page_end": None,
            "line_start": 210,
            "line_end": 342,
        }

    def test_minimal_construction(self):
        evidence = TreeEvidence(source_path="a.pdf", doc_id="d", node_id="n", text="")
        assert evidence.score is None
        assert evidence.section is None
        assert evidence.page_start is None
        assert evidence.line_end is None

    @pytest.mark.parametrize(
        "kwargs, fragment",
        [
            ({"source_path": ""}, "source_path"),
            ({"doc_id": ""}, "doc_id"),
            ({"node_id": None}, "node_id"),
            ({"text": 3}, "text"),
            ({"score": "0.9"}, "score"),
            ({"score": True}, "score"),
            ({"section": 1}, "section"),
            ({"page_start": "3"}, "page_start"),
            ({"page_end": True}, "page_end"),
            ({"line_start": 1.5}, "line_start"),
            ({"line_end": False}, "line_end"),
        ],
    )
    def test_invalid_values_raise(self, kwargs, fragment):
        base = {"source_path": "a.md", "doc_id": "d", "node_id": "n", "text": "t"}
        base.update(kwargs)
        with pytest.raises(ValueError, match=fragment):
            TreeEvidence(**base)


class TestTreeSearchResponse:
    def test_defaults_and_to_dict(self):
        evidence = TreeEvidence(source_path="a.md", doc_id="d", node_id="n", text="t")
        response = TreeSearchResponse(
            evidences=[evidence],
            llm_call_count=4,
            total_llm_tokens=8412,
            elapsed_ms=65000.0,
            telemetry={"invalid_output": 0},
        )
        assert response.to_dict() == {
            "evidences": [evidence.to_dict()],
            "llm_call_count": 4,
            "total_llm_tokens": 8412,
            "elapsed_ms": 65000.0,
            "telemetry": {"invalid_output": 0},
        }

    def test_empty_default(self):
        response = TreeSearchResponse()
        assert response.evidences == []
        assert response.telemetry is None

    def test_evidence_type_enforced(self):
        with pytest.raises(ValueError, match="TreeEvidence"):
            TreeSearchResponse(evidences=[{"source_path": "a"}])

    def test_telemetry_type_enforced(self):
        with pytest.raises(ValueError, match="telemetry"):
            TreeSearchResponse(telemetry=["x"])


class TestErrorHierarchy:
    def test_subclassing(self):
        assert issubclass(TreeSearchTimeoutError, TreeSearchError)
        assert issubclass(TreeSearchBackendError, TreeSearchError)
        assert issubclass(TreeSearchResponseError, TreeSearchError)
        assert issubclass(TreeSearchError, Exception)

    def test_base_class_is_abstract(self):
        class _NoSearch(BaseTreeSearch):
            pass

        with pytest.raises(TypeError):
            _NoSearch()  # type: ignore[abstract]
