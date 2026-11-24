"""PageIndex 树检索真实 sidecar 冒烟（PAGEINDEX_TREE_RETRIEVAL_SPEC §4 / PI7，门控）。

⚠️ 会产生真实 sidecar / LLM 费用（树检索为多轮 LLM 调用，单题十秒到数十秒级），
默认跳过；``RAG_PAGEINDEX_INTEGRATION=1`` 启用::

    RAG_PAGEINDEX_INTEGRATION=1 pytest tests/e2e/test_pageindex_integration.py -v -s

前置：sidecar 已按 USAGE_GUIDE 第 12 章启动（``bash scripts/pageindex/start_sidecar.sh``）
且已用 ``scripts/pageindex_sync.py`` 同步索引；``config/settings.yaml`` 中
``tree_search.enabled: true`` 且 ``PAGEINDEX_API_TOKEN`` 已导出；Chat 用例另需 settings
中的 chat LLM 可用。凭据缺失或 sidecar 不可达时跳过（不 fail）。断言只做结构性检查
（树检索选节点与答案生成均非确定性），延迟与 token 经 print 观测记录。
"""

from __future__ import annotations

import json
import os
import time
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
        os.environ.get("RAG_PAGEINDEX_INTEGRATION") != "1",
        reason="需真实 PageIndex sidecar 与 LLM API 费用：设置 RAG_PAGEINDEX_INTEGRATION=1 启用",
    ),
]

_QUESTION = os.environ.get("RAG_CHAT_PAGEINDEX_QUESTION", "这些文档主要讲了什么？")


def _real_settings():
    """按真实配置装配；tree_search 未启用或 token 缺失时 skip。"""
    from core.settings import load_settings

    settings = load_settings(str(PROJECT_ROOT / "config" / "settings.yaml"))
    if not settings.tree_search.enabled:
        pytest.skip("config/settings.yaml 中 tree_search.enabled=false")
    token = settings.tree_search.api_token
    if not token or token.startswith("${"):
        pytest.skip("缺少 PAGEINDEX_API_TOKEN（未导出或仍为占位符）")
    return settings


def _headers(settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.tree_search.api_token}"}


def _base_url(settings) -> str:
    return settings.tree_search.base_url.rstrip("/")


def _require_sidecar(settings) -> dict[str, Any]:
    """探活 /status 并返回其 data；不可达时 skip。"""
    try:
        resp = httpx.get(
            f"{_base_url(settings)}/api/v1/status",
            headers=_headers(settings),
            timeout=5,
        )
    except httpx.HTTPError as exc:
        pytest.skip(f"sidecar 不可达（{_base_url(settings)}）：{type(exc).__name__}；先启动 sidecar")
    resp.raise_for_status()
    body = resp.json()
    assert body.get("success") is True, body
    return body["data"]


def _indexed_docs(settings) -> list[dict[str, Any]]:
    resp = httpx.get(
        f"{_base_url(settings)}/api/v1/documents",
        headers=_headers(settings),
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()
    assert body.get("success") is True, body
    return body["data"]


def _pick_doc(docs: list[dict[str, Any]], fmt: str) -> dict[str, Any]:
    matched = [d for d in docs if d.get("format") == fmt]
    if not matched:
        pytest.skip(f"sidecar 中没有 format={fmt} 的已索引文档；先运行 scripts/pageindex_sync.py")
    return matched[0]


def _search(settings, query: str, source_paths: list[str]):
    from libs.tree_search.base_tree_search import TreeSearchRequest
    from libs.tree_search.http_backend import PageIndexHttpBackend

    backend = PageIndexHttpBackend(settings.tree_search)
    started = time.perf_counter()
    response = backend.search(TreeSearchRequest(query=query, source_paths=source_paths))
    elapsed_ms = (time.perf_counter() - started) * 1000
    return response, elapsed_ms


def test_real_sidecar_status():
    """真实 sidecar /status 返回版本与文档计数。"""
    settings = _real_settings()
    data = _require_sidecar(settings)
    assert data.get("pageindex_version"), data
    assert isinstance(data.get("documents"), int)
    print(f"[status] pageindex={data['pageindex_version']} documents={data['documents']}")


def test_search_md_evidence_has_section_and_lines():
    """对已索引 MD 检索：证据回映射原 MD 且带「章节 · 行号」（D12 / 验收清单）。"""
    settings = _real_settings()
    _require_sidecar(settings)
    doc = _pick_doc(_indexed_docs(settings), "md")
    source_path = doc["source_path"]

    response, elapsed_ms = _search(settings, _QUESTION, [source_path])
    assert response.evidences, "树检索应返回至少一条证据"
    # D12：MD 证据的 source_path 必须是原 MD，绝不为派生 PDF（data/db 下）路径。
    for ev in response.evidences:
        assert ev.source_path == source_path, ev.source_path
        assert "/derived/" not in ev.source_path
    located = [ev for ev in response.evidences if ev.section and ev.line_start is not None]
    assert located, f"MD 证据应带章节与行号：{[(e.section, e.line_start) for e in response.evidences]}"
    print(
        f"[md] evidences={len(response.evidences)} elapsed={elapsed_ms:.0f}ms "
        f"llm_tokens={response.total_llm_tokens} calls={response.llm_call_count}"
    )


def test_search_pdf_evidence_has_pages():
    """对已索引 PDF 检索：证据带页码（D8：页码只出现在原生 PDF 证据上）。"""
    settings = _real_settings()
    _require_sidecar(settings)
    doc = _pick_doc(_indexed_docs(settings), "pdf")
    source_path = doc["source_path"]

    response, elapsed_ms = _search(settings, _QUESTION, [source_path])
    assert response.evidences, "树检索应返回至少一条证据"
    for ev in response.evidences:
        assert ev.source_path == source_path, ev.source_path
    assert any(ev.page_start is not None for ev in response.evidences), (
        f"PDF 证据应带页码：{[(e.page_start, e.page_end) for e in response.evidences]}"
    )
    print(
        f"[pdf] evidences={len(response.evidences)} elapsed={elapsed_ms:.0f}ms "
        f"llm_tokens={response.total_llm_tokens} calls={response.llm_call_count}"
    )


def test_chat_longdoc_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Chat longdoc 模式端到端：出答案与引用，trace 记 tree_search 阶段且不泄漏 token。"""
    monkeypatch.chdir(PROJECT_ROOT)
    from core.chat import ChatEngine, SessionStore
    from core.trace.persist import persist_trace
    from observability.dashboard.services.chat_retrievers import build_chat_retrievers

    import libs.llm  # noqa: F401  # 触发 provider 注册
    from libs.llm.llm_factory import LLMFactory

    settings = _real_settings()
    _require_sidecar(settings)

    # settings.yaml 的 chat LLM 不可用时，用环境变量临时覆盖（先例：
    # test_chat_real_integration.py 的 RAG_CHAT_LLM_PROVIDER / _MODEL / _BASE_URL / _API_KEY）。
    llm_provider = os.environ.get("RAG_CHAT_LLM_PROVIDER")
    if llm_provider:
        settings = replace(
            settings,
            llm=replace(
                settings.llm,
                provider=llm_provider,
                model=os.environ.get("RAG_CHAT_LLM_MODEL", settings.llm.model),
                base_url=os.environ.get("RAG_CHAT_LLM_BASE_URL") or settings.llm.base_url,
                api_key=os.environ.get("RAG_CHAT_LLM_API_KEY") or settings.llm.api_key,
                azure_endpoint=None,
            ),
        )

    store = SessionStore(tmp_path / "chat.db")
    trace_path = tmp_path / "traces.jsonl"
    engine = ChatEngine(
        retrievers=build_chat_retrievers(settings),
        llm=LLMFactory.create(settings),
        session_store=store,
        settings=settings.chat,
        persist_trace=partial(persist_trace, log_file=str(trace_path)),
    )
    try:
        frames = list(engine.run_turn(_QUESTION, retrieval="longdoc"))
    finally:
        store.close()

    errors = [f for f in frames if f["type"] == "error"]
    assert not errors, f"turn failed: {errors}"
    result = frames[-1]
    assert result["type"] == "result"
    assert result["content"].strip()
    sources = next(f for f in frames if f["type"] == "sources")
    assert sources["citations"], "longdoc 轮次应携带引用"
    located = [
        c
        for c in sources["citations"]
        if c.get("section") or c.get("line_start") is not None or c.get("page") is not None
    ]
    assert located, f"引用应带章节 / 行号 / 页码：{sources['citations']}"

    traces = _read_traces(trace_path)
    assert len(traces) == 1
    assert traces[0]["metadata"]["retrieval"] == "longdoc"
    stage = next(s for s in traces[0]["stages"] if s.get("stage") == "tree_search")
    assert "api_token" not in json.dumps(stage)
    assert "base_url" not in json.dumps(stage)
    print(f"[chat] citations={len(sources['citations'])} located={len(located)}")


def _read_traces(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
