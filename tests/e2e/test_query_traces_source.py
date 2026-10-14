"""WC7.4: Query Traces 页按 ``metadata.routes`` 显示来源标签并可筛选（AppTest，离线）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from observability.dashboard.services.trace_service import TraceService

_SRC_DIR = Path(__file__).resolve().parents[2] / "src"


def _trace(trace_id: str, query: str, started: str, *, routes: str | None, stages: list, **meta) -> dict:
    metadata = {"query": query, **meta}
    if routes is not None:
        metadata["routes"] = routes
    return {
        "trace_id": trace_id,
        "trace_type": "query",
        "started_at": started,
        "finished_at": started,
        "total_elapsed_ms": 50.0,
        "metadata": metadata,
        "stages": stages,
    }


_DEFAULT = _trace(
    "t-default", "default question", "2026-10-01T00:00:01+00:00", routes=None,
    stages=[{"stage": "dense_retrieval", "elapsed_ms": 5.0, "details": {"preview": []}}],
)
_AGENTIC = _trace(
    "t-agentic", "agentic question", "2026-10-01T00:00:02+00:00", routes="agentic", mode="FAST",
    stages=[{"stage": "agentic_search", "elapsed_ms": 40.0, "method": "fake", "details": {"mode": "FAST", "backend": "fake"}}],
)
_CHAT = _trace(
    "t-chat", "chat question", "2026-10-01T00:00:03+00:00", routes="chat", retrieval="hybrid", session_id="sess-1",
    stages=[
        {"stage": "chat_history", "elapsed_ms": 1.0, "messages_before": 4, "messages_after_filter": 2},
        {"stage": "chat_rewrite", "elapsed_ms": 2.0, "rewritten": True, "rewritten_query": "完整的改写查询"},
        {"stage": "dense_retrieval", "elapsed_ms": 5.0, "details": {"preview": []}},
        {
            "stage": "chat_generation", "elapsed_ms": 30.0, "method": "llm_stream", "grounded": True,
            "chunks": 3, "chars": 120, "cancelled": False, "usage": {"total_tokens": 9},
        },
    ],
)


@pytest.fixture()
def traces_path(tmp_path: Path) -> Path:
    path = tmp_path / "traces.jsonl"
    path.write_text("\n".join(json.dumps(t) for t in (_DEFAULT, _AGENTIC, _CHAT)) + "\n", encoding="utf-8")
    return path


def _run(tmp_path: Path, traces_path: Path) -> AppTest:
    driver = tmp_path / "driver_query_traces_source.py"
    driver.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(_SRC_DIR)!r})\n"
        "from observability.dashboard.pages import query_traces\n"
        "from observability.dashboard.services.trace_service import TraceService\n"
        f"query_traces.render(trace_service=TraceService(traces_path={str(traces_path)!r}))\n",
        encoding="utf-8",
    )
    at = AppTest.from_file(str(driver), default_timeout=30)
    at.run()
    return at


def _box(at: AppTest, label: str):
    return next(box for box in at.selectbox if box.label == label)


# --------------------------------------------------------------------------- service
class TestTraceServiceSource:
    def test_trace_source_label(self):
        assert TraceService.trace_source(_CHAT) == "chat"
        assert TraceService.trace_source(_AGENTIC) == "agentic"
        assert TraceService.trace_source(_DEFAULT) == "default"
        assert TraceService.trace_source({"metadata": {"routes": "  "}}) == "default"
        assert TraceService.trace_source({"metadata": {"routes": 5}}) == "default"
        assert TraceService.trace_source({}) == "default"

    def test_search_filters_by_source_and_keyword(self, traces_path):
        service = TraceService(traces_path)
        assert [t["trace_id"] for t in service.search_query_traces("")] == ["t-chat", "t-agentic", "t-default"]
        assert [t["trace_id"] for t in service.search_query_traces("", source="chat")] == ["t-chat"]
        assert [t["trace_id"] for t in service.search_query_traces("", source="default")] == ["t-default"]
        assert service.search_query_traces("agentic", source="chat") == []
        assert [t["trace_id"] for t in service.search_query_traces("QUESTION", source="agentic")] == ["t-agentic"]
        assert service.search_query_traces("", source=None) == service.search_query_traces("")

    def test_query_sources_lists_known_plus_unexpected(self, tmp_path):
        service = TraceService(tmp_path / "missing.jsonl")
        assert service.query_sources() == ["default", "agentic", "chat"]
        path = tmp_path / "t.jsonl"
        path.write_text(json.dumps(_trace("x", "q", "2026", routes="custom", stages=[])) + "\n", encoding="utf-8")
        assert TraceService(path).query_sources() == ["default", "agentic", "chat", "custom"]


# --------------------------------------------------------------------------- page
class TestPage:
    def test_default_shows_all_with_source_prefixes_and_filter_options(self, tmp_path, traces_path):
        at = _run(tmp_path, traces_path)
        assert not at.exception
        assert _box(at, "来源").options == ["全部", "default", "agentic", "chat"]
        assert _box(at, "查询记录").options == [
            "[chat] chat question · 50.0 ms",
            "[agentic] agentic question · 50.0 ms",
            "default question · 50.0 ms",  # 默认来源保持原标签，不加前缀
        ]

    def test_selecting_chat_filters_and_renders_chat_stages(self, tmp_path, traces_path):
        at = _run(tmp_path, traces_path)
        _box(at, "来源").select("chat").run()

        assert not at.exception
        assert _box(at, "查询记录").options == ["[chat] chat question · 50.0 ms"]
        assert "Chat 阶段" in [h.value for h in at.subheader]
        metrics = {m.label: m.value for m in at.metric}
        assert metrics["历史消息"] == "4 → 2" and metrics["查询改写"] == "是"
        assert metrics["引用片段"] == "3" and metrics["回答字数"] == "120"
        captions = [c.value for c in at.caption]
        assert any("来源=`chat`" in c and "retrieval=`hybrid`" in c and "session_id=`sess-1`" in c for c in captions)
        assert any("method=llm_stream" in c and "grounded=True" in c for c in captions)
        assert any("完整的改写查询" in c for c in captions)

    def test_selecting_agentic_and_default(self, tmp_path, traces_path):
        at = _run(tmp_path, traces_path)
        _box(at, "来源").select("agentic").run()
        assert _box(at, "查询记录").options == ["[agentic] agentic question · 50.0 ms"]
        subheaders = [h.value for h in at.subheader]
        assert "Agentic 检索" in subheaders and "Chat 阶段" not in subheaders

        _box(at, "来源").select("default").run()
        assert _box(at, "查询记录").options == ["default question · 50.0 ms"]
        subheaders = [h.value for h in at.subheader]
        assert "Chat 阶段" not in subheaders and "Agentic 检索" not in subheaders
        assert not any("来源=" in c.value for c in at.caption)

    def test_empty_filter_result_shows_info_instead_of_crashing(self, tmp_path):
        path = tmp_path / "only_default.jsonl"
        path.write_text(json.dumps(_DEFAULT) + "\n", encoding="utf-8")
        at = _run(tmp_path, path)
        _box(at, "来源").select("chat").run()
        assert not at.exception
        assert any("没有符合当前筛选条件" in i.value for i in at.info)

    def test_chat_waterfall_includes_chat_and_agentic_stages(self, tmp_path):
        chat_agentic = _trace(
            "t-ca", "chat agentic", "2026-10-01T00:00:04+00:00", routes="chat", retrieval="agentic_fast",
            stages=[
                {"stage": "chat_history", "elapsed_ms": 1.0, "messages_before": 0, "messages_after_filter": 0},
                {"stage": "agentic_search", "elapsed_ms": 80.0, "method": "fake", "details": {"mode": "FAST"}},
                {"stage": "chat_generation", "elapsed_ms": 2.0, "method": "sidecar_passthrough", "grounded": True,
                 "chunks": 1, "chars": 10},
            ],
        )
        path = tmp_path / "ca.jsonl"
        path.write_text(json.dumps(chat_agentic) + "\n", encoding="utf-8")
        at = _run(tmp_path, path)
        assert not at.exception
        assert "耗时瀑布图" in [h.value for h in at.subheader]
        assert "Chat 阶段" in [h.value for h in at.subheader]
