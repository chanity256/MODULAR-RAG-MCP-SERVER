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

可测试性：解析/渲染/执行全部为可导入纯函数，``main(argv, *, runner=...,
out=...)`` 支持注入已构建的 EvalRunner（离线单测零网络零子进程）。

退出码：``0`` = 评估完成（含部分 case 失败）；``1`` = 整批评估失败
（test set 非法/检索链路初始化失败等）；``2`` = 用法/配置错误。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable

# 与 scripts/ingest.py、scripts/query.py 同款 sys.path shim。
_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from core.query_engine.hybrid_search import HybridSearchError  # noqa: E402
from core.settings import ConfigError, Settings, load_settings  # noqa: E402
from libs.evaluator.custom_evaluator import CustomEvaluator  # noqa: E402
from libs.evaluator.evaluator_factory import EvaluatorBackendError, EvaluatorFactory  # noqa: E402
from observability.evaluation.composite_evaluator import CompositeEvaluator  # noqa: E402
from observability.evaluation.eval_runner import EvalReport, EvalRunner  # noqa: E402

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "EXIT_OK",
    "EXIT_RUN_FAILED",
    "EXIT_USAGE_ERROR",
    "build_parser",
    "parse_args",
    "resolve_test_set_path",
    "build_runner",
    "render_report",
    "main",
]

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_USAGE_ERROR = 2


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
        if runner is None:
            runner = build_runner(settings, args.evaluator)
        test_set = resolve_test_set_path(args, settings)
    except (ConfigError, FileNotFoundError, ValueError, TypeError, EvaluatorBackendError) as exc:
        out(f"error: {exc}")
        return EXIT_USAGE_ERROR

    filters = {"collection": args.collection} if args.collection else None
    try:
        report = runner.run(test_set, top_k=args.top_k, filters=filters)
    except (ValueError, HybridSearchError) as exc:
        out(f"error: evaluation failed: {exc}")
        return EXIT_RUN_FAILED

    render_report(report, out=out)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
