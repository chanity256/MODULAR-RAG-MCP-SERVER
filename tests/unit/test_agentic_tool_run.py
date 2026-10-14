"""WC7.3: ``AgenticSearchTool.run(arguments, trace=)`` 内部入口（D7：注入 trace 时不 finish/不落盘）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.settings import AgenticSearchConfig
from core.trace.trace_context import TRACE_TYPE_QUERY, TraceContext
from libs.agentic_search.base_agentic_search import AgenticEvidence, AgenticSearchBackendError
from libs.agentic_search.fake_backend import FakeAgenticSearch
from mcp_server.protocol_handler import ToolInvalidParams
from mcp_server.tools.agentic_search import AgenticSearchTool


class _Settings:
    def __init__(self, config: AgenticSearchConfig, log_file: str) -> None:
        self.agentic_search = config
        self.observability = type("O", (), {"log_file": log_file})()


@pytest.fixture()
def env(tmp_path: Path) -> dict:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("hello", encoding="utf-8")
    config = AgenticSearchConfig(enabled=True, allowed_roots=[str(docs)])
    log_file = tmp_path / "traces.jsonl"
    return {"docs": docs, "log_file": log_file, "settings": _Settings(config, str(log_file))}


def _fake(env, **kwargs) -> FakeAgenticSearch:
    return FakeAgenticSearch(
        evidences=[
            AgenticEvidence(
                source_path=str(env["docs"] / "a.md"), snippet="evidence", score=0.9, char_start=0, char_end=8
            )
        ],
        source_files=[str(env["docs"] / "a.md")],
        **kwargs,
    )


def _trace() -> TraceContext:
    return TraceContext(trace_type=TRACE_TYPE_QUERY, metadata={"routes": "chat"})


def test_injected_trace_gets_stage_but_is_not_finished_or_persisted(env):
    tool = AgenticSearchTool(search=_fake(env), settings=env["settings"])
    trace = _trace()

    tool.run({"query": "q"}, trace=trace)

    stage = trace.get_stage("agentic_search")
    assert stage is not None and stage["details"]["evidences"] == 1
    assert trace.total_elapsed_ms is None and trace.finished_at is None
    assert not env["log_file"].exists()
    # 调用方的 metadata 不被工具改写（routes 仍由调用方决定）。
    assert trace.metadata == {"routes": "chat"}


def test_injected_trace_error_path_records_stage_without_finish(env):
    tool = AgenticSearchTool(search=_fake(env, error=AgenticSearchBackendError("down")), settings=env["settings"])
    trace = _trace()

    result = tool.run({"query": "q"}, trace=trace)

    assert result["isError"] is True
    assert trace.get_stage("agentic_search")["method"] == "error"
    assert trace.total_elapsed_ms is None
    assert not env["log_file"].exists()


def test_validation_still_raises_with_injected_trace(env):
    tool = AgenticSearchTool(search=_fake(env), settings=env["settings"])
    trace = _trace()
    with pytest.raises(ToolInvalidParams):
        tool.run({"query": ""}, trace=trace)
    assert trace.stages == []


def test_call_delegates_to_run_and_still_persists_its_own_trace(env):
    tool = AgenticSearchTool(search=_fake(env), settings=env["settings"])

    via_call = tool({"query": "q"})
    via_run = tool.run({"query": "q"})

    assert via_call == via_run
    lines = env["log_file"].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    record = json.loads(lines[0])
    assert record["metadata"] == {"query": "q", "mode": "FAST", "routes": "agentic"}
    assert record["total_elapsed_ms"] is not None
    assert record["stages"][0]["stage"] == "agentic_search"


def test_call_error_path_keeps_legacy_no_persist(env):
    tool = AgenticSearchTool(search=_fake(env, error=AgenticSearchBackendError("down")), settings=env["settings"])
    assert tool({"query": "q"})["isError"] is True
    assert not env["log_file"].exists()
