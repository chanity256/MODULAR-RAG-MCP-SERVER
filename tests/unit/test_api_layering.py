"""``src/api`` 分层与纪律守卫：薄适配层，不越层、不碰 stdout、不 import sirchmunk。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_API_DIR = Path(__file__).resolve().parents[2] / "src" / "api"
_FILES = sorted(_API_DIR.rglob("*.py"))

_FORBIDDEN_MODULES = {
    "mcp_server": "API 层经应用服务访问能力，不直接依赖 MCP 协议层",
    "sirchmunk": "主 venv 永不 import sirchmunk（进程隔离红线）",
    "streamlit": "API 不依赖 Streamlit",
    "ingestion": "页面/路由层禁止直接 import Pipeline 与核心组件，须经应用服务",
}


def test_api_package_is_discovered():
    assert len(_FILES) >= 10


@pytest.mark.parametrize("path", _FILES, ids=lambda p: str(p.relative_to(_API_DIR)))
def test_no_forbidden_imports_and_no_print(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        roots: list[str] = []
        if isinstance(node, ast.Import):
            roots = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots = [node.module.split(".")[0]]
        for root in roots:
            assert root not in _FORBIDDEN_MODULES, f"{path.name}: imports {root} ({_FORBIDDEN_MODULES[root]})"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "print", f"{path.name}: print() forbidden (stdout discipline)"
