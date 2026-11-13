"""过程度量（AI_NATIVE_SDLC_SPEC SD5.1）：离线、只读地从 git 历史算出流程指标基线。

五项指标（口径见规格 §3.5，基线与版本记录见 ``docs/analysis/process-metrics.md``）：

1. 立项 → 首个任务提交时长：立项 = ``docs/specs/<NAME>.md`` 首次出现的提交；首个任务提交 =
   拓扑序上立项之后、第一个主题含 ``[<系列><数字>`` 的非合并提交（立项提交本身不算）。
2. 立项 → 冻结周期：冻结 = 该文件从 ``docs/specs/`` 移入 ``docs/frozen/`` 的提交，重开后再冻结的
   取最后一次。改动过大、git 没识别为重命名时，退回「同一提交删 specs 文件、增同名 frozen 文件」。
3. auto-coder 修复轮数分布：提交信息中独占一行的 ``Fix-Rounds: N``。按行匹配而不用
   ``git interpret-trailers``——早期提交在它和 ``Co-authored-by`` 之间留了空行，git 不认作 trailer。
4. QA ⬜→✅ 周转：``QA_TEST_PROGRESS.md``（含 SD6.1 前的 ``.github`` 路径）各版本中同一用例
   首次 ⬜ 到其后首次 ✅ 的时长。
5. revert / hotfix / fix 提交数：非合并提交主题以 ``Revert``、``hotfix``、``fix(`` / ``fix:`` 开头。

系列前缀取规格最新版本（冻结后读 frozen 文件）任务表中出现最多的 2–3 位大写字母编号前缀；
立项版本可能还没有任务表（如 SR 由台账晋升），所以不读立项版本。时间一律用提交者时间。
规格从未在 ``docs/specs/`` 出现过的（DEV_SPEC 等直接冻结的早期文档）不在统计范围。

用法::

    python scripts/measure_process.py               # Markdown 报告
    python scripts/measure_process.py --json        # JSON
    python scripts/measure_process.py --repo DIR --rev REV
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

SPECS_DIR = "docs/specs/"
FROZEN_DIR = "docs/frozen/"
QA_PROGRESS_PATHS = (
    ".factory/skills/qa-tester/QA_TEST_PROGRESS.md",
    ".github/skills/qa-tester/QA_TEST_PROGRESS.md",
)

_TASK_ROW = re.compile(r"^\|\s*\**([A-Z]{2,3})\d+(?:\.\d+)?\**\s*\|", re.M)
_FIX_ROUNDS = re.compile(r"^Fix-Rounds:\s*(\d+)\s*$", re.M)
_QA_ROW = re.compile(r"^\|\s*(✅|❌|⏭️|🔧|⬜)\s*\|\s*([A-Z]-\d+)\s*\|", re.M)
_REVERT = re.compile(r"^revert\b", re.I)
_HOTFIX = re.compile(r"^hotfix\b", re.I)
_FIX = re.compile(r"^fix[(:!]")

_SEP_FIELD = "\x1f"
_SEP_RECORD = "\x1e"


@dataclass(frozen=True)
class Commit:
    sha: str
    ts: int
    parents: int
    subject: str
    body: str


@dataclass
class SpecLifecycle:
    path: str
    series: str | None
    charter_sha: str
    charter_ts: int
    first_task_sha: str | None = None
    first_task_ts: int | None = None
    frozen_sha: str | None = None
    frozen_ts: int | None = None
    frozen_path: str | None = None

    @property
    def name(self) -> str:
        return Path(self.path).stem

    @property
    def charter_to_first_task_h(self) -> float | None:
        if self.first_task_ts is None:
            return None
        return round((self.first_task_ts - self.charter_ts) / 3600, 2)

    @property
    def charter_to_frozen_h(self) -> float | None:
        if self.frozen_ts is None:
            return None
        return round((self.frozen_ts - self.charter_ts) / 3600, 2)


@dataclass
class Report:
    repo: str
    rev: str
    head_sha: str
    commits_total: int
    commits_non_merge: int
    specs: list[SpecLifecycle]
    fix_rounds: dict[int, int]
    qa_turnaround_h: list[float]
    qa_pass_without_pending: int
    revert_hotfix: dict[str, list[str]] = field(default_factory=dict)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


def _show(repo: Path, sha: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{sha}:{path}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout if result.returncode == 0 else None


def list_commits(repo: Path, rev: str) -> list[Commit]:
    """拓扑序（父在前）的全部提交。"""
    fmt = _SEP_FIELD.join(["%H", "%ct", "%P", "%s", "%b"]) + _SEP_RECORD
    raw = _git(repo, "log", "--reverse", "--topo-order", f"--format={fmt}", rev)
    commits = []
    for record in raw.split(_SEP_RECORD):
        record = record.strip("\n")
        if not record:
            continue
        sha, ts, parents, subject, body = record.split(_SEP_FIELD, 4)
        commits.append(Commit(sha, int(ts), len(parents.split()), subject, body))
    return commits


def _name_status_log(
    repo: Path, rev: str, *opts: str, paths: tuple[str, ...] = ()
) -> list[tuple[str, list[list[str]]]]:
    pathspec = ("--", *paths) if paths else ()
    raw = _git(
        repo, "log", "--reverse", "--topo-order", "--name-status", f"--format={_SEP_RECORD}%H", *opts, rev, *pathspec
    )
    entries = []
    for chunk in raw.split(_SEP_RECORD):
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            continue
        entries.append((lines[0].strip(), [ln.split("\t") for ln in lines[1:]]))
    return entries


def _series_of(text: str | None) -> str | None:
    if not text:
        return None
    counts = Counter(m.group(1) for m in _TASK_ROW.finditer(text))
    return counts.most_common(1)[0][0] if counts else None


def spec_lifecycles(repo: Path, rev: str, commits: list[Commit]) -> list[SpecLifecycle]:
    index = {c.sha: i for i, c in enumerate(commits)}
    by_sha = {c.sha: c for c in commits}

    charters: dict[str, str] = {}
    for sha, changes in _name_status_log(repo, rev, "--no-renames", "--diff-filter=A", paths=(SPECS_DIR,)):
        for status, *paths in changes:
            path = paths[-1]
            if status == "A" and path.startswith(SPECS_DIR) and path.endswith(".md"):
                charters.setdefault(path, sha)

    freezes: dict[str, tuple[str, str]] = {}
    renames: list[tuple[int, str, str]] = []
    for sha, changes in _name_status_log(repo, rev, "-M"):
        for status, *paths in changes:
            if status.startswith("R"):
                renames.append((index[sha], paths[0], paths[1]))
        deleted = {p[0] for s, *p in changes if s == "D" and p[0].startswith(SPECS_DIR)}
        added = [p[0] for s, *p in changes if s == "A" and p[0].startswith(FROZEN_DIR)]
        for status, *paths in changes:
            if status.startswith("R") and paths[0].startswith(SPECS_DIR) and paths[1].startswith(FROZEN_DIR):
                freezes[paths[0]] = (sha, paths[1])
        for old in deleted:
            stem = Path(old).stem
            match = next((new for new in added if Path(new).stem.endswith(f"{stem}_frozen")), None)
            if match:
                freezes[old] = (sha, match)

    rev_sha = _git(repo, "rev-parse", rev).strip()
    result = []
    for path, charter_sha in sorted(charters.items(), key=lambda kv: index[kv[1]]):
        charter = by_sha[charter_sha]
        frozen = freezes.get(path)
        latest_path = path
        if frozen and _show(repo, rev_sha, path) is None:
            # frozen 文件之后可能再被重命名（如 4682305 统一加序号前缀），沿重命名链找到最新路径。
            latest_path = frozen[1]
            for pos, old, new in renames:
                if pos > index[frozen[0]] and old == latest_path:
                    latest_path = new
        series = _series_of(_show(repo, rev_sha, latest_path))
        life = SpecLifecycle(path=path, series=series, charter_sha=charter_sha, charter_ts=charter.ts)
        if series:
            marker = re.compile(rf"\[{series}\d")
            for c in commits[index[charter_sha] + 1:]:
                if c.parents <= 1 and marker.search(c.subject):
                    life.first_task_sha, life.first_task_ts = c.sha, c.ts
                    break
        if frozen:
            life.frozen_sha, life.frozen_path = frozen
            life.frozen_ts = by_sha[frozen[0]].ts
        result.append(life)
    return result


def fix_rounds(commits: list[Commit]) -> dict[int, int]:
    counts: Counter[int] = Counter()
    for c in commits:
        for m in _FIX_ROUNDS.finditer(c.body):
            counts[int(m.group(1))] += 1
    return dict(sorted(counts.items()))


def qa_turnaround(repo: Path, rev: str, commits: list[Commit]) -> tuple[list[float], int]:
    """返回 (各用例 ⬜→✅ 时长列表, 首次出现即 ✅、无 ⬜ 记录的用例数)。"""
    by_sha = {c.sha: c for c in commits}
    shas = _git(repo, "log", "--reverse", "--topo-order", "--format=%H", rev, "--", *QA_PROGRESS_PATHS).split()
    pending_since: dict[str, int] = {}
    passed: dict[str, float] = {}
    direct_pass: set[str] = set()
    for sha in shas:
        text = next((t for t in (_show(repo, sha, p) for p in QA_PROGRESS_PATHS) if t is not None), None)
        if text is None:
            continue
        ts = by_sha[sha].ts
        for status, case_id in _QA_ROW.findall(text):
            if case_id in passed or case_id in direct_pass:
                continue
            if status == "⬜":
                pending_since.setdefault(case_id, ts)
            elif status == "✅":
                if case_id in pending_since:
                    passed[case_id] = round((ts - pending_since[case_id]) / 3600, 2)
                else:
                    direct_pass.add(case_id)
    return [passed[k] for k in sorted(passed)], len(direct_pass)


def revert_hotfix(commits: list[Commit]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"revert": [], "hotfix": [], "fix": []}
    for c in commits:
        if c.parents > 1:
            continue
        if _REVERT.search(c.subject):
            out["revert"].append(c.sha)
        elif _HOTFIX.search(c.subject):
            out["hotfix"].append(c.sha)
        elif _FIX.search(c.subject):
            out["fix"].append(c.sha)
    return out


def measure(repo: Path, rev: str = "HEAD") -> Report:
    commits = list_commits(repo, rev)
    qa_samples, qa_direct = qa_turnaround(repo, rev, commits)
    return Report(
        repo=str(repo),
        rev=rev,
        head_sha=_git(repo, "rev-parse", rev).strip(),
        commits_total=len(commits),
        commits_non_merge=sum(1 for c in commits if c.parents <= 1),
        specs=spec_lifecycles(repo, rev, commits),
        fix_rounds=fix_rounds(commits),
        qa_turnaround_h=qa_samples,
        qa_pass_without_pending=qa_direct,
        revert_hotfix=revert_hotfix(commits),
    )


def _stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "median": None, "mean": None, "min": None, "max": None}
    return {
        "n": len(values),
        "median": round(statistics.median(values), 2),
        "mean": round(statistics.fmean(values), 2),
        "min": min(values),
        "max": max(values),
    }


def _date(ts: int | None) -> str:
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _ref(sha: str | None, ts: int | None) -> str:
    return "—" if sha is None else f"{sha[:7]} {_date(ts)}"


def _num(value: float | int | None) -> str:
    return "—" if value is None else f"{value}"


def to_dict(report: Report) -> dict:
    specs = []
    for s in report.specs:
        item = asdict(s)
        item["name"] = s.name
        item["charter_to_first_task_h"] = s.charter_to_first_task_h
        item["charter_to_frozen_h"] = s.charter_to_frozen_h
        specs.append(item)
    first = [s.charter_to_first_task_h for s in report.specs if s.charter_to_first_task_h is not None]
    cycle = [s.charter_to_frozen_h for s in report.specs if s.charter_to_frozen_h is not None]
    fr_values = [k for k, n in report.fix_rounds.items() for _ in range(n)]
    return {
        "repo": report.repo,
        "rev": report.rev,
        "head_sha": report.head_sha,
        "commits_total": report.commits_total,
        "commits_non_merge": report.commits_non_merge,
        "specs": specs,
        "charter_to_first_task_h": _stats(first),
        "charter_to_frozen_h": _stats(cycle),
        "fix_rounds": {"distribution": report.fix_rounds, **_stats(fr_values)},
        "qa_turnaround_h": {**_stats(report.qa_turnaround_h), "pass_without_pending": report.qa_pass_without_pending},
        "revert_hotfix": {k: len(v) for k, v in report.revert_hotfix.items()},
        "revert_hotfix_shas": report.revert_hotfix,
    }


def to_markdown(report: Report) -> str:
    data = to_dict(report)
    lines = [
        f"# 过程度量基线（{report.rev} = {report.head_sha[:7]}）",
        "",
        f"提交总数 {report.commits_total}，非合并提交 {report.commits_non_merge}。时长单位：小时（提交者时间）。",
        "",
        "## 1–2. 规格生命周期",
        "",
        "| 规格 | 系列 | 立项 | 首个任务提交 | 立项→首任务 (h) | 冻结 | 立项→冻结 (h) |",
        "|------|------|------|--------------|-----------------|------|---------------|",
    ]
    for s in report.specs:
        lines.append(
            f"| {s.name} | {s.series or '—'} | {_ref(s.charter_sha, s.charter_ts)} | "
            f"{_ref(s.first_task_sha, s.first_task_ts)} | {_num(s.charter_to_first_task_h)} | "
            f"{_ref(s.frozen_sha, s.frozen_ts)} | {_num(s.charter_to_frozen_h)} |"
        )
    lines.append("")
    for key, title in (("charter_to_first_task_h", "立项→首任务"), ("charter_to_frozen_h", "立项→冻结")):
        st = data[key]
        lines.append(
            f"- {title}：n={st['n']}，中位数 {_num(st['median'])} h，均值 {_num(st['mean'])} h，"
            f"范围 {_num(st['min'])}–{_num(st['max'])} h"
        )
    fr = data["fix_rounds"]
    dist = "，".join(f"{k} 轮 × {n}" for k, n in fr["distribution"].items()) or "无样本"
    qa = data["qa_turnaround_h"]
    rh = data["revert_hotfix"]
    lines += [
        "",
        "## 3. auto-coder 修复轮数（`Fix-Rounds: N`）",
        "",
        f"- 样本 {fr['n']}：{dist}；均值 {_num(fr['mean'])}，最大 {_num(fr['max'])}",
        "",
        "## 4. QA ⬜→✅ 周转",
        "",
        f"- 样本 {qa['n']}，中位数 {_num(qa['median'])} h，均值 {_num(qa['mean'])} h；"
        f"首次出现即 ✅（无 ⬜ 记录）的用例 {qa['pass_without_pending']} 条",
        "",
        "## 5. revert / hotfix / fix 提交",
        "",
        f"- revert {rh['revert']}，hotfix {rh['hotfix']}，fix {rh['fix']}；"
        f"合计 {sum(rh.values())} / 非合并提交 {report.commits_non_merge}",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线从 git 历史计算过程度量基线（SD5.1）")
    parser.add_argument("--repo", type=Path, default=_ROOT, help="git 仓库根目录（默认本仓库）")
    parser.add_argument("--rev", default="HEAD", help="统计到哪个提交（默认 HEAD）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)
    try:
        report = measure(args.repo, args.rev)
    except subprocess.CalledProcessError as exc:
        print(f"git 调用失败：{exc.stderr.strip() or exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(to_dict(report), ensure_ascii=False, indent=2))
    else:
        print(to_markdown(report), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
