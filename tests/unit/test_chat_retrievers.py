"""WC7.3: HybridChatRetriever / AgenticChatRetriever（假 QueryService / 假 sidecar 后端，离线）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.chat.engine import ERROR_UPSTREAM, classify_error
from core.chat.retriever import ChatRetrievalResult, ChatRetriever, DeepDisabledError
from core.settings import AgenticSearchConfig
from core.trace.trace_context import TRACE_TYPE_QUERY, TraceContext
from core.types import RetrievalResult
from libs.agentic_search.base_agentic_search import (
    AgenticEvidence,
    AgenticSearchBackendError,
    AgenticSearchError,
    AgenticSearchResponseError,
    AgenticSearchTimeoutError,
)
from libs.agentic_search.fake_backend import DEFAULT_STREAM_EVENTS, FakeAgenticSearch
from mcp_server.protocol_handler import ToolInvalidParams
from mcp_server.tools.agentic_search import AgenticSearchTool
from observability.dashboard.services.chat_retrievers import (
    AgenticChatRetriever,
    HybridChatRetriever,
    build_chat_retrievers,
)


class _Settings:
    def __init__(self, config: AgenticSearchConfig, log_file: str) -> None:
        self.agentic_search = config
        self.observability = type("O", (), {"log_file": log_file})()


def _trace() -> TraceContext:
    return TraceContext(trace_type=TRACE_TYPE_QUERY, metadata={"routes": "chat"})


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, Any]:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("hello", encoding="utf-8")
    log_file = tmp_path / "traces.jsonl"

    def make(*, allow_deep: bool = False, **fake_kwargs: Any):
        config = AgenticSearchConfig(enabled=True, allow_deep=allow_deep, allowed_roots=[str(docs)])
        settings = _Settings(config, str(log_file))
        kwargs: dict[str, Any] = {
            "evidences": [
                AgenticEvidence(
                    source_path=str(docs / "a.md"), snippet="evidence", score=0.9, char_start=0, char_end=8
                )
            ],
            "source_files": [str(docs / "a.md")],
            **fake_kwargs,
        }
        fake = FakeAgenticSearch(**kwargs)
        return settings, fake, AgenticSearchTool(search=fake, settings=settings)

    return {"make": make, "log_file": log_file, "docs": docs}


# --------------------------------------------------------------------------- hybrid
class FakeQueryService:
    def __init__(self, results=None, error: Exception | None = None):
        self.results = results if results is not None else [RetrievalResult("c1", 0.8, "text", {"source_path": "a.md"})]
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def search(self, query, *, collection=None, top_k=None, trace=None):
        self.calls.append({"query": query, "collection": collection, "top_k": top_k, "trace": trace})
        if self.error is not None:
            raise self.error
        if trace is not None:
            trace.record_stage("dense_retrieval", count=len(self.results))
        return list(self.results)


class TestHybrid:
    def test_wraps_query_service_and_passes_trace_through(self):
        service = FakeQueryService()
        retriever = HybridChatRetriever(service)
        trace = _trace()

        out = retriever.retrieve("q", collection="docs", top_k=3, trace=trace)

        assert isinstance(retriever, ChatRetriever)
        assert isinstance(out, ChatRetrievalResult)
        assert [r.chunk_id for r in out.results] == ["c1"]
        assert out.answer is None and out.citations == []
        assert service.calls == [{"query": "q", "collection": "docs", "top_k": 3, "trace": trace}]
        assert trace.get_stage("dense_retrieval") is not None
        assert trace.total_elapsed_ms is None  # 绝不 finish

    def test_errors_propagate_for_engine_classification(self):
        retriever = HybridChatRetriever(FakeQueryService(error=RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            retriever.retrieve("q")


# --------------------------------------------------------------------------- agentic
class TestAgentic:
    def test_extracts_citations_and_sidecar_answer(self, env):
        settings, _, tool = env["make"](answer="sidecar says hi [1]")
        retriever = AgenticChatRetriever(tool, mode="FAST", settings=settings)
        trace = _trace()

        out = retriever.retrieve("q", top_k=2, trace=trace)

        assert out.answer == "sidecar says hi [1]"
        assert out.results == []
        assert [c["source"] for c in out.citations] == [str(env["docs"] / "a.md")]
        assert out.citations[0]["id"] == 1 and out.citations[0]["page"] is None
        assert out.metadata["mode"] == "FAST"
        stage = trace.get_stage("agentic_search")
        assert stage is not None and stage["method"] == "fake"
        assert trace.total_elapsed_ms is None
        assert not env["log_file"].exists()  # 注入 trace 时工具不落盘

    def test_mode_is_forwarded_and_top_k_optional(self, env):
        settings, fake, _ = env["make"](allow_deep=True)
        seen: list[Any] = []

        class SpyTool:
            def run(self, arguments, *, trace=None):
                seen.append(arguments)
                return {"structuredContent": {"agentic": {"agentic_answer": "a"}, "citations": []}}

        AgenticChatRetriever(SpyTool(), mode="FAST", settings=settings).retrieve("q")
        AgenticChatRetriever(SpyTool(), mode="deep", settings=settings).retrieve("q", top_k=4)
        assert seen == [{"query": "q", "mode": "FAST"}, {"query": "q", "mode": "DEEP", "top_k": 4}]

    def test_invalid_mode_rejected(self, env):
        settings, _, tool = env["make"]()
        with pytest.raises(ValueError):
            AgenticChatRetriever(tool, mode="TURBO", settings=settings)

    def test_deep_gated_by_allow_deep_without_calling_tool(self, env):
        settings, fake, tool = env["make"](allow_deep=False)
        calls: list[Any] = []
        tool.run = lambda *a, **k: calls.append(a)  # type: ignore[method-assign]

        with pytest.raises(DeepDisabledError):
            AgenticChatRetriever(tool, mode="DEEP", settings=settings).retrieve("q", trace=_trace())
        assert calls == []

    def test_deep_allowed_when_configured(self, env):
        settings, _, tool = env["make"](allow_deep=True)
        out = AgenticChatRetriever(tool, mode="DEEP", settings=settings).retrieve("q", trace=_trace())
        assert out.metadata["mode"] == "DEEP"

    def test_allow_deep_read_from_tool_settings_when_not_given(self, env):
        _, _, tool = env["make"](allow_deep=False)
        with pytest.raises(DeepDisabledError):
            AgenticChatRetriever(tool, mode="DEEP").retrieve("q")

    def test_tool_level_deep_rejection_maps_to_deep_disabled(self, env):
        settings, _, _ = env["make"](allow_deep=True)

        class Rejecting:
            def run(self, arguments, *, trace=None):
                raise ToolInvalidParams("'mode: DEEP' is disabled by server config (agentic_search.allow_deep=false)")

        with pytest.raises(DeepDisabledError):
            AgenticChatRetriever(Rejecting(), mode="DEEP", settings=settings).retrieve("q")

    def test_other_invalid_params_become_value_error(self, env):
        settings, _, _ = env["make"]()

        class Rejecting:
            def run(self, arguments, *, trace=None):
                raise ToolInvalidParams("'paths' rejected by allowlist")

        with pytest.raises(ValueError, match="allowlist"):
            AgenticChatRetriever(Rejecting(), mode="FAST", settings=settings).retrieve("q")

    @pytest.mark.parametrize(
        ("error", "transient"),
        [
            (AgenticSearchTimeoutError("t"), True),
            (AgenticSearchBackendError("b"), True),
            (AgenticSearchResponseError("r"), False),
        ],
    )
    def test_is_error_results_are_mapped_back_to_exception_types(self, env, error, transient):
        settings, _, tool = env["make"](error=error)
        trace = _trace()

        with pytest.raises(type(error)) as info:
            AgenticChatRetriever(tool, mode="FAST", settings=settings).retrieve("q", trace=trace)

        assert isinstance(info.value, AgenticSearchError)
        classified = classify_error(info.value)
        assert classified.code == ERROR_UPSTREAM and classified.transient is transient
        assert trace.get_stage("agentic_search")["method"] == "error"
        assert trace.total_elapsed_ms is None

    def test_unknown_error_type_falls_back_to_base_error(self):
        class OddTool:
            def run(self, arguments, *, trace=None):
                return {"isError": True, "content": [{"type": "text", "text": "x"}], "structuredContent": {"error_type": "Weird"}}

        with pytest.raises(AgenticSearchError):
            AgenticChatRetriever(OddTool(), settings=_Settings(AgenticSearchConfig(), "x")).retrieve("q")

    def test_malformed_tool_result(self):
        class Bad:
            def run(self, arguments, *, trace=None):
                return "nope"

        with pytest.raises(AgenticSearchResponseError):
            AgenticChatRetriever(Bad(), settings=_Settings(AgenticSearchConfig(), "x")).retrieve("q")

    def test_falls_back_to_markdown_when_sidecar_answer_empty_but_cited(self, env):
        settings, _, tool = env["make"](answer="")
        out = AgenticChatRetriever(tool, settings=settings).retrieve("q", trace=_trace())
        assert out.citations and out.answer and "a.md" in out.answer

    def test_no_answer_and_no_citations_gives_no_passthrough(self, env):
        settings, _, tool = env["make"](answer="", evidences=[], source_files=[])
        out = AgenticChatRetriever(tool, settings=settings).retrieve("q", trace=_trace())
        assert out.answer is None and out.citations == []

    def test_works_without_trace_by_delegating_like_call(self, env):
        settings, _, tool = env["make"]()
        AgenticChatRetriever(tool, settings=settings).retrieve("q")
        assert env["log_file"].exists()  # 无注入 trace 时沿用工具自建并落盘的旧语义


class TestProgressStream:
    def test_stream_logs_are_forwarded_and_result_still_extracted(self, env):
        settings, fake, tool = env["make"](answer="streamed answer")
        logs: list[tuple[str, str, float | None]] = []
        trace = _trace()

        out = AgenticChatRetriever(tool, settings=settings).retrieve(
            "q", trace=trace, on_log=lambda level, message, ts: logs.append((level, message, ts))
        )

        assert logs == list(DEFAULT_STREAM_EVENTS)
        assert out.answer == "streamed answer" and len(out.citations) == 1
        assert trace.get_stage("agentic_search") is not None and trace.total_elapsed_ms is None
        assert not env["log_file"].exists()

    def test_backend_without_stream_support_falls_back_to_sync_search(self, env):
        settings, _, tool = env["make"](supports_stream=False, answer="sync answer")
        logs: list[Any] = []

        out = AgenticChatRetriever(tool, settings=settings).retrieve(
            "q", trace=_trace(), on_log=lambda *a: logs.append(a)
        )

        assert logs == [] and out.answer == "sync answer"

    def test_non_tool_instance_without_factory_uses_sync_path(self):
        class Plain:
            def run(self, arguments, *, trace=None):
                return {"structuredContent": {"agentic": {"agentic_answer": "plain"}, "citations": []}}

        logs: list[Any] = []
        out = AgenticChatRetriever(Plain(), settings=_Settings(AgenticSearchConfig(), "x")).retrieve(
            "q", on_log=lambda *a: logs.append(a)
        )
        assert out.answer == "plain" and logs == []

    def test_custom_stream_tool_factory(self):
        built: list[Any] = []

        class StreamTool:
            def run(self, arguments, *, trace=None):
                return {"structuredContent": {"agentic": {"agentic_answer": "from stream tool"}, "citations": []}}

        def factory(on_log):
            built.append(on_log)
            return StreamTool()

        retriever = AgenticChatRetriever(
            object(), settings=_Settings(AgenticSearchConfig(), "x"), stream_tool_factory=factory
        )
        out = retriever.retrieve("q", on_log=lambda *a: None)
        assert out.answer == "from stream tool" and len(built) == 1


class TestBuildRetrievers:
    def test_agentic_disabled_registers_only_hybrid(self):
        settings = _Settings(AgenticSearchConfig(enabled=False), "x")
        retrievers = build_chat_retrievers(settings, query_service=FakeQueryService())
        assert list(retrievers) == ["hybrid"]

    def test_agentic_enabled_registers_all_modes_with_shared_tool(self, env):
        settings, _, tool = env["make"]()
        retrievers = build_chat_retrievers(settings, query_service=FakeQueryService(), agentic_tool=tool)
        assert list(retrievers) == ["hybrid", "agentic_fast", "agentic_deep"]
        assert retrievers["agentic_fast"].mode == "FAST" and retrievers["agentic_deep"].mode == "DEEP"
        with pytest.raises(DeepDisabledError):
            retrievers["agentic_deep"].retrieve("q")
