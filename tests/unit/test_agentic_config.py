"""S1.1: AgenticSearchConfig 配置解析单元测试（离线）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.settings import ConfigError, load_settings

_MINIMAL_YAML = """\
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
"""


def _write(tmp_path: Path, agentic_section: str) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(_MINIMAL_YAML + agentic_section, encoding="utf-8")
    return path


class TestAgenticSearchConfig:
    def test_section_missing_defaults_disabled(self, tmp_path: Path):
        settings = load_settings(_write(tmp_path, ""))
        config = settings.agentic_search
        assert config.enabled is False
        assert config.backend == "sirchmunk_http"
        assert config.base_url == "http://127.0.0.1:8584"
        assert config.mode == "FAST"
        assert config.allow_deep is False
        assert config.timeout_s == 30.0
        assert config.top_k_files == 5
        assert config.max_loops == 6
        assert config.max_token_budget == 32000
        assert config.denied_substrings == [".env", ".git", "data/db"]

    @pytest.mark.parametrize(
        "section,fragment",
        [
            ("agentic_search:\n  enabled: true\n  mode: TURBO\n", "mode"),
            ("agentic_search:\n  enabled: true\n  base_url: ftp://x\n", "base_url"),
            ("agentic_search:\n  enabled: true\n  timeout_s: 0\n", "timeout_s"),
            ("agentic_search:\n  enabled: true\n  timeout_s: -1\n", "timeout_s"),
            ("agentic_search:\n  enabled: true\n  top_k_files: abc\n", "top_k_files"),
            ("agentic_search:\n  enabled: true\n  max_loops: 0\n", "max_loops"),
            ("agentic_search:\n  enabled: true\n  max_token_budget: 0\n", "max_token_budget"),
            ("agentic_search:\n  enabled: true\n  backend: ''\n", "backend"),
        ],
    )
    def test_invalid_values_fail_fast(self, tmp_path: Path, section: str, fragment: str):
        with pytest.raises(ConfigError, match=fragment):
            load_settings(_write(tmp_path, section))

    def test_enabled_requires_allowed_roots(self, tmp_path: Path):
        yaml = "agentic_search:\n  enabled: true\n"
        with pytest.raises(ConfigError, match="allowed_roots"):
            load_settings(_write(tmp_path, yaml))

    def test_enabled_with_allowed_roots_loads(self, tmp_path: Path):
        root = tmp_path / "docs"
        root.mkdir()
        yaml = (
            "agentic_search:\n"
            "  enabled: true\n"
            f"  allowed_roots:\n    - {root}\n"
        )
        config = load_settings(_write(tmp_path, yaml)).agentic_search
        assert config.enabled is True
        assert config.allowed_roots == [str(root)]

    def test_mode_lowercased_normalized_to_upper(self, tmp_path: Path):
        yaml = "agentic_search:\n  mode: deep\n"
        assert load_settings(_write(tmp_path, yaml)).agentic_search.mode == "DEEP"

    def test_env_ref_resolved_for_token(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SIRCHMUNK_API_TOKEN", "tok-123")
        yaml = "agentic_search:\n  api_token: ${SIRCHMUNK_API_TOKEN}\n"
        assert load_settings(_write(tmp_path, yaml)).agentic_search.api_token == "tok-123"

    def test_denied_substrings_override(self, tmp_path: Path):
        yaml = "agentic_search:\n  denied_substrings: ['.secret']\n"
        assert load_settings(_write(tmp_path, yaml)).agentic_search.denied_substrings == [".secret"]

    def test_extra_keys_preserved(self, tmp_path: Path):
        yaml = "agentic_search:\n  custom_future_field: 1\n"
        assert load_settings(_write(tmp_path, yaml)).agentic_search.extra == {"custom_future_field": 1}

    def test_section_must_be_mapping(self, tmp_path: Path):
        path = tmp_path / "settings.yaml"
        path.write_text(_MINIMAL_YAML + "agentic_search: [1]\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="agentic_search"):
            load_settings(path)


class TestRequestPassthroughConfig:
    """SX2：max_depth / enable_dir_scan 可选键（缺失走默认、非法 fail-fast）。"""

    def test_defaults_when_missing(self, tmp_path: Path):
        config = load_settings(_write(tmp_path, "")).agentic_search
        assert config.max_depth is None
        assert config.enable_dir_scan is True
        assert "max_depth" not in config.extra and "enable_dir_scan" not in config.extra

    def test_explicit_values_loaded(self, tmp_path: Path):
        yaml = "agentic_search:\n  max_depth: 4\n  enable_dir_scan: false\n"
        config = load_settings(_write(tmp_path, yaml)).agentic_search
        assert config.max_depth == 4
        assert config.enable_dir_scan is False

    def test_null_max_depth_allowed(self, tmp_path: Path):
        yaml = "agentic_search:\n  max_depth: null\n"
        assert load_settings(_write(tmp_path, yaml)).agentic_search.max_depth is None

    def test_null_enable_dir_scan_falls_back_to_default(self, tmp_path: Path):
        """与其他可选键一致：显式 null 视同缺失（_opt 语义）。"""
        yaml = "agentic_search:\n  enable_dir_scan: null\n"
        assert load_settings(_write(tmp_path, yaml)).agentic_search.enable_dir_scan is True

    @pytest.mark.parametrize(
        "section,fragment",
        [
            ("agentic_search:\n  max_depth: 0\n", "max_depth"),
            ("agentic_search:\n  max_depth: -2\n", "max_depth"),
            ("agentic_search:\n  max_depth: '3'\n", "max_depth"),
            ("agentic_search:\n  max_depth: true\n", "max_depth"),
            ("agentic_search:\n  max_depth: 2.5\n", "max_depth"),
            ("agentic_search:\n  enable_dir_scan: 'yes'\n", "enable_dir_scan"),
            ("agentic_search:\n  enable_dir_scan: 1\n", "enable_dir_scan"),
        ],
    )
    def test_invalid_values_fail_fast(self, tmp_path: Path, section: str, fragment: str):
        with pytest.raises(ConfigError, match=fragment):
            load_settings(_write(tmp_path, section))

    def test_repo_settings_yaml_loads(self):
        """仓库 config/settings.yaml 中新键可解析（防模板与校验漂移）。"""
        repo_yaml = Path(__file__).resolve().parents[2] / "config/settings.yaml"
        import yaml as _yaml

        section = _yaml.safe_load(repo_yaml.read_text(encoding="utf-8"))["agentic_search"]
        assert section["max_depth"] is None
        assert section["enable_dir_scan"] is True
