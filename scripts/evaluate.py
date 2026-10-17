"""评估命令行入口（阶段 H3，开发调试用）。

用法（spec H3 验收形态）::

    python scripts/evaluate.py
    python scripts/evaluate.py --test-set tests/fixtures/golden_test_set.json --top-k 5

行为契约：
- ``--test-set``：golden test set JSON 路径（默认
  ``settings.evaluation.golden_test_set``）。
- ``--top-k``：可选检索数量（默认 ``retrieval.top_k_final``）。
- ``--collection``：可选，限定检索集合（filters={"collection": ...}）。
- ``--evaluator``：评估后端（``custom`` / ``ragas`` / ``composite``，默认
  ``composite``——按 ``evaluation.backends`` 组合；ragas 未安装时自动降级，
  单后端异常由 CompositeEvaluator 隔离）。
- 输出：聚合指标块 + 逐 query 明细（命中/首中排名/错误）。
- ``--retrieval-mode``（S3.1）：``hybrid``（现状默认，行为零回归）/
  ``agentic``（纯第三路：每个问题直接走 AgenticSearch 后端）/
  ``hybrid+agentic``（HybridSearch 注入第三路，按
  ``settings.agentic_search.trigger`` 触发；可用 ``--agentic-trigger``
  覆盖触发策略以便全量触发对比）。``hybrid`` 以外模式额外输出
  **source 级指标**（基于 ``expected_sources``，与 chunk 级指标并列
  输出、不混算——agentic 确定性 ID ``agentic:{sha1...}`` 与
  ``expected_chunk_ids`` 永不匹配（spec 4.6），chunk 级指标对
  agentic / hybrid+agentic 模式无意义，报告注明口径）以及
  **延迟与 Token 对比列**（hybrid 模式 token 口径为 0/无）。
- 真实 sidecar 永不自动调用：``agentic`` / ``hybrid+agentic`` 模式下
  ``backend=sirchmunk_http`` 且未设 ``RAG_AGENTIC_INTEGRATION=1`` 时
  按用法错误拒绝（退出码 2）；离线路径一律 fake 后端或注入。

可测试性：解析/渲染/执行全部为可导入纯函数，``main(argv, *, runner=...,
out=...)`` 支持注入已构建的 EvalRunner（离线单测零网络零子进程）。

退出码：``0`` = 评估完成（含部分 case 失败）；``1`` = 整批评估失败
（test set 非法/检索链路初始化失败等）；``2`` = 用法/配置错误。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

# 与 scripts/ingest.py、scripts/query.py 同款 sys.path shim。
_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import libs.agentic_search  # noqa: F401,E402 - 导入即完成后端注册（fake/sirchmunk_http）
from core.query_engine.hybrid_search import HybridSearch, HybridSearchError  # noqa: E402
from core.settings import ConfigError, Settings, load_settings  # noqa: E402
from core.trace.trace_context import TRACE_TYPE_QUERY, TraceContext  # noqa: E402
from core.types import RetrievalResult  # noqa: E402
from libs.agentic_search.adapter import AgenticRouteAdapter, build_agentic_retriever  # noqa: E402
from libs.agentic_search.base_agentic_search import AgenticSearchError  # noqa: E402
from libs.evaluator.base_evaluator import BaseEvaluator  # noqa: E402
from libs.evaluator.custom_evaluator import CustomEvaluator  # noqa: E402
from libs.evaluator.evaluator_factory import EvaluatorBackendError, EvaluatorFactory  # noqa: E402
from observability.evaluation.composite_evaluator import CompositeEvaluator  # noqa: E402
from observability.evaluation.eval_runner import EvalReport, EvalRunner  # noqa: E402

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "EXIT_OK",
    "EXIT_RUN_FAILED",
    "EXIT_USAGE_ERROR",
    "RETRIEVAL_MODES",
    "ModeQueryDetail",
    "ModeEvalReport",
    "build_parser",
    "parse_args",
    "resolve_test_set_path",
    "build_runner",
    "render_report",
    "normalize_source",
    "compute_source_metrics",
    "run_mode_eval",
    "render_mode_report",
    "main",
]

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_USAGE_ERROR = 2

#: ``--retrieval-mode`` 合法取值（S3.1）。
RETRIEVAL_MODES = ("hybrid", "agentic", "hybrid+agentic")

#: 真实 sidecar 集成门控环境变量（对齐 tests/e2e/test_agentic_mcp.py）。
INTEGRATION_GATE_ENV = "RAG_AGENTIC_INTEGRATION"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evaluate.py",
        description="Run RAG retrieval evaluation against a golden test set.",
    )
    parser.add_argument(
        "--test-set",
        default=None,
        help="golden test set JSON path (default: settings.evaluation.golden_test_set)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="retrieval top-k (default: settings.retrieval.top_k_final)",
    )
    parser.add_argument("--collection", default=None, help="restrict search to a collection")
    parser.add_argument(
        "--evaluator",
        choices=["custom", "ragas", "composite"],
        default="composite",
        help="evaluator backend (default: composite via settings.evaluation.backends)",
    )
    parser.add_argument(
        "--retrieval-mode",
        choices=list(RETRIEVAL_MODES),
        default="hybrid",
        help=(
            "retrieval mode (S3.1): hybrid (default, baseline) / agentic "
            "(pure third route) / hybrid+agentic (third route injected into "
            "HybridSearch, triggered per settings.agentic_search.trigger)"
        ),
    )
    parser.add_argument(
        "--agentic-trigger",
        choices=["tool_only", "fallback", "always"],
        default=None,
        help=(
            "override agentic_search.trigger for hybrid+agentic mode "
            "(e.g. always to fire the third route on every query); "
            "default: use settings.agentic_search.trigger"
        ),
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"path to settings.yaml (default: {DEFAULT_CONFIG_PATH})",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def resolve_test_set_path(args: argparse.Namespace, settings: Settings) -> Path:
    """解析 test set 路径：显式参数 > settings.evaluation.golden_test_set。"""
    if args.test_set:
        path = Path(args.test_set)
    else:
        path = Path(settings.evaluation.golden_test_set)
    if not path.is_absolute():
        # 相对路径相对仓库根解析（settings 中的相对路径以仓库根为基准）。
        repo_root = Path(__file__).resolve().parents[1]
        candidate = repo_root / path
        path = candidate if candidate.exists() else path
    if not path.is_file():
        raise FileNotFoundError(f"golden test set not found: {path}")
    return path


def build_runner(settings: Settings, evaluator_backend: str) -> EvalRunner:
    if evaluator_backend == "composite":
        evaluator = CompositeEvaluator.from_settings(settings)
    elif evaluator_backend == "ragas":
        evaluator = EvaluatorFactory.create("ragas")
    else:
        evaluator = CustomEvaluator()
    return EvalRunner(settings, evaluator=evaluator)


def render_report(report: EvalReport, out: Callable[[str], None] = print) -> None:
    out("═" * 60)
    out(f"Evaluation Report  ({report.evaluated}/{report.total} evaluated, {report.failed} failed)")
    out("═" * 60)
    if report.metrics:
        for key in sorted(report.metrics):
            out(f"  {key:<24} {report.metrics[key]:.4f}")
    else:
        out("  (no metrics)")
    out("─" * 60)
    for i, q in enumerate(report.per_query, start=1):
        hit = q.metrics.get("hit_rate") == 1.0
        status = "✓ hit" if hit else ("✗ miss" if not q.error else "✗ error")
        out(f"[{i}] {status}  {q.query}")
        if q.error:
            out(f"    error: {q.error}")
        else:
            mrr = q.metrics.get("mrr")
            out(f"    mrr={mrr:.3f}  retrieved={q.retrieved_ids[:5]}" if mrr is not None else f"    retrieved={q.retrieved_ids[:5]}")
    out("═" * 60)


# ---------------------------------------------------------------------------
# S3.1：评估 A/B（--retrieval-mode agentic / hybrid+agentic）
# ---------------------------------------------------------------------------


def normalize_source(source: str) -> str:
    """来源归一化：取文件名（basename）并转小写。

    测试集 ``expected_sources`` 是裸文件名（如 ``chinese_technical_doc.pdf``），
    而检索结果 ``metadata.source_path`` 是路径（如 ``/data/docs/xxx.pdf``），
    统一到 basename 口径比较。
    """
    return Path(source).name.strip().lower()


def compute_source_metrics(
    retrieved_sources: list[str],
    expected_sources: list[str],
) -> dict[str, float]:
    """source 级指标（手算可断言的纯函数）。

    - ``source_hit_rate``：检索到的来源（去重后）与期望来源有交集记 1，
      否则 0。
    - ``source_mrr``：首个命中来源在「去重保序」检索来源序列中的排名
      倒数；无命中为 0。
    - ``expected_sources`` 为空时不计口径（返回空 dict，聚合时跳过）。
    """
    expected = {normalize_source(s) for s in expected_sources if s}
    if not expected:
        return {}
    seen: set[str] = set()
    first_rank: int | None = None
    rank = 0  # 去重保序序列中的排名（同源重复条目不计新排名）。
    for source in retrieved_sources:
        normalized = normalize_source(source)
        if not normalized or normalized in seen:
            continue  # 去重（含 agentic 去重扩展后的同源条目）
        rank += 1
        seen.add(normalized)
        if first_rank is None and normalized in expected:
            first_rank = rank
    hit = 1.0 if seen & expected else 0.0
    mrr = 1.0 / first_rank if first_rank is not None else 0.0
    return {"source_hit_rate": hit, "source_mrr": mrr}


def collect_sources(results: list[RetrievalResult]) -> list[str]:
    """按结果顺序收集 ``metadata.source_path``（去重保序）。"""
    sources: list[str] = []
    seen: set[str] = set()
    for result in results:
        source = result.metadata.get("source_path")
        if isinstance(source, str) and source and source not in seen:
            seen.add(source)
            sources.append(source)
    return sources


@dataclass
class ModeQueryDetail:
    """单条 query 的模式评估明细（chunk 级 + source 级并列，不混算）。"""

    query: str
    retrieved_ids: list[str]
    retrieved_sources: list[str]
    expected_chunk_ids: list[str]
    expected_sources: list[str]
    chunk_metrics: dict[str, float]
    source_metrics: dict[str, float]
    latency_ms: float | None = None
    total_llm_tokens: int | None = None
    error: str | None = None


@dataclass
class ModeEvalReport:
    """单模式评估报告（S3.1；跨模式对比由多次运行并列输出构成）。"""

    mode: str
    total: int
    evaluated: int
    failed: int
    metrics: dict[str, float]  # chunk 级 + source 级聚合（前缀区分）
    per_query: list[ModeQueryDetail] = field(default_factory=list)
    total_latency_ms: float | None = None
    total_llm_tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "total": self.total,
            "evaluated": self.evaluated,
            "failed": self.failed,
            "metrics": dict(self.metrics),
            "per_query": [
                {
                    "query": q.query,
                    "retrieved_ids": list(q.retrieved_ids),
                    "retrieved_sources": list(q.retrieved_sources),
                    "chunk_metrics": dict(q.chunk_metrics),
                    "source_metrics": dict(q.source_metrics),
                    "latency_ms": q.latency_ms,
                    "total_llm_tokens": q.total_llm_tokens,
                    "error": q.error,
                }
                for q in self.per_query
            ],
            "total_latency_ms": self.total_latency_ms,
            "total_llm_tokens": self.total_llm_tokens,
        }


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def run_mode_eval(
    settings: Settings,
    mode: str,
    test_set_path: str | Path,
    *,
    top_k: int | None = None,
    filters: dict[str, Any] | None = None,
    evaluator: BaseEvaluator | None = None,
    hybrid_search: Any | None = None,
    agentic_adapter: AgenticRouteAdapter | None = None,
) -> ModeEvalReport:
    """运行单模式（``agentic`` / ``hybrid+agentic``）评估并返回报告。

    hybrid 模式不走本函数（沿用既有 EvalRunner 路径，行为零回归）。

    Args:
        settings: 配置（本函数**不就地修改**；触发策略覆盖等由调用方
            用 ``dataclasses.replace`` 生成副本）。
        hybrid_search: 可注入检索栈（鸭子类型 ``search(query, top_k,
            filters, trace=...)``；离线单测 fake 用）。
        agentic_adapter: 可注入第三路适配器（``agentic`` 模式直接用；
            ``hybrid+agentic`` 模式在未注入 ``hybrid_search`` 时用于构造
            ``HybridSearch``）。

    Raises:
        ValueError: mode 非法。
        AgenticSearchError / HybridSearchError: 检索链路初始化或执行失败
            （由调用方转为退出码 1）。
    """
    if mode not in ("agentic", "hybrid+agentic"):
        raise ValueError(f"unsupported retrieval mode for run_mode_eval: {mode!r}")

    evaluator = evaluator if evaluator is not None else CustomEvaluator()
    from observability.evaluation.eval_runner import load_golden_test_set

    cases = load_golden_test_set(test_set_path)

    # ---- 检索栈构建 ----
    # 评估属"实验/评估"用途，显式启用第三路（覆盖 settings enabled=false
    # 默认值）；触发策略由 settings.agentic_search.trigger 决定（调用方可
    # 用 replace 副本覆盖为 always）。
    eval_settings = replace(settings, agentic_search=replace(settings.agentic_search, enabled=True))
    if mode == "agentic":
        adapter = agentic_adapter if agentic_adapter is not None else build_agentic_retriever(eval_settings)

        def retrieve_one(query: str) -> tuple[list[RetrievalResult], float | None, int | None]:
            # 纯第三路：每问直接走 AgenticSearch；延迟取适配器实测，
            # Token 取后端响应的 total_llm_tokens。
            started = time.perf_counter()
            results = adapter.retrieve(query, top_k=top_k)
            latency = round((time.perf_counter() - started) * 1000, 3)
            response = adapter.last_response
            tokens = getattr(response, "total_llm_tokens", None) if response is not None else None
            return results, latency, tokens

    else:  # hybrid+agentic
        adapter = agentic_adapter if agentic_adapter is not None else build_agentic_retriever(eval_settings)
        search_stack = (
            hybrid_search
            if hybrid_search is not None
            else HybridSearch(eval_settings, agentic_retriever=adapter)
        )

        def retrieve_one(query: str) -> tuple[list[RetrievalResult], float | None, int | None]:
            trace = TraceContext(trace_type=TRACE_TYPE_QUERY)
            started = time.perf_counter()
            results = search_stack.search(query, top_k=top_k, filters=filters, trace=trace)
            latency = round((time.perf_counter() - started) * 1000, 3)
            response = adapter.last_response if adapter is not None else None
            tokens = getattr(response, "total_llm_tokens", None) if response is not None else None
            return results, latency, tokens

    per_query: list[ModeQueryDetail] = []
    chunk_sums: dict[str, float] = {}
    source_sums: dict[str, float] = {}
    latencies: list[float] = []
    tokens_all: list[int] = []
    evaluated = 0

    for case in cases:
        try:
            results, latency, tokens = retrieve_one(case.query)
            retrieved_ids = [r.chunk_id for r in results]
            chunk_metrics = {
                k: float(v)
                for k, v in evaluator.evaluate(
                    case.query, retrieved_ids, case.expected_chunk_ids, top_k=top_k
                ).items()
            }
            source_metrics = compute_source_metrics(collect_sources(results), case.expected_sources)
            per_query.append(
                ModeQueryDetail(
                    query=case.query,
                    retrieved_ids=retrieved_ids,
                    retrieved_sources=collect_sources(results),
                    expected_chunk_ids=case.expected_chunk_ids,
                    expected_sources=case.expected_sources,
                    chunk_metrics=chunk_metrics,
                    source_metrics=source_metrics,
                    latency_ms=latency,
                    total_llm_tokens=tokens,
                )
            )
            evaluated += 1
            if latency is not None:
                latencies.append(latency)
            if tokens is not None:
                tokens_all.append(tokens)
            for key, value in chunk_metrics.items():
                chunk_sums[key] = chunk_sums.get(key, 0.0) + value
            for key, value in source_metrics.items():
                source_sums[key] = source_sums.get(key, 0.0) + value
        except Exception as exc:  # 单条失败隔离（对齐 EvalRunner 手法）。
            per_query.append(
                ModeQueryDetail(
                    query=case.query,
                    retrieved_ids=[],
                    retrieved_sources=[],
                    expected_chunk_ids=case.expected_chunk_ids,
                    expected_sources=case.expected_sources,
                    chunk_metrics={},
                    source_metrics={},
                    error=str(exc),
                )
            )

    aggregate: dict[str, float] = {}
    for key, value in chunk_sums.items():
        aggregate[f"chunk_{key}"] = value / evaluated if evaluated else 0.0
    for key, value in source_sums.items():
        aggregate[key] = value / evaluated if evaluated else 0.0
    if latencies:
        aggregate["avg_latency_ms"] = _mean(latencies)

    return ModeEvalReport(
        mode=mode,
        total=len(cases),
        evaluated=evaluated,
        failed=len(cases) - evaluated,
        metrics=aggregate,
        per_query=per_query,
        total_latency_ms=round(sum(latencies), 3) if latencies else None,
        total_llm_tokens=sum(tokens_all) if tokens_all else None,
    )


def render_mode_report(report: ModeEvalReport, out: Callable[[str], None] = print) -> None:
    """渲染单模式评估报告（chunk 级与 source 级指标并列输出，不混算）。"""
    out("═" * 60)
    out(
        f"Evaluation Report [{report.mode}]  "
        f"({report.evaluated}/{report.total} evaluated, {report.failed} failed)"
    )
    out("═" * 60)
    if report.mode in ("agentic", "hybrid+agentic"):
        out(
            "  口径注明：chunk 级指标（chunk_*，基于 expected_chunk_ids）对"
            " agentic/hybrid+agentic 模式无意义——agentic 确定性 ID"
            " `agentic:{sha1...}` 与 chunk_id 空间永不匹配（spec 4.6）；"
            "命中率对比请以 source 级指标（source_hit_rate/source_mrr，"
            "基于 expected_sources）为准。"
        )
    if report.metrics:
        for key in sorted(report.metrics):
            out(f"  {key:<24} {report.metrics[key]:.4f}")
    else:
        out("  (no metrics)")
    out("─" * 60)
    out(
        "  延迟/Token：avg_latency={:.1f}ms  total_latency={}ms  total_llm_tokens={}".format(
            report.metrics.get("avg_latency_ms", 0.0),
            f"{report.total_latency_ms:.1f}" if report.total_latency_ms is not None else "无",
            report.total_llm_tokens if report.total_llm_tokens is not None else "0/无",
        )
    )
    out("─" * 60)
    for i, q in enumerate(report.per_query, start=1):
        if q.error:
            out(f"[{i}] ✗ error  {q.query}")
            out(f"    error: {q.error}")
            continue
        source_hit = q.source_metrics.get("source_hit_rate") == 1.0
        status = "✓ source hit" if source_hit else "✗ source miss"
        latency = f"{q.latency_ms:.1f}ms" if q.latency_ms is not None else "无"
        tokens = str(q.total_llm_tokens) if q.total_llm_tokens is not None else "-"
        out(f"[{i}] {status}  {q.query}  (latency={latency}, llm_tokens={tokens})")
        out(f"    sources={q.retrieved_sources[:5]}")
        smrr = q.source_metrics.get("source_mrr")
        if smrr is not None:
            out(f"    source_mrr={smrr:.3f}")
    out("═" * 60)


def main(
    argv: list[str] | None = None,
    *,
    runner: EvalRunner | None = None,
    settings: Settings | None = None,
    out: Callable[[str], None] = print,
) -> int:
    args = parse_args(argv)
    try:
        if args.top_k is not None and args.top_k <= 0:
            raise ValueError("--top-k must be a positive integer")
        if settings is None:
            settings = load_settings(args.config)
        if args.retrieval_mode == "hybrid":
            if runner is None:
                runner = build_runner(settings, args.evaluator)
        test_set = resolve_test_set_path(args, settings)
    except (ConfigError, FileNotFoundError, ValueError, TypeError, EvaluatorBackendError) as exc:
        out(f"error: {exc}")
        return EXIT_USAGE_ERROR

    filters = {"collection": args.collection} if args.collection else None

    # ---- S3.1：agentic / hybrid+agentic 模式走独立评估路径 ----
    if args.retrieval_mode != "hybrid":
        return _run_agentic_mode(args, settings, test_set, filters=filters, out=out)

    try:
        report = runner.run(test_set, top_k=args.top_k, filters=filters)
    except (ValueError, HybridSearchError) as exc:
        out(f"error: evaluation failed: {exc}")
        return EXIT_RUN_FAILED

    render_report(report, out=out)
    return EXIT_OK


def _run_agentic_mode(
    args: argparse.Namespace,
    settings: Settings,
    test_set: Path,
    *,
    filters: dict[str, Any] | None,
    out: Callable[[str], None],
) -> int:
    """``agentic`` / ``hybrid+agentic`` 模式评估（S3.1）。

    真实 sidecar 永不自动调用：``backend=sirchmunk_http`` 时沿用既有
    ``RAG_AGENTIC_INTEGRATION=1`` 门控（默认拒绝，退出码 2）。
    """
    if settings.agentic_search.backend == "sirchmunk_http" and os.environ.get(INTEGRATION_GATE_ENV) != "1":
        out(
            f"error: retrieval-mode {args.retrieval_mode!r} with backend "
            f"'sirchmunk_http' would call a real sidecar; set {INTEGRATION_GATE_ENV}=1 "
            "(real smoke) or use backend 'fake' / dependency injection instead"
        )
        return EXIT_USAGE_ERROR

    eval_settings = settings
    if args.agentic_trigger is not None:
        # 触发策略覆盖（生成副本，不就地修改 settings）。
        eval_settings = replace(
            settings,
            agentic_search=replace(settings.agentic_search, trigger=args.agentic_trigger),
        )
        out(f"# note: --agentic-trigger={args.agentic_trigger} (overrides settings.agentic_search.trigger)")

    try:
        evaluator = build_runner(eval_settings, args.evaluator).evaluator
        report = run_mode_eval(
            eval_settings,
            args.retrieval_mode,
            test_set,
            top_k=args.top_k,
            filters=filters,
            evaluator=evaluator,
        )
    except (ValueError, HybridSearchError, AgenticSearchError) as exc:
        out(f"error: evaluation failed: {exc}")
        return EXIT_RUN_FAILED

    render_mode_report(report, out=out)
    return EXIT_OK


if __name__ == "__main__":
    from observability.logger import setup_cli_logging

    setup_cli_logging("evaluate", sys.argv[1:], DEFAULT_CONFIG_PATH)
    sys.exit(main())
