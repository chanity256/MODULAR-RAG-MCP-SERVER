"""KE1: evolve_launcher.py 的 AST / import-order 静态检查（离线，主 venv 不 import 启动器）。

启动器运行于 sidecar 独立 venv（import sirchmunk），主 venv 的离线测试只能做
静态检查（SIRCHMUNK_KNOWLEDGE_EVOLUTION_SPEC 7.1）：
- 模块级不 import sirchmunk（惰性到函数内，主 venv 解析本文件不会触发上游导入）；
- 补丁只包装 ``enable_knowledge_evolution``（setdefault，显式传参仍胜出），其余原样转发；
- uvicorn ``reload=False``，显式传 host / port / log_level；
- CLI 初始化顺序：work path 解析 + .env 加载在补丁之前，补丁在 import api.main 之前；
- 开关 / 签名 fail-fast 守卫存在（演化依赖复用、上游签名漂移拒绝启动）。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "scripts/sirchmunk/evolve_launcher.py"

_TREE = ast.parse(LAUNCHER.read_text(encoding="utf-8"), filename=str(LAUNCHER))


def _module_level_imports() -> list[str]:
    names: list[str] = []
    for node in _TREE.body:
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


def _find_call(func_name: str) -> ast.Call:
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id == func_name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == func_name)
        ):
            return node
    raise AssertionError(f"call not found: {func_name}")


def _func(name: str) -> ast.FunctionDef:
    for node in ast.walk(_TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function not found: {name}")


def _line_of(substring: str) -> int:
    for i, line in enumerate(LAUNCHER.read_text(encoding="utf-8").splitlines(), start=1):
        if substring in line:
            return i
    raise AssertionError(f"substring not found: {substring}")


class TestModuleLevelImports:
    def test_no_sirchmunk_import_at_module_level(self):
        """主 venv 解析（非运行）本文件不得触发 sirchmunk 导入：全部惰性到函数内。"""
        assert not any(n == "sirchmunk" or n.startswith("sirchmunk.") for n in _module_level_imports())

    def test_no_network_or_subprocess_at_module_level(self):
        assert not any(n.split(".")[0] in {"httpx", "requests", "socket", "subprocess", "urllib"} for n in _module_level_imports())


class TestPatchSemantics:
    def test_patched_init_only_wraps_evolution_flag(self):
        """补丁体只 setdefault enable_knowledge_evolution，其余参数原样转发。"""
        patched = _func("_patched_init")
        kwarg_names = []
        for node in ast.walk(patched):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setdefault":
                key = node.args[0]
                assert isinstance(key, ast.Constant)
                kwarg_names.append(key.value)
        assert kwarg_names == ["enable_knowledge_evolution"]
        # 转发：原始 __init__ 以 *args, **kwargs 调用，不增删参数。
        forwarded = [
            node for node in ast.walk(patched)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "original_init"
        ]
        assert len(forwarded) == 1
        assert any(isinstance(a, ast.Starred) for a in forwarded[0].args)
        assert any(kw.arg is None for kw in forwarded[0].keywords)

    def test_evolution_flag_set_true(self):
        patched = _func("_patched_init")
        for node in ast.walk(patched):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setdefault"
            ):
                assert isinstance(node.args[1], ast.Constant) and node.args[1].value is True
                return
        raise AssertionError("setdefault call not found")

    def test_signature_guard_present(self):
        """上游签名漂移时 fail-fast：检查 enable_knowledge_evolution 参数存在。"""
        patcher = _func("_patch_agentic_search")
        assert any(
            isinstance(node, ast.Constant) and node.value == "enable_knowledge_evolution"
            for node in ast.walk(patcher)
        )


class TestUicornRun:
    def test_reload_false_and_explicit_host_port_log_level(self):
        run = _find_call("run")
        kw = {k.arg: k.value for k in run.keywords}
        assert isinstance(kw["reload"], ast.Constant) and kw["reload"].value is False
        assert {k.arg for k in run.keywords} >= {"host", "port", "reload", "log_level"}


class TestImportOrder:
    def test_work_path_and_env_load_before_patch(self):
        main_fn = _func("main")
        calls = [
            node.func.id
            for node in ast.walk(main_fn)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        assert calls.index("_resolve_work_path") < calls.index("_patch_agentic_search")
        assert calls.index("_load_work_path_env") < calls.index("_patch_agentic_search")

    def test_patch_before_api_main_import(self):
        """补丁必须先于 import sirchmunk.api.main，使同步 / SSE / Chat 构造共用已补丁类。"""
        assert _line_of("AgenticSearch.__init__ = _patched_init") < _line_of("from sirchmunk.api.main import app")

    def test_env_load_replicates_cli_override_semantics(self):
        """.env 加载须与上游 cmd_serve 同源：经 sirchmunk.cli.cli._load_env_file（override=False）。"""
        loader = _func("_load_work_path_env")
        assert any(
            isinstance(n, ast.ImportFrom) and n.module == "sirchmunk.cli.cli"
            for n in ast.walk(loader)
        )
        # 回写：resolver 把绝对化后的 work path 写回 SIRCHMUNK_WORK_PATH。
        resolver = _func("_resolve_work_path")
        assert any(
            isinstance(n, ast.Subscript)
            and isinstance(n.value, ast.Attribute)
            and n.value.attr == "environ"
            for n in ast.walk(resolver)
        )


class TestFlagGuards:
    def test_evolution_flag_checked(self):
        assert _line_of('EVOLUTION_FLAG = "SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION"') > 0

    def test_reuse_combination_guard(self):
        """演化开启而复用显式关闭 → fail-fast（embedding_client 仅在复用开启时创建）。"""
        assert _line_of('REUSE_FLAG = "SIRCHMUNK_ENABLE_CLUSTER_REUSE"') > 0
        guards = _func("_check_flags")
        assert any(
            isinstance(node, ast.Constant) and node.value == "true"
            for node in ast.walk(guards)
        )
