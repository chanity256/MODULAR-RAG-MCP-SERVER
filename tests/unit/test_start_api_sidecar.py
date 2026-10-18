"""``scripts/start_api.py --manage-sidecar`` 托管编排单测（WEBUI_SIDECAR_SPEC WS0）。

不真正启动 uvicorn / sidecar：``uvicorn.run`` 与 ``sidecar_manager`` 的编排函数
全部注入 fake，零网络。
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import uvicorn

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "start_api.py"
_spec = importlib.util.spec_from_file_location("start_api_script_ws0", _SCRIPT)
start_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(start_api)


class FakeProc:
    pid = 4321

    def poll(self):
        return None


@pytest.fixture
def run_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    monkeypatch.delenv("RAG_API_TOKEN", raising=False)
    return calls


@pytest.fixture
def inject_settings(monkeypatch):
    """注入指定 ``agentic_search.enabled`` 的 Settings（默认配置 agentic 为 true，须显式覆盖）。"""
    from core.settings import load_settings

    def _inject(enabled: bool):
        settings = load_settings(start_api.DEFAULT_CONFIG_PATH)
        settings = replace(
            settings, agentic_search=replace(settings.agentic_search, enabled=enabled)
        )
        monkeypatch.setattr("core.settings.load_settings", lambda config_path: settings)
        return settings

    return _inject


def test_manage_sidecar_flag_defaults_off():
    assert start_api.build_parser().parse_args([]).manage_sidecar is False
    assert start_api.build_parser().parse_args(["--manage-sidecar"]).manage_sidecar is True


def test_manage_sidecar_default_off_never_touches_sidecar(run_calls, monkeypatch):
    """不传 ``--manage-sidecar``：完全不进入托管路径（现状不变）。"""
    import sidecar_manager

    def fail(*a, **k):
        raise AssertionError("should not manage sidecar")

    monkeypatch.setattr(sidecar_manager, "ensure_sidecar", fail)
    monkeypatch.setattr(sidecar_manager, "stop_sidecar", fail)
    assert start_api.main(["--force"]) == 0
    assert len(run_calls) == 1


def test_manage_sidecar_skips_when_agentic_disabled(run_calls, monkeypatch, capsys, inject_settings):
    """agentic 关闭：只打提示不报错，uvicorn 照常启动。"""
    import sidecar_manager

    inject_settings(enabled=False)

    def fail(*a, **k):
        raise AssertionError("should not manage sidecar")

    monkeypatch.setattr(sidecar_manager, "ensure_sidecar", fail)
    assert start_api.main(["--force", "--manage-sidecar"]) == 0
    assert len(run_calls) == 1
    assert "agentic_search.enabled=false" in capsys.readouterr().err


def test_manage_sidecar_ensures_before_uvicorn_and_stops_after(monkeypatch, inject_settings):
    """编排顺序：探活/拉起 → uvicorn → 退出回收（先于 uvicorn 启动、后于其退出）。"""
    import sidecar_manager

    inject_settings(enabled=True)
    order: list[str] = []
    proc = FakeProc()
    monkeypatch.setattr(sidecar_manager, "ensure_sidecar", lambda s: (order.append("ensure"), proc)[1])
    monkeypatch.setattr(sidecar_manager, "stop_sidecar", lambda p: order.append(f"stop:{p is proc}"))
    monkeypatch.setattr(uvicorn, "run", lambda app, **k: order.append("uvicorn"))
    monkeypatch.delenv("RAG_API_TOKEN", raising=False)

    assert start_api.main(["--force", "--manage-sidecar"]) == 0
    assert order == ["ensure", "uvicorn", "stop:True"]


def test_manage_sidecar_failure_is_not_fatal(run_calls, monkeypatch, inject_settings):
    """拉起失败/超时（返回 None）不致命：API 照常启动，回收收到 None 为无操作。"""
    import sidecar_manager

    inject_settings(enabled=True)
    stops: list[object] = []
    monkeypatch.setattr(sidecar_manager, "ensure_sidecar", lambda s: None)
    monkeypatch.setattr(sidecar_manager, "stop_sidecar", lambda p: stops.append(p))

    assert start_api.main(["--force", "--manage-sidecar"]) == 0
    assert len(run_calls) == 1
    assert stops == [None]
