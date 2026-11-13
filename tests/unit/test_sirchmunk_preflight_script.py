"""SX4.1: scripts/sirchmunk/preflight.sh 的复用模型预下载步骤（离线）。

PATH 上放 fake rg/rga/curl，sidecar venv 放 fake ``bin/python``（记录收到的
预下载参数并按环境变量决定成败）——零网络、不下载任何模型。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts/sirchmunk/preflight.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def _exe(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture()
def harness(tmp_path: Path):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _exe(fake_bin / "rg", 'echo "ripgrep 14.0.0"\n')
    _exe(fake_bin / "rga", 'echo "ripgrep-all 0.10.0"\n')
    _exe(fake_bin / "curl", "exit 7\n")  # sidecar 未启动（只告警）

    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    record = tmp_path / "preload.txt"
    _exe(
        venv / "bin/python",
        'printf "%s\\n%s\\n" "$PRELOAD_CACHE_DIR" "$PRELOAD_MODEL_ID" > "$FAKE_RECORD"\n'
        '[ "${FAKE_PRELOAD_FAIL:-0}" = "1" ] && exit 1\n'
        'printf "%s" "$PRELOAD_CACHE_DIR/model"\n',
    )
    env_file = tmp_path / "env-sirchmunk"

    def run(env_text: str | None, **extra: str):
        if record.exists():
            record.unlink()
        if env_text is None:
            if env_file.exists():
                env_file.unlink()
        else:
            env_file.write_text(env_text, encoding="utf-8")
        env = {
            k: v for k, v in os.environ.items()
            if not k.startswith(("SIRCHMUNK_", "EMBEDDING_", "CLUSTER_"))
        }
        env.update(
            PATH=f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            SIRCHMUNK_ENV_FILE=str(env_file),
            SIRCHMUNK_VENV=str(venv),
            FAKE_RECORD=str(record),
            HOME=str(tmp_path / "home"),
        )
        env.update(extra)
        proc = subprocess.run([BASH, str(SCRIPT)], capture_output=True, text=True, env=env, timeout=30)
        recorded = record.read_text(encoding="utf-8").split("\n") if record.exists() else None
        return proc, recorded

    return {"run": run, "tmp": tmp_path, "venv": venv}


class TestClusterReusePreload:
    def test_disabled_skips_preload(self, harness):
        proc, recorded = harness["run"]("SIRCHMUNK_ENABLE_CLUSTER_REUSE=false\n")
        assert proc.returncode == 0, proc.stdout
        assert "cluster reuse disabled" in proc.stdout
        assert recorded is None

    @pytest.mark.parametrize("value", ["true", "TRUE", "True"])
    def test_enabled_preloads_into_work_path_cache(self, harness, value):
        work = harness["tmp"] / "work"
        proc, recorded = harness["run"](
            f"SIRCHMUNK_WORK_PATH={work}\nSIRCHMUNK_ENABLE_CLUSTER_REUSE={value}\n"
        )
        assert proc.returncode == 0, proc.stdout
        assert recorded[0] == f"{work}/.cache/models"
        assert recorded[1] == ""  # 未设 EMBEDDING_MODEL_ID → 上游默认
        assert "[ok] embedding model ready" in proc.stdout

    def test_explicit_cache_dir_and_model_id(self, harness):
        work = harness["tmp"] / "work"
        proc, recorded = harness["run"](
            f"SIRCHMUNK_WORK_PATH={work}\n"
            "SIRCHMUNK_ENABLE_CLUSTER_REUSE=true\n"
            "EMBEDDING_CACHE_DIR=${SIRCHMUNK_WORK_PATH}/models-x\n"
            "EMBEDDING_MODEL_ID=org/some-model\n"
        )
        assert proc.returncode == 0, proc.stdout
        assert recorded[:2] == [f"{work}/models-x", "org/some-model"]

    def test_unset_flag_treated_as_enabled(self, harness):
        """上游缺省为 true：env 文件没有该键时必须告警并照常预下载。"""
        work = harness["tmp"] / "work"
        proc, recorded = harness["run"](f"SIRCHMUNK_WORK_PATH={work}\n")
        assert proc.returncode == 0, proc.stdout
        assert "upstream treats this as ENABLED" in proc.stdout
        assert recorded is not None

    def test_env_file_overrides_process_env(self, harness):
        proc, recorded = harness["run"](
            "SIRCHMUNK_ENABLE_CLUSTER_REUSE=false\n", SIRCHMUNK_ENABLE_CLUSTER_REUSE="true"
        )
        assert "cluster reuse disabled" in proc.stdout
        assert recorded is None

    def test_preload_failure_fails_preflight(self, harness):
        proc, _ = harness["run"]("SIRCHMUNK_ENABLE_CLUSTER_REUSE=true\n", FAKE_PRELOAD_FAIL="1")
        assert proc.returncode == 1
        assert "[FAIL] embedding model preload failed" in proc.stdout

    def test_skip_flag(self, harness):
        proc, recorded = harness["run"](
            "SIRCHMUNK_ENABLE_CLUSTER_REUSE=true\n", SIRCHMUNK_SKIP_MODEL_PRELOAD="1"
        )
        assert proc.returncode == 0
        assert "SIRCHMUNK_SKIP_MODEL_PRELOAD=1" in proc.stdout
        assert recorded is None

    def test_missing_venv_python_fails(self, harness):
        proc, _ = harness["run"](
            "SIRCHMUNK_ENABLE_CLUSTER_REUSE=true\n", SIRCHMUNK_VENV=str(harness["tmp"] / "nope")
        )
        assert proc.returncode == 1
        assert "setup_venv.sh" in proc.stdout

    def test_no_env_file_uses_process_env(self, harness):
        proc, recorded = harness["run"](None, SIRCHMUNK_ENABLE_CLUSTER_REUSE="false")
        assert proc.returncode == 0
        assert recorded is None


class TestEnvTemplate:
    def test_reuse_stays_explicitly_disabled(self):
        """模板必须保留生效的 =false：注释掉它会按上游缺省开启复用。"""
        lines = (REPO_ROOT / "scripts/sirchmunk/env.example").read_text(encoding="utf-8").splitlines()
        assert "SIRCHMUNK_ENABLE_CLUSTER_REUSE=false" in lines
        assert "SIRCHMUNK_ENABLE_CLUSTER_REUSE=true" not in lines

    @pytest.mark.parametrize(
        "key", ["CLUSTER_SIM_THRESHOLD", "CLUSTER_SIM_TOP_K", "EMBEDDING_MODEL_ID", "EMBEDDING_CACHE_DIR"]
    )
    def test_reuse_knobs_documented_but_commented(self, harness, key):
        lines = (REPO_ROOT / "scripts/sirchmunk/env.example").read_text(encoding="utf-8").splitlines()
        assert any(line.startswith(f"# {key}=") for line in lines)
        assert not any(line.startswith(f"{key}=") for line in lines)
