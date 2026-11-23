"""SD1.6: scripts/check_redlines.py 三条红线的命中 / 放行样例（离线，样例写在 tmp_path）。"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check_redlines.py"
_spec = importlib.util.spec_from_file_location("check_redlines_script", _SCRIPT)
redlines = importlib.util.module_from_spec(_spec)
sys.modules["check_redlines_script"] = redlines
_spec.loader.exec_module(redlines)


def _write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")


def _rules(violations) -> list[tuple[str, str, int]]:
    return [(v.rule, v.path, v.line) for v in violations]


class TestSirchmunkImport:
    def test_flags_import_forms(self, tmp_path):
        _write(tmp_path, "src/a.py", "import sirchmunk\n")
        _write(tmp_path, "scripts/b.py", "from sirchmunk.search import X\n")
        _write(tmp_path, "tests/c.py", "import os, sirchmunk.core as sc\n")
        _write(tmp_path, "main.py", "import importlib\nimportlib.import_module('sirchmunk')\n")
        _write(tmp_path, "src/d.py", "__import__('sirchmunk.x')\n")
        got = _rules(redlines.check_sirchmunk_imports(tmp_path))
        assert {(p, ln) for _, p, ln in got} == {
            ("src/a.py", 1), ("scripts/b.py", 1), ("tests/c.py", 1), ("main.py", 2), ("src/d.py", 1),
        }
        assert all(rule == "no-sirchmunk-import" for rule, _, _ in got)

    def test_docstring_and_strings_are_not_imports(self, tmp_path):
        _write(tmp_path, "src/libs/agentic_search/http_backend.py", '''\
            """主 venv 永不
            import sirchmunk——所有上游耦合收敛在本文件。"""
            BACKEND = "sirchmunk_http"
            import sirchmunk_like_but_different
            ''')
        assert redlines.check_sirchmunk_imports(tmp_path) == []

    def test_shell_scripts_are_whitelisted(self, tmp_path):
        _write(tmp_path, "scripts/sirchmunk/preflight.sh", "python -c 'from sirchmunk.api import x'\n")
        assert redlines.check_sirchmunk_imports(tmp_path) == []

    def test_sidecar_venv_is_skipped(self, tmp_path):
        _write(tmp_path, "scripts/.venv-sirchmunk/lib/site.py", "import sirchmunk\n")
        assert redlines.check_sirchmunk_imports(tmp_path) == []

    def test_sidecar_launcher_whitelist_allows_exact_file(self, tmp_path):
        """KE1 白名单：按文件路径精确匹配，启动器自身放行（sidecar venv 侧脚本）。"""
        _write(tmp_path, "scripts/sirchmunk/evolve_launcher.py", "from sirchmunk.search import AgenticSearch\n")
        assert redlines.check_sirchmunk_imports(tmp_path) == []

    def test_sidecar_whitelist_does_not_cover_siblings(self, tmp_path):
        """白名单不得放宽到目录：同目录其他 .py 仍被拦截。"""
        _write(tmp_path, "scripts/sirchmunk/other.py", "import sirchmunk\n")
        _write(tmp_path, "scripts/sirchmunk/evolve_launcher_impl.py", "import sirchmunk\n")
        got = _rules(redlines.check_sirchmunk_imports(tmp_path))
        assert got == [
            ("no-sirchmunk-import", "scripts/sirchmunk/evolve_launcher_impl.py", 1),
            ("no-sirchmunk-import", "scripts/sirchmunk/other.py", 1),
        ]

    def test_sidecar_whitelist_entries_are_scoped_and_cited(self):
        """白名单条目必须限定在 scripts/sirchmunk/ 的 .py 且带 KE 规格引用。"""
        assert redlines.SIRCHMUNK_IMPORT_EXEMPTIONS, "whitelist must be explicit, not implicit"
        for path, ref in redlines.SIRCHMUNK_IMPORT_EXEMPTIONS.items():
            assert path.startswith("scripts/sirchmunk/") and path.endswith(".py")
            assert "SIRCHMUNK_KNOWLEDGE_EVOLUTION" in ref


class TestLlmFallback:
    def test_literal_false_passes(self, tmp_path):
        _write(tmp_path, "src/x.py", '''\
            payload = {"llm_fallback": False}
            call(llm_fallback=False)
            ''')
        assert redlines.check_llm_fallback(tmp_path) == []

    def test_other_values_fail(self, tmp_path):
        _write(tmp_path, "src/x.py", '''\
            payload = {"llm_fallback": True}
            call(llm_fallback=cfg.flag)
            payload["llm_fallback"] = 1
            self.llm_fallback = None
            ''')
        _write(tmp_path, "scripts/y.py", "llm_fallback = os.environ.get('X')\n")
        got = _rules(redlines.check_llm_fallback(tmp_path))
        assert {(p, ln) for _, p, ln in got} == {
            ("src/x.py", 1), ("src/x.py", 2), ("src/x.py", 3), ("src/x.py", 4), ("scripts/y.py", 1),
        }

    def test_tests_dir_not_scanned(self, tmp_path):
        _write(tmp_path, "tests/t.py", "assert {'llm_fallback': True}\n")
        assert redlines.check_llm_fallback(tmp_path) == []

    def test_exemption_entry_unlocks(self, tmp_path, monkeypatch):
        _write(tmp_path, "src/x.py", 'payload = {"llm_fallback": True}\n')
        monkeypatch.setitem(redlines.LLM_FALLBACK_EXEMPTIONS, ("src/x.py", 1), "SPEC §x.y")
        assert redlines.check_llm_fallback(tmp_path) == []


class TestMcpStdout:
    def test_server_serve_channel_allowed(self, tmp_path):
        _write(tmp_path, "src/mcp_server/server.py", '''\
            import sys
            class MCPServer:
                def serve(self, stdout=None):
                    stdout = stdout or sys.stdout
            ''')
        assert redlines.check_mcp_stdout(tmp_path) == []

    def test_stdout_elsewhere_in_server_fails(self, tmp_path):
        _write(tmp_path, "src/mcp_server/server.py", '''\
            import sys
            class MCPServer:
                def process_line(self):
                    sys.stdout.write("x")
            ''')
        assert _rules(redlines.check_mcp_stdout(tmp_path)) == [("mcp-stdout", "src/mcp_server/server.py", 4)]

    def test_print_and_handlers_fail(self, tmp_path):
        _write(tmp_path, "src/mcp_server/tools/t.py", '''\
            import logging, sys
            from sys import stdout
            print("debug")
            logging.StreamHandler(sys.stdout)
            logging.basicConfig(stream=sys.__stdout__)
            ''')
        got = _rules(redlines.check_mcp_stdout(tmp_path))
        assert sorted(ln for _, _, ln in got) == [2, 3, 4, 5]

    def test_outside_mcp_server_not_scanned(self, tmp_path):
        _write(tmp_path, "src/core/x.py", "print('ok')\nimport sys\nsys.stdout.write('x')\n")
        assert redlines.check_mcp_stdout(tmp_path) == []


class TestPageindexImport:
    """PI2：``no-pageindex-import`` 规则（pageindex / litellm 及子模块）。"""

    def test_flags_import_forms(self, tmp_path):
        _write(tmp_path, "src/a.py", "import pageindex\n")
        _write(tmp_path, "scripts/b.py", "from pageindex.flash import x\n")
        _write(tmp_path, "scripts/c.py", "import litellm\n")
        _write(tmp_path, "tests/d.py", "import importlib\nimportlib.import_module('litellm')\n")
        _write(tmp_path, "main.py", "__import__('pageindex.utils')\n")
        got = _rules(redlines.check_pageindex_imports(tmp_path))
        assert {(p, ln) for _, p, ln in got} == {
            ("src/a.py", 1), ("scripts/b.py", 1), ("scripts/c.py", 1),
            ("tests/d.py", 2), ("main.py", 1),
        }
        assert all(rule == "no-pageindex-import" for rule, _, _ in got)

    def test_docstring_and_strings_are_not_imports(self, tmp_path):
        _write(tmp_path, "src/libs/tree_search/http_backend.py", '''\
            """主 venv 永不
            import pageindex——所有上游耦合收敛在 sidecar 适配层。"""
            BACKEND = "pageindex_http"
            import pageindex_like_but_different
            SUMMARY = "litellm.completion 的文档说明"
            ''')
        assert redlines.check_pageindex_imports(tmp_path) == []

    def test_sidecar_venv_is_skipped(self, tmp_path):
        _write(tmp_path, "scripts/.venv-pageindex/lib/site.py", "import pageindex\n")
        assert redlines.check_pageindex_imports(tmp_path) == []

    def test_whitelist_allows_exact_files(self, tmp_path):
        _write(tmp_path, "scripts/pageindex/pi_adapter.py", "import litellm\n")
        _write(tmp_path, "scripts/pageindex/poc_compare.py", "from pageindex import PageIndexClient\n")
        assert redlines.check_pageindex_imports(tmp_path) == []

    def test_whitelist_does_not_cover_siblings(self, tmp_path):
        """白名单不得放宽到目录：同目录其他 .py 仍被拦截。"""
        _write(tmp_path, "scripts/pageindex/server.py", "import pageindex\n")
        _write(tmp_path, "scripts/pageindex/pi_adapter_impl.py", "import pageindex\n")
        got = _rules(redlines.check_pageindex_imports(tmp_path))
        assert got == [
            ("no-pageindex-import", "scripts/pageindex/pi_adapter_impl.py", 1),
            ("no-pageindex-import", "scripts/pageindex/server.py", 1),
        ]

    def test_whitelist_entries_are_scoped_and_cited(self):
        """白名单条目必须限定在 scripts/pageindex/ 的 .py 且带规格引用。"""
        assert redlines.PAGEINDEX_IMPORT_EXEMPTIONS, "whitelist must be explicit, not implicit"
        for path, ref in redlines.PAGEINDEX_IMPORT_EXEMPTIONS.items():
            assert path.startswith("scripts/pageindex/") and path.endswith(".py")
            assert "PAGEINDEX_TREE_RETRIEVAL" in ref


def test_real_repository_has_no_violations():
    assert [str(v) for v in redlines.check_repo(REPO_ROOT)] == []


def test_main_exit_codes(tmp_path, capsys):
    assert redlines.main(["--root", str(tmp_path)]) == 0
    _write(tmp_path, "src/a.py", "import sirchmunk\n")
    assert redlines.main(["--root", str(tmp_path)]) == 1
    assert "src/a.py:1: [no-sirchmunk-import]" in capsys.readouterr().out
