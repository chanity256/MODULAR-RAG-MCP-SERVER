"""红线静态检查（AI_NATIVE_SDLC_SPEC SD1.6）：只覆盖现有单测没覆盖的三条红线。

1. ``no-sirchmunk-import``：主 venv 永不 import sirchmunk（SIRCHMUNK_INTEGRATION_SPEC 第 4 章）。
   用 AST 而非 grep：``http_backend.py`` 的 docstring 含 "import sirchmunk" 字样，grep 会误报。
   只扫 ``.py``——``scripts/sirchmunk/preflight.sh`` 里的 ``from sirchmunk...`` 运行在
   sidecar 独立 venv 中，属合法白名单，因此 shell 脚本不在扫描范围内。
2. ``llm-fallback-false``：``src/`` 与 ``scripts/`` 中 ``llm_fallback`` 只允许字面量 ``False``。
   载荷值锁定已由 ``test_agentic_backend.py`` / ``test_agentic_backend_stream.py`` 覆盖，这里只兜底
   单测走不到的写法与新位置；规格明示放开时在 ``LLM_FALLBACK_EXEMPTIONS`` 加带规格引用的条目。
3. ``mcp-stdout``：``src/mcp_server/`` 禁止 ``print(`` 与任何 ``sys.stdout`` 引用（含经
   ``StreamHandler`` / ``basicConfig`` 指向 stdout 的日志），唯一放行 JSON-RPC 输出通道
   ``server.py`` 的 ``MCPServer.serve``。

用法::

    python scripts/check_redlines.py            # 扫描仓库，违规时退出码 1
    python scripts/check_redlines.py --root DIR
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

SIRCHMUNK_SCAN = ("src", "scripts", "tests", "main.py")
LLM_FALLBACK_SCAN = ("src", "scripts")
MCP_SCAN = "src/mcp_server"
SKIP_DIRS = {"__pycache__", ".venv", ".venv-sirchmunk", "node_modules"}

#: ``sys.stdout`` 放行：(仓库相对路径, 限定函数名)。
STDOUT_ALLOWLIST = {("src/mcp_server/server.py", "MCPServer.serve")}

#: ``llm_fallback`` 豁免：(仓库相对路径, 行号) -> 规格引用。新增条目须经 code owner 审批。
LLM_FALLBACK_EXEMPTIONS: dict[tuple[str, int], str] = {}


@dataclass(frozen=True)
class Violation:
    rule: str
    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.message}"


def _iter_py(root: Path, entries: tuple[str, ...] | str) -> list[Path]:
    if isinstance(entries, str):
        entries = (entries,)
    files: list[Path] = []
    for entry in entries:
        target = root / entry
        if target.is_file() and target.suffix == ".py":
            files.append(target)
        elif target.is_dir():
            files.extend(
                p for p in sorted(target.rglob("*.py"))
                if not SKIP_DIRS.intersection(p.relative_to(root).parts)
            )
    return files


def _parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _is_sirchmunk(name: str | None) -> bool:
    return bool(name) and (name == "sirchmunk" or name.startswith("sirchmunk."))


def _dynamic_import_target(node: ast.Call) -> str | None:
    func = node.func
    is_import_module = (
        isinstance(func, ast.Attribute) and func.attr == "import_module"
    ) or (isinstance(func, ast.Name) and func.id in {"import_module", "__import__"})
    if is_import_module and node.args and isinstance(node.args[0], ast.Constant):
        value = node.args[0].value
        return value if isinstance(value, str) else None
    return None


def check_sirchmunk_imports(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for path in _iter_py(root, SIRCHMUNK_SCAN):
        tree = _parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            hit: str | None = None
            if isinstance(node, ast.Import):
                hit = next((a.name for a in node.names if _is_sirchmunk(a.name)), None)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and _is_sirchmunk(node.module):
                hit = node.module
            elif isinstance(node, ast.Call):
                target = _dynamic_import_target(node)
                hit = target if _is_sirchmunk(target) else None
            if hit:
                violations.append(Violation(
                    "no-sirchmunk-import", rel, node.lineno,
                    f"imports {hit!r}; reach sirchmunk only via the HTTP sidecar",
                ))
    return violations


def _llm_fallback_values(tree: ast.AST) -> list[tuple[int, ast.AST]]:
    found: list[tuple[int, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "llm_fallback":
                    found.append((key.lineno, value))
        elif isinstance(node, ast.keyword) and node.arg == "llm_fallback":
            found.append((node.value.lineno, node.value))
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if node.value is None:
                continue
            for t in targets:
                named = (
                    (isinstance(t, ast.Name) and t.id == "llm_fallback")
                    or (isinstance(t, ast.Attribute) and t.attr == "llm_fallback")
                    or (isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                        and t.slice.value == "llm_fallback")
                )
                if named:
                    found.append((node.lineno, node.value))
    return found


def check_llm_fallback(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for path in _iter_py(root, LLM_FALLBACK_SCAN):
        tree = _parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()
        for lineno, value in _llm_fallback_values(tree):
            if isinstance(value, ast.Constant) and value.value is False:
                continue
            if (rel, lineno) in LLM_FALLBACK_EXEMPTIONS:
                continue
            violations.append(Violation(
                "llm-fallback-false", rel, lineno,
                f"llm_fallback must be the literal False, got {ast.unparse(value)!r}",
            ))
    return violations


class _StdoutVisitor(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.scope: list[str] = []
        self.violations: list[Violation] = []

    def _scoped(self, node: ast.AST) -> None:
        self.scope.append(node.name)  # type: ignore[attr-defined]
        self.generic_visit(node)
        self.scope.pop()

    visit_ClassDef = _scoped
    visit_FunctionDef = _scoped
    visit_AsyncFunctionDef = _scoped

    def _flag(self, node: ast.AST, message: str) -> None:
        self.violations.append(Violation("mcp-stdout", self.rel, node.lineno, message))  # type: ignore[attr-defined]

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "print":
            self._flag(node, "print() in MCP server code; log to stderr instead")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (isinstance(node.value, ast.Name) and node.value.id == "sys"
                and node.attr in {"stdout", "__stdout__"}):
            if (self.rel, ".".join(self.scope)) not in STDOUT_ALLOWLIST:
                self._flag(node, f"sys.{node.attr} reference; stdout is reserved for JSON-RPC")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "sys" and any(a.name in {"stdout", "__stdout__"} for a in node.names):
            self._flag(node, "imports sys.stdout; stdout is reserved for JSON-RPC")
        self.generic_visit(node)


def check_mcp_stdout(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for path in _iter_py(root, MCP_SCAN):
        tree = _parse(path)
        if tree is None:
            continue
        visitor = _StdoutVisitor(path.relative_to(root).as_posix())
        visitor.visit(tree)
        violations.extend(visitor.violations)
    return violations


def check_repo(root: Path = _ROOT) -> list[Violation]:
    return check_sirchmunk_imports(root) + check_llm_fallback(root) + check_mcp_stdout(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Static red-line checks (AI_NATIVE_SDLC_SPEC SD1.6).")
    parser.add_argument("--root", type=Path, default=_ROOT, help="repository root to scan")
    args = parser.parse_args(argv)
    violations = check_repo(args.root.resolve())
    for v in violations:
        print(v)
    if violations:
        print(f"check_redlines: {len(violations)} violation(s)", file=sys.stderr)
        return 1
    print("check_redlines: OK (0 violations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
