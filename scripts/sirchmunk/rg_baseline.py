#!/usr/bin/env python3
"""裸 rg 基线（SIRCHMUNK_INTEGRATION_SPEC 第 7 章 G0 重开路径 ②）。

对 ``tests/fixtures/agentic/golden_questions.json`` 逐题做**确定性分词**
（无 LLM），用 ``rg -il`` 在仓库根检索，source 级对比 ``expected_sources``。

目的：为 G0 提供「不用 agentic、只靠词法检索能到多少」的下界基线。
若 rg 基线命中明显高于 sidecar（复测 2/6），则 sidecar 增益论证不成立。

口径与 compare.py 对齐：
- 检索根 = 仓库根，``-g '!tests/fixtures/**'`` 排除问题集自身（防泄题）；
- rg 默认遵循 .gitignore（data/、cache/、logs/ 等自动排除）；
- 命中判定（source 级）：该题全部 ``expected_sources`` 中出现在 rg 文件列表
  的比例 ≥ 50% 记命中，同时输出逐文件明细。

用法：``python scripts/sirchmunk/rg_baseline.py [--output <json>]``
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/agentic/golden_questions.json"
REPO_ROOT = Path(__file__).resolve().parents[2]

# 与 compare.py 相同的泄题排除口径
EXCLUDE_GLOBS = ["tests/fixtures/**"]

# 确定性停用词（疑问词/功能词）。刻意保持极小：基线越朴素越有说服力。
STOPWORDS = {
    "a", "an", "and", "are", "by", "does", "for", "from", "how", "in", "into",
    "is", "it", "of", "on", "or", "s", "the", "their", "to", "what", "which",
    "according", "recorded", "recorded", "produced", "travels", "flow",
}


def extract_keywords(query: str) -> list[str]:
    """确定性分词：保留含数字/大写/下划线/连字符的词与普通实词，去停用词。"""
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-\.]*", query)
    keywords: list[str] = []
    for tok in tokens:
        lowered = tok.lower().strip(".-")
        if not lowered or lowered in STOPWORDS:
            continue
        if lowered not in keywords:
            keywords.append(lowered)
    return keywords


def rg_files(keywords: list[str]) -> tuple[list[str], float]:
    """以关键词 OR 正则跑 ``rg -il``，返回（相对路径列表, 耗时秒）。"""
    pattern = "|".join(re.escape(k) for k in keywords)
    cmd = ["rg", "--no-messages", "--ignore-case", "--files-with-matches", pattern]
    for glob in EXCLUDE_GLOBS:
        cmd += ["-g", f"!{glob}"]
    start = time.perf_counter()
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    elapsed = time.perf_counter() - start
    files = sorted(line for line in proc.stdout.splitlines() if line)
    return files, elapsed


def score_question(files: list[str], expected: list[str]) -> dict:
    present = [src for src in expected if src in files]
    ratio = len(present) / len(expected) if expected else 0.0
    return {
        "hit": ratio >= 0.5,
        "matched_sources": present,
        "missing_sources": [src for src in expected if src not in files],
        "matched_ratio": round(ratio, 4),
        "rg_file_count": len(files),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Raw-rg baseline over the sirchmunk golden question set.")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path, default=None, help="把结果 JSON 写到该路径（缺省仅打印）")
    args = parser.parse_args()

    if shutil.which("rg") is None:
        print("error: rg (ripgrep) not found on PATH", file=sys.stderr)
        return 1

    fixture = args.fixture.resolve()
    data = json.loads(fixture.read_text(encoding="utf-8"))
    questions = data["questions"]

    results: list[dict] = []
    for q in questions:
        keywords = extract_keywords(q["query"])
        files, elapsed = rg_files(keywords)
        entry = {
            "id": q["id"],
            "category": q["category"],
            "query": q["query"],
            "expected_sources": q["expected_sources"],
            "keywords": keywords,
            "elapsed_ms": round(elapsed * 1000, 1),
            **score_question(files, q["expected_sources"]),
        }
        results.append(entry)
        mark = "✅" if entry["hit"] else "❌"
        print(f"{mark} {q['id']} [{q['category']}] hit={entry['matched_ratio']:.0%} "
              f"rg_files={entry['rg_file_count']} {entry['elapsed_ms']}ms")
        print(f"    keywords: {', '.join(keywords[:8])}{'…' if len(keywords) > 8 else ''}")
        print(f"    matched: {entry['matched_sources'] or '—'}  missing: {entry['missing_sources'] or '—'}")

    hits = sum(1 for r in results if r["hit"])
    by_category: dict[str, dict] = {}
    for r in results:
        cat = by_category.setdefault(r["category"], {"hit": 0, "total": 0})
        cat["total"] += 1
        cat["hit"] += 1 if r["hit"] else 0

    summary = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "fixture": str(fixture),
        "corpus_root": str(REPO_ROOT),
        "exclude_globs": EXCLUDE_GLOBS,
        "method": "deterministic tokenization (no LLM) + rg -il, source-level match >=50% of expected_sources",
        "overall": {"hit": hits, "total": len(results)},
        "by_category": by_category,
        "results": results,
    }

    print(f"\n=== 裸 rg 基线汇总：{hits}/{len(results)} ===")
    for cat, c in by_category.items():
        print(f"  {cat}: {c['hit']}/{c['total']}")

    if args.output:
        out = args.output.resolve()
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"results -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
