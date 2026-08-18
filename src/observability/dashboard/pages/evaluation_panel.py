"""评估面板页面（阶段 H4）。

运行评估（选择后端 / golden test set / top-k / collection）、
展示聚合指标与逐 query 明细，并保留本次会话内的历史结果对比。

可测试性：``render(*, settings=None, run_eval=None)`` 支持注入
Settings 与评估函数（签名 ``(test_set_path, top_k, filters) -> EvalReport``），
离线单测无需真实检索栈；页面主体按 spec 以手动验证为主。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import streamlit as st

from core.settings import Settings
from libs.evaluator.base_evaluator import BaseEvaluator
from libs.evaluator.custom_evaluator import CustomEvaluator
from observability.evaluation.composite_evaluator import CompositeEvaluator
from observability.evaluation.eval_runner import EvalReport
from observability.logger import get_logger

__all__ = ["render", "evaluator_for_backend", "default_test_set_path", "EVALUATOR_BACKENDS"]

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[4]

#: 可选评估后端（与 scripts/evaluate.py 对齐）。
EVALUATOR_BACKENDS = ("composite", "custom", "ragas")

#: 会话内历史记录键。
_HISTORY_KEY = "evaluation_history"

RunEvalFn = Callable[..., EvalReport]


def evaluator_for_backend(backend: str, settings: Settings) -> BaseEvaluator:
    """按后端名构建评估器（composite 经 settings.evaluation.backends 组合）。"""
    if backend == "composite":
        return CompositeEvaluator.from_settings(settings)
    if backend == "ragas":
        from libs.evaluator.evaluator_factory import EvaluatorFactory

        return EvaluatorFactory.create("ragas")
    if backend == "custom":
        return CustomEvaluator()
    raise ValueError(f"unknown evaluator backend: {backend!r}")


def default_test_set_path(settings: Settings) -> Path:
    """settings.evaluation.golden_test_set（相对路径按仓库根解析）。"""
    path = Path(settings.evaluation.golden_test_set)
    if not path.is_absolute():
        candidate = _REPO_ROOT / path
        path = candidate if candidate.exists() else path
    return path


def _default_run_eval(settings: Settings, backend: str) -> RunEvalFn:
    """默认评估函数：按 Settings 构建 EvalRunner（首次运行时创建）。"""

    def run(test_set_path: Any, *, top_k: int | None, filters: dict[str, Any] | None) -> EvalReport:
        from core.query_engine.hybrid_search import HybridSearch
        from observability.evaluation.eval_runner import EvalRunner

        runner = EvalRunner(settings, hybrid_search=HybridSearch(settings), evaluator=evaluator_for_backend(backend, settings))
        return runner.run(test_set_path, top_k=top_k, filters=filters)

    return run


def render(
    *,
    settings: Settings | None = None,
    run_eval: RunEvalFn | None = None,
) -> None:
    st.header("评估面板")
    st.caption("选择评估后端与 golden test set，运行检索质量评估并对比历史结果。")

    if settings is None:
        from observability.dashboard.services.config_service import ConfigService

        settings = ConfigService().get_settings()

    left, right = st.columns([2, 3])
    with left:
        backend = st.selectbox("评估后端", EVALUATOR_BACKENDS, index=0)
        test_set_path = st.text_input(
            "Golden test set 路径",
            value=str(default_test_set_path(settings)),
        )
        top_k = st.number_input("Top-K（0 = 使用配置默认）", min_value=0, value=0, step=1)
        collection = st.text_input("限定集合（可选）", value="").strip()

    run_fn = run_eval or _default_run_eval(settings, backend)

    if st.button("运行评估", type="primary"):
        path = Path(test_set_path)
        if not path.is_file():
            st.error(f"golden test set 不存在：{path}")
        else:
            filters = {"collection": collection} if collection else None
            try:
                with st.spinner("评估运行中…"):
                    report = run_fn(
                        path,
                        top_k=top_k or None,
                        filters=filters,
                    )
            except Exception as exc:
                logger.warning("evaluation panel run failed: %s", exc)
                st.error(f"评估失败：{exc}")
            else:
                _record_history(report)
                st.rerun()

    _render_history()


#: 会话内最近一次报告（含逐 query 明细）。
_LAST_REPORT_KEY = "evaluation_last_report"


def _record_history(report: EvalReport) -> None:
    history = st.session_state.setdefault(_HISTORY_KEY, [])
    history.append(
        {
            "run": len(history) + 1,
            **{key: round(value, 4) for key, value in report.metrics.items()},
            "evaluated": report.evaluated,
            "failed": report.failed,
        }
    )
    st.session_state[_LAST_REPORT_KEY] = report


def _render_history() -> None:
    history: list[dict[str, Any]] = st.session_state.get(_HISTORY_KEY, [])
    st.divider()

    if not history:
        st.info("尚未运行评估。选择后端后点击「运行评估」。")
        return

    latest = history[-1]
    metric_keys = [k for k in latest if k not in ("run", "evaluated", "failed")]

    st.subheader(f"最近一次结果（{latest['evaluated']} evaluated / {latest['failed']} failed）")
    cols = st.columns(min(len(metric_keys), 4) or 1)
    for col, key in zip(cols, metric_keys):
        col.metric(label=key, value=f"{latest[key]:.4f}")

    report: EvalReport | None = st.session_state.get(_LAST_REPORT_KEY)
    if report is not None and report.per_query:
        st.subheader("各 Query 明细")
        st.dataframe(
            [
                {
                    "query": q.query,
                    "hit_rate": q.metrics.get("hit_rate"),
                    "mrr": q.metrics.get("mrr"),
                    "first_expected": q.expected_chunk_ids[0] if q.expected_chunk_ids else "",
                    "retrieved_top1": q.retrieved_ids[0] if q.retrieved_ids else "",
                    "error": q.error or "",
                }
                for q in report.per_query
            ],
            use_container_width=True,
        )

    if len(history) > 1 and len(metric_keys) >= 2:
        st.subheader("历史趋势")
        chart_keys = metric_keys[:3]
        st.line_chart(
            [{k: row.get(k) for k in chart_keys} | {"run": row["run"]} for row in history],
            x="run",
            y=chart_keys,
        )

    st.subheader("历史记录")
    st.dataframe(history, use_container_width=True)
