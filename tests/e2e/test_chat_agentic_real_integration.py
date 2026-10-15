"""Chat agentic 路由经真实 sidecar 的冒烟（WEBUI_CHAT_SPEC §6.2 / WC10.3，门控）。

⚠️ 会产生真实 sidecar / LLM 费用，默认跳过；需先按 S0.1 runbook 启动 sidecar 后运行::

    RAG_AGENTIC_INTEGRATION=1 pytest tests/e2e/test_chat_agentic_real_integration.py -v

前置：``config/settings.yaml`` 中 ``agentic_search.enabled: true`` 且 ``base_url`` 指向存活 sidecar
（``SIRCHMUNK_BASE_URL`` 可覆盖探活地址）；``allowed_roots`` 下有可检索文档。
首轮问题可用 ``RAG_CHAT_AGENTIC_QUESTION`` 覆盖。会话库与 trace 落 tmp。
断言只做结构性检查（LLM 非确定性）。
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("RAG_AGENTIC_INTEGRATION") != "1",
        reason="需真实 sirchmunk sidecar 与 LLM API 费用：设置 RAG_AGENTIC_INTEGRATION=1 启用",
    ),
]

_QUESTION = os.environ.get("RAG_CHAT_AGENTIC_QUESTION", "这些文档主要讲了什么？")


def _require_sidecar(base_url: str) -> None:
    url = os.environ.get("SIRCHMUNK_BASE_URL", base_url).rstrip("/")
    try:
        httpx.get(f"{url}/api/v1/search/status", timeout=5)
    except httpx.HTTPError as exc:
        pytest.skip(f"sidecar 不可达（{url}）：{type(exc).__name__}；先启动 sidecar")


def _read_traces(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture()
def build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """返回 ``build(**chat_overrides / allow_deep) -> (engine, store, trace_path, settings)``。"""
    monkeypatch.chdir(PROJECT_ROOT)
    from core.chat import ChatEngine, SessionStore
    from core.settings import load_settings
    from core.trace.persist import persist_trace
    from observability.dashboard.services.chat_retrievers import build_chat_retrievers

    import libs.llm  # noqa: F401  # 触发 provider 注册
    from libs.llm.llm_factory import LLMFactory

    base = load_settings(str(PROJECT_ROOT / "config" / "settings.yaml"))
    if not base.agentic_search.enabled:
        pytest.skip("config/settings.yaml 中 agentic_search.enabled=false")

    stores: list[Any] = []

    def _build(*, allow_deep: bool | None = None, **chat_overrides: Any):
        agentic = base.agentic_search
        if allow_deep is not None:
            agentic = replace(agentic, allow_deep=allow_deep)
        settings = replace(base, agentic_search=agentic, chat=replace(base.chat, **chat_overrides))
        store = SessionStore(tmp_path / f"chat_{len(stores)}.db")
        stores.append(store)
        trace_path = tmp_path / f"traces_{len(stores)}.jsonl"
        engine = ChatEngine(
            retrievers=build_chat_retrievers(settings),
            llm=LLMFactory.create(settings),
            session_store=store,
            settings=settings.chat,
            persist_trace=partial(persist_trace, log_file=str(trace_path)),
        )
        return engine, store, trace_path, settings

    yield _build
    for store in stores:
        store.close()


def test_agentic_fast_passthrough(build):
    engine, store, trace_path, settings = build()
    _require_sidecar(settings.agentic_search.base_url)

    frames = list(engine.run_turn(_QUESTION, retrieval="agentic_fast"))
    errors = [f for f in frames if f["type"] == "error"]
    assert not errors, f"turn failed: {errors}"

    types = [f["type"] for f in frames]
    # sidecar 答案整段透传：恰好一个 stream 帧，不再经本地 LLM 生成。
    assert types.count("stream") == 1, types
    result = frames[-1]
    assert result["type"] == "result"
    assert result["content"].strip()
    assert result["content"] == next(f["content"] for f in frames if f["type"] == "stream")
    sources = next(f for f in frames if f["type"] == "sources")
    assert sources["citations"], "agentic answer should carry citations"

    traces = _read_traces(trace_path)
    assert len(traces) == 1
    assert traces[0]["metadata"]["routes"] == "chat"
    assert traces[0]["metadata"]["retrieval"] == "agentic_fast"
    generation = next(s for s in traces[0]["stages"] if s.get("stage") == "chat_generation")
    assert generation["method"] == "sidecar_passthrough"

    saved = store.get_messages(result["session_id"])
    assert [m["role"] for m in saved] == ["user", "assistant"]


def test_agentic_deep_rejected_when_not_allowed(build):
    engine, store, trace_path, _ = build(allow_deep=False)

    frames = list(engine.run_turn(_QUESTION, retrieval="agentic_deep"))
    assert frames[-1]["type"] == "error"
    assert frames[-1]["code"] == "DEEP_DISABLED"
    assert not any(f["type"] == "stream" for f in frames)
    assert len(_read_traces(trace_path)) == 1


def test_agentic_deep_when_allowed(build):
    engine, _, trace_path, settings = build()
    if not settings.agentic_search.allow_deep:
        pytest.skip("settings 中 agentic_search.allow_deep=false，DEEP 真实路径不测")
    _require_sidecar(settings.agentic_search.base_url)

    frames = list(engine.run_turn(_QUESTION, retrieval="agentic_deep"))
    errors = [f for f in frames if f["type"] == "error"]
    assert not errors, f"turn failed: {errors}"
    assert frames[-1]["type"] == "result"
    assert frames[-1]["content"].strip()
    traces = _read_traces(trace_path)
    assert len(traces) == 1
    assert traces[0]["metadata"]["retrieval"] == "agentic_deep"


def test_agentic_progress_stream_emits_search_log(build):
    # 已知：SX5.3 流式检索在工作线程里跑，取消/提前结束时线程会遗留（daemon，结果被丢弃）；
    # 本用例跑完整轮次，不触发取消，线程正常退出。
    engine, _, _, settings = build(agentic_progress_stream=True)
    _require_sidecar(settings.agentic_search.base_url)

    frames = list(engine.run_turn(_QUESTION, retrieval="agentic_fast"))
    errors = [f for f in frames if f["type"] == "error"]
    assert not errors, f"turn failed: {errors}"

    logs = [f for f in frames if f["type"] == "search_log"]
    assert logs, "progress stream should relay at least one search_log frame"
    for frame in logs:
        assert frame["message"]
        assert frame["task_id"]
    first_stream = next(i for i, f in enumerate(frames) if f["type"] == "stream")
    assert all(frames.index(f) < first_stream for f in logs)
    assert frames[-1]["type"] == "result"
