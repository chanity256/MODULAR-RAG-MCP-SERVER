"""E4: list_collections tool 单元测试（注入 fake client，离线）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from mcp_server.protocol_handler import ProtocolHandler
from mcp_server.tools.list_collections import (
    EMPTY_COLLECTIONS_MESSAGE,
    ListCollectionsTool,
    TOOL_NAME,
    create_list_collections_definition,
)


class FakeCollection:
    def __init__(self, name, count):
        self.name = name
        self._count = count

    def count(self):
        return self._count


class FakeClient:
    def __init__(self, collections):
        self._collections = collections

    def list_collections(self):
        return list(self._collections)


class TestListCollections:
    def test_returns_names_and_chunk_counts(self):
        tool = ListCollectionsTool(client=FakeClient([FakeCollection("docs", 12), FakeCollection("notes", 3)]))
        response = tool({})
        markdown = response["content"][0]["text"]
        assert "**docs** (12 chunks)" in markdown and "**notes** (3 chunks)" in markdown
        assert response["structuredContent"]["collections"] == [
            {"name": "docs", "chunk_count": 12},
            {"name": "notes", "chunk_count": 3},
        ]

    def test_empty_collections_friendly_message(self):
        tool = ListCollectionsTool(client=FakeClient([]))
        response = tool({})
        assert response["content"][0]["text"] == EMPTY_COLLECTIONS_MESSAGE
        assert response["structuredContent"]["collections"] == []

    def test_count_failure_falls_back_to_zero(self):
        class BrokenCount(FakeCollection):
            def count(self):
                raise RuntimeError("boom")

        tool = ListCollectionsTool(client=FakeClient([BrokenCount("x", 5)]))
        response = tool({})
        assert response["structuredContent"]["collections"] == [{"name": "x", "chunk_count": 0}]

    def test_lazy_client_not_built_until_first_call(self):
        tool = ListCollectionsTool()
        assert tool._client is None
        tool._client = FakeClient([])
        tool({})

    def test_none_arguments_accepted(self):
        tool = ListCollectionsTool(client=FakeClient([]))
        tool(None)

    def test_non_dict_arguments_raises(self):
        tool = ListCollectionsTool(client=FakeClient([]))
        with pytest.raises(TypeError):
            tool(["x"])

    def test_registration_and_call_via_protocol_handler(self):
        handler = ProtocolHandler()
        handler.register_tool(create_list_collections_definition(client=FakeClient([FakeCollection("hub", 7)])))
        listed = handler.handle_tools_list()
        assert [t["name"] for t in listed["tools"]] == [TOOL_NAME]

        response = handler.handle_tools_call(1, TOOL_NAME, {})
        assert response["result"]["structuredContent"]["collections"] == [
            {"name": "hub", "chunk_count": 7}
        ]
