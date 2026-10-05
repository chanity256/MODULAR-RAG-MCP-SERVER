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
