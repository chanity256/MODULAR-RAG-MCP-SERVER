#!/usr/bin/env python3
"""docs/ 变更守卫 hook（Droid Stop hook）。

只做检测与回传，不改任何文件；修复流程由 docs-governance skill 承担：
- 结构漂移（坏链 / MOC 漏登记）必须修，判定标准只存在于
  tests/unit/test_docs_links.py，本脚本复用 pytest，不另写一份规则；
- 语义同步只提醒：按静态映射表指出本轮改动应核对的关联文档。

子命令：
- snapshot  挂在 UserPromptSubmit，给 docs/** 拍哈希快照；
- stop      挂在 Stop，对比快照得本轮改动，检查后决定是否 block 一次。

fail-open：venv 缺失、pytest 自身出错、超时、stdin 解析失败一律放行
（exit 0），只往 stderr 记警告；退出码 2 不用于故障，仅语义上等价
于「拦截」的 JSON decision: block 也只在检查确实失败时输出。

环境变量（测试注入口）：
- DOCS_GUARD_PROJECT_DIR  项目根，缺省取 stdin 的 cwd；
- DOCS_GUARD_SNAPSHOT_DIR 快照目录，缺省 $TMPDIR/droid-docs-guard；
- DOCS_GUARD_PYTHON       结构检查用的解释器，缺省 <项目根>/.venv/bin/python；
- DOCS_GUARD_TIMEOUT      结构检查超时秒数，缺省 30。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DOCS_DIRNAME = "docs"
SNAPSHOT_SUBDIR = "droid-docs-guard"
PYTEST_ARGS = ["-m", "pytest", "tests/unit/test_docs_links.py", "-q", "-p", "no:cacheprovider"]

# 本轮改动路径前缀 -> 应核对的关联文档（仓库根相对路径）。
# docs/MOC.md 本身的增删由 test_docs_links.py 兜底，不进映射表。
SEMANTIC_MAP: list[tuple[str, list[str]]] = [
    ("docs/frozen/", ["AGENTS.md", "README.md", "docs/MOC.md"]),
    ("docs/specs/", ["docs/MOC.md", "AGENTS.md"]),
    ("docs/backlog/", ["docs/backlog/README.md", "docs/MOC.md"]),
    ("docs/analysis/", ["docs/MOC.md"]),
    ("docs/reference/USAGE_GUIDE.md", ["docs/MOC.md", "AGENTS.md"]),
    ("docs/reference/", ["docs/MOC.md"]),
]


def _warn(message: str) -> None:
    print(f"docs_guard: {message}", file=sys.stderr)


def _read_stdin() -> dict:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _project_dir(payload: dict) -> Path:
    override = os.environ.get("DOCS_GUARD_PROJECT_DIR")
    if override:
        return Path(override)
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd:
        return Path(cwd)
    return Path.cwd()


def _snapshot_dir(payload: dict) -> Path:
    override = os.environ.get("DOCS_GUARD_SNAPSHOT_DIR")
    base = Path(override) if override else Path(tempfile.gettempdir()) / SNAPSHOT_SUBDIR
    session_id = payload.get("session_id")
    safe = str(session_id) if isinstance(session_id, str) and session_id else "default"
    # session_id 可能含路径分隔符，压平成安全文件名。
    return base / (hashlib.sha1(safe.encode()).hexdigest()[:16] + ".json")


def _hash_tree(docs_dir: Path) -> dict[str, str]:
    # 键为仓库根相对路径（带 docs/ 前缀），与 SEMANTIC_MAP 前缀对齐。
    state: dict[str, str] = {}
    if not docs_dir.is_dir():
        return state
    for path in sorted(docs_dir.rglob("*")):
        if path.is_file():
            rel = (DOCS_DIRNAME / path.relative_to(docs_dir)).as_posix()
            try:
                digest = hashlib.sha1(path.read_bytes()).hexdigest()
            except OSError:
                digest = "unreadable"
            state[rel] = digest
    return state


def cmd_snapshot(payload: dict) -> int:
    docs_dir = _project_dir(payload) / DOCS_DIRNAME
    state = _hash_tree(docs_dir)
    out = _snapshot_dir(payload)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        _warn(f"快照写入失败，本轮守卫将退化为仅结构检查: {exc}")
        return 0
    return 0


def _load_prev_snapshot(payload: dict) -> dict[str, str] | None:
    path = _snapshot_dir(payload)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _diff_changed(prev: dict[str, str], curr: dict[str, str]) -> list[str]:
    changed = [
        rel
        for rel in prev.keys() | curr.keys()
        if prev.get(rel) != curr.get(rel)
    ]
    return sorted(changed)


def _semantic_reminders(changed: list[str]) -> list[str]:
    reminders: list[str] = []
    for rel in changed:
        for prefix, docs in SEMANTIC_MAP:
            if rel.startswith(prefix):
                for doc in docs:
                    if doc not in reminders:
                        reminders.append(doc)
    return reminders


def _run_structure_check(project_dir: Path) -> tuple[str, str]:
    """返回 (status, 摘要)。status: pass / fail / error；error 为故障，调用方放行。

    用 pytest 退出码判定而非解析 stdout：pyproject 已有 addopts=-q，
    命令行再传 -q 会抑制汇总行，文本启发式不可靠。0=通过，1=用例失败，
    其余（2 收集错误、5 未收集到用例等）视为故障。
    """
    python = os.environ.get("DOCS_GUARD_PYTHON") or str(project_dir / ".venv" / "bin" / "python")
    if not Path(python).exists():
        _warn(f"结构检查解释器不存在，放行: {python}")
        return "error", ""
    timeout_s = float(os.environ.get("DOCS_GUARD_TIMEOUT", "30"))
    try:
        proc = subprocess.run(
            [python, *PYTEST_ARGS],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _warn(f"结构检查执行失败，放行: {exc}")
        return "error", ""
    if proc.returncode == 0:
        status = "pass"
    elif proc.returncode == 1:
        status = "fail"
    else:
        _warn(f"pytest 退出码异常（{proc.returncode}），视为故障放行")
        status = "error"
    return status, "\n".join(proc.stdout.strip().splitlines()[-8:])


def cmd_stop(payload: dict) -> int:
    if payload.get("stop_hook_active"):
        return 0

    project_dir = _project_dir(payload)
    docs_dir = project_dir / DOCS_DIRNAME
    curr = _hash_tree(docs_dir)
    prev = _load_prev_snapshot(payload)
    changed: list[str] | None = None
    if prev is not None:
        changed = _diff_changed(prev, curr)
        if not changed:
            return 0

    status, summary = _run_structure_check(project_dir)
    if status == "error":
        return 0  # fail-open

    structure_failed = status == "fail"
    reminders = _semantic_reminders(changed) if changed is not None else []

    if not structure_failed and not reminders:
        return 0

    lines: list[str] = []
    if changed:
        shown = ", ".join(changed[:20]) + ("…" if len(changed) > 20 else "")
        lines.append(f"本轮 docs/ 改动：{shown}")
    if structure_failed:
        lines.append("结构漂移（必须修）：tests/unit/test_docs_links.py 未通过：\n" + summary)
    if reminders:
        lines.append("语义同步提醒（请逐项判断是否需要更新，无需更新的说明理由）：\n- " + "\n- ".join(reminders))
    lines.append("请加载 docs-governance skill 处理；确认无需更新时说明理由后结束。")

    reason = "\n\n".join(lines)
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


def main() -> int:
    payload = _read_stdin()
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "snapshot":
        return cmd_snapshot(payload)
    if command == "stop":
        return cmd_stop(payload)
    _warn(f"未知子命令 {command!r}，放行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
