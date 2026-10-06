"""S4.1: SearchService 三模式分发 + 门控单元测试（Fake 工具，离线）。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from core.settings import AgenticSearchConfig
from mcp_server.protocol_handler import ToolInvalidParams
from observability.dashboard.services.search_service import (
    SearchOutcome,
    SearchService,
)


# --------------------------------------------------------------------- #
# Fake 工具与 settings
# --------------------------------------------------------------------- #


def _settings(enabled: bool = False, allow_deep: bool = False) -> SimpleNamespace:
    return SimpleNamespace(agentic_search=AgenticSearchConfig(enabled=enabled, allow_deep=allow_deep))


class FakeTool:
    """可编程假工具：记录入参，按配置返回结果或抛异常。"""

    def __init__(
        self,
        result: dict | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.calls: list[dict] = []
        self.result = result if result is not None else _ok_result()
        self.exc = exc

    def __call__(self, arguments: dict) -> dict:
        self.calls.append(arguments)
        if self.exc is not None:
            raise self.exc
        return self.result

    @property
    def call_count(self) -> int:
        return len(self.calls)


def _ok_result(
    *,
    markdown: str = "Top results for query: **q**",
    citations: list[dict] | None = None,
    agentic: dict | None = None,
) -> dict:
    structured: dict = {"query": "q", "answer": markdown, "citations": citations if citations is not None else []}
    if agentic is not None:
        structured["agentic"] = agentic
    return {"content": [{"type": "text", "text": markdown}], "structuredContent": structured}


def _citation(idx: int = 1, source: str = "docs/a.pdf", page: int | None = 1) -> dict:
    return {
        "id": idx,
        "source": source,
        "page": page,
        "chunk_id": f"chunk_{idx}",
        "text": "excerpt",
        "score": 0.9,
    }


def _service(query_tool=None, agentic_tool=None, **settings_kwargs) -> SearchService:
    return SearchService(
        settings=_settings(**settings_kwargs),
        query_tool=query_tool,
        agentic_tool=agentic_tool,
    )


# --------------------------------------------------------------------- #
# 可用性矩阵：enabled × allow_deep 四种组合
# --------------------------------------------------------------------- #


def test_availability_matrix():
    # (enabled, allow_deep) → (normal, fast, deep) 可用性
    cases = [
        (False, False, (True, False, False)),
        (True, False, (True, True, False)),
        (False, True, (True, False, False)),
        (True, True, (True, True, True)),
    ]
    for enabled, allow_deep, expected in cases:
        matrix = SearchService(_settings(enabled, allow_deep)).availability()
        assert (
            matrix["normal"].available,
            matrix["fast"].available,
            matrix["deep"].available,
        ) == expected, (enabled, allow_deep)

    # 不可用时必须给出 reason（页面 help 悬停提示），可用时 reason 为 None
    off = SearchService(_settings(False, False)).availability()
    assert off["fast"].reason and "agentic_search.enabled" in off["fast"].reason
    assert off["deep"].reason and "agentic_search.enabled" in off["deep"].reason
    partial = SearchService(_settings(True, False)).availability()
    assert partial["deep"].reason and "allow_deep" in partial["deep"].reason
    assert partial["fast"].reason is None
    on = SearchService(_settings(True, True)).availability()
    assert on["fast"].reason is None and on["deep"].reason is None


def test_availability_accepts_explicit_settings():
    matrix = SearchService().availability(_settings(enabled=True, allow_deep=True))
    assert matrix["fast"].available and matrix["deep"].available


# --------------------------------------------------------------------- #
# 分发路由：normal → QueryKnowledgeHubTool；fast/deep → AgenticSearchTool
# --------------------------------------------------------------------- #


def test_run_normal_routes_to_query_tool():
    tool = FakeTool()
    outcome = _service(query_tool=tool).run("normal", "bm25 scoring", top_k=3, collection="docs")
    assert tool.call_count == 1
    assert tool.calls[0] == {"query": "bm25 scoring", "top_k": 3, "collection": "docs"}
    assert outcome.mode == "normal"
    assert not outcome.is_error
    assert outcome.error is None
    assert outcome.agentic_meta is None


def test_run_fast_routes_to_agentic_tool():
    meta = {"mode": "FAST", "total_llm_tokens": 123, "llm_call_count": 2}
    tool = FakeTool(_ok_result(agentic=meta))
    outcome = _service(enabled=True, agentic_tool=tool).run("fast", "who wrote bm25", top_k=5)
    assert tool.call_count == 1
    assert tool.calls[0] == {"query": "who wrote bm25", "mode": "FAST", "top_k": 5}
    assert outcome.mode == "fast"
    assert outcome.agentic_meta == meta


def test_run_deep_routes_to_agentic_tool():
    tool = FakeTool(_ok_result(agentic={"mode": "DEEP"}))
    outcome = _service(enabled=True, allow_deep=True, agentic_tool=tool).run("deep", "multi hop q")
    assert tool.calls[0]["mode"] == "DEEP"
    assert outcome.mode == "deep"


def test_run_normal_without_optional_args():
    tool = FakeTool()
    _service(query_tool=tool).run("normal", "q")
    assert tool.calls[0] == {"query": "q"}


# --------------------------------------------------------------------- #
# 门控：enabled=false 时 fast/deep 不发请求
# --------------------------------------------------------------------- #


def test_run_fast_gated_when_disabled():
    tool = FakeTool()
    outcome = _service(enabled=False, agentic_tool=tool).run("fast", "q")
    assert tool.call_count == 0
    assert outcome.is_error
    assert "agentic_search.enabled" in (outcome.error or "")


def test_run_deep_gated_when_allow_deep_false():
    tool = FakeTool()
    outcome = _service(enabled=True, allow_deep=False, agentic_tool=tool).run("deep", "q")
    assert tool.call_count == 0
    assert outcome.is_error
    assert "allow_deep" in (outcome.error or "")


def test_run_deep_gated_when_fully_disabled():
    tool = FakeTool()
    outcome = _service(enabled=False, allow_deep=True, agentic_tool=tool).run("deep", "q")
    assert tool.call_count == 0
    assert outcome.is_error


# --------------------------------------------------------------------- #
# 结果归一化：成功 / isError / ToolInvalidParams / 空结果
# --------------------------------------------------------------------- #


def test_run_normal_success_normalization():
    citations = [_citation(1), _citation(2, page=None)]
    tool = FakeTool(_ok_result(markdown="answer md", citations=citations))
    outcome = _service(query_tool=tool).run("normal", "q")
    assert isinstance(outcome, SearchOutcome)
    assert outcome.markdown == "answer md"
    assert [c["source"] for c in outcome.citations] == ["docs/a.pdf", "docs/a.pdf"]
    assert outcome.citations[0]["page"] == 1
    assert outcome.citations[1]["page"] is None
    assert outcome.elapsed_ms >= 0.0


def test_run_iserror_result_normalized():
    unavailable = {
        "content": [{"type": "text", "text": "Agentic search is currently unavailable: sidecar down"}],
        "isError": True,
        "structuredContent": {"error_type": "AgenticSearchBackendError"},
    }
    tool = FakeTool(unavailable)
    outcome = _service(enabled=True, agentic_tool=tool).run("fast", "q")
    assert outcome.is_error
    assert "sidecar down" in outcome.error
    assert outcome.markdown == "Agentic search is currently unavailable: sidecar down"


def test_run_invalid_params_normalized():
    tool = FakeTool(exc=ToolInvalidParams("'query' must be a non-empty string"))
    outcome = _service(query_tool=tool).run("normal", "q")
    assert outcome.is_error
    assert "参数非法" in outcome.error
    assert outcome.citations == []


def test_run_unexpected_exception_normalized():
    tool = FakeTool(exc=RuntimeError("boom"))
    outcome = _service(query_tool=tool).run("normal", "q")
    assert outcome.is_error
    assert "boom" in outcome.error


def test_run_empty_result_normalized():
    # ResponseBuilder 空结果：citations=[] + EMPTY_RESULT_MESSAGE 文案，
    # 归一化为「成功但无引用」（页面展示 Markdown，不报错）。
    tool = FakeTool(_ok_result(markdown="No results found.", citations=[]))
    outcome = _service(query_tool=tool).run("normal", "q")
    assert not outcome.is_error
    assert outcome.citations == []
    assert outcome.markdown == "No results found."


def test_run_unknown_mode():
    outcome = _service().run("bogus", "q")
    assert outcome.is_error
    assert "未知检索模式" in outcome.error


# --------------------------------------------------------------------- #
# 工具懒构建与会话内缓存
# --------------------------------------------------------------------- #


def test_tools_built_lazily_and_cached(monkeypatch):
    service = SearchService(settings=_settings(enabled=True, allow_deep=True))
    built: list[str] = []

    def fake_query_builder(self):
        built.append("query")
        return FakeTool()

    def fake_agentic_builder(self):
        built.append("agentic")
        return FakeTool()

    monkeypatch.setattr(SearchService, "_build_query_tool", fake_query_builder)
    monkeypatch.setattr(SearchService, "_build_agentic_tool", fake_agentic_builder)

    service.run("normal", "q1")
    service.run("normal", "q2")
    service.run("fast", "q3")
    service.run("fast", "q4")
    assert built == ["query", "agentic"]  # 各构建一次，后续复用
