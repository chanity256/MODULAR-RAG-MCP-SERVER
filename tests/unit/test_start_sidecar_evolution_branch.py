"""KE1: start_sidecar.sh 演化开关分支（离线，fake venv / fake 可执行文件）。

SIRCHMUNK_KNOWLEDGE_EVOLUTION_SPEC 7.1：验证关闭分支仍执行原
``sirchmunk serve`` 且不加载启动器；开启分支先跑 preflight 前置、再改走
sidecar venv 内的 evolve_launcher.py 并显式传 host / port / log level；
env 文件语义（source 覆盖进程环境）；env.example 显式默认 false。
零网络、零真实模型：rg / rga / curl / venv python 全部为 PATH 上的 fake。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts/sirchmunk/start_sidecar.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def _exe(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture()
def harness(tmp_path: Path):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    # preflight 的 rg / rga / curl 依赖：rga 缺失会非零退出，curl 不可达仅告警。
    _exe(fake_bin / "rg", 'echo "ripgrep 14.0.0"\n')
    _exe(fake_bin / "rga", 'echo "ripgrep-all 0.10.0"\n')
    _exe(fake_bin / "curl", "exit 7\n")

    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    serve_record = tmp_path / "serve_args.txt"
    launch_record = tmp_path / "launch_args.txt"
    # fake venv python：preflight 的 -c 预下载调用返回成功目录；
    # start_sidecar 的启动器调用（位置参数形式）记录 argv。
    _exe(
        venv / "bin/python",
        'if [ "$1" = "-c" ]; then\n'
        '  case "$2" in *preload_model*) printf "%s" "$FAKE_PRELOAD_DIR";; esac\n'
        "  exit 0\n"
        "fi\n"
        'printf "%s\\n" "$@" > "$FAKE_LAUNCH_RECORD"\n'
        "exit 0\n",
    )
    # fake sirchmunk CLI：记录 serve argv。
    _exe(venv / "bin/sirchmunk", 'printf "%s\\n" "$@" > "$FAKE_SERVE_RECORD"\nexit 0\n')
    # fake preflight：默认透传到真脚本（见用例），可被 FAKE_PREFLIGHT 替换。
    fake_preflight = tmp_path / "fake_preflight.sh"
    _exe(fake_preflight, 'printf "fake-preflight\\n"\nexit 0\n')

    env_file = tmp_path / "env-sirchmunk"

    def run(env_text: str | None, preflight: str = "real", **extra: str):
        for rec in (serve_record, launch_record):
            if rec.exists():
                rec.unlink()
        if env_text is None:
            if env_file.exists():
                env_file.unlink()
        else:
            env_file.write_text(env_text, encoding="utf-8")
        env = {
            k: v for k, v in os.environ.items()
            if not k.startswith(("SIRCHMUNK_", "EMBEDDING_", "CLUSTER_", "FAKE_"))
        }
        env.update(
            PATH=f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            SIRCHMUNK_ENV_FILE=str(env_file),
            SIRCHMUNK_VENV=str(venv),
            FAKE_SERVE_RECORD=str(serve_record),
            FAKE_LAUNCH_RECORD=str(launch_record),
            FAKE_PRELOAD_DIR=str(tmp_path / "models"),
        )
        if preflight == "fake":
            env["SIRCHMUNK_PREFLIGHT_SCRIPT"] = str(fake_preflight)
        env.update(extra)
        proc = subprocess.run(
            [BASH, str(SCRIPT), str(venv), str(env_file)],
            capture_output=True, text=True, env=env, timeout=30, cwd=str(REPO_ROOT),
        )
        serve_args = serve_record.read_text().splitlines() if serve_record.exists() else None
        launch_args = launch_record.read_text().splitlines() if launch_record.exists() else None
        return proc, serve_args, launch_args

    return {
        "run": run,
        "tmp": tmp_path,
        "venv": venv,
        "fake_preflight": fake_preflight,
    }


ENV_CLOSED = "SIRCHMUNK_API_TOKEN=tok\nSIRCHMUNK_ENABLE_CLUSTER_REUSE=false\n"
ENV_OPEN = (
    "SIRCHMUNK_API_TOKEN=tok\n"
    "SIRCHMUNK_ENABLE_CLUSTER_REUSE=true\n"
    "SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION=true\n"
)


class TestClosedBranch:
    def test_closed_branch_runs_original_serve(self, harness):
        proc, serve_args, launch_args = harness["run"](ENV_CLOSED)
        assert proc.returncode == 0, proc.stderr
        assert serve_args == ["serve", "--host", "127.0.0.1", "--port", "8584"]
        assert launch_args is None  # 启动器不被加载
        assert "evolve_launcher.py" not in proc.stdout

    def test_unset_evolution_defaults_to_closed(self, harness):
        proc, serve_args, launch_args = harness["run"]("SIRCHMUNK_API_TOKEN=tok\n")
        assert proc.returncode == 0, proc.stderr
        assert serve_args is not None and launch_args is None

    def test_env_file_overrides_process_env(self, harness):
        """env 文件语义与既有口径一致：source 覆盖进程环境（文件显式 false 胜出）。"""
        proc, serve_args, launch_args = harness["run"](
            ENV_CLOSED + "SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION=false\n",
            SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION="true",
        )
        assert proc.returncode == 0, proc.stderr
        assert serve_args is not None and launch_args is None

    def test_host_port_override(self, harness):
        proc, serve_args, _ = harness["run"](
            ENV_CLOSED, SIRCHMUNK_HOST="127.0.0.2", SIRCHMUNK_PORT="8599"
        )
        assert proc.returncode == 0, proc.stderr
        assert serve_args == ["serve", "--host", "127.0.0.2", "--port", "8599"]


class TestOpenBranch:
    def test_open_branch_runs_preflight_then_launcher(self, harness):
        proc, serve_args, launch_args = harness["run"](ENV_OPEN, preflight="fake")
        assert proc.returncode == 0, proc.stderr
        assert serve_args is None  # 原命令不再执行
        assert launch_args is not None and len(launch_args) >= 1
        assert launch_args[0].endswith("evolve_launcher.py")
        assert "--host" in launch_args and "127.0.0.1" in launch_args
        assert "--port" in launch_args and "8584" in launch_args
        assert "--log-level" in launch_args and "info" in launch_args

    def test_open_branch_runs_real_preflight_first(self, harness):
        """开启分支默认先跑仓库内真 preflight.sh（fake 工具环境下应通过），再进启动器。"""
        proc, _, launch_args = harness["run"](ENV_OPEN)
        assert proc.returncode == 0, proc.stderr
        assert "== preflight: sidecar dependencies ==" in proc.stdout
        assert launch_args is not None  # preflight 通过后照常进启动器

    def test_preflight_failure_blocks_launcher(self, harness):
        failing = harness["tmp"] / "failing_preflight.sh"
        _exe(failing, 'echo "preflight failed"\nexit 1\n')
        proc, _, launch_args = harness["run"](
            ENV_OPEN, preflight="fake", SIRCHMUNK_PREFLIGHT_SCRIPT=str(failing)
        )
        assert proc.returncode != 0
        assert launch_args is None
        assert "preflight failed" in proc.stdout

    def test_log_level_propagated(self, harness):
        proc, _, launch_args = harness["run"](
            ENV_OPEN, preflight="fake", SIRCHMUNK_LOG_LEVEL="debug"
        )
        assert proc.returncode == 0, proc.stderr
        assert launch_args is not None and "debug" in launch_args


class TestEnvTemplate:
    def test_evolution_stays_explicitly_disabled(self):
        """模板必须显式保留 =false（开启是显式动作，SIRCHMUNK_KNOWLEDGE_EVOLUTION_SPEC KE1）。"""
        lines = (REPO_ROOT / "scripts/sirchmunk/env.example").read_text(encoding="utf-8").splitlines()
        assert "SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION=false" in lines
        assert "SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION=true" not in lines
