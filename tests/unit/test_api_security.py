"""API 鉴权纯函数与 ``api:`` 配置节单测（WC2.1）。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml

from api import deps
from api.security import (
    extract_bearer,
    is_loopback_host,
    normalize_token,
    token_matches,
    validate_api_binding,
    verify_ws_token,
)
from core.settings import ApiSettings, ConfigError, load_settings

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"


class TestNormalizeToken:
    @pytest.mark.parametrize("raw", [None, "", "   ", "${RAG_API_TOKEN}", " ${X} ", "${A_B_1}"])
    def test_unset_values(self, raw):
        assert normalize_token(raw) is None

    def test_real_token_is_stripped(self):
        assert normalize_token("  s3cret ") == "s3cret"

    def test_dollar_in_middle_is_a_real_token(self):
        assert normalize_token("pa${ss}word") == "pa${ss}word"


class TestLoopback:
    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "LOCALHOST", "::1", "[::1]", "127.0.0.5"])
    def test_loopback(self, host):
        assert is_loopback_host(host)

    @pytest.mark.parametrize("host", ["0.0.0.0", "::", "", "192.168.1.5", "example.com", "10.0.0.1"])
    def test_not_loopback(self, host):
        assert not is_loopback_host(host)


class TestValidateBinding:
    def test_loopback_without_token_is_allowed(self):
        assert validate_api_binding("127.0.0.1", None) is None

    def test_non_loopback_without_token_fails_fast(self):
        with pytest.raises(ConfigError, match="RAG_API_TOKEN"):
            validate_api_binding("0.0.0.0", None)

    def test_non_loopback_with_unresolved_placeholder_fails_fast(self):
        with pytest.raises(ConfigError):
            validate_api_binding("0.0.0.0", "${RAG_API_TOKEN}")

    def test_non_loopback_with_token_returns_it(self):
        assert validate_api_binding("0.0.0.0", "tok") == "tok"


class TestTokenCompare:
    def test_match_and_mismatch(self):
        assert token_matches("abc", "abc")
        assert not token_matches("abc", "abd")
        assert not token_matches("abc", None)
        assert not token_matches("abc", "")

    def test_non_ascii_does_not_raise(self):
        assert not token_matches("abc", "tökén")
        assert token_matches("tökén", "tökén")

    def test_extract_bearer(self):
        assert extract_bearer("Bearer abc") == "abc"
        assert extract_bearer("bearer abc") == "abc"
        assert extract_bearer("Basic abc") is None
        assert extract_bearer("Bearer ") is None
        assert extract_bearer(None) is None


class TestWsToken:
    def test_no_token_configured_allows(self):
        assert verify_ws_token(None, query_params={}, headers={})

    def test_query_param(self):
        assert verify_ws_token("t", query_params={"token": "t"}, headers={})
        assert not verify_ws_token("t", query_params={"token": "x"}, headers={})

    def test_header(self):
        assert verify_ws_token("t", query_params={}, headers={"authorization": "Bearer t"})
        assert verify_ws_token("t", headers={"Authorization": "Bearer t"})
        assert not verify_ws_token("t", headers={"authorization": "Bearer x"})

    def test_missing_credentials(self):
        assert not verify_ws_token("t", query_params={}, headers={})
        assert not verify_ws_token("t")


class TestSidecarProbe:
    def test_any_http_response_counts_as_alive(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(401))
        assert deps.probe_sidecar("http://127.0.0.1:8584")

    def test_connection_error_is_not_alive(self, monkeypatch):
        def boom(*a, **k):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(httpx, "get", boom)
        assert not deps.probe_sidecar("http://127.0.0.1:8584")

    def test_url_has_status_path(self, monkeypatch):
        seen = {}

        def fake_get(url, **kwargs):
            seen["url"] = url
            return httpx.Response(200)

        monkeypatch.setattr(httpx, "get", fake_get)
        deps.probe_sidecar("http://h:1/")
        assert seen["url"] == "http://h:1/api/v1/search/status"


def _write_config(tmp_path, mutate) -> Path:
    data = yaml.safe_load(_REPO_CONFIG.read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


class TestApiSettingsSection:
    def test_repo_config_default_is_disabled_loopback(self, monkeypatch):
        monkeypatch.delenv("RAG_API_TOKEN", raising=False)
        api = load_settings(_REPO_CONFIG).api
        assert api.enabled is False
        assert api.host == "127.0.0.1"
        assert api.port == 8600
        assert api.allowed_origins == ["http://localhost:3000"]
        assert api.serve_ui is False
        # 环境变量缺失时占位符被原样保留，由 normalize_token 视为未配置
        assert api.api_token == "${RAG_API_TOKEN}"
        assert normalize_token(api.api_token) is None

    def test_token_resolved_from_env(self, monkeypatch):
        monkeypatch.setenv("RAG_API_TOKEN", "from-env")
        assert load_settings(_REPO_CONFIG).api.api_token == "from-env"

    def test_missing_section_uses_defaults(self, tmp_path):
        path = _write_config(tmp_path, lambda d: d.pop("api", None))
        assert load_settings(path).api == ApiSettings()

    def test_partial_section_merges_defaults(self, tmp_path):
        path = _write_config(tmp_path, lambda d: d.__setitem__("api", {"port": 9000}))
        api = load_settings(path).api
        assert api.port == 9000
        assert api.host == "127.0.0.1"

    @pytest.mark.parametrize(
        "patch",
        [
            {"enabled": "yes"},
            {"port": 0},
            {"port": 70000},
            {"port": "8600"},
            {"port": True},
            {"host": ""},
            {"allowed_origins": "http://localhost:3000"},
            {"allowed_origins": ["ok", ""]},
            {"serve_ui": 1},
            {"static_dir": ""},
            {"api_token": 123},
        ],
    )
    def test_invalid_values_fail_fast(self, tmp_path, patch):
        path = _write_config(tmp_path, lambda d: d.__setitem__("api", patch))
        with pytest.raises(ConfigError, match="api"):
            load_settings(path)

    def test_section_must_be_mapping(self, tmp_path):
        path = _write_config(tmp_path, lambda d: d.__setitem__("api", ["x"]))
        with pytest.raises(ConfigError, match="api"):
            load_settings(path)
