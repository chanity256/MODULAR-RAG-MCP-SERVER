"""SX3.1: scripts/sirchmunk/compile.sh 护栏与参数拼装（离线）。

fake sidecar venv（``bin/sirchmunk`` 把参数逐行写入文件）+ PATH 上的 fake
``curl``（按环境变量回显 HTTP 状态码）——零网络、不需要真实 sirchmunk。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts/sirchmunk/compile.sh"

BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


@pytest.fixture()
def harness(tmp_path: Path):
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    args_file = tmp_path / "args.txt"
    fake_sirchmunk = venv / "bin/sirchmunk"
    fake_sirchmunk.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$FAKE_ARGS_FILE"\nexit "${FAKE_RC:-0}"\n',
        encoding="utf-8",
    )
    fake_sirchmunk.chmod(0o755)

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        '#!/usr/bin/env bash\n'
        'printf "%s" "${FAKE_CURL_CODE:-000}"\n'
        '[ "${FAKE_CURL_CODE:-000}" = "000" ] && exit 7\n'
        'exit 0\n',
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    work = tmp_path / "work"
    env_file = tmp_path / "env-sirchmunk"
    env_file.write_text(
        f"SIRCHMUNK_WORK_PATH={work}\nLLM_API_KEY=fake\nSIRCHMUNK_COMPILE_PATHS=\"README.md docs\"\n",
        encoding="utf-8",
    )

    def run(*args: str, curl_code: str = "000", rc: int = 0, env_extra: dict | None = None):
        if args_file.exists():
            args_file.unlink()
        env = {
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "SIRCHMUNK_VENV": str(venv),
            "SIRCHMUNK_ENV_FILE": str(env_file),
            "FAKE_ARGS_FILE": str(args_file),
            "FAKE_CURL_CODE": curl_code,
            "FAKE_RC": str(rc),
        }
        env.pop("SIRCHMUNK_COMPILE_PATHS", None)
        env.update(env_extra or {})
        proc = subprocess.run(
            [BASH, str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=30
        )
        argv = args_file.read_text(encoding="utf-8").splitlines() if args_file.exists() else None
        return proc, argv

    return {"run": run, "tmp": tmp_path, "work": work, "env_file": env_file, "venv": venv}


def _paths_of(argv: list[str]) -> list[str]:
    start = argv.index("--paths") + 1
    end = next((i for i in range(start, len(argv)) if argv[i].startswith("--")), len(argv))
    return argv[start:end]


class TestArgumentAssembly:
    def test_default_paths_from_env(self, harness):
        proc, argv = harness["run"]()
        assert proc.returncode == 0, proc.stderr
        assert argv[:3] == ["compile", "--work-path", str(harness["work"])]
        assert _paths_of(argv) == [str(REPO_ROOT / "README.md"), str(REPO_ROOT / "docs")]
        assert "--full" not in argv  # 缺省增量编译

    def test_explicit_paths_override_env(self, harness):
        proc, argv = harness["run"]("--", "docs/reference")
        assert proc.returncode == 0, proc.stderr
        assert _paths_of(argv) == [str(REPO_ROOT / "docs/reference")]

    def test_full_shallow_and_numeric_flags(self, harness):
        proc, argv = harness["run"](
            "--full", "--shallow", "--max-files", "20", "--concurrency", "2", "--", "README.md"
        )
        assert proc.returncode == 0, proc.stderr
        assert "--full" in argv and "--shallow" in argv
        assert argv[argv.index("--max-files") + 1] == "20"
        assert argv[argv.index("--concurrency") + 1] == "2"

    def test_relative_work_path_resolved_against_repo_root(self, harness):
        harness["env_file"].write_text("SIRCHMUNK_WORK_PATH=./data/db/sirchmunk\n", encoding="utf-8")
        proc, argv = harness["run"]("--", "README.md")
        assert proc.returncode == 0, proc.stderr
        assert argv[argv.index("--work-path") + 1] == str(REPO_ROOT / "data/db/sirchmunk")

    def test_restart_notice_after_write_compile(self, harness):
        proc, _ = harness["run"]("--", "README.md")
        assert "start_sidecar.sh" in proc.stderr

    def test_upstream_exit_code_propagated_without_notice(self, harness):
        proc, _ = harness["run"]("--", "README.md", rc=5)
        assert proc.returncode == 5
        assert "start_sidecar.sh" not in proc.stderr

    @pytest.mark.parametrize(
        "args",
        [
            pytest.param(("--status", "--lint"), id="status-and-lint"),
            pytest.param(("--status", "--full"), id="status-with-full"),
            pytest.param(("--lint", "--shallow"), id="lint-with-shallow"),
            pytest.param(("--fix",), id="fix-without-lint"),
            pytest.param(("--max-files", "0"), id="max-files-zero"),
            pytest.param(("--concurrency", "x"), id="concurrency-nan"),
            pytest.param(("--max-files",), id="max-files-missing-value"),
            pytest.param(("README.md",), id="path-without-separator"),
        ],
    )
    def test_usage_errors(self, harness, args):
        proc, argv = harness["run"](*args)
        assert proc.returncode == 2, proc.stderr
        assert argv is None


class TestStopGuard:
    @pytest.mark.parametrize("code", ["200", "401", "500"])
    def test_refuses_when_sidecar_alive(self, harness, code):
        """任何 HTTP 响应（含 401 token 不一致）都说明 sidecar 在跑。"""
        proc, argv = harness["run"]("--", "README.md", curl_code=code)
        assert proc.returncode == 3
        assert "stop it before compiling" in proc.stderr
        assert argv is None

    def test_lint_fix_is_a_write_mode(self, harness):
        proc, argv = harness["run"]("--lint", "--fix", "--", "README.md", curl_code="200")
        assert proc.returncode == 3
        assert argv is None

    @pytest.mark.parametrize("args", [("--status",), ("--lint",)])
    def test_read_only_modes_skip_probe(self, harness, args):
        proc, argv = harness["run"](*args, "--", "README.md", curl_code="200")
        assert proc.returncode == 0, proc.stderr
        assert argv[-1] == args[0]
        assert "start_sidecar.sh" not in proc.stderr

    def test_fails_closed_without_curl(self, harness, tmp_path):
        bare_bin = tmp_path / "barebin"
        bare_bin.mkdir()
        for tool in ("dirname", "find", "sed"):
            src = shutil.which(tool)
            if src:
                (bare_bin / tool).symlink_to(src)
        proc, argv = harness["run"]("--", "README.md", env_extra={"PATH": str(bare_bin)})
        assert proc.returncode == 1
        assert "curl not found" in proc.stderr
        assert argv is None


class TestPathGuard:
    @pytest.mark.parametrize("raw", [".", "./", str(REPO_ROOT), "..", "/"])
    def test_repo_root_and_ancestors_rejected(self, harness, raw):
        proc, argv = harness["run"]("--", raw)
        assert proc.returncode == 2, proc.stderr
        assert argv is None

    @pytest.mark.parametrize("name", [".venv", ".venv-sirchmunk", ".git", "build", "logs", "cache"])
    def test_path_containing_forbidden_dir_rejected(self, harness, name):
        target = harness["tmp"] / "corpus"
        (target / "sub" / name).mkdir(parents=True)
        proc, argv = harness["run"]("--", str(target))
        assert proc.returncode == 2
        assert "forbidden directory" in proc.stderr
        assert argv is None

    def test_path_inside_forbidden_dir_rejected(self, harness):
        target = harness["tmp"] / "build" / "docs"
        target.mkdir(parents=True)
        proc, argv = harness["run"]("--", str(target))
        assert proc.returncode == 2
        assert "'build'" in proc.stderr

    def test_repo_internal_forbidden_dir_rejected(self, harness):
        proc, argv = harness["run"]("--", "logs")
        assert proc.returncode == 2
        assert argv is None

    def test_work_path_overlap_rejected(self, harness):
        (harness["work"] / "inner").mkdir(parents=True)
        proc, _ = harness["run"]("--", str(harness["work"].parent))
        assert proc.returncode == 2 and "work_path" in proc.stderr
        proc, _ = harness["run"]("--", str(harness["work"] / "inner"))
        assert proc.returncode == 2 and "work_path" in proc.stderr

    def test_missing_path_rejected(self, harness):
        proc, argv = harness["run"]("--", "does/not/exist")
        assert proc.returncode == 2 and "does not exist" in proc.stderr
        assert argv is None

    def test_clean_external_dir_accepted(self, harness):
        target = harness["tmp"] / "corpus"
        (target / "notes").mkdir(parents=True)
        (target / "notes" / "a.md").write_text("x", encoding="utf-8")
        proc, argv = harness["run"]("--", str(target))
        assert proc.returncode == 0, proc.stderr
        assert _paths_of(argv) == [str(target)]  # 不展开符号链接（macOS /var → /private/var）

    def test_empty_default_paths_rejected(self, harness):
        harness["env_file"].write_text(
            f"SIRCHMUNK_WORK_PATH={harness['work']}\nSIRCHMUNK_COMPILE_PATHS=\n", encoding="utf-8"
        )
        proc, argv = harness["run"]()
        assert proc.returncode == 2 and "SIRCHMUNK_COMPILE_PATHS" in proc.stderr


class TestEnvironment:
    def test_missing_venv(self, harness, tmp_path):
        proc, _ = harness["run"]("--", "README.md", env_extra={"SIRCHMUNK_VENV": str(tmp_path / "nope")})
        assert proc.returncode == 1 and "setup_venv.sh" in proc.stderr

    def test_missing_env_file(self, harness, tmp_path):
        proc, _ = harness["run"](
            "--", "README.md", env_extra={"SIRCHMUNK_ENV_FILE": str(tmp_path / "nope")}
        )
        assert proc.returncode == 1 and "env.example" in proc.stderr

    def test_missing_work_path(self, harness):
        harness["env_file"].write_text("LLM_API_KEY=fake\n", encoding="utf-8")
        proc, _ = harness["run"]("--", "README.md")
        assert proc.returncode == 1 and "SIRCHMUNK_WORK_PATH" in proc.stderr

    def test_env_example_ships_safe_default_paths(self):
        text = (REPO_ROOT / "scripts/sirchmunk/env.example").read_text(encoding="utf-8")
        assert 'SIRCHMUNK_COMPILE_PATHS="README.md docs"' in text
