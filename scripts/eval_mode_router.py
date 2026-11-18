"""Mode router 决策质量评估（MODE_ROUTER_SPEC MR0.2，真实运行，不进 ``pytest tests``）。

用法::

    set -a; . ~/.config/modular-rag-mcp/mode_router.env; set +a
    python scripts/eval_mode_router.py
    python scripts/eval_mode_router.py --models clef-flash --criteria zh --limit 6
    python scripts/eval_mode_router.py --json-out logs/mode_router_eval.json

行为契约：
- 读独立标注集（默认 ``tests/fixtures/mode_router/labeled_set.jsonl``，与
  golden test set 分离），对 ``--models`` × ``--criteria`` 的每个组合逐条调用
  Cloudflare Workers AI 上的 Clef（System One ``choice`` 问题，候选固定为三档）。
- 每个组合输出：总体命中率、按语言命中率、三档混淆矩阵（含 ``invalid`` 列）、
  deep 误选率（期望非 deep 却选 deep）、hybrid 误降率（期望非 hybrid 却选
  hybrid）、非法输出率与错误分类、置信度分桶命中率、不同 ``min_confidence``
  下的覆盖率 / 覆盖内命中率 / 回退 ``--fallback`` 后的整体命中率、决策延迟
  p50 / p95、平均输入 token。
- 延迟口径：同一 ``httpx.Client`` 复用连接，每个组合先发一次预热请求（不计入
  统计），之后串行测量，反映连接复用后的真实往返。
- 凭据只从环境变量 ``CLOUDFLARE_ACCOUNT_ID`` / ``MODE_ROUTER_API_TOKEN`` 读取，
  不写入报告与 JSON 输出。
- MR0.2 期间请求构造与 criteria 文案在本脚本内（门控通过前不改 ``src/``）；
  MR2 起移入 ``libs/mode_router/routing_question.py``。

可测试性：解析 / 请求构造 / 指标 / 渲染为可导入纯函数，``main(argv, *,
client=..., env=..., out=...)`` 支持注入 ``httpx.MockTransport`` 客户端。

退出码：``0`` = 评估完成（含部分请求失败）；``1`` = 整批失败（预热请求失败，
如鉴权错误或网络不可达）；``2`` = 用法 / 配置错误（缺凭据、标注集非法）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, TextIO

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LABELED_SET = REPO_ROOT / "tests" / "fixtures" / "mode_router" / "labeled_set.jsonl"
ENDPOINT_TEMPLATE = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/cloudflare/{model}"

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_USAGE_ERROR = 2

MODES: tuple[str, ...] = ("hybrid", "agentic_fast", "agentic_deep")
MODELS: tuple[str, ...] = ("clef", "clef-flash")
QUESTION_KEY = "retrieval_mode"
INVALID = "invalid"

# zh 为 en 的直译，两者只差语言，便于单独比较语言对决策的影响。
CRITERIA: dict[str, dict[str, Any]] = {
    "en": {
        "instructions": "Which retrieval mode should answer this question?",
        "criteria": {
            "hybrid": "Answerable from the already-ingested knowledge base with keyword and semantic search; fastest",
            "agentic_fast": "Needs one exploratory scan of allowed local directories beyond the ingested corpus",
            "agentic_deep": "Needs multi-step investigation across many files; slowest and most expensive",
        },
    },
    "zh": {
        "instructions": "应由哪种检索模式回答这个问题？",
        "criteria": {
            "hybrid": "可用关键词与语义检索从已摄取的知识库中回答；最快",
            "agentic_fast": "需要对允许访问的本地目录做一次探索式扫描，范围超出已摄取的语料",
            "agentic_deep": "需要跨多个文件的多步调查；最慢、成本最高",
        },
    },
}

CONFIDENCE_BUCKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.5),
    (0.5, 0.6),
    (0.6, 0.7),
    (0.7, 0.8),
    (0.8, 0.9),
    (0.9, 1.0),
)
DEFAULT_MIN_CONFIDENCE_GRID: tuple[float, ...] = (0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


class UsageError(Exception):
    """用法 / 配置错误（退出码 2）。"""


@dataclass(frozen=True)
class LabeledItem:
    id: str
    query: str
    expected: str
    lang: str
    note: str = ""


@dataclass(frozen=True)
class Decision:
    choice: str | None
    confidence: float | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    input_tokens: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class ItemResult:
    id: str
    query: str
    lang: str
    expected: str
    predicted: str
    confidence: float | None
    probabilities: dict[str, float]
    latency_ms: float
    input_tokens: int | None
    error: str | None

    @property
    def hit(self) -> bool:
        return self.predicted == self.expected


def load_labeled_set(path: Path) -> list[LabeledItem]:
    if not path.is_file():
        raise UsageError(f"labeled set not found: {path}")
    items: list[LabeledItem] = []
    seen: set[str] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise UsageError(f"{path}:{lineno}: invalid JSON ({exc.msg})") from exc
        if not isinstance(row, dict):
            raise UsageError(f"{path}:{lineno}: each line must be a JSON object")
        missing = [k for k in ("id", "query", "expected", "lang") if not row.get(k)]
        if missing:
            raise UsageError(f"{path}:{lineno}: missing fields {missing}")
        if row["expected"] not in MODES:
            raise UsageError(f"{path}:{lineno}: expected must be one of {list(MODES)}, got {row['expected']!r}")
        if row["id"] in seen:
            raise UsageError(f"{path}:{lineno}: duplicate id {row['id']!r}")
        seen.add(row["id"])
        items.append(
            LabeledItem(
                id=str(row["id"]),
                query=str(row["query"]),
                expected=row["expected"],
                lang=str(row["lang"]),
                note=str(row.get("note", "")),
            )
        )
    if not items:
        raise UsageError(f"labeled set is empty: {path}")
    return items


def build_request(query: str, *, model: str, criteria_lang: str) -> dict[str, Any]:
    spec = CRITERIA[criteria_lang]
    return {
        "model": model,
        "state": query,
        "questions": {
            QUESTION_KEY: {
                "type": "choice",
                "instructions": spec["instructions"],
                "criteria": dict(spec["criteria"]),
            }
        },
    }


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def parse_decision(payload: Any, candidates: Sequence[str] = MODES) -> Decision:
    """解析 System One 答案，同时接受 Workers AI ``{result: ...}`` 包装与无包装形态。"""
    if not isinstance(payload, dict):
        return Decision(choice=None, error="invalid_output")
    if "result" in payload and ("success" in payload or "errors" in payload):
        if payload.get("success") is False:
            return Decision(choice=None, error="error")
        body = payload.get("result")
    else:
        body = payload
    if not isinstance(body, dict):
        return Decision(choice=None, error="invalid_output")

    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    tokens_raw = usage.get("input_tokens", usage.get("prompt_tokens"))
    input_tokens = tokens_raw if isinstance(tokens_raw, int) and not isinstance(tokens_raw, bool) else None

    answers = body.get("answers")
    answer = answers.get(QUESTION_KEY) if isinstance(answers, dict) else None
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        return Decision(choice=None, input_tokens=input_tokens, error="invalid_output")
    choice = answer.get("choice")
    probabilities_raw = answer.get("probabilities")
    probabilities = (
        {str(k): f for k, v in probabilities_raw.items() if (f := _to_float(v)) is not None}
        if isinstance(probabilities_raw, dict)
        else {}
    )
    confidence = _to_float(answer.get("confidence"))
    if choice not in candidates:
        return Decision(
            choice=None,
            confidence=confidence,
            probabilities=probabilities,
            input_tokens=input_tokens,
            error="invalid_output",
        )
    return Decision(
        choice=choice,
        confidence=confidence,
        probabilities=probabilities,
        input_tokens=input_tokens,
    )


def call_router(
    client: httpx.Client,
    *,
    url: str,
    token: str,
    body: Mapping[str, Any],
) -> tuple[Decision, float]:
    """发一次请求，返回 (决策, 毫秒延迟)。网络与 HTTP 错误归类为 Decision.error。"""
    start = time.perf_counter()
    try:
        response = client.post(url, json=body, headers={"Authorization": f"Bearer {token}"})
    except httpx.TimeoutException:
        return Decision(choice=None, error="timeout"), (time.perf_counter() - start) * 1000
    except httpx.TransportError:
        return Decision(choice=None, error="unreachable"), (time.perf_counter() - start) * 1000
    latency_ms = (time.perf_counter() - start) * 1000
    if response.status_code >= 400:
        return Decision(choice=None, error=f"http_{response.status_code}"), latency_ms
    try:
        payload = response.json()
    except ValueError:
        return Decision(choice=None, error="invalid_output"), latency_ms
    return parse_decision(payload), latency_ms


def percentile(values: Sequence[float], q: float) -> float | None:
    """线性插值分位数（q ∈ [0, 1]）。"""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lower = int(pos)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (pos - lower)


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def _bucket_label(low: float, high: float) -> str:
    closing = "]" if high >= 1.0 else ")"
    return f"[{low:.1f}, {high:.1f}{closing}"


def compute_metrics(
    results: Sequence[ItemResult],
    *,
    min_confidence_grid: Iterable[float] = DEFAULT_MIN_CONFIDENCE_GRID,
    fallback: str = "hybrid",
) -> dict[str, Any]:
    total = len(results)
    hits = sum(r.hit for r in results)
    valid = [r for r in results if r.predicted != INVALID]

    by_lang: dict[str, dict[str, Any]] = {}
    for lang in sorted({r.lang for r in results}):
        subset = [r for r in results if r.lang == lang]
        by_lang[lang] = {"n": len(subset), "hit_rate": _rate(sum(r.hit for r in subset), len(subset))}

    by_expected: dict[str, dict[str, Any]] = {}
    confusion: dict[str, dict[str, int]] = {}
    for mode in MODES:
        subset = [r for r in results if r.expected == mode]
        by_expected[mode] = {"n": len(subset), "hit_rate": _rate(sum(r.hit for r in subset), len(subset))}
        confusion[mode] = {col: sum(r.predicted == col for r in subset) for col in (*MODES, INVALID)}

    non_deep = [r for r in results if r.expected != "agentic_deep"]
    non_hybrid = [r for r in results if r.expected != "hybrid"]

    errors: dict[str, int] = {}
    for r in results:
        if r.error:
            errors[r.error] = errors.get(r.error, 0) + 1

    buckets = []
    for low, high in CONFIDENCE_BUCKETS:
        in_bucket = [
            r
            for r in valid
            if r.confidence is not None
            and low <= r.confidence
            and (r.confidence < high or (high >= 1.0 and r.confidence <= high))
        ]
        buckets.append(
            {
                "bucket": _bucket_label(low, high),
                "n": len(in_bucket),
                "hit_rate": _rate(sum(r.hit for r in in_bucket), len(in_bucket)),
            }
        )

    sweep = []
    for threshold in min_confidence_grid:
        covered = [r for r in valid if r.confidence is not None and r.confidence >= threshold]
        covered_ids = {r.id for r in covered}
        # 未覆盖（低置信度或非法输出）的用例按运行时语义回退到 fallback 模式。
        effective_hits = sum(r.hit if r.id in covered_ids else r.expected == fallback for r in results)
        sweep.append(
            {
                "min_confidence": threshold,
                "coverage": _rate(len(covered), total),
                "covered_hit_rate": _rate(sum(r.hit for r in covered), len(covered)),
                "effective_hit_rate_with_fallback": _rate(effective_hits, total),
            }
        )

    latencies = [r.latency_ms for r in results]
    tokens = [r.input_tokens for r in results if r.input_tokens is not None]
    return {
        "n": total,
        "hit_rate": _rate(hits, total),
        "by_lang": by_lang,
        "by_expected": by_expected,
        "confusion": confusion,
        "deep_false_selection_rate": _rate(sum(r.predicted == "agentic_deep" for r in non_deep), len(non_deep)),
        "hybrid_false_downgrade_rate": _rate(sum(r.predicted == "hybrid" for r in non_hybrid), len(non_hybrid)),
        "invalid_rate": _rate(total - len(valid), total),
        "errors": errors,
        "confidence_buckets": buckets,
        "min_confidence_sweep": sweep,
        "fallback": fallback,
        "latency_ms": {
            "p50": percentile(latencies, 0.5),
            "p95": percentile(latencies, 0.95),
            "mean": sum(latencies) / len(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
        "avg_input_tokens": sum(tokens) / len(tokens) if tokens else None,
    }


def _fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _fmt_ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f} ms"


def render_run(model: str, criteria_lang: str, metrics: Mapping[str, Any], warmup_ms: float | None) -> str:
    lines = [f"== model={model} criteria={criteria_lang} (n={metrics['n']}) =="]
    lines.append(f"hit rate: {_fmt_rate(metrics['hit_rate'])}")
    lang_part = ", ".join(f"{lang} {_fmt_rate(v['hit_rate'])} (n={v['n']})" for lang, v in metrics["by_lang"].items())
    lines.append(f"hit rate by query language: {lang_part}")
    mode_part = ", ".join(
        f"{mode} {_fmt_rate(v['hit_rate'])} (n={v['n']})" for mode, v in metrics["by_expected"].items()
    )
    lines.append(f"hit rate by expected mode: {mode_part}")
    lines.append(f"deep false-selection rate: {_fmt_rate(metrics['deep_false_selection_rate'])}")
    lines.append(f"hybrid false-downgrade rate: {_fmt_rate(metrics['hybrid_false_downgrade_rate'])}")
    errors = ", ".join(f"{k}={v}" for k, v in sorted(metrics["errors"].items())) or "none"
    lines.append(f"invalid-output rate: {_fmt_rate(metrics['invalid_rate'])} (errors: {errors})")

    cols = (*MODES, INVALID)
    width = max(len(c) for c in cols) + 2
    lines.append("confusion matrix (rows = expected, cols = predicted):")
    lines.append(" " * width + "".join(c.rjust(width) for c in cols))
    for mode in MODES:
        row = metrics["confusion"][mode]
        lines.append(mode.ljust(width) + "".join(str(row[c]).rjust(width) for c in cols))

    lines.append("confidence buckets:")
    for b in metrics["confidence_buckets"]:
        lines.append(f"  {b['bucket']:<12} n={b['n']:<3} hit {_fmt_rate(b['hit_rate'])}")
    lines.append(f"min_confidence sweep (uncovered -> fallback {metrics['fallback']}):")
    for s in metrics["min_confidence_sweep"]:
        lines.append(
            f"  >= {s['min_confidence']:.2f}  coverage {_fmt_rate(s['coverage'])}"
            f"  covered hit {_fmt_rate(s['covered_hit_rate'])}"
            f"  effective hit {_fmt_rate(s['effective_hit_rate_with_fallback'])}"
        )
    lat = metrics["latency_ms"]
    lines.append(
        f"latency (connection reused): p50 {_fmt_ms(lat['p50'])}, p95 {_fmt_ms(lat['p95'])},"
        f" mean {_fmt_ms(lat['mean'])}, max {_fmt_ms(lat['max'])}; warmup {_fmt_ms(warmup_ms)}"
    )
    tokens = metrics["avg_input_tokens"]
    lines.append(f"avg input tokens: {'n/a' if tokens is None else f'{tokens:.1f}'}")
    return "\n".join(lines)


def render_misses(results: Sequence[ItemResult]) -> str:
    misses = [r for r in results if not r.hit]
    if not misses:
        return "misses: none"
    lines = ["misses:"]
    for r in misses:
        conf = "n/a" if r.confidence is None else f"{r.confidence:.2f}"
        reason = f" [{r.error}]" if r.error else ""
        lines.append(f"  {r.id} expected={r.expected} predicted={r.predicted} conf={conf}{reason}  {r.query}")
    return "\n".join(lines)


def run_combo(
    client: httpx.Client,
    items: Sequence[LabeledItem],
    *,
    url: str,
    token: str,
    model: str,
    criteria_lang: str,
) -> tuple[list[ItemResult], Decision, float]:
    warmup, warmup_ms = call_router(
        client, url=url, token=token, body=build_request(items[0].query, model=model, criteria_lang=criteria_lang)
    )
    results: list[ItemResult] = []
    if warmup.error and warmup.error != "invalid_output":
        return results, warmup, warmup_ms
    for item in items:
        decision, latency_ms = call_router(
            client, url=url, token=token, body=build_request(item.query, model=model, criteria_lang=criteria_lang)
        )
        results.append(
            ItemResult(
                id=item.id,
                query=item.query,
                lang=item.lang,
                expected=item.expected,
                predicted=decision.choice or INVALID,
                confidence=decision.confidence,
                probabilities=decision.probabilities,
                latency_ms=latency_ms,
                input_tokens=decision.input_tokens,
                error=decision.error,
            )
        )
    return results, warmup, warmup_ms


def _csv(value: str, allowed: Sequence[str], flag: str) -> list[str]:
    parts = [p.strip() for p in value.split(",") if p.strip()]
    bad = [p for p in parts if p not in allowed]
    if not parts or bad:
        raise UsageError(f"{flag} must be a comma-separated subset of {list(allowed)}, got {value!r}")
    return parts


def _float_grid(value: str) -> list[float]:
    try:
        grid = [float(p) for p in value.split(",") if p.strip()]
    except ValueError as exc:
        raise UsageError(f"--min-confidence-grid must be comma-separated numbers, got {value!r}") from exc
    if not grid or any(not 0.0 <= g <= 1.0 for g in grid):
        raise UsageError("--min-confidence-grid values must be in [0, 1]")
    return grid


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MR0.2 mode router decision-quality baseline (real Clef calls).")
    parser.add_argument("--labeled-set", type=Path, default=DEFAULT_LABELED_SET)
    parser.add_argument("--models", default=",".join(MODELS), help="comma-separated: clef,clef-flash")
    parser.add_argument("--criteria", default="zh,en", help="criteria wording languages: zh,en")
    parser.add_argument("--min-confidence-grid", default=",".join(str(g) for g in DEFAULT_MIN_CONFIDENCE_GRID))
    parser.add_argument("--fallback", default="hybrid", choices=MODES)
    parser.add_argument("--limit", type=int, default=None, help="only the first N items (pipeline check)")
    parser.add_argument("--timeout", type=float, default=15.0, help="per-request timeout in seconds")
    parser.add_argument("--json-out", type=Path, default=None, help="write per-item results and metrics as JSON")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    client: httpx.Client | None = None,
    env: Mapping[str, str] | None = None,
    out: TextIO | None = None,
) -> int:
    out = out or sys.stdout
    env = os.environ if env is None else env
    args = build_parser().parse_args(argv)
    try:
        models = _csv(args.models, MODELS, "--models")
        langs = _csv(args.criteria, tuple(CRITERIA), "--criteria")
        grid = _float_grid(args.min_confidence_grid)
        if args.limit is not None and args.limit < 1:
            raise UsageError("--limit must be >= 1")
        items = load_labeled_set(args.labeled_set)
        account_id = env.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
        token = env.get("MODE_ROUTER_API_TOKEN", "").strip()
        if not account_id or not token:
            raise UsageError("CLOUDFLARE_ACCOUNT_ID and MODE_ROUTER_API_TOKEN must be set")
    except UsageError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return EXIT_USAGE_ERROR
    if args.limit is not None:
        items = items[: args.limit]

    owns_client = client is None
    client = client or httpx.Client(timeout=args.timeout)
    report: dict[str, Any] = {"labeled_set": str(args.labeled_set), "n_items": len(items), "runs": []}
    failed = False
    try:
        for model in models:
            url = ENDPOINT_TEMPLATE.format(account_id=account_id, model=model)
            for lang in langs:
                results, warmup, warmup_ms = run_combo(
                    client, items, url=url, token=token, model=model, criteria_lang=lang
                )
                if not results:
                    print(f"== model={model} criteria={lang}: warmup failed ({warmup.error}), skipped ==", file=out)
                    failed = True
                    continue
                metrics = compute_metrics(results, min_confidence_grid=grid, fallback=args.fallback)
                print(render_run(model, lang, metrics, warmup_ms), file=out)
                print(render_misses(results), file=out)
                print(file=out)
                report["runs"].append(
                    {
                        "model": model,
                        "criteria": lang,
                        "criteria_text": CRITERIA[lang],
                        "warmup_ms": warmup_ms,
                        "metrics": metrics,
                        "items": [asdict(r) for r in results],
                    }
                )
    finally:
        if owns_client:
            client.close()

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {args.json_out}", file=out)
    if failed and not report["runs"]:
        return EXIT_RUN_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
