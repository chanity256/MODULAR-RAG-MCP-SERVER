"""``scripts/sidecar_manager.py`` 托管编排单测（SR1，自 test_start_dashboard.py 迁移）。

覆盖探活 / 复用 / 拉起 / 脚本退出四条真实编排逻辑（``test_start_api_sidecar.py``
把 ``ensure_sidecar`` 整体替换为 fake，不覆盖这些路径）；零网络零子进程。
"""

from __future__ import annotations

import importlib.util
import urllib.error
from pathlib import Path
from types import SimpleNamespace

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sidecar_manager.py"
_spec = importlib.util.spec_from_file_location("sidecar_manager_script", _SCRIPT)
sidecar_manager = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sidecar_manager)


def _agentic_settings(enabled: bool, base_url: str = "http://127.0.0.1:8584"):
    return SimpleNamespace(
        agentic_search=SimpleNamespace(enabled=enabled, base_url=base_url)
    )


def test_sidecar_healthy_http_error_means_alive(monkeypatch):
    """任何 HTTP 响应（含 401 鉴权拒绝）都算 sidecar 存活。"""

    def raise_http_error(*a, **k):
        raise urllib.error.HTTPError(url="", code=401, msg="unauthorized", hdrs=None, fp=None)

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)
    assert sidecar_manager.sidecar_healthy("http://127.0.0.1:8584") is True


def test_sidecar_healthy_connection_error_means_down(monkeypatch):
    def raise_url_error(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", raise_url_error)
    assert sidecar_manager.sidecar_healthy("http://127.0.0.1:8584") is False


def test_ensure_sidecar_skips_when_disabled(monkeypatch):
    """agentic 关闭：不探活、不拉起。"""

    def fail(*a, **k):
        raise AssertionError("should not probe or spawn")

    import subprocess as sp

    monkeypatch.setattr(sp, "Popen", fail)
    assert sidecar_manager.ensure_sidecar(_agentic_settings(False), probe=fail) is None


def test_ensure_sidecar_reuses_healthy_instance(monkeypatch):
    """外部已启动且健康：只复用，不接管（返回 None）。"""

    def fail(*a, **k):
        raise AssertionError("should not spawn")

    import subprocess as sp

    monkeypatch.setattr(sp, "Popen", fail)
    assert sidecar_manager.ensure_sidecar(_agentic_settings(True), probe=lambda url: True) is None


def test_ensure_sidecar_spawns_and_waits_healthy(monkeypatch):
    """未运行：拉起脚本并轮询到健康，返回进程句柄供退出时回收。"""
    calls = {"healthy": 0}

    def fake_healthy(url):
        calls["healthy"] += 1
        return calls["healthy"] >= 2  # 第一次未就绪，第二次就绪

    spawned = {}

    class FakeProc:
        pid = 4242

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        spawned["cmd"] = cmd
        return FakeProc()

    import subprocess as sp

    monkeypatch.setattr(sp, "Popen", fake_popen)

    root = Path(__file__).resolve().parents[2]
    proc = sidecar_manager.ensure_sidecar(
        _agentic_settings(True), root=root, probe=fake_healthy, startup_timeout_s=5.0
    )
    assert proc is not None and proc.pid == 4242
    assert spawned["cmd"] == ["bash", str(root / "scripts" / "sirchmunk" / "start_sidecar.sh")]


def test_ensure_sidecar_script_exit_is_not_fatal(monkeypatch):
    """启动脚本秒退（如缺 venv/env）：警告并返回 None，主服务照常启动。"""

    class DeadProc:
        pid = 1

        def poll(self):
            return 1  # 已退出

        @property
        def returncode(self):
            return 1

    import subprocess as sp

    monkeypatch.setattr(sp, "Popen", lambda cmd, **k: DeadProc())
    root = Path(__file__).resolve().parents[2]
    assert (
        sidecar_manager.ensure_sidecar(
            _agentic_settings(True), root=root, probe=lambda url: False
        )
        is None
    )
