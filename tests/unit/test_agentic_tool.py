"""S1.6: agentic_search MCP tool 单元测试（注入 fake 后端，离线）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

sys_path_guard = str(Path(__file__).resolve().parents[2] / "src")

import sys

sys.path.insert(0, sys_path_guard)

from core.settings import AgenticSearchConfig
from libs.agentic_search.base_agentic_search import (
    AgenticEvidence,
    AgenticSearchBackendError,
    AgenticSearchTimeoutError,
)
from libs.agentic_search.fake_backend import FakeAgenticSearch
from mcp_server.protocol_handler import ProtocolHandler, ToolInvalidParams
from mcp_server.server import build_default_protocol_handler
from mcp_server.tools.agentic_search import (
    TOOL_NAME,
    AgenticSearchTool,
    create_agentic_search_definition,
)


def _config(**overrides) -> AgenticSearchConfig:
    defaults = dict(
        enabled=True,
        base_url="http://127.0.0.1:8584",
        mode="FAST",
        allow_deep=False,
        timeout_s=30.0,
        top_k_files=5,
        max_loops=6,
        max_token_budget=32000,
        allowed_roots=[],
        denied_substrings=[".env", ".git", "data/db"],
    )
    defaults.update(overrides)
    return AgenticSearchConfig(**defaults)


class _Settings:
    def __init__(self, config: AgenticSearchConfig, log_file: str) -> None:
        self.agentic_search = config
        self.observability = type("O", (), {"log_file": log_file})()


@pytest.fixture()
def env(tmp_path: Path) -> dict:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("hello", encoding="utf-8")
    return {
        "docs": docs,
        "log_file": str(tmp_path / "traces.jsonl"),
        "config": _config(allowed_roots=[str(docs)]),
        "settings": _Settings(_config(allowed_roots=[str(docs)]), str(tmp_path / "traces.jsonl")),
    }


@pytest.fixture()
def tool(env) -> AgenticSearchTool:
    # Fake 证据路径必须落在允许根内（PathGuard 调用后过滤会剔除根外证据）。
    fake = FakeAgenticSearch(
        evidences=[
            AgenticEvidence(
                source_path=str(env["docs"] / "a.md"),
                snippet="fake evidence snippet",
                score=0.9,
                char_start=0,
                char_end=22,
            )
        ],
        source_files=[str(env["docs"] / "a.md")],
    )
    return AgenticSearchTool(search=fake, settings=env["settings"])


class TestValidation:
    def test_query_required(self, tool):
        with pytest.raises(ToolInvalidParams, match="query"):
            tool({})
        with pytest.raises(ToolInvalidParams, match="query"):
            tool({"query": "   "})

    def test_mode_validation(self, tool):
        with pytest.raises(ToolInvalidParams, match="mode"):
            tool({"query": "q", "mode": "TURBO"})
        with pytest.raises(ToolInvalidParams, match="allow_deep"):
            tool({"query": "q", "mode": "DEEP"})

    def test_deep_allowed_when_config_permits(self, env):
        fake = FakeAgenticSearch()
        settings = _Settings(_config(allow_deep=True, allowed_roots=[str(env["docs"])]), env["log_file"])
        tool = AgenticSearchTool(search=fake, settings=settings)
        tool({"query": "q", "mode": "DEEP"})
        assert fake.last_request.mode == "DEEP"

    def test_paths_outside_allowlist_rejected_without_request(self, env):
        fake = FakeAgenticSearch()
        tool = AgenticSearchTool(search=fake, settings=env["settings"])
        with pytest.raises(ToolInvalidParams, match="allowlist"):
            tool({"query": "q", "paths": ["/etc"]})
        assert fake.last_request is None  # fail-closed：未发请求

    def test_paths_must_be_list(self, tool):
        with pytest.raises(ToolInvalidParams, match="paths"):
            tool({"query": "q", "paths": "docs"})

    def test_top_k_validation(self, tool):
        with pytest.raises(ToolInvalidParams, match="top_k"):
            tool({"query": "q", "top_k": 0})
        with pytest.raises(ToolInvalidParams, match="top_k"):
            tool({"query": "q", "top_k": True})

    def test_non_dict_arguments(self, tool):
        with pytest.raises(TypeError):
            tool(["q"])  # type: ignore[arg-type]

    def test_default_paths_from_allowed_roots(self, tool, env):
        tool({"query": "q"})
        assert tool._search.last_request.paths == [str(env["docs"])]


class TestHappyPath:
    def test_markdown_and_citations(self, tool):
        result = tool({"query": "what is k1?"})
        markdown = result["content"][0]["text"]
        assert "[1]" in markdown and "a.md" in markdown
        citations = result["structuredContent"]["citations"]
        assert citations[0]["chunk_id"].startswith("agentic:")
        assert citations[0]["page"] is None  # agentic 无页码口径（spec 4.6）

    def test_structured_agentic_block(self, tool):
        result = tool({"query": "q"})
        block = result["structuredContent"]["agentic"]
        assert block["mode"] == "FAST"
        assert block["agentic_answer"] == "fake agentic answer"
        assert block["llm_call_count"] == 2
        assert block["total_llm_tokens"] == 1024

    def test_top_k_truncation(self, tool):
        result = tool({"query": "q", "top_k": 1})
        assert len(result["structuredContent"]["citations"]) == 1

    def test_no_evidence_friendly_answer(self, env):
        fake = FakeAgenticSearch(evidences=[], source_files=[], answer="you asked about nothing")
        tool = AgenticSearchTool(search=fake, settings=env["settings"])
        result = tool({"query": "q"})
        text = result["content"][0]["text"]
        assert "you asked about nothing" in text
        assert "no citable evidence" in text
        assert result["structuredContent"]["citations"] == []


class TestEvidenceFiltering:
    def test_out_of_allowlist_evidence_dropped(self, env, tmp_path):
        outside = tmp_path / "secret-leak.md"
        response_evidences = [
            AgenticEvidence(source_path=str(env["docs"] / "a.md"), snippet="ok", score=1.0),
            AgenticEvidence(source_path=str(outside), snippet="leak", score=0.9),
        ]
        fake = FakeAgenticSearch(
            evidences=response_evidences,
            source_files=[str(env["docs"] / "a.md"), str(outside)],
        )
        tool = AgenticSearchTool(search=fake, settings=env["settings"])
        result = tool({"query": "q"})
        sources = [c["source"] for c in result["structuredContent"]["citations"]]
        assert all("secret" not in s for s in sources)
        assert result["structuredContent"]["agentic"]["evidence_files"] == [str(env["docs"] / "a.md")]


class TestUnavailableSidecar:
    @pytest.mark.parametrize(
        "error",
        [
            AgenticSearchTimeoutError("timed out"),
            AgenticSearchBackendError("connection refused"),
        ],
    )
    def test_is_error_friendly_response(self, env, error):
        fake = FakeAgenticSearch(error=error)
        tool = AgenticSearchTool(search=fake, settings=env["settings"])
        result = tool({"query": "q"})
        assert result["isError"] is True
        assert "unavailable" in result["content"][0]["text"]
        assert result["structuredContent"]["error_type"] == type(error).__name__


class TestTrace:
    def test_trace_stage_persisted(self, tool, env):
        tool({"query": "persisted?"})
        lines = Path(env["log_file"]).read_text(encoding="utf-8").strip().splitlines()
        assert lines, "trace must be persisted when settings provided"
        trace = json.loads(lines[-1])
        assert trace["trace_type"] == "query"
        stage = next(s for s in trace["stages"] if s["stage"] == "agentic_search")
        assert stage["method"] == "fake"
        assert stage["details"]["mode"] == "FAST"
        assert stage["details"]["evidences"] == 1

    def test_no_persist_without_settings(self, env):
        tool = AgenticSearchTool(search=FakeAgenticSearch(), settings=None)
        result = tool({"query": "q"})  # settings=None 时不落 trace，不报错
        assert result["structuredContent"]["agentic"]["mode"] == "FAST"


class TestRegistration:
    def test_disabled_not_registered(self, env):
        handler = build_default_protocol_handler(settings=_Settings(_config(enabled=False), env["log_file"]))
        names = [t["name"] for t in handler.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]]
        assert TOOL_NAME not in names

    def test_enabled_registered(self, env):
        handler = build_default_protocol_handler(settings=env["settings"])
        tools = handler.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        names = [t["name"] for t in tools]
        assert TOOL_NAME in names
        agentic = next(t for t in tools if t["name"] == TOOL_NAME)
        assert agentic["inputSchema"]["required"] == ["query"]

    def test_registration_via_definition_factory(self, env):
        handler = ProtocolHandler()
        handler.register_tool(
            create_agentic_search_definition(search=FakeAgenticSearch(), settings=env["settings"])
        )
        response = handler.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": TOOL_NAME, "arguments": {"query": "q"}}}
        )
        assert response["result"]["structuredContent"]["agentic"]["mode"] == "FAST"
