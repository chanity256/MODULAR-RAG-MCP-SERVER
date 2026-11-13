"""Core 层 ``Reranker`` 编排与回退单元测试（阶段 D6）。

纯离线：注入 fake ``BaseReranker`` 后端（可控顺序/分数/异常/内部回退标记），
验证：

- 编排：``RetrievalResult → RerankCandidate → 后端 → RetrievalResult``，
  只改顺序与 score，text/metadata 保留融合载荷。
- fallback（D6 验收核心）：后端异常时返回输入原序且 ``fallback=true``；
  后端内部回退（last_fallback 契约）透传标记。
- 防御：后端返回未知 id 跳过；合法截断（top_m）被尊重。
- 空候选不触后端；NoneReranker 恒等；标记逐次重置。
- trace 打点 ``rerank`` 阶段。
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from core.query_engine.reranker import Reranker
from core.settings import load_settings
from core.trace.trace_context import TraceContext
from core.types import RetrievalResult
from libs.reranker import BaseReranker, NoneReranker, RerankCandidate

_VALID_YAML = dedent(
    """\
    llm:
      provider: azure
      model: gpt-4o
    embedding:
      provider: openai
      model: text-embedding-3-small
    vision_llm:
      provider: azure
      model: gpt-4o
    vector_store:
      backend: chroma
      persist_path: ./data/db/chroma
    retrieval:
      sparse_backend: bm25
      fusion_algorithm: rrf
    rerank:
      backend: none
    evaluation:
      backends: [custom]
    """
)


def _settings(tmp_path: Path, backend: str = "none"):
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    settings = load_settings(path)
    if backend != "none":
        from dataclasses import replace

        settings = replace(settings, rerank=replace(settings.rerank, backend=backend))
    return settings


def _result(chunk_id: str, score: float, text: str = "", metadata: dict | None = None) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id, score=score, text=text or f"text-{chunk_id}", metadata=metadata or {"page": 1}
    )


class _ScriptedBackend(BaseReranker):
    """可脚本化 fake：按构造参数重排/抛错/标记内部回退。"""

    def __init__(self, *, order: list[str] | None = None, scores: dict[str, float] | None = None,
                 error: Exception | None = None, internal_fallback: bool = False,
                 drop: bool = False):
        self.last_fallback = False
        self.calls: list[tuple[str, list[str]]] = []
        self._order = order
        self._scores = scores or {}
        self._error = error
        self._internal_fallback = internal_fallback
        self._drop = drop

    def rerank(self, query, candidates, *, trace=None, **kwargs):
        self.last_fallback = False
        self.calls.append((query, [c.id for c in candidates]))
        if self._error is not None:
            raise self._error
        by_id = {c.id: c for c in candidates}
        ordered_ids = self._order if self._order is not None else [c.id for c in candidates]
        out = []
        for cid in ordered_ids:
            candidate = by_id.get(cid)
            if candidate is None:
                # 模拟后端返回未知 id（非法输出）。
                out.append(RerankCandidate(id=cid, text="ghost", score=1.0))
                continue
            out.append(
                RerankCandidate(
                    id=candidate.id,
                    text=candidate.text,
                    score=self._scores.get(candidate.id, candidate.score),
                    metadata=candidate.metadata,
                )
            )
        if self._drop and out:
            out = out[:1]  # 模拟 top_m 截断
        if self._internal_fallback:
            self.last_fallback = True
        return out


# ---------------------------------------------------------------------------
# 编排契约
# ---------------------------------------------------------------------------


def test_reorder_updates_order_and_scores_preserves_payload(tmp_path):
    backend = _ScriptedBackend(order=["c2", "c1"], scores={"c2": 9.9, "c1": 0.1})
    reranker = Reranker(_settings(tmp_path), backend=backend)
    results = [_result("c1", 0.5, text="alpha", metadata={"page": 3}), _result("c2", 0.4, text="beta", metadata={"page": 4})]
    out = reranker.rerank("query", results)
    assert [r.chunk_id for r in out] == ["c2", "c1"]
    assert out[0].score == pytest.approx(9.9)
    assert out[0].text == "beta"  # 载荷保留自原候选
    assert out[0].metadata == {"page": 4}
    assert reranker.last_fallback is False


def test_backend_receives_candidates_with_payload(tmp_path):
    backend = _ScriptedBackend()
    reranker = Reranker(_settings(tmp_path), backend=backend)
    reranker.rerank("user query", [_result("c1", 0.5, text="payload")])
    query, ids = backend.calls[0]
    assert query == "user query"
    assert ids == ["c1"]


def test_empty_results_skips_backend(tmp_path):
    backend = _ScriptedBackend()
    reranker = Reranker(_settings(tmp_path), backend=backend)
    assert reranker.rerank("q", []) == []
    assert backend.calls == []
    assert reranker.last_fallback is False


def test_none_backend_identity(tmp_path):
    reranker = Reranker(_settings(tmp_path))  # rerank.backend=none → NoneReranker
    assert isinstance(reranker.backend, NoneReranker)
    results = [_result("c1", 0.3), _result("c2", 0.2)]
    out = reranker.rerank("q", results)
    assert out == results
    assert reranker.last_fallback is False


# ---------------------------------------------------------------------------
# fallback（D6 验收核心）
# ---------------------------------------------------------------------------


def test_backend_exception_returns_input_order_with_fallback_flag(tmp_path):
    backend = _ScriptedBackend(error=RuntimeError("cross-encoder model missing"))
    reranker = Reranker(_settings(tmp_path), backend=backend)
    results = [_result("c1", 0.5), _result("c2", 0.4), _result("c3", 0.3)]
    out = reranker.rerank("q", results)
    assert out == results  # 原序原分（等价回退到融合排名）
    assert reranker.last_fallback is True
    assert "cross-encoder model missing" in (reranker.last_error or "")


def test_backend_internal_fallback_flag_propagated(tmp_path):
    backend = _ScriptedBackend(internal_fallback=True)  # 原序返回 + last_fallback=True
    reranker = Reranker(_settings(tmp_path), backend=backend)
    results = [_result("c1", 0.5), _result("c2", 0.4)]
    out = reranker.rerank("q", results)
    assert [r.chunk_id for r in out] == ["c1", "c2"]
    assert reranker.last_fallback is True
    assert "internal fallback" in (reranker.last_error or "")


def test_fallback_flag_reset_between_calls(tmp_path):
    backend = _ScriptedBackend(error=RuntimeError("boom"))
    reranker = Reranker(_settings(tmp_path), backend=backend)
    reranker.rerank("q", [_result("c1", 0.5)])
    assert reranker.last_fallback is True
    # 换成健康后端再调一次：标记被重置。
    healthy = _ScriptedBackend(order=["c1"])
    reranker._backend = healthy
    reranker.rerank("q", [_result("c1", 0.5)])
    assert reranker.last_fallback is False
    assert reranker.last_error is None


# ---------------------------------------------------------------------------
# 防御性处理
# ---------------------------------------------------------------------------


def test_unknown_id_from_backend_skipped(tmp_path):
    backend = _ScriptedBackend(order=["ghost", "c1"])
    reranker = Reranker(_settings(tmp_path), backend=backend)
    out = reranker.rerank("q", [_result("c1", 0.5)])
    assert [r.chunk_id for r in out] == ["c1"]


def test_backend_truncation_respected(tmp_path):
    """后端合法截断（如 CrossEncoder top_m）不强行补齐。"""
    backend = _ScriptedBackend(drop=True)
    reranker = Reranker(_settings(tmp_path), backend=backend)
    out = reranker.rerank("q", [_result("c1", 0.5), _result("c2", 0.4), _result("c3", 0.3)])
    assert [r.chunk_id for r in out] == ["c1"]


def test_duplicate_id_from_backend_emitted_once(tmp_path):
    backend = _ScriptedBackend(order=["c1", "c1", "c2"])
    reranker = Reranker(_settings(tmp_path), backend=backend)
    out = reranker.rerank("q", [_result("c1", 0.5), _result("c2", 0.4)])
    assert [r.chunk_id for r in out] == ["c1", "c2"]


# ---------------------------------------------------------------------------
# fail-fast 与 trace
# ---------------------------------------------------------------------------


def test_invalid_query_fails_fast(tmp_path):
    reranker = Reranker(_settings(tmp_path))
    with pytest.raises(ValueError):
        reranker.rerank("   ", [_result("c1", 0.5)])
    with pytest.raises(TypeError):
        reranker.rerank(None, [_result("c1", 0.5)])  # type: ignore[arg-type]


def test_non_list_results_fails_fast(tmp_path):
    reranker = Reranker(_settings(tmp_path))
    with pytest.raises(TypeError):
        reranker.rerank("q", "nope")  # type: ignore[arg-type]


def test_trace_success_stage(tmp_path):
    backend = _ScriptedBackend(order=["c2", "c1"])
    reranker = Reranker(_settings(tmp_path), backend=backend)
    trace = TraceContext()
    reranker.rerank("q", [_result("c1", 0.5), _result("c2", 0.4)], trace=trace)
    stage = trace.get_stage("rerank")
    assert stage is not None
    assert stage["method"] == "reranker"
    assert stage["details"]["backend"] == "none"
    assert stage["details"]["candidates"] == 2
    assert stage["details"]["returned"] == 2
    assert stage["details"]["fallback"] is False
    assert "elapsed_ms" in stage["details"]


def test_trace_fallback_stage_carries_error(tmp_path):
    backend = _ScriptedBackend(error=RuntimeError("llm timeout"))
    reranker = Reranker(_settings(tmp_path), backend=backend)
    trace = TraceContext()
    reranker.rerank("q", [_result("c1", 0.5)], trace=trace)
    stage = trace.get_stage("rerank")
    assert stage["details"]["fallback"] is True
    assert "llm timeout" in stage["details"]["error"]


def test_backend_name_property(tmp_path):
    reranker = Reranker(_settings(tmp_path))
    assert reranker.backend_name == "none"
