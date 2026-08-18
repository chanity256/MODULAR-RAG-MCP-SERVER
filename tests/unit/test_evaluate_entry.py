"""scripts/evaluate.py CLI 单元测试（阶段 H3）。经 importlib 按路径加载。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_entry() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "evaluate_entry", _REPO_ROOT / "scripts" / "evaluate.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["evaluate_entry"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def entry() -> ModuleType:
    return _load_entry()


class FakeRunner:
    def __init__(self, report=None, error: Exception | None = None):
        self.report = report
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def run(self, test_set_path, *, top_k=None, filters=None):
        self.calls.append(
            {"test_set_path": str(test_set_path), "top_k": top_k, "filters": filters}
        )
        if self.error is not None:
            raise self.error
        return self.report


def _fake_report(evaluated=1, total=1, failed=0, metrics=None, per_query=None):
    from observability.evaluation.eval_runner import EvalReport, QueryEvalResult

    return EvalReport(
        total=total,
        evaluated=evaluated,
        failed=failed,
        metrics=metrics or {"hit_rate": 1.0, "mrr": 0.5},
        per_query=per_query
        or [QueryEvalResult("q1", ["a"], ["a"], {"hit_rate": 1.0, "mrr": 1.0})],
    )


@pytest.fixture()
def test_set(tmp_path):
    path = tmp_path / "golden.json"
    path.write_text(
        json.dumps({"test_cases": [{"query": "q1", "expected_chunk_ids": ["a"]}]}),
        encoding="utf-8",
    )
    return path


class TestParseArgs:
    def test_defaults(self, entry):
        args = entry.parse_args([])
        assert args.evaluator == "composite"
        assert args.top_k is None
        assert args.collection is None
        assert args.test_set is None

    def test_all_flags(self, entry):
        args = entry.parse_args(
            ["--test-set", "a.json", "--top-k", "5", "--collection", "docs", "--evaluator", "custom"]
        )
        assert args.test_set == "a.json"
        assert args.top_k == 5
        assert args.collection == "docs"
        assert args.evaluator == "custom"

    def test_invalid_evaluator_choice(self, entry, capsys):
        with pytest.raises(SystemExit):
            entry.parse_args(["--evaluator", "nope"])


class TestMain:
    def test_ok_exit_0_and_renders_metrics(self, entry, test_set, capsys):
        runner = FakeRunner(_fake_report())
        code = entry.main(["--test-set", str(test_set)], runner=runner, out=print)
        assert code == 0
        assert runner.calls[0]["test_set_path"] == str(test_set)
        rendered = capsys.readouterr().out
        assert "hit_rate" in rendered
        assert "1/1 evaluated" in rendered

    def test_collection_builds_filters(self, entry, test_set):
        runner = FakeRunner(_fake_report())
        entry.main(
            ["--test-set", str(test_set), "--collection", "docs"],
            runner=runner,
            out=lambda s: None,
        )
        assert runner.calls[0]["filters"] == {"collection": "docs"}

    def test_no_collection_no_filters(self, entry, test_set):
        runner = FakeRunner(_fake_report())
        entry.main(["--test-set", str(test_set)], runner=runner, out=lambda s: None)
        assert runner.calls[0]["filters"] is None

    def test_top_k_forwarded(self, entry, test_set):
        runner = FakeRunner(_fake_report())
        entry.main(
            ["--test-set", str(test_set), "--top-k", "7"], runner=runner, out=lambda s: None
        )
        assert runner.calls[0]["top_k"] == 7

    def test_missing_test_set_exit_2(self, entry, tmp_path, capsys):
        runner = FakeRunner(_fake_report())
        code = entry.main(
            ["--test-set", str(tmp_path / "nope.json")], runner=runner, out=print
        )
        assert code == 2
        assert "not found" in capsys.readouterr().out

    def test_invalid_top_k_exit_2(self, entry, test_set, capsys):
        runner = FakeRunner(_fake_report())
        code = entry.main(
            ["--test-set", str(test_set), "--top-k", "0"], runner=runner, out=print
        )
        assert code == 2

    def test_bad_config_exit_2(self, entry, tmp_path, capsys):
        code = entry.main(
            ["--config", str(tmp_path / "missing.yaml")], out=print
        )
        assert code == 2

    def test_run_value_error_exit_1(self, entry, test_set, capsys):
        from core.query_engine.hybrid_search import HybridSearchError

        runner = FakeRunner(error=HybridSearchError("down"))
        code = entry.main(["--test-set", str(test_set)], runner=runner, out=print)
        assert code == 1
        assert "failed" in capsys.readouterr().out

    def test_partial_failure_still_exit_0(self, entry, test_set, capsys):
        from observability.evaluation.eval_runner import EvalReport, QueryEvalResult

        report = EvalReport(
            total=2,
            evaluated=1,
            failed=1,
            metrics={"hit_rate": 1.0},
            per_query=[
                QueryEvalResult("q1", ["a"], ["a"], {"hit_rate": 1.0}),
                QueryEvalResult("bad", [], ["x"], {}, error="boom"),
            ],
        )
        code = entry.main(
            ["--test-set", str(test_set)], runner=FakeRunner(report), out=print
        )
        assert code == 0
        rendered = capsys.readouterr().out
        assert "error: boom" in rendered


class TestDefaults:
    def test_default_test_set_resolves_from_settings(self, entry, monkeypatch, tmp_path):
        # settings.evaluation.golden_test_set 相对路径 → 相对仓库根解析。
        from core.settings import load_settings

        monkeypatch.chdir(tmp_path)
        settings = load_settings(str(_REPO_ROOT / "config" / "settings.yaml"))
        path = entry.resolve_test_set_path(
            entry.parse_args([]), settings
        )
        assert path.name == "golden_test_set.json"
        assert path.is_file()

    def test_build_runner_custom_backend(self, entry, monkeypatch, tmp_path):
        from core.settings import load_settings
        from libs.evaluator.custom_evaluator import CustomEvaluator

        monkeypatch.chdir(tmp_path)
        settings = load_settings(str(_REPO_ROOT / "config" / "settings.yaml"))
        runner = entry.build_runner(settings, "custom")
        assert isinstance(runner.evaluator, CustomEvaluator)
