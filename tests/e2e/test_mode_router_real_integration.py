"""Mode router 真实端点冒烟（MODE_ROUTER_SPEC §4 MR5，门控）。

⚠️ 会产生真实决策模型调用与费用（Cloudflare Workers AI，计费按输入 token），默认跳过；
``RAG_MODE_ROUTER_INTEGRATION=1`` 启用::

    RAG_MODE_ROUTER_INTEGRATION=1 pytest tests/e2e/test_mode_router_real_integration.py -v

前置：``CLOUDFLARE_ACCOUNT_ID`` 与 ``MODE_ROUTER_API_TOKEN`` 已导出（Workers AI API token）。
凭据缺失时跳过（不 fail）。决策质量基线另有 ``scripts/eval_mode_router.py``（不进 pytest）。

覆盖：
1. 真实端点对三类典型问题（每档一题，取自标注集）给出候选内的合法决策；
2. ``base_url`` 覆盖为不可达地址后，经真实 settings 装配的 auto 轮次回退
   ``fallback_retrieval``，result 帧与 trace ``mode_routing`` 阶段均记 ``unreachable``。
"""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import replace
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("RAG_MODE_ROUTER_INTEGRATION") != "1",
        reason="需真实决策模型费用：设置 RAG_MODE_ROUTER_INTEGRATION=1 启用",
    ),
]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LABELED_SET = _PROJECT_ROOT / "tests" / "fixtures" / "mode_router" / "labeled_set.jsonl"


def _real_router(settings):
    """按真实配置建路由器；凭据缺失（未解析占位符）时 skip。"""
    from core.settings import load_settings

    if settings is None:
        settings = load_settings(_PROJECT_ROOT / "config" / "settings.yaml")
    enabled = replace(settings, mode_router=replace(settings.mode_router, enabled=True))
    # 工厂不复检凭据；未导出环境变量时 ${...} 保持占位符原值，在此显式 skip。
    for field in ("account_id", "api_token"):
        value = getattr(enabled.mode_router, field, None)
        if not value or (isinstance(value, str) and value.startswith("${")):
            pytest.skip(f"缺少 mode router 凭据：{field} 未设置")
    try:
        from libs.mode_router import create_mode_router

        router = create_mode_router(enabled)
    except Exception as exc:  # ConfigError：后端未配置等
        pytest.skip(f"mode router 配置不可用：{exc}")
    assert router is not None
    return router, enabled


def _typical_questions() -> dict[str, str]:
    """从标注集每档取一题（顺序第一个），键为期望模式、值为问题文本。"""
    picked: dict[str, str] = {}
    for line in _LABELED_SET.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        picked.setdefault(item["expected_mode"], item["query"])
        if len(picked) == 3:
            break
    assert len(picked) == 3, "标注集应覆盖三档模式"
    return picked


def test_real_endpoint_gives_legal_decisions():
    """真实端点对三类典型问题给出候选内的合法决策（含延迟观测）。"""
    router, _ = _real_router(None)
    candidates = ("hybrid", "agentic_fast", "agentic_deep")
    try:
        for expected, query in _typical_questions().items():
            started = time.perf_counter()
            decision = router.decide(query, candidates)
            elapsed_ms = (time.perf_counter() - started) * 1000
            assert decision.mode in candidates, f"非法决策：{decision.mode!r}"
            print(f"[expected={expected}] decided={decision.mode} conf={decision.confidence} {elapsed_ms:.0f} ms")
    finally:
        router.close()


def test_unreachable_base_url_falls_back_with_trace(tmp_path):
    """settings 装配路径：base_url 指向不可达地址 → 回退 fallback_retrieval，trace 记 unreachable。"""
    from core.chat import SessionStore
    from core.types import RetrievalResult

    class _FakeLLM:
        last_usage = None

        def chat(self, messages, *, trace=None, **kwargs):
            from libs.llm.base_llm import ChatResponse

            return ChatResponse(content="[1]")

        def chat_stream(self, messages, *, trace=None, **kwargs):
            yield "答案 [1]"

    class _FakeRetriever:
        def __init__(self, name):
            self.name = name

        def retrieve(self, query, *, collection=None, top_k=None, trace=None, on_log=None):
            from core.chat.retriever import ChatRetrievalResult

            return ChatRetrievalResult(
                results=[RetrievalResult("c1", 0.9, "文本 [1]", {"source_path": "d.md", "page": 1})]
            )

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        dead_port = sock.getsockname()[1]

    from core.chat.retriever import RETRIEVAL_AGENTIC_FAST, RETRIEVAL_HYBRID
    from api.chat_wiring import ChatServices
    from core.settings import load_settings

    settings = load_settings(_PROJECT_ROOT / "config" / "settings.yaml")
    enabled = replace(settings, mode_router=replace(settings.mode_router, enabled=True))
    # 真实装配路径（create_mode_router），仅端点换成不可达地址。工厂不复检凭据，
    # 请求也根本不会发出，凭据是否就绪与本用例无关。
    dead_mode_router = replace(enabled.mode_router, base_url=f"http://127.0.0.1:{dead_port}")
    unreachable = replace(
        enabled,
        mode_router=dead_mode_router,
        observability=replace(enabled.observability, log_file=str(tmp_path / "traces.jsonl")),
    )
    services = ChatServices(
        unreachable,
        session_store=SessionStore(tmp_path / "chat.db"),
        retrievers={
            RETRIEVAL_HYBRID: _FakeRetriever(RETRIEVAL_HYBRID),
            RETRIEVAL_AGENTIC_FAST: _FakeRetriever(RETRIEVAL_AGENTIC_FAST),
        },
        llm_factory=lambda _s: _FakeLLM(),
    )
    engine = services.new_engine()
    frames = list(engine.run_turn("什么是 RAG？", retrieval="auto"))

    result = frames[-1]
    assert result["type"] == "result"
    assert result["retrieval"] == enabled.mode_router.fallback_retrieval
    assert result["routing"]["fallback_reason"] == "unreachable"
