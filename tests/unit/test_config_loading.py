"""``src/core/settings.py`` 的单元测试（阶段 A3）。

覆盖：
- 成功加载 ``config/settings.yaml`` 并得到 ``Settings`` 对象。
- 缺失关键字段时 ``load_settings()`` 抛出可读错误（明确指出缺的是哪个字段）。
- ``validate_settings`` 的独立调用。
- 环境变量引用 ``${ENV_VAR}`` 解析。
- ``main.py`` fail-fast 行为。
"""

from __future__ import annotations

import os
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import ConfigError, load_settings, validate_settings

# 供测试使用的最小合法配置（含所有必填字段）。
_MINIMAL_VALID_YAML = dedent(
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
      persist_path: ./data/db/chroma
    retrieval:
      sparse_backend: bm25
      fusion_algorithm: rrf
      top_k_dense: 20
      top_k_sparse: 20
      top_k_final: 10
    rerank:
      backend: cross_encoder
      model: cross-encoder/ms-marco-MiniLM-L-6-v2
      top_m: 30
    evaluation:
      backends: [ragas, custom]
      golden_test_set: ./tests/fixtures/golden_test_set.json
    observability:
      enabled: true
      log_file: ./logs/traces.jsonl
      logging:
        log_level: INFO
      detail_level: standard
    dashboard:
      enabled: true
      port: 8501
      traces_dir: ./logs
      auto_refresh: true
      refresh_interval: 5
    """
)


def _write(tmp_path: Path, content: str, name: str = "settings.yaml") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 成功加载
# ---------------------------------------------------------------------------


def test_load_settings_from_repo_config() -> None:
    """能成功加载仓库内的 config/settings.yaml 并拿到 Settings 对象。"""
    repo_config = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
    settings = load_settings(repo_config)
    assert settings.llm.provider == "azure"
    assert settings.embedding.model == "text-embedding-3-small"
    assert settings.vector_store.backend == "chroma"
    assert settings.retrieval.fusion_algorithm == "rrf"
    assert settings.rerank.backend == "cross_encoder"
    assert settings.evaluation.backends == ["ragas", "custom"]


def test_load_settings_minimal_valid(tmp_path: Path) -> None:
    path = _write(tmp_path, _MINIMAL_VALID_YAML)
    settings = load_settings(path)
    assert settings.llm.provider == "azure"
    assert settings.embedding.provider == "openai"
    assert settings.retrieval.top_k_dense == 20


def test_validate_settings_passes_on_valid(tmp_path: Path) -> None:
    path = _write(tmp_path, _MINIMAL_VALID_YAML)
    settings = load_settings(path)
    # 不抛异常即通过。
    validate_settings(settings)


# ---------------------------------------------------------------------------
# 缺失关键字段 → 可读错误
# ---------------------------------------------------------------------------


def test_missing_embedding_provider_raises_with_path(tmp_path: Path) -> None:
    bad = dedent(
        """\
        llm:
          provider: azure
          model: gpt-4o
        embedding:
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
    )
    path = _write(tmp_path, bad)
    with pytest.raises(ConfigError) as exc_info:
        load_settings(path)
    assert "embedding.provider" in str(exc_info.value)


def test_missing_section_raises(tmp_path: Path) -> None:
    bad = dedent(
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
        # evaluation 节缺失
        """
    )
    path = _write(tmp_path, bad)
    with pytest.raises(ConfigError) as exc_info:
        load_settings(path)
    assert "evaluation" in str(exc_info.value)


def test_empty_required_field_raises(tmp_path: Path) -> None:
    bad = _MINIMAL_VALID_YAML.replace("provider: openai", "provider: \"\"")
    path = _write(tmp_path, bad)
    with pytest.raises(ConfigError) as exc_info:
        load_settings(path)
    assert "embedding.provider" in str(exc_info.value)


def test_invalid_evaluation_backends_raises(tmp_path: Path) -> None:
    bad = _MINIMAL_VALID_YAML.replace("backends: [ragas, custom]", "backends: []")
    path = _write(tmp_path, bad)
    with pytest.raises(ConfigError) as exc_info:
        load_settings(path)
    assert "evaluation.backends" in str(exc_info.value)


def test_missing_config_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_settings(tmp_path / "nonexistent.yaml")
    assert "not found" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 环境变量引用解析
# ---------------------------------------------------------------------------


def test_env_var_reference_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_OPENAI_KEY", "sk-test-12345")
    bad = _MINIMAL_VALID_YAML.replace(
        "model: text-embedding-3-small",
        "model: text-embedding-3-small\n  api_key: ${TEST_OPENAI_KEY}",
    )
    path = _write(tmp_path, bad)
    settings = load_settings(path)
    assert settings.embedding.api_key == "sk-test-12345"


def test_env_var_missing_keeps_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_MISSING_KEY", raising=False)
    bad = _MINIMAL_VALID_YAML.replace(
        "model: text-embedding-3-small",
        "model: text-embedding-3-small\n  api_key: ${TEST_MISSING_KEY}",
    )
    path = _write(tmp_path, bad)
    settings = load_settings(path)
    assert settings.embedding.api_key == "${TEST_MISSING_KEY}"


# ---------------------------------------------------------------------------
# main.py fail-fast
# ---------------------------------------------------------------------------


def test_main_fail_fast_on_bad_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """main.py 在配置校验失败时返回非零退出码。"""
    bad = _MINIMAL_VALID_YAML.replace("provider: openai", "provider: \"\"")
    bad_config = _write(tmp_path, bad, name="settings.yaml")
    # 切换工作目录到临时目录，并复制一个 main.py 进去，使其读取该配置。
    main_src = Path(__file__).resolve().parents[2] / "main.py"
    tmp_main = tmp_path / "main.py"
    tmp_main.write_text(main_src.read_text(encoding="utf-8"), encoding="utf-8")

    import importlib.util

    spec = importlib.util.spec_from_file_location("main_under_test", tmp_main)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # 让 main.py 内的相对路径解析到我们的临时配置。
    monkeypatch.chdir(tmp_path)
    # main.py 中 config_path 基于 __file__ 的 parents[0]/config/settings.yaml，
    # 所以需要在 tmp_path 下建 config/settings.yaml。
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "settings.yaml").write_text(bad, encoding="utf-8")
    spec.loader.exec_module(module)
    assert module.main() == 1


def test_main_ok_on_valid_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    main_src = Path(__file__).resolve().parents[2] / "main.py"
    tmp_main = tmp_path / "main.py"
    tmp_main.write_text(main_src.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "settings.yaml").write_text(_MINIMAL_VALID_YAML, encoding="utf-8")

    import importlib.util

    spec = importlib.util.spec_from_file_location("main_ok", tmp_main)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.chdir(tmp_path)
    spec.loader.exec_module(module)
    assert module.main() == 0
