"""PI3: TreeSearchConfig 配置解析与校验单测（离线，PAGEINDEX_TREE_RETRIEVAL_SPEC §3.3）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.settings import ConfigError, load_settings, validate_settings

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


def _write(tmp_path: Path, tree_section: str) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(_MINIMAL_YAML + tree_section, encoding="utf-8")
    return path


class TestDefaults:
    def test_section_missing_defaults_disabled(self, tmp_path: Path):
        config = load_settings(_write(tmp_path, "")).tree_search
        assert config.enabled is False
        assert config.backend == "pageindex_http"
        assert config.base_url == "http://127.0.0.1:8585"
        assert config.api_token is None
        assert config.timeout_s == 240.0
        assert config.index_timeout_s == 1800.0
        assert config.max_nodes == 5
        assert config.max_loops == 3
        assert config.max_token_budget == 32000
        assert config.allowed_roots == []
        assert config.denied_substrings == [".env", ".git", "data/db"]

    def test_repo_default_config_disables_tree_search(self):
        """仓库默认配置：tree_search.enabled=false 且默认值与 3.3 一致。"""
        repo_root = Path(__file__).resolve().parents[2]
        config = load_settings(repo_root / "config" / "settings.yaml").tree_search
        assert config.enabled is False
        assert config.backend == "pageindex_http"
        assert config.base_url == "http://127.0.0.1:8585"
        assert config.timeout_s == 240.0
        assert config.index_timeout_s == 1800.0
        assert config.max_nodes == 5
        assert config.max_loops == 3
        assert config.max_token_budget == 32000

    def test_section_non_mapping_fails(self, tmp_path: Path):
        path = tmp_path / "settings.yaml"
        path.write_text(_MINIMAL_YAML + "tree_search: [1]\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="tree_search"):
            load_settings(path)

    def test_extra_fields_preserved(self, tmp_path: Path):
        config = load_settings(_write(tmp_path, "tree_search:\n  custom_flag: true\n")).tree_search
        assert config.extra == {"custom_flag": True}


class TestFieldValidation:
    @pytest.mark.parametrize(
        "section,fragment",
        [
            ("tree_search:\n  enabled: yes-please\n", "enabled"),
            ("tree_search:\n  backend: sirchmunk_http\n", "backend"),
            ("tree_search:\n  backend: ''\n", "backend"),
            ("tree_search:\n  base_url: ftp://x\n", "base_url"),
            ("tree_search:\n  timeout_s: 0\n", "timeout_s"),
            ("tree_search:\n  timeout_s: -1\n", "timeout_s"),
            ("tree_search:\n  index_timeout_s: 0\n", "index_timeout_s"),
            ("tree_search:\n  max_nodes: 0\n", "max_nodes"),
            ("tree_search:\n  max_nodes: abc\n", "max_nodes"),
            ("tree_search:\n  max_loops: 0\n", "max_loops"),
            ("tree_search:\n  max_token_budget: 0\n", "max_token_budget"),
            ("tree_search:\n  allowed_roots: not-a-list\n", "allowed_roots"),
            ("tree_search:\n  allowed_roots: ['']\n", "allowed_roots"),
            ("tree_search:\n  denied_substrings: 'x'\n", "denied_substrings"),
        ],
    )
    def test_invalid_values_fail_fast(self, tmp_path: Path, section: str, fragment: str):
        with pytest.raises(ConfigError, match=fragment):
            load_settings(_write(tmp_path, section))


class TestEnabledConstraints:
    def test_enabled_requires_allowed_roots(self, tmp_path: Path):
        yaml = "tree_search:\n  enabled: true\n"
        with pytest.raises(ConfigError, match="allowed_roots"):
            load_settings(_write(tmp_path, yaml))

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://127.0.0.1.evil.com:8585",
            "https://127.0.0.1:8585",
            "http://0.0.0.0:8585",
            "http://10.0.0.5:8585",
        ],
    )
    def test_enabled_rejects_non_loopback_or_https_base_url(self, tmp_path: Path, base_url: str):
        yaml = (
            "tree_search:\n"
            "  enabled: true\n"
            "  api_token: tok\n"
            f"  base_url: {base_url}\n"
            "  allowed_roots:\n    - ./data/documents\n"
        )
        with pytest.raises(ConfigError, match="base_url"):
            load_settings(_write(tmp_path, yaml))

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://127.0.0.1:8585",
            "http://localhost:8585",
            "http://[::1]:8585",
        ],
    )
    def test_enabled_accepts_loopback_http(self, tmp_path: Path, base_url: str):
        yaml = (
            "tree_search:\n"
            "  enabled: true\n"
            "  api_token: tok\n"
            f"  base_url: {base_url}\n"
            "  allowed_roots:\n    - ./data/documents\n"
        )
        config = load_settings(_write(tmp_path, yaml)).tree_search
        assert config.base_url == base_url

    def test_enabled_requires_api_token(self, tmp_path: Path):
        yaml = (
            "tree_search:\n"
            "  enabled: true\n"
            "  allowed_roots:\n    - ./data/documents\n"
        )
        with pytest.raises(ConfigError, match="api_token"):
            load_settings(_write(tmp_path, yaml))

    def test_enabled_rejects_unresolved_placeholder_token(self, tmp_path: Path):
        yaml = (
            "tree_search:\n"
            "  enabled: true\n"
            "  api_token: ${PAGEINDEX_API_TOKEN_MISSING}\n"
            "  allowed_roots:\n    - ./data/documents\n"
        )
        with pytest.raises(ConfigError, match="api_token"):
            load_settings(_write(tmp_path, yaml))

    def test_disabled_tolerates_missing_token_and_non_loopback_url(self, tmp_path: Path):
        yaml = (
            "tree_search:\n"
            "  enabled: false\n"
            "  base_url: http://10.0.0.5:8585\n"
        )
        config = load_settings(_write(tmp_path, yaml)).tree_search
        assert config.enabled is False
        assert config.api_token is None


class TestAllowedRootsAnchoring:
    def test_relative_roots_anchored_to_project_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """相对 allowed_roots 按项目根锚定为绝对路径（ROOT_ANCHOR_SPEC RA2）。"""
        monkeypatch.setenv("RAG_PROJECT_ROOT", str(tmp_path))
        yaml = (
            "tree_search:\n"
            "  enabled: true\n"
            "  api_token: tok\n"
            "  allowed_roots:\n"
            "    - ./data/documents\n"
            "    - ./data/uploads\n"
        )
        config = load_settings(_write(tmp_path, yaml)).tree_search
        assert config.allowed_roots == [
            str(tmp_path / "data" / "documents"),
            str(tmp_path / "data" / "uploads"),
        ]

    def test_absolute_and_tilde_roots_preserved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("RAG_PROJECT_ROOT", str(tmp_path))
        home_relative = "~/corpus"
        yaml = (
            "tree_search:\n"
            "  enabled: true\n"
            "  api_token: tok\n"
            f"  allowed_roots:\n    - {home_relative}\n"
        )
        config = load_settings(_write(tmp_path, yaml)).tree_search
        assert config.allowed_roots == [str(Path(home_relative).expanduser())]


class TestCrossSectionValidation:
    def _settings_with_default_retrieval(self, tmp_path: Path, enabled: bool):
        settings = load_settings(_write(tmp_path, ""))
        settings.chat.default_retrieval = "longdoc"  # PI5 接入前绕过 _build_chat 直测跨节校验
        settings.tree_search.enabled = enabled
        return settings

    def test_longdoc_default_requires_enabled_tree_search(self, tmp_path: Path):
        settings = self._settings_with_default_retrieval(tmp_path, enabled=False)
        with pytest.raises(ConfigError, match="longdoc"):
            validate_settings(settings)

    def test_longdoc_default_with_enabled_tree_search_passes(self, tmp_path: Path):
        settings = self._settings_with_default_retrieval(tmp_path, enabled=True)
        validate_settings(settings)  # 不抛即通过

    def test_hybrid_default_unaffected(self, tmp_path: Path):
        settings = load_settings(_write(tmp_path, ""))
        validate_settings(settings)
