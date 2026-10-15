"""Chat 真实 LLM 多轮冒烟（WEBUI_CHAT_SPEC §6.2 / WC10.3，门控）。

⚠️ 会产生真实 LLM / embedding 调用与费用，默认跳过；``RAG_CHAT_INTEGRATION=1`` 启用::

    RAG_CHAT_INTEGRATION=1 pytest tests/e2e/test_chat_real_integration.py -v

前置：``config/settings.yaml`` 的 LLM / embedding 可用，且本地索引（``data/db``）非空。
测试题目由索引内某篇文档的标题动态生成（可用环境变量覆盖，见 ``_pick_topic``）；
会话库与 trace 文件落在 tmp，不污染 ``data/db/chat_sessions.db`` 与 ``logs/traces.jsonl``。

覆盖：流式端到端、指代消解（改写生效）、话题漂移（历史被丢弃）、会话重启恢复。
断言只做结构性检查（LLM 非确定性），不比对具体文本。
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("RAG_CHAT_INTEGRATION") != "1",
        reason="需真实 LLM/embedding API 费用：设置 RAG_CHAT_INTEGRATION=1 启用",
    ),
]

_FRAME_RANK = {
    "session": 0,
    "status": 1,
    "search_complete": 2,
    "sources": 3,
    "stream": 4,
    "result": 5,
}
_DRIFT_QUESTION = "请问番茄炒蛋应该怎么做？"


def _pick_topic(settings: Any) -> tuple[str, str | None]:
    """返回 (第一轮问题, collection)。

    ``RAG_CHAT_QUESTION`` / ``RAG_CHAT_COLLECTION`` 可显式覆盖；否则选一篇标题像样的文档，
    问「《标题》的主要内容是什么」，保证 hybrid 检索必有命中。
    """
    from observability.dashboard.services.data_service import DataService

    collection = os.environ.get("RAG_CHAT_COLLECTION") or None
    question = os.environ.get("RAG_CHAT_QUESTION")
    if question:
        return question, collection
    docs = [
        d
        for d in DataService(settings=settings).list_documents()
        if (collection is None or d.collection == collection)
        and d.chunk_count >= 2
        and d.title
        and len(d.title.strip()) >= 4
        and "|" not in d.title
    ]
    if not docs:
        pytest.skip("本地索引中没有可用文档（需先摄取）；或用 RAG_CHAT_QUESTION 指定首轮问题")
    doc = max(docs, key=lambda d: d.chunk_count)
    return f"请介绍一下《{doc.title.strip()}》的主要内容。", doc.collection


def _run(engine: Any, message: str, **kwargs: Any) -> list[dict[str, Any]]:
    return list(engine.run_turn(message, **kwargs))


def _assert_ok(frames: list[dict[str, Any]]) -> dict[str, Any]:
    errors = [f for f in frames if f["type"] == "error"]
    assert not errors, f"turn failed: {errors}"
    assert frames[-1]["type"] == "result"
    return frames[-1]


def _read_traces(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _stage(trace: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [s for s in trace["stages"] if s.get("stage") == name]
    assert matches, f"stage {name!r} missing; got {[s.get('stage') for s in trace['stages']]}"
    return matches[0]


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory):
    mp = pytest.MonkeyPatch()
    mp.chdir(PROJECT_ROOT)  # settings 中的 ./data 等相对路径以仓库根为基准
    try:
        from core.chat import ChatEngine, SessionStore
        from core.settings import load_settings
        from core.trace.persist import persist_trace
        from observability.dashboard.services.chat_retrievers import build_chat_retrievers

        import libs.llm  # noqa: F401  # 触发 provider 注册
        from libs.llm.llm_factory import LLMFactory

        base = load_settings(str(PROJECT_ROOT / "config" / "settings.yaml"))
        # 只测 hybrid 路径：关掉 agentic，避免装配 sidecar 工具。
        settings = replace(base, agentic_search=replace(base.agentic_search, enabled=False))
        question, collection = _pick_topic(settings)

        tmp = tmp_path_factory.mktemp("chat_real")
        db_path = tmp / "chat_sessions.db"
        trace_path = tmp / "traces.jsonl"
        retrievers = build_chat_retrievers(settings)

        def make_engine(store: SessionStore) -> ChatEngine:
            return ChatEngine(
                retrievers=retrievers,
                llm=LLMFactory.create(settings),
                session_store=store,
                settings=settings.chat,
                persist_trace=partial(persist_trace, log_file=str(trace_path)),
            )

        yield {
            "make_engine": make_engine,
            "SessionStore": SessionStore,
            "db_path": db_path,
            "trace_path": trace_path,
            "question": question,
            "collection": collection,
            "store": SessionStore(db_path),
        }
    finally:
        mp.undo()


@pytest.fixture(scope="module")
def conversation(env):
    """三轮对话只跑一次：首轮（流式）、指代追问、跑题。"""
    engine = env["make_engine"](env["store"])
    kw = {"retrieval": "hybrid", "collection": env["collection"]}

    turn1 = _run(engine, env["question"], **kw)
    result1 = _assert_ok(turn1)
    session_id = result1["session_id"]

    follow_up = "它的核心要点有哪些？"
    turn2 = _run(engine, follow_up, session_id=session_id, **kw)
    turn3 = _run(engine, _DRIFT_QUESTION, session_id=session_id, **kw)
    return {
        "session_id": session_id,
        "turn1": turn1,
        "turn2": turn2,
        "turn3": turn3,
        "follow_up": follow_up,
        "traces": _read_traces(env["trace_path"]),
    }


def test_streaming_end_to_end(env, conversation):
    frames = conversation["turn1"]
    types = [f["type"] for f in frames]
    assert types[0] == "session"
    assert types.count("stream") >= 2, f"expected multiple stream frames, got {types}"

    ranks = [_FRAME_RANK[t] for t in types]
    assert ranks == sorted(ranks), f"frames out of order: {types}"
    assert "sources" in types and "search_complete" in types

    result = frames[-1]
    assert result["type"] == "result"
    assert result["grounded"] is True
    assert result["content"].strip()
    assert result["content"] == "".join(f["content"] for f in frames if f["type"] == "stream")
    sources = next(f for f in frames if f["type"] == "sources")
    assert sources["citations"], "grounded answer must carry citations"

    traces = [t for t in conversation["traces"] if t["trace_id"] == result["trace_id"]]
    assert len(traces) == 1
    assert traces[0]["metadata"]["routes"] == "chat"
    assert traces[0]["metadata"]["session_id"] == conversation["session_id"]
    # 每轮恰好一条 chat trace。
    assert len(conversation["traces"]) == 3


def test_reference_resolution_rewrites_followup(conversation):
    result = _assert_ok(conversation["turn2"])
    rewritten = result.get("rewritten_query")
    assert rewritten, "follow-up with a pronoun should be rewritten"
    assert rewritten.strip() != conversation["follow_up"].strip()

    trace = next(t for t in conversation["traces"] if t["trace_id"] == result["trace_id"])
    rewrite = _stage(trace, "chat_rewrite")
    assert rewrite["called"] is True and rewrite["rewritten"] is True
    assert any(f["type"] == "status" and f.get("stage") == "rewriting" for f in conversation["turn2"])


def test_topic_drift_drops_history(conversation):
    result = _assert_ok(conversation["turn3"])
    trace = next(t for t in conversation["traces"] if t["trace_id"] == result["trace_id"])
    history = _stage(trace, "chat_history")
    assert history["messages_after_trim"] >= 4
    assert history["filter_called"] is True
    assert history["filter_failed"] is False
    assert history["messages_after_filter"] < history["messages_after_trim"], (
        f"off-topic question should drop history: {history}"
    )
    assert history["turns_dropped"] >= 1


def test_session_restart_recovery(env, conversation):
    session_id = conversation["session_id"]
    before = env["store"].get_messages(session_id)
    assert len(before) == 6  # 3 轮 × (user + assistant)
    env["store"].close()

    reopened = env["SessionStore"](env["db_path"])
    try:
        restored = reopened.get_messages(session_id)
        assert [(m["role"], m["content"]) for m in restored] == [(m["role"], m["content"]) for m in before]
        assert restored[-1]["role"] == "assistant"

        engine = env["make_engine"](reopened)
        frames = _run(
            engine,
            "能再用一句话概括一下吗？",
            session_id=session_id,
            retrieval="hybrid",
            collection=env["collection"],
        )
        result = _assert_ok(frames)
        assert result["session_id"] == session_id
        assert len(reopened.get_messages(session_id)) == 8
    finally:
        reopened.close()
