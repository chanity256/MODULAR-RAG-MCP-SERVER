"""WC6: ``chat:`` 配置节解析与校验（离线）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.settings import ChatSettings, ConfigError, load_settings

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


def _load(tmp_path: Path, chat_section: str = ""):
    path = tmp_path / "settings.yaml"
    path.write_text(_MINIMAL_YAML + chat_section, encoding="utf-8")
    return load_settings(path)


def test_missing_section_uses_defaults(tmp_path):
    chat = _load(tmp_path).chat
    assert chat == ChatSettings()
    assert chat.enabled is True
    assert chat.history_max_turns == 10
    assert chat.history_max_tokens == 32000
    assert chat.relevance_filter is True
    assert chat.query_rewrite is True
    assert chat.default_retrieval == "hybrid"
    assert chat.max_context_chunks == 8
    assert chat.session_db == "./data/db/chat_sessions.db"
    assert chat.max_connections == 100
    assert chat.pipeline_max_retries == 1
    assert chat.fallback_to_chat_only is False
    assert chat.agentic_progress_stream is False


def test_empty_section_uses_defaults(tmp_path):
    assert _load(tmp_path, "chat:\n").chat == ChatSettings()


def test_all_keys_override(tmp_path):
    section = (
        "chat:\n"
        "  enabled: false\n"
        "  history_max_turns: 3\n"
        "  history_max_tokens: 1000\n"
        "  relevance_filter: false\n"
        "  query_rewrite: false\n"
        "  default_retrieval: agentic_deep\n"
        "  max_context_chunks: 4\n"
        "  session_db: /tmp/x.db\n"
        "  max_connections: 5\n"
        "  pipeline_max_retries: 0\n"
        "  fallback_to_chat_only: true\n"
        "  agentic_progress_stream: true\n"
    )
    chat = _load(tmp_path, section).chat
    assert chat == ChatSettings(
        enabled=False,
        history_max_turns=3,
        history_max_tokens=1000,
        relevance_filter=False,
        query_rewrite=False,
        default_retrieval="agentic_deep",
        max_context_chunks=4,
        session_db="/tmp/x.db",
        max_connections=5,
        pipeline_max_retries=0,
        fallback_to_chat_only=True,
        agentic_progress_stream=True,
    )


@pytest.mark.parametrize("mode", ["hybrid", "agentic_fast", "agentic_deep"])
def test_valid_retrieval_modes(tmp_path, mode):
    assert _load(tmp_path, f"chat:\n  default_retrieval: {mode}\n").chat.default_retrieval == mode


@pytest.mark.parametrize(
    "body,fragment",
    [
        ("default_retrieval: turbo", "default_retrieval"),
        ("default_retrieval: HYBRID", "default_retrieval"),
        ("default_retrieval: 3", "default_retrieval"),
        ("enabled: yes_please", "enabled"),
        ("enabled: 1", "enabled"),
        ("relevance_filter: 'true'", "relevance_filter"),
        ("query_rewrite: 0", "query_rewrite"),
        ("fallback_to_chat_only: 'false'", "fallback_to_chat_only"),
        ("agentic_progress_stream: 1", "agentic_progress_stream"),
        ("history_max_turns: 0", "history_max_turns"),
        ("history_max_turns: true", "history_max_turns"),
        ("history_max_turns: 2.5", "history_max_turns"),
        ("history_max_tokens: -1", "history_max_tokens"),
        ("max_context_chunks: 0", "max_context_chunks"),
        ("max_connections: 0", "max_connections"),
        ("pipeline_max_retries: -1", "pipeline_max_retries"),
        ("pipeline_max_retries: true", "pipeline_max_retries"),
        ("session_db: ''", "session_db"),
        ("session_db: 5", "session_db"),
    ],
)
def test_invalid_values_fail_fast(tmp_path, body, fragment):
    with pytest.raises(ConfigError, match=fragment):
        _load(tmp_path, f"chat:\n  {body}\n")


def test_section_must_be_mapping(tmp_path):
    with pytest.raises(ConfigError, match="chat"):
        _load(tmp_path, "chat: [1, 2]\n")


def test_repo_settings_yaml_chat_block_parses():
    root = Path(__file__).resolve().parents[2]
    chat = load_settings(root / "config" / "settings.yaml").chat
    assert chat == ChatSettings()
