"""分层守卫：``src/core/chat/`` 不得 import ``mcp_server`` / ``observability``（WEBUI_CHAT_SPEC D8）。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

CHAT_DIR = Path(__file__).resolve().parents[2] / "src" / "core" / "chat"
FORBIDDEN = ("mcp_server", "observability")


def _imported_modules(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.lineno, node.module))
    return found


def _chat_files() -> list[Path]:
    return sorted(CHAT_DIR.rglob("*.py"))


def test_chat_package_has_sources():
    assert any(p.name == "answer_generator.py" for p in _chat_files())


@pytest.mark.parametrize("path", _chat_files(), ids=lambda p: p.name)
def test_chat_modules_do_not_import_upper_layers(path):
    offenders = [
        f"{path.name}:{lineno} imports {mod}"
        for lineno, mod in _imported_modules(path)
        if mod.split(".")[0] in FORBIDDEN
    ]
    assert not offenders, offenders


def test_importing_chat_package_does_not_load_upper_layers():
    import subprocess
    import sys

    code = (
        "import sys; import core.chat; "
        "bad = [m for m in sys.modules if m.split('.')[0] in ('mcp_server','observability')]; "
        "sys.exit(1 if bad else 0)"
    )
    src = str(CHAT_DIR.parents[1])
    proc = subprocess.run([sys.executable, "-c", code], env={"PYTHONPATH": src}, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
