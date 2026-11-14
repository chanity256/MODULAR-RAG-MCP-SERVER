#!/usr/bin/env python3
"""测试文件保护 hook（Droid PreToolUse / SessionStart hook，AI_NATIVE_SDLC_SPEC SD3.7）。

只在 auto-coder 的 Test & Auto-Fix 阶段生效：auto-coder 进入该阶段时创建标记
文件 .factory/state/fix_mode，离开时删除（规则见 auto-coder SKILL.md）。标记存在
时拦截对 <项目根>/tests/ 下**已有**文件的修改，新建测试文件不拦截，避免与
「写代码时同时写测试」冲突。

子命令：
- pre    挂在 PreToolUse（Edit / Create / ApplyPatch / Execute）：标记不存在立即
         放行；存在时找出本次调用要写的路径，命中已有测试文件就 deny。
         Execute 是启发式：重定向目标、rm/mv/tee/cp 目标、sed -i / perl -i、
         git rm/mv/checkout/restore、dd of=、find -delete。python -c、脚本内部
         写文件、cd 之后的相对路径等不在覆盖范围内。
- reset  挂在 SessionStart：source 为 startup / clear 时删除上个会话崩溃留下的
         标记，免得它在 Implement 阶段误拦合法的测试改动。

局限：只在本地 Droid 会话生效，agent 也能删掉标记；确定性兜底仍是 review 规则
（弱化测试断言记 Important）与 agent evals 判据③。

fail-open：stdin / shell 解析失败、未知子命令一律放行（exit 0），只往 stderr
记警告。

环境变量（测试注入口）：TEST_GUARD_PROJECT_DIR 项目根，缺省依次取
FACTORY_PROJECT_DIR、stdin 的 cwd。
"""

from __future__ import annotations

import glob
import json
import os
import re
import shlex
import sys
from pathlib import Path

MARKER = Path(".factory") / "state" / "fix_mode"
TESTS_DIRNAME = "tests"
RESET_SOURCES = {"startup", "clear"}

_PATCH_TARGET = re.compile(r"^\*\*\* (?:Update|Delete) File:\s*(.+?)\s*$", re.MULTILINE)
_HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_GLOB_CHARS = re.compile(r"[*?\[]")
_WRAPPERS = {"sudo", "command", "env", "nohup", "time", "exec"}
_DELETE_ALL = {"rm", "unlink", "truncate", "shred", "mv", "tee"}
_COPY_TO_LAST = {"cp", "install", "ln"}
_GIT_WRITES = {"rm", "mv", "checkout", "restore"}


def _warn(message: str) -> None:
    print(f"test_guard: {message}", file=sys.stderr)


def _read_stdin() -> dict:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _project_dir(payload: dict) -> Path:
    for key in ("TEST_GUARD_PROJECT_DIR", "FACTORY_PROJECT_DIR"):
        value = os.environ.get(key)
        if value:
            return Path(value)
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd:
        return Path(cwd)
    return Path.cwd()


def _cwd(payload: dict, project: Path) -> Path:
    cwd = payload.get("cwd")
    return Path(cwd) if isinstance(cwd, str) and cwd else project


def _protected(raw: str, cwd: Path, project: Path) -> list[str]:
    """raw（可含 glob）指向 tests/ 下已有文件或含已有文件的目录时，返回其项目相对路径。"""
    if not raw:
        return []
    path = Path(os.path.expanduser(raw))
    if not path.is_absolute():
        path = cwd / path
    candidates = [Path(p) for p in glob.glob(str(path))] if _GLOB_CHARS.search(raw) else [path]
    # realpath 两边一起取：macOS 的 /var 与 /private/var、软链进来的 tests/ 都要对齐。
    real_project = Path(os.path.realpath(project))
    tests_dir = real_project / TESTS_DIRNAME
    hits: list[str] = []
    for candidate in candidates:
        real = Path(os.path.realpath(candidate))
        if real != tests_dir and tests_dir not in real.parents:
            continue
        if real.is_file() or (real.is_dir() and any(p.is_file() for p in real.rglob("*"))):
            hits.append(real.relative_to(real_project).as_posix())
    return hits


def _logical_lines(command: str) -> list[str]:
    """按行切分，合并反斜杠续行，跳过 here-doc 正文（正文不是命令）。"""
    lines: list[str] = []
    pending = ""
    heredoc_end: str | None = None
    for line in command.splitlines():
        if heredoc_end is not None:
            if line.strip() == heredoc_end:
                heredoc_end = None
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        full = pending + line
        pending = ""
        lines.append(full)
        match = _HEREDOC.search(full)
        if match:
            heredoc_end = match.group(2)
    if pending:
        lines.append(pending)
    return lines


def _segments(line: str) -> list[tuple[list[str], list[str]]]:
    """把一行拆成 (命令词, 重定向写入目标) 的简单命令列表。"""
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens = list(lexer)
    segments: list[tuple[list[str], list[str]]] = []
    words: list[str] = []
    targets: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        is_op = bool(token) and set(token) <= set("();<>|&")
        if is_op and ">" in token:
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            # >&2、2>&1、>&- 是 fd 复制 / 关闭，不写文件。
            if not (token.endswith("&") and (nxt.isdigit() or nxt == "-")):
                targets.append(nxt)
            i += 2
            continue
        if is_op and "<" in token:
            i += 2
            continue
        if is_op:
            segments.append((words, targets))
            words, targets = [], []
            i += 1
            continue
        words.append(token)
        i += 1
    segments.append((words, targets))
    return segments


def _command_targets(words: list[str]) -> list[str]:
    i = 0
    while i < len(words) and (_ASSIGNMENT.match(words[i]) or words[i] in _WRAPPERS):
        i += 1
    if i >= len(words):
        return []
    verb = os.path.basename(words[i])
    args = words[i + 1:]
    operands = [a for a in args if a and not a.startswith("-")]
    if verb in _DELETE_ALL:
        return operands
    if verb in _COPY_TO_LAST:
        return operands[-1:] if len(operands) >= 2 else []
    if verb in {"sed", "gsed"} and any(re.match(r"^-[A-Za-z]*i", a) or a.startswith("--in-place") for a in args):
        return operands
    if verb == "perl" and any(re.match(r"^-[A-Za-z]*i", a) for a in args):
        return operands
    if verb == "git" and operands and operands[0] in _GIT_WRITES:
        return operands[1:]
    if verb == "dd":
        return [a[3:] for a in args if a.startswith("of=")]
    if verb == "find" and "-delete" in args:
        roots = []
        for a in args:
            if a.startswith("-") or a in {"(", "!"}:
                break
            roots.append(a)
        return roots
    return []


def _execute_paths(command: str) -> list[str]:
    paths: list[str] = []
    for line in _logical_lines(command):
        try:
            segments = _segments(line)
        except ValueError as exc:  # 引号不配对等：这一行放行
            _warn(f"无法解析命令行，放行: {exc}")
            continue
        for words, targets in segments:
            paths.extend(targets)
            paths.extend(_command_targets(words))
    return paths


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _candidate_paths(tool_name: str, tool_input: dict) -> list[str]:
    if tool_name in {"Edit", "Create"}:
        raw = tool_input.get("file_path") or tool_input.get("path")
        return [raw] if isinstance(raw, str) else []
    if tool_name == "ApplyPatch":
        # 只认 Update / Delete；*** Add File 是新建，放行。
        return [m.group(1) for s in _strings(tool_input) for m in _PATCH_TARGET.finditer(s)]
    if tool_name == "Execute":
        command = tool_input.get("command")
        return _execute_paths(command) if isinstance(command, str) else []
    return []


def cmd_pre(payload: dict) -> int:
    project = _project_dir(payload)
    if not (project / MARKER).exists():
        return 0
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
        return 0
    cwd = _cwd(payload, project)
    hits: list[str] = []
    for raw in _candidate_paths(tool_name, tool_input):
        for hit in _protected(raw, cwd, project):
            if hit not in hits:
                hits.append(hit)
    if not hits:
        return 0
    reason = (
        f"test_guard（AI_NATIVE_SDLC_SPEC SD3.7）：当前处于 auto-coder Test & Auto-Fix 阶段"
        f"（标记 {MARKER.as_posix()} 存在），禁止修改已有测试文件：{', '.join(hits)}。"
        "请修改实现让测试通过；如果判断测试本身有误，停止修复并向用户报告，不要改测试。"
        "新建测试文件不受此限制。"
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
    marker = _project_dir(payload) / MARKER
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        _warn(f"无法删除残留标记 {marker}: {exc}")
    return 0


def main() -> int:
    payload = _read_stdin()
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
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
