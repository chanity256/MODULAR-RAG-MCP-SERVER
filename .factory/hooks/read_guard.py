#!/usr/bin/env python3
"""必读参考守卫 hook（Droid PreToolUse / PostToolUse / SessionStart hook，EVALS_READ_DISCIPLINE_SPEC ERD1）。

把 AGENTS.md / skill 里「先读规格再改代码」的规则变成确定性检查：会话里用 Read 工具
读过的项目文件记在 .factory/state/reads-<session_id>.jsonl，编辑命中 READ_MAP 的
路径前，对应的必读参考还没读过就 deny，并在理由里列出要读的确切路径。

子命令：
- record  挂在 PostToolUse（Read）：记录读过的项目相对路径。失败的 Read 不触发
          PostToolUse（ERD0 实测），所以被调用即视为读成功。
- pre     挂在 PreToolUse（Edit / Create / ApplyPatch）：求本次编辑目标的必读集合，
          缺读则 deny。新建文件同样检查；Execute 写入不在范围内（与 evals 判据①口径一致）。
- reset   挂在 SessionStart：source 为 startup / clear 时清掉当前会话的记录，并顺带删
          除超过 STALE_SECONDS 的旧会话记录。

只认 Read 工具，Grep / cat 不算读过；带 offset / limit 的 Read 也算（规格 §3.6 裁决）。
必读参考在项目里一个都不存在时跳过该条，不让规则在别的项目或残缺 checkout 里卡死。

fail-open：stdin / 状态文件解析失败、未知子命令、内部异常一律放行（exit 0），只往
stderr 记警告。

环境变量（测试注入口）：READ_GUARD_PROJECT_DIR 项目根，缺省依次取
FACTORY_PROJECT_DIR、stdin 的 cwd。
"""

from __future__ import annotations

import fnmatch
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

STATE_DIR = Path(".factory") / "state"
RESET_SOURCES = {"startup", "clear"}
STALE_SECONDS = 7 * 24 * 3600
EDIT_TOOLS = {"Edit", "Create", "ApplyPatch"}

SPEC_01 = "docs/frozen/01_DEV_SPEC_frozen.md"
SPEC_04 = "docs/frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md"
REFERENCES = ".factory/skills/auto-coder/references/*.md"
QA_PLAN = ".factory/skills/qa-tester/QA_TEST_PLAN.md"

# (编辑目标 glob, 必读备选（任一即可）, 出处)。glob 用 fnmatch，* 可跨目录。
# 规则只能来自 AGENTS.md / skill 的既有条文，不得照抄 evals 用例的 required_reads。
READ_MAP: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    (
        ("src/*", "scripts/*", "tests/*"),
        (SPEC_01, REFERENCES),
        "AGENTS.md「编写代码之前，先阅读 docs/frozen/ 下的已冻结规格」；auto-coder Implement 第 1 步",
    ),
    (
        (".agents/plans/*",),
        (SPEC_01, REFERENCES),
        "auto-coder Implement 第 1 步读规格，先于第 4 步写 plan",
    ),
    (
        ("src/ingestion/*", "src/libs/loader/*", "scripts/ingest.py", "tests/*test_*ingest*", "tests/*test_*loader*"),
        (SPEC_04,),
        "AGENTS.md「04_INGESTION_ROBUSTNESS_SPEC … 改动摄取链路前先读」",
    ),
    (
        (".factory/skills/qa-tester/QA_TEST_PROGRESS.md",),
        (QA_PLAN,),
        "qa-tester skill（按 QA_TEST_PLAN.md 执行并记录进度）",
    ),
)
EXEMPT = {".agents/plans/README.md"}

_PATCH_TARGET = re.compile(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$", re.MULTILINE)
_GLOB_CHARS = re.compile(r"[*?\[]")
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _warn(message: str) -> None:
    print(f"read_guard: {message}", file=sys.stderr)


def _read_stdin() -> dict:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _project_dir(payload: dict) -> Path:
    for key in ("READ_GUARD_PROJECT_DIR", "FACTORY_PROJECT_DIR"):
        value = os.environ.get(key)
        if value:
            return Path(value)
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd:
        return Path(cwd)
    return Path.cwd()


def _state_file(project: Path, payload: dict) -> Path:
    session = payload.get("session_id")
    name = f"reads-{_UNSAFE.sub('_', session)}.jsonl" if isinstance(session, str) and session else "reads.jsonl"
    return project / STATE_DIR / name


def _rel(raw: str, payload: dict, project: Path) -> str | None:
    """raw 指向项目内时返回项目相对路径（两边取 realpath），否则 None。"""
    if not raw:
        return None
    path = Path(os.path.expanduser(raw))
    if not path.is_absolute():
        cwd = payload.get("cwd")
        path = (Path(cwd) if isinstance(cwd, str) and cwd else project) / path
    # macOS 的 /tmp ↔ /private/tmp：ERD0 实测 file_path 与 FACTORY_PROJECT_DIR 前缀不一致。
    real = Path(os.path.realpath(path))
    real_project = Path(os.path.realpath(project))
    try:
        return real.relative_to(real_project).as_posix()
    except ValueError:
        return None


def _load_reads(state: Path) -> set[str]:
    try:
        text = state.read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()
    return {line.strip() for line in text.splitlines() if line.strip()}


def _transcript_reads(payload: dict, project: Path) -> set[str]:
    """会话 transcript 里成功的 Read。

    Script 包装模式下嵌套的 Read 不触发 PostToolUse（只有外层 Script 触发），但
    transcript 照常记下嵌套调用的 tool_use / tool_result，所以两路取并集。transcript
    是 Droid 内部格式，读不到或解析不了时只用 record 的结果。
    """
    raw = payload.get("transcript_path")
    if not isinstance(raw, str) or not raw:
        return set()
    try:
        lines = Path(raw).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return set()
    paths: dict[str, str] = {}
    ok: set[str] = set()
    for line in lines:
        if '"tool_use"' not in line and '"tool_result"' not in line:
            continue
        try:
            content = json.loads(line).get("message", {}).get("content")
        except (ValueError, AttributeError):
            continue
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use" and item.get("name") == "Read":
                file_path = (item.get("input") or {}).get("file_path")
                if isinstance(item.get("id"), str) and isinstance(file_path, str):
                    paths[item["id"]] = file_path
            elif item.get("type") == "tool_result" and not item.get("is_error"):
                if isinstance(item.get("tool_use_id"), str):
                    ok.add(item["tool_use_id"])
    found = set()
    for call_id, file_path in paths.items():
        if call_id in ok:
            rel = _rel(file_path, payload, project)
            if rel is not None:
                found.add(rel)
    return found


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _targets(tool_name: str, tool_input: dict) -> list[str]:
    if tool_name in {"Edit", "Create"}:
        raw = tool_input.get("file_path") or tool_input.get("path")
        return [raw] if isinstance(raw, str) else []
    if tool_name == "ApplyPatch":
        return [m.group(1) for s in _strings(tool_input) for m in _PATCH_TARGET.finditer(s)]
    return []


def _existing(project: Path, options: tuple[str, ...]) -> list[str]:
    found = []
    for option in options:
        if _GLOB_CHARS.search(option):
            if glob.glob(str(project / option)):
                found.append(option)
        elif (project / option).is_file():
            found.append(option)
    return found


def required_groups(rel: str, project: Path) -> list[tuple[tuple[str, ...], str]]:
    """rel 命中的必读组：(项目中存在的备选, 出处)。"""
    if rel in EXEMPT:
        return []
    groups = []
    for patterns, options, source in READ_MAP:
        if not any(fnmatch.fnmatchcase(rel, p) for p in patterns):
            continue
        present = tuple(_existing(project, options))
        if present and all(g != present for g, _ in groups):
            groups.append((present, source))
    return groups


def _satisfied(options: tuple[str, ...], reads: set[str]) -> bool:
    return any(fnmatch.fnmatchcase(r, o) for r in reads for o in options)


def cmd_record(payload: dict) -> int:
    if payload.get("tool_name") != "Read":
        return 0
    tool_input = payload.get("tool_input")
    raw = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(raw, str):
        return 0
    project = _project_dir(payload)
    rel = _rel(raw, payload, project)
    if rel is None:
        return 0
    state = _state_file(project, payload)
    state.parent.mkdir(parents=True, exist_ok=True)
    # 并行 Read 的 PostToolUse 几乎同时触发：单次 O_APPEND 写一行，免得读改写互相覆盖。
    fd = os.open(state, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (rel + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return 0


def cmd_pre(payload: dict) -> int:
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if tool_name not in EDIT_TOOLS or not isinstance(tool_input, dict):
        return 0
    project = _project_dir(payload)
    targets: list[str] = []
    missing: list[tuple[tuple[str, ...], str]] = []
    reads: set[str] | None = None
    for raw in _targets(tool_name, tool_input):
        rel = _rel(raw, payload, project)
        if rel is None:
            continue
        for options, source in required_groups(rel, project):
            if reads is None:
                reads = _load_reads(_state_file(project, payload)) | _transcript_reads(payload, project)
            if _satisfied(options, reads):
                continue
            if rel not in targets:
                targets.append(rel)
            if all(m[0] != options for m in missing):
                missing.append((options, source))
    if not missing:
        return 0
    lines = [
        f"- {' 或 '.join(f'{o}（任一）' if _GLOB_CHARS.search(o) else o for o in options)}（出处：{source}）"
        for options, source in missing
    ]
    reason = (
        f"read_guard（EVALS_READ_DISCIPLINE_SPEC）：修改 {', '.join(targets)} 前，先用 Read 工具读必读参考：\n"
        + "\n".join(lines)
        + "\n读完后重试本次编辑。Grep 命中或 cat / head 输出不算读过。"
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))
    return 0


def cmd_reset(payload: dict) -> int:
    if payload.get("source") not in RESET_SOURCES:
        return 0
    project = _project_dir(payload)
    current = _state_file(project, payload)
    cutoff = time.time() - STALE_SECONDS
    for state in (project / STATE_DIR).glob("reads*.jsonl"):
        try:
            if state == current or state.stat().st_mtime < cutoff:
                state.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            _warn(f"无法删除 {state}: {exc}")
    return 0


def main() -> int:
    payload = _read_stdin()
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if command == "record":
            return cmd_record(payload)
        if command == "pre":
            return cmd_pre(payload)
        if command == "reset":
            return cmd_reset(payload)
    except Exception as exc:  # hook 故障不能卡住会话
        _warn(f"内部错误，放行: {exc!r}")
        return 0
    _warn(f"未知子命令 {command!r}，放行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
