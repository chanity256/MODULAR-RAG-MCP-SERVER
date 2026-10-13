#!/usr/bin/env python3
"""S0.2 Golden 问题旁路对比脚本（SIRCHMUNK_INTEGRATION_SPEC 第 7 章）。

对 ``tests/fixtures/agentic/golden_questions.json`` 逐条调用 sidecar
``POST /api/v1/search``（``response_format=context``），记录：
命中文件（source 级）、耗时、``llm_call_count``、``total_llm_tokens``，
输出 Markdown 结果表（回填规格文档进度跟踪表用）。

用法:
    python scripts/sirchmunk/compare.py \
        --base-url http://127.0.0.1:8584 \
        --token "$SIRCHMUNK_API_TOKEN" \
        --repo-root /path/to/this/repo

测延迟时用 ``caffeinate -i python scripts/sirchmunk/compare.py ...`` 防止系统睡眠；
墙钟与 perf_counter 相差过大的条目会标记「睡眠疑似」。

退出码: 0=全部成功 / 1=存在失败调用。
需要 sidecar 已启动且 LLM_API_KEY 可用（真实调用，不在离线测试套件内）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = REPO_ROOT / "tests/fixtures/agentic/golden_questions.json"
# Golden 问题的期望来源覆盖 src/ 与根目录文档（仓库自身代码语料，spec S0.2 三类问题），
# 故旁路对比以仓库根为检索根；rg/rga 遵循 .gitignore，自动排除 .venv*/data/db 等非语料目录。
DEFAULT_ROOTS = ["."]
# macOS 上 perf_counter（mach_absolute_time）在系统睡眠期间不走，time.time() 照走；
# 两者差值超过阈值说明调用期间发生过睡眠/挂起，该条耗时不可信（S0 Q6 473s 即此情况）。
SLEEP_SUSPECT_THRESHOLD_S = 5.0
# 泄题排除：问题集 fixtures 本身，以及入库的历史结果文件（含题面与 expected_sources）。
# 字段名必须是 exclude_patterns（上游 SearchRequest），FastAPI 会静默丢弃未知字段。
# 与 rg_baseline.py 的 EXCLUDE_GLOBS 保持同一口径。
EXCLUDE_PATTERNS = ["tests/fixtures/**", "scripts/sirchmunk/*results*"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run golden questions against the sirchmunk sidecar.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8584")
    parser.add_argument("--token", default=None, help="Bearer token (SIRCHMUNK_API_TOKEN)")
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--repo-root", default=".", help="Resolve relative expected_sources against this root")
    parser.add_argument("--mode", default="FAST", choices=["FAST", "DEEP"])
    parser.add_argument("--out", default=None, help="Write Markdown report to this file")
    parser.add_argument("--json-out", default=None, help="Write per-question rows (incl. telemetry) as JSON")
    return parser


def _parse_telemetry(data: dict, mode: str) -> dict | None:
    """复用主项目 converter 的白名单解析（SX1），与 MCP 工具口径一致。"""
    src = str(REPO_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from libs.agentic_search.converter import parse_search_context

    try:
        return parse_search_context(data, mode=mode).telemetry
    except Exception:  # 旁路对比：解析失败只丢 telemetry，不丢该行
        return None


def extract_metrics(data: dict, mode: str) -> dict:
    """从 sidecar ``data`` 抽取对比所需字段；``llm_call_count`` 优先取 telemetry。"""
    cluster = data.get("cluster") or {}
    evidence_files = [
        e.get("file_or_url") for e in (cluster.get("evidences") or []) if isinstance(e, dict)
    ]
    telemetry = _parse_telemetry(data, mode)
    llm_calls = telemetry.get("llm_call_count") if telemetry else None
    if llm_calls is None:
        usages = data.get("llm_usages")
        llm_calls = len(usages) if isinstance(usages, list) else None
    return {
        "answer": str(data.get("answer") or ""),
        "evidence_files": [str(e) for e in evidence_files if e],
        "llm_call_count": llm_calls,
        "total_llm_tokens": data.get("total_llm_tokens"),
        "telemetry": telemetry,
    }


def source_hit(expected: list[str], evidence_files: list[str], repo_root: str) -> bool:
    """source 级命中：期望来源的**文件名**出现在证据文件路径中（spec S3.1 口径）。"""
    root = Path(repo_root).resolve()
    for exp in expected:
        exp_name = Path(exp).name
        for ev in evidence_files:
            ev_path = Path(ev)
            if ev_path.name == exp_name:
                try:  # 同名还需同树（相对 repo_root 比对），避免跨库同名误判
                    ev_path.resolve().relative_to(root)
                except (ValueError, OSError):
                    continue
                return True
    return False


def sleep_suspected(perf_ms: float, wall_ms: float, threshold_s: float = SLEEP_SUSPECT_THRESHOLD_S) -> bool:
    return abs(wall_ms - perf_ms) > threshold_s * 1000


def run(base_url: str, token: str | None, fixture_path: str, repo_root: str, mode: str) -> list[dict]:
    import httpx

    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    rows: list[dict] = []
    with httpx.Client(timeout=600.0) as client:  # 旁路对比不走 30s 工具超时（spec S0.2）
        for question in fixture["questions"]:
            started = time.perf_counter()
            wall_started = time.time()
            error = None
            metrics = {
                "answer": "", "evidence_files": [], "llm_call_count": None,
                "total_llm_tokens": None, "telemetry": None,
            }
            try:
                resp = client.post(
                    f"{base_url.rstrip('/')}/api/v1/search",
                    json={
                        "query": question["query"],
                        "mode": mode,
                        "response_format": "context",
                        "paths": [str((Path(repo_root) / r).resolve()) for r in DEFAULT_ROOTS],
                        "exclude_patterns": EXCLUDE_PATTERNS,
                        # 与 http_backend._build_payload 口径一致（SX2）
                        "enable_dir_scan": True,
                        "llm_fallback": False,
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                metrics = extract_metrics(resp.json().get("data") or {}, mode)
            except Exception as exc:  # 旁路对比逐条隔离
                error = f"{type(exc).__name__}: {exc}"
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            wall_elapsed = round((time.time() - wall_started) * 1000, 1)
            rows.append(
                {
                    "id": question["id"],
                    "category": question["category"],
                    "query": question["query"],
                    "mode": mode,
                    "hit": source_hit(question.get("expected_sources", []), metrics["evidence_files"], repo_root),
                    "evidence_files": metrics["evidence_files"],
                    "llm_call_count": metrics["llm_call_count"],
                    "total_llm_tokens": metrics["total_llm_tokens"],
                    "telemetry": metrics["telemetry"],
                    "elapsed_ms": elapsed,
                    "wall_elapsed_ms": wall_elapsed,
                    "sleep_suspected": sleep_suspected(elapsed, wall_elapsed),
                    "answer_preview": metrics["answer"][:160],
                    "answer": metrics["answer"],
                    "error": error,
                }
            )
    return rows


def render_markdown(rows: list[dict], mode: str) -> str:
    lines = [
        f"## S0.2 旁路对比结果（mode={mode}）",
        "",
        "| id | 类别 | source 级命中 | 耗时 ms | 墙钟 ms | 睡眠疑似 | LLM 调用 | Token | 首个证据文件 |",
        "|----|------|--------------|---------|---------|----------|----------|-------|--------------|",
    ]
    for r in rows:
        first_ev = r["evidence_files"][0] if r["evidence_files"] else (r["error"] or "-")
        lines.append(
            f"| {r['id']} | {r['category']} | {'✅' if r['hit'] else '❌'} "
            f"| {r['elapsed_ms']} | {r['wall_elapsed_ms']} | {'⚠️ 作废' if r['sleep_suspected'] else '-'} "
            f"| {r['llm_call_count'] if r['llm_call_count'] is not None else '-'} "
            f"| {r['total_llm_tokens'] if r['total_llm_tokens'] is not None else '-'} | {first_ev} |"
        )
    suspects = [r["id"] for r in rows if r["sleep_suspected"]]
    if suspects:
        lines += [
            "",
            f"⚠️ 墙钟与 perf_counter 相差 >{SLEEP_SUSPECT_THRESHOLD_S:g}s（疑似系统睡眠），"
            f"以下条目耗时作废需重测：{', '.join(suspects)}。建议用 `caffeinate -i` 包住运行。",
        ]
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    lines += ["", "分类命中汇总："]
    for cat, items in sorted(by_cat.items()):
        hits = sum(1 for i in items if i["hit"])
        lines.append(f"- {cat}: {hits}/{len(items)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rows = run(args.base_url, args.token, args.fixture, args.repo_root, args.mode)
    report = render_markdown(rows, args.mode)
    print(report)
    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if any(r["error"] for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
