"""E3: query_knowledge_hub tool 单元测试（注入 fake 查询栈，离线）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from core.types import RetrievalResult
from mcp_server.protocol_handler import ProtocolHandler, ToolInvalidParams
from mcp_server.tools.query_knowledge_hub import (
    TOOL_DESCRIPTION,
    TOOL_INPUT_SCHEMA,
    TOOL_NAME,
    QueryKnowledgeHubTool,
    create_query_knowledge_hub_definition,
)


class FakeSearch:
    def __init__(self, results=None):
        self.results = results or []
        self.calls = []

    def search(self, query, top_k=None, filters=None, trace=None):
        self.calls.append({"query": query, "top_k": top_k, "filters": filters})
        return list(self.results)


class FakeReranker:
    def __init__(self):
        self.calls = []

    def rerank(self, query, results, trace=None):
        self.calls.append({"query": query, "n": len(results)})
        return list(reversed(results))


def _results(n=2):
    return [
        RetrievalResult(chunk_id=f"c{i}", score=0.9 - i * 0.1, text=f"chunk {i}",
                        metadata={"source_path": f"doc{i}.pdf", "page": i + 1})
        for i in range(n)
    ]


class TestValidation:
    @pytest.mark.parametrize("arguments", [{}, {"query": ""}, {"query": "   "}, {"query": 123}])
    def test_invalid_query_raises_invalid_params(self, arguments):
        tool = QueryKnowledgeHubTool(search=FakeSearch())
        with pytest.raises(ToolInvalidParams):
            tool(arguments)

    @pytest.mark.parametrize("top_k", [0, -1, 1.5, True, "5"])
    def test_invalid_top_k_raises(self, top_k):
        tool = QueryKnowledgeHubTool(search=FakeSearch())
        with pytest.raises(ToolInvalidParams):
            tool({"query": "q", "top_k": top_k})

    @pytest.mark.parametrize("collection", ["", "   ", 123])
    def test_invalid_collection_raises(self, collection):
        tool = QueryKnowledgeHubTool(search=FakeSearch())
        with pytest.raises(ToolInvalidParams):
            tool({"query": "q", "collection": collection})

    def test_non_dict_arguments_raises_type_error(self):
        tool = QueryKnowledgeHubTool(search=FakeSearch())
        with pytest.raises(TypeError):
            tool(["query"])


class TestExecution:
    def test_returns_markdown_content_and_citations(self):
        tool = QueryKnowledgeHubTool(search=FakeSearch(_results(2)), reranker=FakeReranker(), rerank_enabled=False)
        response = tool({"query": "bm25"})
        text = response["content"][0]["text"]
        assert "[1]" in text and "[2]" in text
        citations = response["structuredContent"]["citations"]
        assert [c["chunk_id"] for c in citations] == ["c0", "c1"]
        assert all({"source", "page", "chunk_id", "score"} <= set(c) for c in citations)

    def test_empty_results_friendly_message(self):
        tool = QueryKnowledgeHubTool(search=FakeSearch([]))
        response = tool({"query": "zzz"})
        assert "No relevant content" in response["content"][0]["text"]
        assert response["structuredContent"]["citations"] == []

    def test_passes_top_k_and_collection_filter(self):
        search = FakeSearch(_results(1))
        tool = QueryKnowledgeHubTool(search=search, reranker=None, rerank_enabled=False)
        tool({"query": "q", "top_k": 3, "collection": "docs"})
        assert search.calls == [{"query": "q", "top_k": 3, "filters": {"collection": "docs"}}]

    def test_reranker_applied_when_enabled(self):
        reranker = FakeReranker()
        tool = QueryKnowledgeHubTool(search=FakeSearch(_results(2)), reranker=reranker)
        response = tool({"query": "q"})
        assert reranker.calls == [{"query": "q", "n": 2}]
        assert [c["chunk_id"] for c in response["structuredContent"]["citations"]] == ["c1", "c0"]

    def test_reranker_skipped_on_empty_results(self):
        reranker = FakeReranker()
        tool = QueryKnowledgeHubTool(search=FakeSearch([]), reranker=reranker)
        tool({"query": "q"})
        assert reranker.calls == []

    def test_lazy_stack_not_built_until_first_call(self):
        tool = QueryKnowledgeHubTool()
        assert tool._search is None and tool._reranker is None
        tool._search = FakeSearch([])  # 注入后再调用，不触发默认构建
        tool({"query": "q"})


class TestRegistration:
    def test_definition_fields(self):
        definition = create_query_knowledge_hub_definition(search=FakeSearch([]), rerank_enabled=False)
        assert definition.name == TOOL_NAME == "query_knowledge_hub"
        assert definition.description == TOOL_DESCRIPTION
        assert definition.input_schema == TOOL_INPUT_SCHEMA

    def test_register_and_call_through_protocol_handler(self):
        handler = ProtocolHandler()
        handler.register_tool(create_query_knowledge_hub_definition(search=FakeSearch(_results(1)), rerank_enabled=False))
        listed = handler.handle_tools_list()
        assert listed["tools"][0]["name"] == "query_knowledge_hub"

        response = handler.handle_tools_call(
            1, "query_knowledge_hub", {"query": "azure"}
        )
        assert response["id"] == 1
        assert response["result"]["structuredContent"]["citations"][0]["chunk_id"] == "c0"

    def test_invalid_params_maps_to_32602_via_handler(self):
        handler = ProtocolHandler()
        handler.register_tool(create_query_knowledge_hub_definition(search=FakeSearch()))
        response = handler.handle_tools_call(2, "query_knowledge_hub", {"query": ""})
        assert response["error"]["code"] == -32602

    def test_internal_failure_maps_to_32603_without_leak(self):
        class ExplodingSearch:
            def search(self, *a, **k):
                raise RuntimeError("secret db password")

        handler = ProtocolHandler()
        handler.register_tool(create_query_knowledge_hub_definition(search=ExplodingSearch(), rerank_enabled=False))
        response = handler.handle_tools_call(3, "query_knowledge_hub", {"query": "q"})
        assert response["error"]["code"] == -32603
        assert "secret" not in response["error"]["message"]
