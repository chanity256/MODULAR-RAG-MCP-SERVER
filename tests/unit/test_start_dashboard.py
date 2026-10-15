"""scripts/start_dashboard.py 参数解析（阶段 G1）。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from textwrap import dedent

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "start_dashboard.py"
_MODULE_NAME = "scripts_start_dashboard"


def _load():
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


cli = _load()


def test_parse_headless_and_explicit_port():
    args = cli.build_parser().parse_args(["--port", "8502", "--headless"])
    assert args.port == 8502
    assert args.headless is True
    assert cli.resolve_port(args) == 8502


def test_resolve_port_from_settings(tmp_path):
    config = tmp_path / "settings.yaml"
    config.write_text(
        dedent(
            """\
            llm:
              provider: azure
              model: gpt-4o
            embedding:
              provider: openai
              model: text-embedding-3-small
            vision_llm:
              provider: azure
              model: gpt-4o
            vector_store:
              backend: chroma
            retrieval:
              sparse_backend: bm25
              fusion_algorithm: rrf
            rerank:
              backend: none
            evaluation:
              backends: [custom]
            dashboard:
              port: 8601
            """
        ),
        encoding="utf-8",
    )
    args = cli.build_parser().parse_args(["--config", str(config)])
    assert cli.resolve_port(args) == 8601


# --------------------------------------------------------------------- #
# Sidecar 托管编排（探活 / 拉起 / 回收）
# --------------------------------------------------------------------- #


def _agentic_settings(enabled: bool, base_url: str = "http://127.0.0.1:8584"):
    from types import SimpleNamespace

    return SimpleNamespace(
        agentic_search=SimpleNamespace(enabled=enabled, base_url=base_url)
    )


def test_sidecar_healthy_http_error_means_alive(monkeypatch):
    """任何 HTTP 响应（含 401 鉴权拒绝）都算 sidecar 存活。"""
    import urllib.error

    def raise_http_error(*a, **k):
        raise urllib.error.HTTPError(url="", code=401, msg="unauthorized", hdrs=None, fp=None)

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)
    assert cli.sidecar_healthy("http://127.0.0.1:8584") is True


def test_sidecar_healthy_connection_error_means_down(monkeypatch):
    import urllib.error

    def raise_url_error(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", raise_url_error)
    assert cli.sidecar_healthy("http://127.0.0.1:8584") is False


def test_ensure_sidecar_skips_when_disabled(monkeypatch):
    """agentic 关闭：不探活、不拉起。"""

    def fail(*a, **k):
        raise AssertionError("should not probe or spawn")

    monkeypatch.setattr(cli, "sidecar_healthy", fail)
    import subprocess as sp

    monkeypatch.setattr(sp, "Popen", fail)
    assert cli.ensure_sidecar(_agentic_settings(False)) is None


def test_ensure_sidecar_reuses_healthy_instance(monkeypatch):
    """外部已启动且健康：只复用，不接管（返回 None）。"""
    monkeypatch.setattr(cli, "sidecar_healthy", lambda url: True)
    import subprocess as sp

    def fail(*a, **k):
        raise AssertionError("should not spawn")

    monkeypatch.setattr(sp, "Popen", fail)
    assert cli.ensure_sidecar(_agentic_settings(True)) is None


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

    monkeypatch.setattr(cli, "sidecar_healthy", fake_healthy)
    import subprocess as sp

    monkeypatch.setattr(sp, "Popen", fake_popen)
    monkeypatch.setattr(cli, "SIDECAR_STARTUP_TIMEOUT_S", 5.0)

    root = Path(__file__).resolve().parents[2]
    proc = cli.ensure_sidecar(_agentic_settings(True), root=root)
    assert proc is not None and proc.pid == 4242
    assert spawned["cmd"] == ["bash", str(root / "scripts" / "sirchmunk" / "start_sidecar.sh")]


def test_ensure_sidecar_script_exit_is_not_fatal(monkeypatch):
    """启动脚本秒退（如缺 venv/env）：警告并返回 None，Dashboard 照常启动。"""

    class DeadProc:
        pid = 1

        def poll(self):
            return 1  # 已退出

        @property
        def returncode(self):
            return 1

    monkeypatch.setattr(cli, "sidecar_healthy", lambda url: False)
    import subprocess as sp

    monkeypatch.setattr(sp, "Popen", lambda cmd, **k: DeadProc())
    root = Path(__file__).resolve().parents[2]
    assert cli.ensure_sidecar(_agentic_settings(True), root=root) is None


# --------------------------------------------------------------------- #
# 弃用提示（WC10.2）：只写 stderr，stdout 纪律不变
# --------------------------------------------------------------------- #


def test_deprecation_notice_points_to_web_ui():
    assert "scripts/start_api.py --serve-ui" in cli.DEPRECATION_NOTICE
    assert "DEPRECATED" in cli.DEPRECATION_NOTICE


def test_main_prints_deprecation_notice_to_stderr_only(monkeypatch, capsys):
    """弃用提示进 stderr、不进 stdout；Streamlit 子进程照常启动并回传退出码。"""
    launched = {}

    class FakeStreamlit:
        def wait(self):
            return 0

        def poll(self):
            return 0

    def fake_popen(cmd, **kwargs):
        launched["cmd"] = cmd
        return FakeStreamlit()

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli.signal, "signal", lambda *a, **k: None)

    assert cli.main(["--port", "8502", "--no-sidecar"]) == 0

    captured = capsys.readouterr()
    assert "scripts/start_api.py --serve-ui" in captured.err
    assert "DEPRECATED" not in captured.out
    assert launched["cmd"][1:4] == ["-m", "streamlit", "run"]
    assert "8502" in launched["cmd"]
