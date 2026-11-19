"""scripts/eval_mode_router.py 离线单元测试（MODE_ROUTER_SPEC MR0.2）。经 importlib 按路径加载，httpx.MockTransport 注入，零网络。"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV = {"CLOUDFLARE_ACCOUNT_ID": "acct-test", "MODE_ROUTER_API_TOKEN": "tok-test"}


def _load_entry() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "eval_mode_router_entry", _REPO_ROOT / "scripts" / "eval_mode_router.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_mode_router_entry"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def entry() -> ModuleType:
    return _load_entry()


def _wrapped(choice: str, confidence: float = 0.9, tokens: int = 200) -> dict:
    return {
        "result": {
            "model": "clef",
            "answers": {
                "retrieval_mode": {
                    "type": "choice",
                    "choice": choice,
                    "probabilities": {"hybrid": 0.1, "agentic_fast": 0.1, "agentic_deep": 0.8},
                    "confidence": confidence,
                }
            },
            "usage": {"input_tokens": tokens, "output_tokens": 0},
        },
        "success": True,
        "errors": [],
        "messages": [],
    }


def _write_set(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "set.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return path


def _result(entry, i, expected, predicted, confidence=0.9, lang="zh", error=None):
    return entry.ItemResult(
        id=f"q{i}",
        query=f"query {i}",
        lang=lang,
        expected=expected,
        predicted=predicted,
        confidence=confidence,
        probabilities={},
        latency_ms=100.0 * (i + 1),
        input_tokens=200,
        error=error,
    )


def test_repo_labeled_set_is_valid_and_balanced(entry) -> None:
    items = entry.load_labeled_set(entry.DEFAULT_LABELED_SET)
    counts = {mode: sum(i.expected == mode for i in items) for mode in entry.MODES}
    assert all(n >= 20 for n in counts.values()), counts
    zh = sum(i.lang == "zh" for i in items)
    assert zh > len(items) / 2


def test_repo_holdout_set_is_valid_and_disjoint(entry) -> None:
    holdout = entry.load_labeled_set(entry.DEFAULT_LABELED_SET.with_name("holdout_set.jsonl"))
    counts = {mode: sum(i.expected == mode for i in holdout) for mode in entry.MODES}
    assert all(n >= 10 for n in counts.values()), counts
    labeled = entry.load_labeled_set(entry.DEFAULT_LABELED_SET)
    assert not {i.query for i in holdout} & {i.query for i in labeled}
    assert not {i.id for i in holdout} & {i.id for i in labeled}


@pytest.mark.parametrize(
    "row, message",
    [
        ({"id": "a", "query": "q", "expected": "auto", "lang": "zh"}, "expected must be one of"),
        ({"id": "a", "query": "", "expected": "hybrid", "lang": "zh"}, "missing fields"),
    ],
)
def test_load_labeled_set_rejects_bad_rows(entry, tmp_path, row, message) -> None:
    with pytest.raises(entry.UsageError, match=message):
        entry.load_labeled_set(_write_set(tmp_path, [row]))


def test_load_labeled_set_rejects_duplicate_ids(entry, tmp_path) -> None:
    row = {"id": "a", "query": "q", "expected": "hybrid", "lang": "zh"}
    with pytest.raises(entry.UsageError, match="duplicate id"):
        entry.load_labeled_set(_write_set(tmp_path, [row, row]))


def test_build_request_uses_all_three_candidates(entry) -> None:
    body = entry.build_request("问题", model="clef-flash", criteria_lang="zh")
    assert body["model"] == "clef-flash"
    assert body["state"] == "问题"
    question = body["questions"]["retrieval_mode"]
    assert question["type"] == "choice"
    assert set(question["criteria"]) == set(entry.MODES)
    for variant in entry.CRITERIA.values():
        assert set(variant["criteria"]) == set(entry.MODES)


def test_parse_decision_wrapped_and_unwrapped(entry) -> None:
    wrapped = entry.parse_decision(_wrapped("agentic_deep", 0.74, 210))
    assert wrapped.choice == "agentic_deep"
    assert wrapped.confidence == pytest.approx(0.74)
    assert wrapped.probabilities["agentic_deep"] == pytest.approx(0.8)
    assert wrapped.input_tokens == 210
    assert wrapped.error is None

    unwrapped = entry.parse_decision(_wrapped("hybrid")["result"])
    assert unwrapped.choice == "hybrid"


@pytest.mark.parametrize(
    "payload, error",
    [
        ({"result": None, "success": False, "errors": [{"code": 1}], "messages": []}, "error"),
        ({"result": {"answers": {}}, "success": True, "errors": [], "messages": []}, "invalid_output"),
        (
            {"result": {"answers": {"retrieval_mode": {"type": "boolean", "value": True}}}, "success": True},
            "invalid_output",
        ),
        ("not a dict", "invalid_output"),
    ],
)
def test_parse_decision_errors(entry, payload, error) -> None:
    decision = entry.parse_decision(payload)
    assert decision.choice is None
    assert decision.error == error


def test_parse_decision_rejects_choice_outside_candidates(entry) -> None:
    decision = entry.parse_decision(_wrapped("auto"))
    assert decision.choice is None
    assert decision.error == "invalid_output"


def test_compute_metrics_rates_and_confusion(entry) -> None:
    results = [
        _result(entry, 0, "hybrid", "hybrid", 0.95),
        _result(entry, 1, "hybrid", "agentic_deep", 0.55),
        _result(entry, 2, "agentic_fast", "hybrid", 0.65, lang="en"),
        _result(entry, 3, "agentic_fast", "agentic_fast", 0.85),
        _result(entry, 4, "agentic_deep", "agentic_deep", 0.75),
        _result(entry, 5, "agentic_deep", "invalid", None, error="invalid_output"),
    ]
    m = entry.compute_metrics(results, min_confidence_grid=[0.0, 0.7])
    assert m["n"] == 6
    assert m["hit_rate"] == pytest.approx(3 / 6)
    assert m["by_lang"]["en"] == {"n": 1, "hit_rate": 0.0}
    assert m["confusion"]["hybrid"] == {"hybrid": 1, "agentic_fast": 0, "agentic_deep": 1, "invalid": 0}
    assert m["confusion"]["agentic_deep"]["invalid"] == 1
    # 期望非 deep 的 4 条里 1 条选了 deep；期望非 hybrid 的 4 条里 1 条选了 hybrid。
    assert m["deep_false_selection_rate"] == pytest.approx(1 / 4)
    assert m["hybrid_false_downgrade_rate"] == pytest.approx(1 / 4)
    assert m["invalid_rate"] == pytest.approx(1 / 6)
    assert m["errors"] == {"invalid_output": 1}

    buckets = {b["bucket"]: b for b in m["confidence_buckets"]}
    assert buckets["[0.9, 1.0]"] == {"bucket": "[0.9, 1.0]", "n": 1, "hit_rate": 1.0}
    assert buckets["[0.5, 0.6)"]["hit_rate"] == 0.0

    sweep = {s["min_confidence"]: s for s in m["min_confidence_sweep"]}
    assert sweep[0.0]["coverage"] == pytest.approx(5 / 6)
    # >= 0.7 覆盖 q0/q3/q4（全中）；其余回退 hybrid：q1 期望 hybrid 计中，q2/q5 不中。
    assert sweep[0.7]["coverage"] == pytest.approx(3 / 6)
    assert sweep[0.7]["covered_hit_rate"] == 1.0
    assert sweep[0.7]["effective_hit_rate_with_fallback"] == pytest.approx(4 / 6)

    assert m["latency_ms"]["p50"] == pytest.approx(350.0)
    assert m["avg_input_tokens"] == pytest.approx(200.0)


def test_percentile_interpolates(entry) -> None:
    assert entry.percentile([], 0.5) is None
    assert entry.percentile([7.0], 0.95) == 7.0
    assert entry.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.95) == pytest.approx(4.8)


def test_main_end_to_end_with_mock_transport(entry, tmp_path) -> None:
    rows = [
        {"id": "h1", "query": "LoRA 是什么", "expected": "hybrid", "lang": "zh"},
        {"id": "f1", "query": "哪个文件提到了 RLCD", "expected": "agentic_fast", "lang": "zh"},
        {"id": "d1", "query": "compare three frameworks", "expected": "agentic_deep", "lang": "en"},
    ]
    answers = {"LoRA 是什么": "hybrid", "哪个文件提到了 RLCD": "hybrid", "compare three frameworks": "agentic_deep"}
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        return httpx.Response(200, json=_wrapped(answers[body["state"]]))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = io.StringIO()
    json_out = tmp_path / "report.json"
    code = entry.main(
        ["--labeled-set", str(_write_set(tmp_path, rows)), "--models", "clef-flash", "--criteria", "en",
         "--json-out", str(json_out)],
        client=client,
        env=_ENV,
        out=out,
    )
    assert code == entry.EXIT_OK
    # 1 次预热 + 3 条用例
    assert len(seen) == 4
    assert all(r.headers["Authorization"] == "Bearer tok-test" for r in seen)
    assert all(str(r.url).endswith("/accounts/acct-test/ai/run/@cf/cloudflare/clef-flash") for r in seen)
    text = out.getvalue()
    assert "model=clef-flash criteria=en" in text
    assert "hit rate: 66.7%" in text
    assert "f1 expected=agentic_fast predicted=hybrid" in text

    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["runs"][0]["metrics"]["hit_rate"] == pytest.approx(2 / 3)
    dumped = json_out.read_text(encoding="utf-8")
    assert "tok-test" not in dumped and "acct-test" not in dumped


def test_main_warmup_auth_failure_is_run_failure(entry, tmp_path) -> None:
    rows = [{"id": "h1", "query": "q", "expected": "hybrid", "lang": "zh"}]
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"success": False})))
    out = io.StringIO()
    code = entry.main(
        ["--labeled-set", str(_write_set(tmp_path, rows)), "--models", "clef", "--criteria", "zh"],
        client=client,
        env=_ENV,
        out=out,
    )
    assert code == entry.EXIT_RUN_FAILED
    assert "warmup failed (http_401)" in out.getvalue()


def test_main_transport_errors_are_classified(entry, tmp_path) -> None:
    rows = [{"id": "h1", "query": "q", "expected": "hybrid", "lang": "zh"}]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=_wrapped("hybrid"))
        raise httpx.ReadTimeout("slow", request=request)

    out = io.StringIO()
    code = entry.main(
        ["--labeled-set", str(_write_set(tmp_path, rows)), "--models", "clef", "--criteria", "zh"],
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        env=_ENV,
        out=out,
    )
    assert code == entry.EXIT_OK
    assert "errors: timeout=1" in out.getvalue()


@pytest.mark.parametrize(
    "argv, env",
    [
        ([], {}),
        (["--models", "jev"], _ENV),
        (["--min-confidence-grid", "1.5"], _ENV),
        (["--limit", "0"], _ENV),
    ],
)
def test_main_usage_errors(entry, argv, env) -> None:
    code = entry.main(argv, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
                      env=env, out=io.StringIO())
    assert code == entry.EXIT_USAGE_ERROR
