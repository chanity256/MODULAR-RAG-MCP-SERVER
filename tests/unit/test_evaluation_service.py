"""EvaluationService 单测（WC1.4）：后端路由、路径解析、参数透传（原 evaluation_panel 纯逻辑测试迁入）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from observability.dashboard.services import evaluation_service as svc
from observability.dashboard.services.evaluation_service import (
    EVALUATOR_BACKENDS,
    EvaluationService,
    default_test_set_path,
    evaluator_for_backend,
    resolve_test_set_path,
)
from observability.evaluation.eval_runner import EvalReport

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    from core.settings import load_settings

    monkeypatch.chdir(tmp_path)
    return load_settings(str(_REPO_ROOT / "config" / "settings.yaml"))


def _report() -> EvalReport:
    return EvalReport(total=1, evaluated=1, failed=0, metrics={"hit_rate": 1.0}, per_query=[])


class FakeRunner:
    def __init__(self, error: Exception | None = None):
        self.calls: list[dict] = []
        self.error = error

    def run(self, test_set_path, *, top_k=None, filters=None):
        self.calls.append({"path": test_set_path, "top_k": top_k, "filters": filters})
        if self.error is not None:
            raise self.error
        return _report()


def _service(settings, runner: FakeRunner):
    built: list[tuple] = []

    def factory(s, evaluator):
        built.append((s, evaluator))
        return runner

    service = EvaluationService(settings, runner_factory=factory)
    service.built = built  # type: ignore[attr-defined]
    return service


class TestEvaluatorForBackend:
    def test_custom(self, settings):
        from libs.evaluator.custom_evaluator import CustomEvaluator

        assert isinstance(evaluator_for_backend("custom", settings), CustomEvaluator)

    def test_ragas(self, settings):
        from observability.evaluation.ragas_evaluator import RagasEvaluator

        assert isinstance(evaluator_for_backend("ragas", settings), RagasEvaluator)

    def test_composite_combines_settings_backends(self, settings):
        from observability.evaluation.composite_evaluator import CompositeEvaluator

        evaluator = evaluator_for_backend("composite", settings)
        assert isinstance(evaluator, CompositeEvaluator)
        assert [e.name for e in evaluator.evaluators] == settings.evaluation.backends

    def test_unknown_backend_value_error(self, settings):
        with pytest.raises(ValueError, match="unknown evaluator backend"):
            evaluator_for_backend("nope", settings)

    def test_backends_constant(self):
        assert set(EVALUATOR_BACKENDS) == {"composite", "custom", "ragas"}
        assert set(EvaluationService.backends) == {"composite", "custom", "ragas"}


class TestTestSetPath:
    def test_resolves_repo_relative_path(self, settings):
        path = default_test_set_path(settings)
        assert path.name == "golden_test_set.json"
        assert path.is_file()
        assert EvaluationService(settings).default_test_set_path() == path

    def test_absolute_path_passthrough(self, settings, tmp_path):
        target = tmp_path / "custom_set.json"
        target.write_text("{}", encoding="utf-8")
        settings.evaluation.golden_test_set = str(target)
        assert default_test_set_path(settings) == target

    def test_relative_missing_returned_unchanged(self):
        assert resolve_test_set_path("no/such/set.json") == Path("no/such/set.json")

    def test_relative_existing_in_repo_root(self):
        assert resolve_test_set_path("config/settings.yaml") == _REPO_ROOT / "config" / "settings.yaml"

    def test_run_resolves_repo_relative_when_not_in_cwd(self, settings):
        runner = FakeRunner()
        _service(settings, runner).run("config/settings.yaml", backend="custom")
        assert runner.calls[0]["path"] == _REPO_ROOT / "config" / "settings.yaml"

    def test_run_prefers_existing_cwd_relative_path(self, settings, tmp_path):
        (tmp_path / "set.json").write_text("{}", encoding="utf-8")
        runner = FakeRunner()
        _service(settings, runner).run("set.json", backend="custom")
        assert runner.calls[0]["path"] == Path("set.json")

    def test_run_absolute_path_passthrough(self, settings, tmp_path):
        target = tmp_path / "abs.json"
        runner = FakeRunner()
        _service(settings, runner).run(target, backend="custom")
        assert runner.calls[0]["path"] == target


class TestRun:
    def test_returns_report_and_passes_top_k_and_filters(self, settings, tmp_path):
        runner = FakeRunner()
        service = _service(settings, runner)
        report = service.run(
            tmp_path / "set.json", backend="custom", top_k=7, filters={"collection": "docs"}
        )
        assert isinstance(report, EvalReport)
        assert runner.calls == [
            {"path": tmp_path / "set.json", "top_k": 7, "filters": {"collection": "docs"}}
        ]

    def test_defaults_none_top_k_and_filters(self, settings, tmp_path):
        runner = FakeRunner()
        _service(settings, runner).run(tmp_path / "set.json", backend="custom")
        assert runner.calls[0]["top_k"] is None
        assert runner.calls[0]["filters"] is None

    @pytest.mark.parametrize("backend", ["composite", "custom", "ragas"])
    def test_backend_selects_evaluator(self, settings, tmp_path, backend):
        runner = FakeRunner()
        service = _service(settings, runner)
        service.run(tmp_path / "set.json", backend=backend)
        built_settings, evaluator = service.built[0]
        assert built_settings is settings
        assert isinstance(evaluator, type(evaluator_for_backend(backend, settings)))

    def test_unknown_backend_rejected_before_building_runner(self, settings, tmp_path):
        runner = FakeRunner()
        service = _service(settings, runner)
        with pytest.raises(ValueError, match="unknown evaluator backend"):
            service.run(tmp_path / "set.json", backend="nope")
        assert service.built == []

    @pytest.mark.parametrize("bad", [0, -1, True, 1.5, "3"])
    def test_invalid_top_k_rejected(self, settings, tmp_path, bad):
        runner = FakeRunner()
        with pytest.raises(ValueError, match="top_k"):
            _service(settings, runner).run(tmp_path / "s.json", backend="custom", top_k=bad)
        assert runner.calls == []

    def test_invalid_filters_rejected(self, settings, tmp_path):
        with pytest.raises(ValueError, match="filters"):
            _service(settings, FakeRunner()).run(
                tmp_path / "s.json", backend="custom", filters=["collection"]  # type: ignore[arg-type]
            )

    def test_runner_exception_propagates_unchanged(self, settings, tmp_path):
        boom = FileNotFoundError("golden set missing")
        service = _service(settings, FakeRunner(error=boom))
        with pytest.raises(FileNotFoundError) as info:
            service.run(tmp_path / "missing.json", backend="custom")
        assert info.value is boom

    def test_default_runner_factory_wires_hybrid_search_and_eval_runner(self, settings, monkeypatch, tmp_path):
        captured: dict = {}

        class FakeHybrid:
            def __init__(self, s):
                captured["hybrid_settings"] = s

        class FakeEvalRunner:
            def __init__(self, s, hybrid_search=None, evaluator=None):
                captured.update(runner_settings=s, hybrid=hybrid_search, evaluator=evaluator)

            def run(self, path, *, top_k=None, filters=None):
                captured.update(path=path, top_k=top_k, filters=filters)
                return _report()

        import core.query_engine.hybrid_search as hybrid_module
        import observability.evaluation.eval_runner as runner_module

        monkeypatch.setattr(hybrid_module, "HybridSearch", FakeHybrid)
        monkeypatch.setattr(runner_module, "EvalRunner", FakeEvalRunner)

        EvaluationService(settings).run(tmp_path / "s.json", backend="custom", top_k=2)
        assert captured["hybrid_settings"] is settings
        assert captured["runner_settings"] is settings
        assert isinstance(captured["hybrid"], FakeHybrid)
        assert captured["top_k"] == 2


def test_service_has_no_streamlit_import():
    assert "import streamlit" not in Path(svc.__file__).read_text(encoding="utf-8")
