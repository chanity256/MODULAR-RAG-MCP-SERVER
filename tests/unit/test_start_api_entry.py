"""``scripts/start_api.py`` 入口单测：不真正启动 uvicorn。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import uvicorn

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "start_api.py"
_spec = importlib.util.spec_from_file_location("start_api_script", _SCRIPT)
start_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(start_api)


@pytest.fixture
def run_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    monkeypatch.delenv("RAG_API_TOKEN", raising=False)
    return calls


def test_disabled_by_default_refuses_to_start(run_calls, capsys):
    assert start_api.main([]) == 2
    assert run_calls == []
    assert "api.enabled is false" in capsys.readouterr().err


def test_force_starts_on_default_loopback(run_calls):
    assert start_api.main(["--force"]) == 0
    assert run_calls == [{"host": "127.0.0.1", "port": 8600, "log_level": "info"}]


def test_cli_overrides_host_and_port(run_calls, monkeypatch):
    monkeypatch.setenv("RAG_API_TOKEN", "t")
    assert start_api.main(["--force", "--host", "0.0.0.0", "--port", "9001"]) == 0
    assert run_calls[0]["host"] == "0.0.0.0" and run_calls[0]["port"] == 9001


def test_non_loopback_without_token_fails_fast(run_calls, capsys):
    assert start_api.main(["--force", "--host", "0.0.0.0"]) == 2
    assert run_calls == []
    assert "RAG_API_TOKEN" in capsys.readouterr().err


def test_missing_config_is_a_clean_error(run_calls, tmp_path, capsys):
    assert start_api.main(["--force", "--config", str(tmp_path / "nope.yaml")]) == 2
    assert run_calls == []
    assert "configuration error" in capsys.readouterr().err
