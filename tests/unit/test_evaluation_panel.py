"""评估面板页面纯逻辑单元测试（阶段 H4）。

页面主体按 spec 手动验证；此处覆盖可离线测试的纯函数
（后端路由 / 默认 test set 路径解析）。streamlit 仅在导入层依赖，
不在无 Streamlit runtime 的情况下调用 ``render``。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIR = _REPO_ROOT / "src"


def _load_page() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "evaluation_panel_page",
        _SRC_DIR / "observability" / "dashboard" / "pages" / "evaluation_panel.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["evaluation_panel_page"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def page() -> ModuleType:
    return _load_page()


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    from core.settings import load_settings

    monkeypatch.chdir(tmp_path)
    return load_settings(str(_REPO_ROOT / "config" / "settings.yaml"))


class TestEvaluatorForBackend:
    def test_custom(self, page, settings):
        from libs.evaluator.custom_evaluator import CustomEvaluator

        assert isinstance(page.evaluator_for_backend("custom", settings), CustomEvaluator)

    def test_ragas(self, page, settings):
        from observability.evaluation.ragas_evaluator import RagasEvaluator

        assert isinstance(page.evaluator_for_backend("ragas", settings), RagasEvaluator)

    def test_composite_combines_settings_backends(self, page, settings):
        from observability.evaluation.composite_evaluator import CompositeEvaluator

        evaluator = page.evaluator_for_backend("composite", settings)
        assert isinstance(evaluator, CompositeEvaluator)
        assert [e.name for e in evaluator.evaluators] == settings.evaluation.backends

    def test_unknown_backend_value_error(self, page, settings):
        with pytest.raises(ValueError, match="unknown evaluator backend"):
            page.evaluator_for_backend("nope", settings)

    def test_backends_constant(self, page):
        assert set(page.EVALUATOR_BACKENDS) == {"composite", "custom", "ragas"}


class TestDefaultTestSetPath:
    def test_resolves_repo_relative_path(self, page, settings):
        path = page.default_test_set_path(settings)
        assert path.name == "golden_test_set.json"
        assert path.is_file()

    def test_absolute_path_passthrough(self, page, settings, tmp_path):
        target = tmp_path / "custom_set.json"
        target.write_text("{}", encoding="utf-8")
        settings.evaluation.golden_test_set = str(target)
        assert page.default_test_set_path(settings) == target
