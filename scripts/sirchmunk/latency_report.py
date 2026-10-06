#!/usr/bin/env python3
"""Sirchmunk sidecar 日志分段耗时报告（SIRCHMUNK_INTEGRATION_SPEC 4.5.1）。

解析 ``sirchmunk serve`` 的 stdout 日志，把每次 FAST 检索拆成：

- 查询分析 LLM：``[FAST] Starting`` → ``[FAST:Step1]``
- grep：``[FAST:Step1]`` → ``[FAST:Step3]``（rg/rga 扫描 + 证据抽取）
- 回答 LLM：``[FAST:Step3]`` → ``Search complete``（含证据验收）

并统计每次检索中 LLM HTTP 请求数、流式断连（``Connection error``）次数与
openai SDK 内部重试（``Retrying request``）次数。

用法:
    python scripts/sirchmunk/latency_report.py logs/sirchmunk-sidecar.log [--out report.md]

只读日志，不访问网络。
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 流式输出的回答正文不带换行，后续日志行的时间戳会接在正文末尾，因此按全文匹配而非逐行匹配。
_TS_RE = re.compile(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)[.,](\d{3})")
_EVIDENCE_RE = re.compile(r"Evidence: (\d+) chars")
_QUERY_RE = re.compile(r"Starting greedy search for: '(.*?)'?$")

_MARKERS = (
    ("start", "[FAST] Starting"),
    ("step1", "[FAST:Step1]"),
    ("step3", "[FAST:Step3]"),
    ("done", "Search complete"),
    ("conn_err", "Connection error"),
    ("sdk_retry", "Retrying request"),
    ("llm_http", "chat/completions"),
)


def _parse_ts(match: re.Match[str]) -> datetime:
    return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S") + timedelta(milliseconds=int(match.group(2)))


def iter_events(text: str):
    """产出 (时间, 事件类型, 该时间戳后到行尾的文本)。"""
    for match in _TS_RE.finditer(text):
        end = text.find("\n", match.start())
        line = text[match.start() : end if end != -1 else len(text)]
        # 同一物理行内可能再接一个时间戳（极少），只取到下一个时间戳为止。
        nxt = _TS_RE.search(line, len(match.group(0)))
        if nxt:
            line = line[: nxt.start()]
        for kind, needle in _MARKERS:
            if needle in line:
                yield _parse_ts(match), kind, line
                break


def parse_log(text: str) -> list[dict]:
    text = text.replace("[role=assistant] ", "")
    rows: list[dict] = []
    cur: dict | None = None
    for when, kind, line in iter_events(text):
        if kind == "start":
            q = _QUERY_RE.search(line)
            cur = {
                "start": when,
                "query": q.group(1) if q else "",
                "conn_err": 0,
                "sdk_retry": 0,
                "llm_http": 0,
                "evidence_chars": None,
            }
            rows.append(cur)
        elif cur is None or "done" in cur:
            continue
        elif kind in ("conn_err", "sdk_retry", "llm_http"):
            cur[kind] += 1
        elif kind == "step3":
            cur.setdefault("step3", when)
            ev = _EVIDENCE_RE.search(line)
            if ev and cur["evidence_chars"] is None:
                cur["evidence_chars"] = int(ev.group(1))
        else:  # step1 / done：取首次出现
            cur.setdefault(kind, when)
    return [_durations(r) for r in rows]


def _secs(a: datetime | None, b: datetime | None) -> float | None:
    return round((b - a).total_seconds(), 2) if a and b else None


def _durations(r: dict) -> dict:
    return {
        "query": r["query"],
        "analysis_s": _secs(r["start"], r.get("step1")),
        "grep_s": _secs(r.get("step1"), r.get("step3")),
        "answer_s": _secs(r.get("step3"), r.get("done")),
        "total_s": _secs(r["start"], r.get("done")),
        "evidence_chars": r["evidence_chars"],
        "llm_http": r["llm_http"],
        "conn_err": r["conn_err"],
        "sdk_retry": r["sdk_retry"],
        "complete": "done" in r,
    }


def _fmt(v: object) -> str:
    return "-" if v is None else str(v)


def render_markdown(rows: list[dict]) -> str:
    lines = [
        "| # | 查询 | 查询分析 LLM s | grep s | 回答 LLM s | 合计 s | 证据字符 | LLM 请求 | 断连 | SDK 重试 |",
        "|---|------|----------------|--------|------------|--------|----------|----------|------|----------|",
    ]
    for i, r in enumerate(rows, 1):
        query = r["query"][:48] + ("…" if len(r["query"]) > 48 else "")
        lines.append(
            f"| {i} | {query.replace('|', '/')} | {_fmt(r['analysis_s'])} | {_fmt(r['grep_s'])} "
            f"| {_fmt(r['answer_s'])} | {_fmt(r['total_s'])}{'' if r['complete'] else ' (未完成)'} "
            f"| {_fmt(r['evidence_chars'])} | {r['llm_http']} | {r['conn_err']} | {r['sdk_retry']} |"
        )
    done = [r for r in rows if r["complete"]]
    if done:
        def span(key: str) -> str:
            vals = [r[key] for r in done if r[key] is not None]
            return f"{min(vals)}–{max(vals)}" if vals else "-"

        lines += [
            "",
            f"完成 {len(done)}/{len(rows)} 次；查询分析 LLM {span('analysis_s')}s，grep {span('grep_s')}s，"
            f"回答 LLM {span('answer_s')}s，合计 {span('total_s')}s；"
            f"断连共 {sum(r['conn_err'] for r in rows)} 次，SDK 重试共 {sum(r['sdk_retry'] for r in rows)} 次。",
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-stage latency report from a sirchmunk sidecar log.")
    parser.add_argument("log", help="sidecar stdout log file")
    parser.add_argument("--out", default=None, help="Write Markdown report to this file")
    args = parser.parse_args(argv)
    rows = parse_log(Path(args.log).read_text(encoding="utf-8", errors="replace"))
    if not rows:
        print("no FAST searches found in log", file=sys.stderr)
        return 1
    report = render_markdown(rows)
    print(report)
    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
