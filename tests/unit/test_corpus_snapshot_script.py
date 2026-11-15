"""KE0: scripts/sirchmunk/corpus_snapshot.sh 的离线单测。

在临时 git 仓库上验证：物化内容、SHA256 清单逐字节确定性、不可覆盖护栏与
未知 commit 失败。零网络、不触真实 sidecar。

（2026-11-15：经用户两次明确授权，在 SD3.7 fix_mode 下删除并重建本文件：
①修正 _run_script 调用签名（传第 3 参数 repo_dir）；②修正 fixture 的
git add 范围（原 `git add -A` 把 data/runtime.bin 也提交了，与「未跟踪
文件不进快照」断言矛盾）。脚本本身未改动。新测试文件不受 test_guard 限制，
此处如实记录。）
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts/sirchmunk/corpus_snapshot.sh"
BASH = shutil.which("bash")
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(BASH is None or GIT is None, reason="bash/git not available")


def _run_git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        [GIT, "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


@pytest.fixture()
def sample_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "sample"
    repo.mkdir()
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "test")
    (repo / "README.md").write_text("# sample corpus\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    _run_git(repo, "add", "README.md", "src")
    _run_git(repo, "commit", "-qm", "init")
    commit = _run_git(repo, "rev-parse", "HEAD")
    # 未跟踪文件不得进入快照（git archive 语义）：data/ 从不加入 git。
    (repo / "data").mkdir()
    (repo / "data" / "runtime.bin").write_bytes(b"\x00\x01")
    # 第二个 commit 改动文件，供「不同 commit → 不同清单」用例使用。
    (repo / "README.md").write_text("# sample corpus v2\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-qm", "v2")
    return repo, commit


def _run_script(repo: Path, commit: str, out: Path):
    return subprocess.run(
        [BASH, str(SCRIPT), commit, str(out), str(repo)],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "HOME": str(repo.parent)},
    )


class TestCorpusSnapshot:
    def test_snapshot_contents_exclude_untracked(self, sample_repo, tmp_path):
        repo, commit = sample_repo
        out = tmp_path / "snap1"
        proc = _run_script(repo, commit, out)
        assert proc.returncode == 0, proc.stderr
        assert (out / "corpus" / "README.md").read_text(encoding="utf-8") == "# sample corpus\n"
        assert (out / "corpus" / "src" / "mod.py").exists()
        assert not (out / "corpus" / "data").exists()  # 未跟踪产物不进快照
        assert f"commit={commit}" in (out / "snapshot_info.txt").read_text(encoding="utf-8")

    def test_manifest_matches_and_is_deterministic(self, sample_repo, tmp_path):
        repo, commit = sample_repo
        out1, out2 = tmp_path / "snap1", tmp_path / "snap2"
        assert _run_script(repo, commit, out1).returncode == 0
        assert _run_script(repo, commit, out2).returncode == 0
        lines1 = (out1 / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        assert lines1 == (out2 / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        # 清单与 corpus/ 实际内容一致。
        expected = {
            "README.md": hashlib.sha256(
                (out1 / "corpus" / "README.md").read_bytes()
            ).hexdigest(),
            "src/mod.py": hashlib.sha256(
                (out1 / "corpus" / "src" / "mod.py").read_bytes()
            ).hexdigest(),
        }
        actual = {
            line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in lines1
        }
        assert actual == expected

    def test_different_commit_changes_manifest(self, sample_repo, tmp_path):
        repo, commit = sample_repo
        head = _run_git(repo, "rev-parse", "HEAD")
        out_old, out_new = tmp_path / "old", tmp_path / "new"
        assert _run_script(repo, commit, out_old).returncode == 0
        assert _run_script(repo, head, out_new).returncode == 0
        assert (out_old / "SHA256SUMS").read_text(encoding="utf-8") != (
            out_new / "SHA256SUMS"
        ).read_text(encoding="utf-8")
        assert (out_new / "corpus" / "README.md").read_text(
            encoding="utf-8"
        ) == "# sample corpus v2\n"

    def test_refuses_overwrite(self, sample_repo, tmp_path):
        repo, commit = sample_repo
        out = tmp_path / "snap"
        assert _run_script(repo, commit, out).returncode == 0
        proc = _run_script(repo, commit, out)
        assert proc.returncode == 2
        assert "already exists" in proc.stderr

    def test_unknown_commit_fails(self, sample_repo, tmp_path):
        repo, _ = sample_repo
        proc = _run_script(repo, "deadbeef" * 5, tmp_path / "snap")
        assert proc.returncode == 1
        assert "not found" in proc.stderr
