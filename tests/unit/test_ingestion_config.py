"""IR1.4: ingestion.retry / ingestion.version_policy 配置解析与版本推导（离线）。
IR7.4: ingestion.source_filters 配置解析与校验（离线）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.settings import (
    ConfigError,
    IngestionRetryConfig,
    IngestionSourceFiltersConfig,
    IngestionVersionPolicyConfig,
    load_settings,
    resolve_ingestion_versions,
)

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


def _write(tmp_path: Path, extra: str) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(_MINIMAL_YAML + extra, encoding="utf-8")
    return path


def test_sections_missing_use_defaults(tmp_path: Path):
    ingestion = load_settings(_write(tmp_path, "")).ingestion
    assert ingestion.retry == IngestionRetryConfig()
    assert ingestion.retry.max_attempts == 1
    assert ingestion.retry.initial_backoff_s == 2.0
    assert ingestion.retry.backoff_multiplier == 2.0
    assert ingestion.retry.backoff_jitter == 0.2
    assert ingestion.version_policy == IngestionVersionPolicyConfig()
    assert ingestion.version_policy.on_embedding_mismatch == "stale"
    assert ingestion.extra == {}


def test_partial_sections_fill_defaults(tmp_path: Path):
    yaml_text = (
        "ingestion:\n"
        "  retry:\n    max_attempts: 3\n"
        "  version_policy:\n    embedding_version: emb-v7\n    on_embedding_mismatch: rebuild\n"
    )
    ingestion = load_settings(_write(tmp_path, yaml_text)).ingestion
    assert ingestion.retry.max_attempts == 3
    assert ingestion.retry.initial_backoff_s == 2.0
    assert ingestion.version_policy.embedding_version == "emb-v7"
    assert ingestion.version_policy.parser_version == "auto"
    assert ingestion.version_policy.on_embedding_mismatch == "rebuild"
    assert "retry" not in ingestion.extra and "version_policy" not in ingestion.extra


def test_unknown_keys_go_to_extra(tmp_path: Path):
    yaml_text = "ingestion:\n  retry:\n    foo: 1\n  version_policy:\n    bar: x\n"
    ingestion = load_settings(_write(tmp_path, yaml_text)).ingestion
    assert ingestion.retry.extra == {"foo": 1}
    assert ingestion.version_policy.extra == {"bar": "x"}


@pytest.mark.parametrize(
    "section,fragment",
    [
        ("ingestion:\n  retry: 3\n", "ingestion.retry"),
        ("ingestion:\n  retry:\n    max_attempts: 0\n", "max_attempts"),
        ("ingestion:\n  retry:\n    max_attempts: 1.5\n", "max_attempts"),
        ("ingestion:\n  retry:\n    max_attempts: true\n", "max_attempts"),
        ("ingestion:\n  retry:\n    initial_backoff_s: -1\n", "initial_backoff_s"),
        ("ingestion:\n  retry:\n    backoff_multiplier: 1\n", "backoff_multiplier"),
        ("ingestion:\n  retry:\n    backoff_multiplier: 0.5\n", "backoff_multiplier"),
        ("ingestion:\n  retry:\n    backoff_jitter: 1.0\n", "backoff_jitter"),
        ("ingestion:\n  retry:\n    backoff_jitter: -0.1\n", "backoff_jitter"),
        ("ingestion:\n  version_policy: [a]\n", "ingestion.version_policy"),
        ("ingestion:\n  version_policy:\n    on_embedding_mismatch: auto\n", "on_embedding_mismatch"),
        ("ingestion:\n  version_policy:\n    parser_version: ''\n", "parser_version"),
        ("ingestion:\n  version_policy:\n    chunk_version: 3\n", "chunk_version"),
    ],
)
def test_invalid_values_fail_fast(tmp_path: Path, section: str, fragment: str):
    with pytest.raises(ConfigError, match=fragment):
        load_settings(_write(tmp_path, section))


def test_resolve_versions_auto(tmp_path: Path):
    yaml_text = (
        "embedding:\n  provider: openai\n  model: text-embedding-3-small\n  dimensions: 1536\n"
        "splitter:\n  chunk_size: 800\n  chunk_overlap: 100\n"
    )
    base = _MINIMAL_YAML.replace(
        "embedding:\n  provider: openai\n  model: text-embedding-3-small\n", ""
    )
    path = tmp_path / "settings.yaml"
    path.write_text(base + yaml_text, encoding="utf-8")
    settings = load_settings(path)
    assert resolve_ingestion_versions(settings, loader_name="PdfLoader") == {
        "parser_version": "PdfLoader",
        "chunk_version": "recursive/800/100/refine=rule",
        "embedding_version": "openai/text-embedding-3-small/1536",
    }
    assert resolve_ingestion_versions(settings)["parser_version"] == "unknown"


def test_resolve_versions_explicit_override(tmp_path: Path):
    yaml_text = (
        "ingestion:\n  version_policy:\n"
        "    parser_version: p1\n    chunk_version: c1\n    embedding_version: e1\n"
    )
    settings = load_settings(_write(tmp_path, yaml_text))
    assert resolve_ingestion_versions(settings, loader_name="PdfLoader") == {
        "parser_version": "p1",
        "chunk_version": "c1",
        "embedding_version": "e1",
    }
    defaults = load_settings(_write(tmp_path, ""))
    assert resolve_ingestion_versions(defaults)["embedding_version"] == (
        "openai/text-embedding-3-small/default"
    )


def test_repo_settings_yaml_still_loads_with_defaults():
    repo_root = Path(__file__).resolve().parents[2]
    ingestion = load_settings(repo_root / "config" / "settings.yaml").ingestion
    assert ingestion.retry.max_attempts == 1
    assert ingestion.version_policy.on_embedding_mismatch == "stale"


_DEFAULT_EXCLUDE = [".obsidian/**", ".trash/**", "templates/**"]


def test_source_filters_section_missing_uses_defaults(tmp_path: Path):
    ingestion = load_settings(_write(tmp_path, "")).ingestion
    assert ingestion.source_filters == IngestionSourceFiltersConfig()
    assert ingestion.source_filters.include_patterns == []
    assert ingestion.source_filters.exclude_patterns == _DEFAULT_EXCLUDE


def test_source_filters_partial_section_fills_defaults(tmp_path: Path):
    yaml_text = (
        "ingestion:\n"
        "  source_filters:\n    include_patterns: ['notes/**']\n"
    )
    source_filters = load_settings(_write(tmp_path, yaml_text)).ingestion.source_filters
    assert source_filters.include_patterns == ["notes/**"]
    assert source_filters.exclude_patterns == _DEFAULT_EXCLUDE

    yaml_text = (
        "ingestion:\n"
        "  source_filters:\n    exclude_patterns: ['.git/**']\n"
    )
    source_filters = load_settings(_write(tmp_path, yaml_text)).ingestion.source_filters
    assert source_filters.include_patterns == []
    assert source_filters.exclude_patterns == [".git/**"]
    assert source_filters.extra == {}


def test_source_filters_custom_values(tmp_path: Path):
    yaml_text = (
        "ingestion:\n"
        "  source_filters:\n"
        "    include_patterns: ['vault/**', 'notes/**/*.md']\n"
        "    exclude_patterns: ['.git/**', 'archive/**']\n"
    )
    source_filters = load_settings(_write(tmp_path, yaml_text)).ingestion.source_filters
    assert source_filters.include_patterns == ["vault/**", "notes/**/*.md"]
    assert source_filters.exclude_patterns == [".git/**", "archive/**"]


def test_source_filters_unknown_keys_go_to_extra(tmp_path: Path):
    yaml_text = "ingestion:\n  source_filters:\n    foo: 1\n"
    source_filters = load_settings(_write(tmp_path, yaml_text)).ingestion.source_filters
    assert source_filters.extra == {"foo": 1}


@pytest.mark.parametrize(
    "section,fragment",
    [
        ("ingestion:\n  source_filters: 3\n", "ingestion.source_filters"),
        ("ingestion:\n  source_filters:\n    include_patterns: 'a/**'\n", "include_patterns"),
        ("ingestion:\n  source_filters:\n    exclude_patterns: 'b/**'\n", "exclude_patterns"),
        ("ingestion:\n  source_filters:\n    include_patterns: [1]\n", "include_patterns"),
        ("ingestion:\n  source_filters:\n    exclude_patterns: [true]\n", "exclude_patterns"),
        ("ingestion:\n  source_filters:\n    include_patterns: ['']\n", "include_patterns"),
        ("ingestion:\n  source_filters:\n    exclude_patterns: ['']\n", "exclude_patterns"),
    ],
)
def test_source_filters_invalid_values_fail_fast(tmp_path: Path, section: str, fragment: str):
    with pytest.raises(ConfigError, match=fragment):
        load_settings(_write(tmp_path, section))


def test_source_filters_default_exclude_semantics_locked():
    assert IngestionSourceFiltersConfig().exclude_patterns == _DEFAULT_EXCLUDE
