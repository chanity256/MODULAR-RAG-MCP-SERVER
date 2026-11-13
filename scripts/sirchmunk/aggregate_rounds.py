"""汇总 baseline_round.sh 各轮 JSON → 合并 JSON + Markdown 逐题表（SX0.4/SX3.2）。

用法: python scripts/sirchmunk/aggregate_rounds.py OUTDIR merged.json summary.md [标题]
入库时文件名须含 ``results``，以落入 compare.py 的泄题排除 glob。
"""
import glob
import json
import statistics
import sys
from pathlib import Path

src = Path(sys.argv[1])
dest_json = Path(sys.argv[2])
dest_md = Path(sys.argv[3])
label = sys.argv[4] if len(sys.argv) > 4 else "SX0.4 baseline"

rows = []
for f in sorted(glob.glob(str(src / "*_r*_golden_questions*.json"))):
    name = Path(f).stem  # MODE_rN_fixture
    mode, rep, fixture = name.split("_", 2)
    for r in json.loads(Path(f).read_text()):
        r = dict(r)
        r["rep"] = int(rep[1:])
        r["fixture"] = fixture
        r.pop("answer", None)
        rows.append(r)

dest_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")

def med(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.median(xs)) if xs else "-"

def rng(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return f"{round(min(xs))}–{round(max(xs))}" if xs else "-"

order = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "D1", "D2", "D3", "D4"]
lines = [f"## {label}", "",
         "| id | 类别 | mode | 命中 (3 次) | 耗时中位 ms (范围) | Token 中位 (范围) | LLM 调用中位 | loop 中位 | 错误/睡眠疑似 |",
         "|----|------|------|-------------|---------------------|-------------------|--------------|-----------|---------------|"]
summary = {}
for mode in ("FAST", "DEEP"):
    for qid in order:
        rs = sorted([r for r in rows if r["mode"] == mode and r["id"] == qid], key=lambda r: r["rep"])
        if not rs:
            continue
        hits = "".join("✅" if r["hit"] else "❌" for r in rs)
        n_hit = sum(r["hit"] for r in rs)
        lat = [r["elapsed_ms"] for r in rs if not r["sleep_suspected"] and not r["error"]]
        tok = [r["total_llm_tokens"] for r in rs]
        calls = [r["llm_call_count"] for r in rs]
        loops = [(r["telemetry"] or {}).get("loop_count") for r in rs]
        bad = [f"r{r['rep']}:{'err' if r['error'] else 'sleep'}" for r in rs if r["error"] or r["sleep_suspected"]]
        lines.append(f"| {qid} | {rs[0]['category']} | {mode} | {hits} {n_hit}/{len(rs)} | {med(lat)} ({rng(lat)}) "
                     f"| {med(tok)} ({rng(tok)}) | {med(calls)} | {med(loops)} | {', '.join(bad) or '-'} |")
        s = summary.setdefault((mode, rs[0]["category"]), [0, 0])
        s[0] += n_hit; s[1] += len(rs)

lines += ["", "分类命中汇总（次数口径）：", ""]
for (mode, cat), (h, n) in sorted(summary.items()):
    lines.append(f"- {mode} / {cat}: {h}/{n}")
for mode in ("FAST", "DEEP"):
    rs = [r for r in rows if r["mode"] == mode]
    if rs:
        tel = sum(1 for r in rs if r["telemetry"])
        lines.append(f"- {mode} telemetry 非空: {tel}/{len(rs)}；Token 合计 {sum(r['total_llm_tokens'] or 0 for r in rs)}")
dest_md.write_text("\n".join(lines) + "\n")
print("\n".join(lines))
