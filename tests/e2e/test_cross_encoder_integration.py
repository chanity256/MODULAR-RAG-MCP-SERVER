"""Cross-Encoder 真实模型冒烟（CROSS_ENCODER_RERANKER_SPEC §4 / CER4，门控）。

⚠️ 首次运行会从 Hugging Face 下载模型（约 90 MB，之后离线缓存）并执行真实
CPU 推理；默认跳过，``RAG_RERANKER_INTEGRATION=1`` 启用::

    RAG_RERANKER_INTEGRATION=1 pytest tests/e2e/test_cross_encoder_integration.py -v -s

前置：``pip install -e ".[rerank]"`` 已安装（sentence-transformers + torch）。
断言只做确定性检查（默认英文模型 ``ms-marco-MiniLM-L-6-v2`` 的英文排序
sanity + 端到端 ``fallback=false``）；模型加载与精排延迟经 print 观测，
记录回规格 §5 进度备注。依赖未安装时 skip（不 fail）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("RAG_RERANKER_INTEGRATION") != "1",
        reason="需真实 sentence-transformers 模型（下载/推理开销）：设置 RAG_RERANKER_INTEGRATION=1 启用",
    ),
]

# 英文 sanity 样例（默认模型为英文词表，见 spec §3.4 风险 6；不强制断言中文）。
_QUERY = "What is the capital of France?"
_RELATED = "Paris is the capital of France and its largest city."
_RELATED_ALT = "The French government is seated in Paris."
_UNRELATED = "Quantum computers use qubits to perform parallel computations."


def _require_sentence_transformers() -> None:
    """可选依赖未安装时 skip（与未装依赖回退语义一致，不 fail）。"""
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        pytest.skip("sentence-transformers 未安装（pip install -e \".[rerank]\"）")


def _real_settings():
    """按真实配置装配（真配置 tmp 化：rerank 节即交付形态，不改值）。"""
    from core.settings import load_settings

    return load_settings(str(PROJECT_ROOT / "config" / "settings.yaml"))


def test_real_model_load_and_cache():
    """真模型加载成功且进程内缓存（二次 ensure_loaded 不重复构建）。"""
    _require_sentence_transformers()
    from libs.reranker.cross_encoder_scorer import CrossEncoderScorer

    settings = _real_settings()
    assert settings.rerank.backend == "cross_encoder"
    scorer = CrossEncoderScorer(settings.rerank.model)

    started = time.perf_counter()
    model = scorer.ensure_loaded()
    load_s = time.perf_counter() - started
    assert model is not None and hasattr(model, "predict")
    print(f"\n[model load] {settings.rerank.model} first load: {load_s:.2f}s")

    # 缓存命中：二次调用返回同一对象（无重复加载）。
    assert scorer.ensure_loaded() is model
    print(f"[model load] second ensure_loaded: cached (same object)")


def test_english_relevance_sanity():
    """英文相关性排序 sanity：相关文本得分 > 无关文本。"""
    _require_sentence_transformers()
    from libs.reranker.cross_encoder_scorer import CrossEncoderScorer

    settings = _real_settings()
    scorer = CrossEncoderScorer(settings.rerank.model)
    scores = scorer(_QUERY, [_RELATED, _RELATED_ALT, _UNRELATED])
    print(f"\n[sanity] scores: {[round(s, 4) for s in scores]}")
    assert scores[0] > scores[2], f"related {_RELATED!r} must outrank unrelated: {scores}"
    assert scores[1] > scores[2], f"related {_RELATED_ALT!r} must outrank unrelated: {scores}"


def test_end_to_end_rerank_no_fallback():
    """Core Reranker 端到端：真模型精排 fallback=false，trace 记 cross_encoder。"""
    _require_sentence_transformers()
    from core.query_engine.reranker import Reranker as CoreReranker
    from core.trace.trace_context import TraceContext
    from core.types import RetrievalResult

    settings = _real_settings()
    reranker = CoreReranker(settings)  # 生产路径：工厂默认惰性装配，构造零 IO
    assert reranker.backend_name == "cross_encoder"

    # top_m=30 满额候选：最相关文本插在第 15 位，验证精排真实改序。
    fillers = [f"Filler passage #{i} about unrelated topics." for i in range(30)]
    results = [
        RetrievalResult(chunk_id=f"c{i}", score=1.0 - i * 0.01, text=text, metadata={})
        for i, text in enumerate(fillers)
    ]
    results[14] = RetrievalResult(chunk_id="c14", score=0.85, text=_RELATED, metadata={})
    results[5] = RetrievalResult(chunk_id="c5", score=0.86, text=_UNRELATED, metadata={})

    trace = TraceContext()
    started = time.perf_counter()
    ranked = reranker.rerank(_QUERY, results, trace=trace)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert reranker.last_fallback is False, f"unexpected fallback: {reranker.last_error}"
    assert reranker.last_error is None
    assert len(ranked) == len(results)  # top_m=30 未截断
    assert ranked[0].chunk_id == "c14", f"related text must rank first, got {[r.chunk_id for r in ranked[:3]]}"
    print(f"\n[e2e] rerank top_m={len(results)} CPU latency: {elapsed_ms:.1f}ms")

    rerank_stage = next(s for s in trace.stages if s["stage"] == "rerank")
    details = rerank_stage["details"]
    assert details["backend"] == "cross_encoder"
    assert details["fallback"] is False
    assert "error" not in details
