"""EvaluationService：评估后端路由 + runner 构建 + 执行（框架无关，WC1.4）。

供 ``src/api/`` 路由使用：

- :func:`evaluator_for_backend` —— ``custom`` / ``ragas`` / ``composite`` 路由；
- :func:`resolve_test_set_path` / :meth:`EvaluationService.default_test_set_path`
  —— golden test set 相对路径按仓库根解析；
- :meth:`EvaluationService.run` —— 校验参数后构建 ``EvalRunner`` 并执行，
  返回 ``EvalReport``。

runner 异常原样向调用方传播（不转换为 UI 文案）；本模块不依赖 Streamlit。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from core.settings import Settings
from libs.evaluator.base_evaluator import BaseEvaluator
from libs.evaluator.custom_evaluator import CustomEvaluator
from observability.evaluation.composite_evaluator import CompositeEvaluator
from observability.evaluation.eval_runner import EvalReport

__all__ = [
    "EVALUATOR_BACKENDS",
    "EvaluationService",
    "default_test_set_path",
    "evaluator_for_backend",
    "resolve_test_set_path",
]

_REPO_ROOT = Path(__file__).resolve().parents[4]

#: 可选评估后端（与 scripts/evaluate.py 对齐）。
EVALUATOR_BACKENDS = ("composite", "custom", "ragas")

#: ``runner_factory(settings, evaluator) -> 具备 run(path, *, top_k, filters) 的对象``。
RunnerFactory = Callable[[Settings, BaseEvaluator], Any]


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


def resolve_test_set_path(path: str | Path) -> Path:
    """相对路径优先按仓库根解析（存在才采用），绝对路径原样返回。"""
    resolved = Path(path)
    if not resolved.is_absolute():
        candidate = _REPO_ROOT / resolved
        resolved = candidate if candidate.exists() else resolved
    return resolved


def default_test_set_path(settings: Settings) -> Path:
    """``settings.evaluation.golden_test_set`` 解析后的路径。"""
    return resolve_test_set_path(settings.evaluation.golden_test_set)


def _default_runner_factory(settings: Settings, evaluator: BaseEvaluator) -> Any:
    from core.query_engine.hybrid_search import HybridSearch
    from observability.evaluation.eval_runner import EvalRunner

    return EvalRunner(settings, hybrid_search=HybridSearch(settings), evaluator=evaluator)


class EvaluationService:
    """评估执行服务。

    Args:
        settings: 全局配置。
        runner_factory: 自定义 runner 装配（测试注入 fake；缺省装配
            ``HybridSearch`` + ``EvalRunner``，首次 :meth:`run` 时才创建）。
    """

    backends = EVALUATOR_BACKENDS

    def __init__(self, settings: Settings, *, runner_factory: RunnerFactory | None = None) -> None:
        self._settings = settings
        self._runner_factory = runner_factory or _default_runner_factory

    def default_test_set_path(self) -> Path:
        return default_test_set_path(self._settings)

    def test_set_exists(self, test_set_path: str | Path) -> bool:
        """提交预检（WP2）：``run()`` 同款解析规则下路径是否存在。

        相对路径先按 CWD、再按仓库根解析，与 :meth:`run` 完全一致，避免预检与执行
        判定不一致；只用于显式路径——默认 golden set 缺失属服务端配置问题，不在此拦截。
        """
        path = Path(test_set_path)
        if not path.is_absolute() and not path.exists():
            path = resolve_test_set_path(path)
        return path.exists()

    def build_evaluator(self, backend: str) -> BaseEvaluator:
        return evaluator_for_backend(backend, self._settings)

    def run(
        self,
        test_set_path: str | Path,
        *,
        backend: str = "composite",
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> EvalReport:
        """运行评估。

        Raises:
            ValueError: 后端未知 / ``top_k`` 非正整数 / ``filters`` 非 dict。
            Exception: runner 内部异常（如 golden set 缺失）原样传播。
        """
        if backend not in EVALUATOR_BACKENDS:
            raise ValueError(f"unknown evaluator backend: {backend!r}")
        if top_k is not None and (isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1):
            raise ValueError(f"top_k must be a positive integer or None, got {top_k!r}")
        if filters is not None and not isinstance(filters, dict):
            raise ValueError(f"filters must be a dict or None, got {type(filters).__name__}")

        path = Path(test_set_path)
        if not path.is_absolute() and not path.exists():
            path = resolve_test_set_path(path)
        runner = self._runner_factory(self._settings, self.build_evaluator(backend))
        return runner.run(path, top_k=top_k, filters=filters)
