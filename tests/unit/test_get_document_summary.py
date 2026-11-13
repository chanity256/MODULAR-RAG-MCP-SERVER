"""E5: get_document_summary tool 单元测试（注入 fake reader，离线）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from mcp_server.protocol_handler import ProtocolHandler, ToolInvalidParams
from mcp_server.tools.get_document_summary import (
    TOOL_NAME,
    GetDocumentSummaryTool,
    create_get_document_summary_definition,
)


def _chunk(chunk_id: str, page: int, collection: str = "knowledge_hub") -> dict:
    return {
        "id": chunk_id,
        "text": f"chunk {chunk_id}",
        "metadata": {
            "source_path": "docs/arch.pdf",
            "title": "Architecture Overview",
            "summary": "System architecture in three layers.",
            "tags": ["arch", "design"],
            "page": page,
            "collection": collection,
        },
        "collection": collection,
    }


class TestGetDocumentSummary:
    def test_returns_structured_summary(self):
        reader = lambda doc_id: [_chunk("c1", 1), _chunk("c2", 5), _chunk("c3", 9)]  # noqa: E731
        response = GetDocumentSummaryTool(reader=reader)({"doc_id": "docs/arch.pdf"})
        summary = response["structuredContent"]
        assert summary["found"] is True
        assert summary["title"] == "Architecture Overview"
        assert summary["summary"] == "System architecture in three layers."
        assert summary["tags"] == ["arch", "design"]
        assert summary["chunk_count"] == 3
        assert summary["pages"] == [1, 9]
        assert summary["collection"] == "knowledge_hub"
        assert "isError" not in response

    def test_content_is_readable_markdown(self):
        reader = lambda doc_id: [_chunk("c1", 2)]  # noqa: E731
        markdown = GetDocumentSummaryTool(reader=reader)({"doc_id": "docs/arch.pdf"})["content"][0]["text"]
        assert "# Architecture Overview" in markdown
        assert "docs/arch.pdf" in markdown
        assert "arch, design" in markdown
        assert "System architecture in three layers." in markdown

    def test_unknown_doc_id_returns_is_error_result(self):
        reader = lambda doc_id: []  # noqa: E731
        response = GetDocumentSummaryTool(reader=reader)({"doc_id": "missing.pdf"})
        assert response["isError"] is True
        assert "not found" in response["content"][0]["text"]
        assert response["structuredContent"] == {"doc_id": "missing.pdf", "found": False}

    @pytest.mark.parametrize("arguments", [{}, {"doc_id": ""}, {"doc_id": "  "}, {"doc_id": 42}])
    def test_invalid_doc_id_raises_invalid_params(self, arguments):
        tool = GetDocumentSummaryTool(reader=lambda doc_id: [])
        with pytest.raises(ToolInvalidParams):
            tool(arguments)

    def test_non_dict_arguments_raises_type_error(self):
        with pytest.raises(TypeError):
            GetDocumentSummaryTool(reader=lambda doc_id: [])("x")

    def test_missing_metadata_fields_fall_back(self):
        reader = lambda doc_id: [{  # noqa: E731
            "id": "c1", "text": "t",
            "metadata": {"source_path": "bare.pdf"},
            "collection": "default",
        }]
        summary = GetDocumentSummaryTool(reader=reader)({"doc_id": "bare.pdf"})["structuredContent"]
        assert summary["title"] == "bare.pdf"  # 无 title 兜底 doc_id
        assert summary["summary"] == ""
        assert summary["tags"] == []
        assert summary["pages"] is None

    def test_string_tags_wrapped_to_list(self):
        records = [_chunk("c1", 1)]
        records[0]["metadata"]["tags"] = "single"
        reader = lambda doc_id: records  # noqa: E731
        summary = GetDocumentSummaryTool(reader=reader)({"doc_id": "docs/arch.pdf"})["structuredContent"]
        assert summary["tags"] == ["single"]

    def test_registration_and_call_via_protocol_handler(self):
        handler = ProtocolHandler()
        def reader(doc_id):
            return [_chunk("c1", 3)] if doc_id == "docs/arch.pdf" else []

        handler.register_tool(create_get_document_summary_definition(reader=reader))
        listed = handler.handle_tools_list()
        assert listed["tools"][0]["inputSchema"]["required"] == ["doc_id"]

        response = handler.handle_tools_call(1, TOOL_NAME, {"doc_id": "docs/arch.pdf"})
        assert response["result"]["structuredContent"]["chunk_count"] == 1

        missing = handler.handle_tools_call(2, TOOL_NAME, {"doc_id": "nope.pdf"})
        assert missing["result"]["isError"] is True

        invalid = handler.handle_tools_call(3, TOOL_NAME, {})
        assert invalid["error"]["code"] == -32602
