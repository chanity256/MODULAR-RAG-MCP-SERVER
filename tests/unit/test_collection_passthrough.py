"""AC3: Chat / Playground collection 透传单元测试（AGENTIC_COLLECTION_ISOLATION_SPEC，离线）。

覆盖 spec §4 AC3 前两组：``AgenticChatRetriever`` 同步与流式透传、
``SearchService._run_agentic`` 与 ``run_stream``（Deep 流式）透传；
第三组（前端 vitest）在 ``frontend/lib/search.test.ts`` 随实现更新。
ChatEngine 零改动：collection 已由引擎传入 retriever（含 auto 定档后）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from core.settings import AgenticSearchConfig
from libs.agentic_search.base_agentic_search import AgenticEvidence
from libs.agentic_search.fake_backend import FakeAgenticSearch
from mcp_server.tools.agentic_search import AgenticSearchTool
from observability.dashboard.services.chat_retrievers import AgenticChatRetriever
from observability.dashboard.services.search_service import SearchService

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------- #
# AC3-1：AgenticChatRetriever（同步 + 流式共用同一 arguments）
# --------------------------------------------------------------------- #


class RecordingTool:
    """fake agentic 工具：记录 arguments（``_call`` 优先走 ``run()``）。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, arguments: dict, trace: object = None) -> dict:
        self.calls.append(arguments)
        return {
            "content": [],
            "structuredContent": {"citations": [], "agentic": {"mode": "FAST"}},
        }

    def __call__(self, arguments: dict) -> dict:
        # SearchService 直接以 tool(arguments) 调用；retriever 经 _call 优先 run()。
        return self.run(arguments)


class TestChatRetrieverPassthrough:
    def test_collection_forwarded_trimmed(self):
        tool = RecordingTool()
        AgenticChatRetriever(tool, mode="FAST").retrieve("q", collection="  papers ")
        assert tool.calls[0]["collection"] == "papers"

    def test_no_collection_key_when_absent_or_blank(self):
        tool = RecordingTool()
        retriever = AgenticChatRetriever(tool, mode="FAST")
        retriever.retrieve("q")
        retriever.retrieve("q", collection="   ")
        assert all("collection" not in call for call in tool.calls)

    def test_stream_path_shares_the_same_arguments(self):
        stream_tool = RecordingTool()
        retriever = AgenticChatRetriever(
            RecordingTool(),
            mode="FAST",
            stream_tool_factory=lambda on_log: stream_tool,
        )
        retriever.retrieve("q", collection="c1", on_log=lambda *a: None)
        retriever.retrieve("q", on_log=lambda *a: None)
        assert stream_tool.calls[0]["collection"] == "c1"
        assert "collection" not in stream_tool.calls[1]


# --------------------------------------------------------------------- #
# AC3-2：SearchService（_run_agentic 同步 + run_stream Deep 流式）
# --------------------------------------------------------------------- #


class TestSearchServicePassthrough:
    def _tool_service(self, tool: AgenticSearchTool, allow_deep: bool) -> SearchService:
        settings = SimpleNamespace(
            agentic_search=AgenticSearchConfig(enabled=True, allow_deep=allow_deep)
        )
        return SearchService(settings=settings, agentic_tool=tool)

    def test_run_fast_forwards_collection(self):
        tool = RecordingTool()
        self._tool_service(tool, allow_deep=False).run("fast", "q", collection="c1")
        assert tool.calls[0]["collection"] == "c1"

    def test_run_fast_omits_collection_when_absent(self):
        tool = RecordingTool()
        self._tool_service(tool, allow_deep=False).run("fast", "q")
        assert "collection" not in tool.calls[0]

    def test_run_stream_deep_forwards_collection_to_scoped_paths(self, tmp_path: Path):
        """Deep 流式 collection 透传至工具 → 发送定界 paths（端到端）。"""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "a.md").write_text("alpha", encoding="utf-8")
        settings = SimpleNamespace(
            agentic_search=AgenticSearchConfig(
                enabled=True, allow_deep=True, allowed_roots=[str(docs)]
            ),
            observability=SimpleNamespace(log_file=str(tmp_path / "traces.jsonl")),
        )

        class FakeHistory:
            def list_processed(self) -> list[dict]:
                return [
                    {"source_path": str(docs / "a.md"), "collection": "A", "status": "success"},
                    {"source_path": str(docs / "b.md"), "collection": "B", "status": "success"},
                ]

        fake = FakeAgenticSearch(
            evidences=[AgenticEvidence(source_path=str(docs / "a.md"), snippet="s", score=1.0)],
            source_files=[str(docs / "a.md")],
        )
        tool = AgenticSearchTool(search=fake, settings=settings, history=FakeHistory())
        service = SearchService(settings=settings, agentic_tool=tool)

        outcome = service.run_stream("deep", "q", collection="A", on_event=lambda *a: None)
        assert fake.stream_calls == 1
        assert fake.last_request is not None
        assert fake.last_request.paths == [str((docs / "a.md").resolve())]
        assert not outcome.is_error

    def test_run_fast_scopes_paths_end_to_end(self, tmp_path: Path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "a.md").write_text("alpha", encoding="utf-8")
        settings = SimpleNamespace(
            agentic_search=AgenticSearchConfig(enabled=True, allowed_roots=[str(docs)]),
            observability=SimpleNamespace(log_file=str(tmp_path / "traces.jsonl")),
        )

        class FakeHistory:
            def list_processed(self) -> list[dict]:
                return [
                    {"source_path": str(docs / "a.md"), "collection": "A", "status": "success"},
                ]

        fake = FakeAgenticSearch(
            evidences=[AgenticEvidence(source_path=str(docs / "a.md"), snippet="s", score=1.0)],
            source_files=[str(docs / "a.md")],
        )
        tool = AgenticSearchTool(search=fake, settings=settings, history=FakeHistory())
        service = SearchService(settings=settings, agentic_tool=tool)

        service.run("fast", "q", collection="A")
        assert fake.last_request.paths == [str((docs / "a.md").resolve())]
        service.run("fast", "q")  # 不带 collection：口径与现状一致（全白名单）
        assert fake.last_request.paths == [str(docs.resolve())]
