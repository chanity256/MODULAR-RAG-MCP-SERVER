"""MR1: ModeRouterSettings 配置解析 + RETRIEVAL_AUTO 常量与 ModeRouter 协议（离线）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.chat.retriever import (
    RETRIEVAL_AGENTIC_DEEP,
    RETRIEVAL_AGENTIC_FAST,
    RETRIEVAL_HYBRID,
    RETRIEVAL_AUTO,
    RETRIEVAL_MODES,
    ModeRouter,
    RoutingDecision,
)
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


def _write(tmp_path: Path, section: str) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(_MINIMAL_YAML + section, encoding="utf-8")
    return path


class TestRetrieverConstants:
    """RETRIEVAL_AUTO 独立于三档真实模式；ModeRouter 协议可注入 fake。"""

    def test_auto_not_in_retrieval_modes(self):
        assert RETRIEVAL_AUTO == "auto"
        assert RETRIEVAL_AUTO not in RETRIEVAL_MODES
        assert RETRIEVAL_MODES == (RETRIEVAL_HYBRID, RETRIEVAL_AGENTIC_FAST, RETRIEVAL_AGENTIC_DEEP)

    def test_routing_decision_defaults(self):
        decision = RoutingDecision(mode="hybrid")
        assert decision.confidence is None
        assert decision.probabilities is None
        assert RoutingDecision(mode="hybrid", confidence=0.9, probabilities={"hybrid": 0.9}).confidence == 0.9

    def test_mode_router_protocol_accepts_fake(self):
        class FakeRouter:
            def decide(self, query: str, candidates: tuple[str, ...]) -> RoutingDecision:
                return RoutingDecision(mode=candidates[0])

        assert isinstance(FakeRouter(), ModeRouter)


class TestModeRouterSettings:
    def test_section_missing_defaults_disabled(self, tmp_path: Path):
        config = load_settings(_write(tmp_path, "")).mode_router
        assert config.enabled is False
        assert config.backend == "cloudflare_workers_ai"
        assert config.model == "clef-flash"
        assert config.account_id is None
        assert config.base_url is None
        assert config.api_token is None
        assert config.timeout_s == 3.0
        assert config.min_confidence is None
        assert config.fallback_retrieval == "hybrid"

    def test_yaml_example_section_loads_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """仓库 settings.yaml 的示例节：占位符未解析但 enabled=false 时仍可加载（零加载语义）。"""
        monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
        monkeypatch.delenv("MODE_ROUTER_API_TOKEN", raising=False)
        settings = load_settings(Path("config/settings.yaml"))
        config = settings.mode_router
        assert config.enabled is False
        assert config.model == "clef-flash"
        assert config.account_id == "${CLOUDFLARE_ACCOUNT_ID}"  # 未解析占位符原样保留
        assert settings.chat.default_retrieval == "hybrid"

    @pytest.mark.parametrize(
        "section,fragment",
        [
            ("mode_router:\n  enabled: 'yes'\n", "enabled"),
            ("mode_router:\n  backend: ''\n", "backend"),
            ("mode_router:\n  backend: unknown\n", "backend"),
            ("mode_router:\n  backend: typesafe\n", "MR6"),
            ("mode_router:\n  backend: openai_compatible\n", "MR7"),
            ("mode_router:\n  model: ''\n", "model"),
            ("mode_router:\n  model: gpt-4o\n", "clef"),
            ("mode_router:\n  base_url: ftp://x\n", "base_url"),
            ("mode_router:\n  timeout_s: 0\n", "timeout_s"),
            ("mode_router:\n  timeout_s: 11\n", "timeout_s"),
            ("mode_router:\n  timeout_s: abc\n", "timeout_s"),
            ("mode_router:\n  min_confidence: 0\n", "min_confidence"),
            ("mode_router:\n  min_confidence: 1.0\n", "min_confidence"),
            ("mode_router:\n  min_confidence: -0.5\n", "min_confidence"),
            ("mode_router:\n  fallback_retrieval: auto\n", "fallback_retrieval"),
            ("mode_router:\n  fallback_retrieval: bogus\n", "fallback_retrieval"),
        ],
    )
    def test_invalid_values_fail_fast(self, tmp_path: Path, section: str, fragment: str):
        with pytest.raises(ConfigError, match=fragment):
            load_settings(_write(tmp_path, section))

    def test_valid_values_load(self, tmp_path: Path):
        yaml = "mode_router:\n  model: clef\n  min_confidence: 0.5\n  timeout_s: 10\n"
        config = load_settings(_write(tmp_path, yaml)).mode_router
        assert config.model == "clef"
        assert config.min_confidence == pytest.approx(0.5)
        assert config.timeout_s == 10.0

    def test_enabled_requires_resolvable_credentials(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
        monkeypatch.delenv("MODE_ROUTER_API_TOKEN", raising=False)
        yaml = (
            "mode_router:\n"
            "  enabled: true\n"
            "  account_id: ${CLOUDFLARE_ACCOUNT_ID}\n"
            "  api_token: ${MODE_ROUTER_API_TOKEN}\n"
        )
        # 占位符原样保留 → 视为缺失。
        with pytest.raises(ConfigError, match="account_id"):
            load_settings(_write(tmp_path, yaml))
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct-1")
        with pytest.raises(ConfigError, match="api_token"):
            load_settings(_write(tmp_path, yaml))
        # 凭据齐备后可加载，且占位符被解析。
        monkeypatch.setenv("MODE_ROUTER_API_TOKEN", "tok-1")
        config = load_settings(_write(tmp_path, yaml)).mode_router
        assert config.enabled is True
        assert config.account_id == "acct-1"
        assert config.api_token == "tok-1"

    def test_disabled_tolerates_unresolved_placeholders(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
        monkeypatch.delenv("MODE_ROUTER_API_TOKEN", raising=False)
        config = load_settings(_write(tmp_path, "")).mode_router
        assert config.enabled is False


class TestModeRouterCrossValidation:
    """跨节校验：fallback_retrieval 与 agentic / allow_deep；default_retrieval=auto 与 enabled。"""

    _AGENTIC_ENABLED = (
        "agentic_search:\n  enabled: true\n  allowed_roots: ['./data/documents']\n"
    )

    def test_fallback_agentic_requires_agentic_enabled(self, tmp_path: Path):
        yaml = "mode_router:\n  fallback_retrieval: agentic_fast\n"
        with pytest.raises(ConfigError, match="agentic_search.enabled"):
            load_settings(_write(tmp_path, yaml))

    def test_fallback_agentic_fast_with_agentic_enabled(self, tmp_path: Path):
        yaml = self._AGENTIC_ENABLED + "mode_router:\n  fallback_retrieval: agentic_fast\n"
        config = load_settings(_write(tmp_path, yaml)).mode_router
        assert config.fallback_retrieval == "agentic_fast"

    def test_fallback_deep_requires_allow_deep(self, tmp_path: Path):
        yaml = self._AGENTIC_ENABLED + "mode_router:\n  fallback_retrieval: agentic_deep\n"
        with pytest.raises(ConfigError, match="allow_deep"):
            load_settings(_write(tmp_path, yaml))

    def test_fallback_deep_with_allow_deep(self, tmp_path: Path):
        yaml = (
            self._AGENTIC_ENABLED
            + "  allow_deep: true\n"
            + "mode_router:\n  fallback_retrieval: agentic_deep\n"
        )
        config = load_settings(_write(tmp_path, yaml)).mode_router
        assert config.fallback_retrieval == "agentic_deep"

    def test_default_retrieval_auto_requires_mode_router(self, tmp_path: Path):
        yaml = "chat:\n  default_retrieval: auto\n"
        with pytest.raises(ConfigError, match="mode_router.enabled"):
            load_settings(_write(tmp_path, yaml))

    def test_default_retrieval_auto_with_mode_router_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct-1")
        monkeypatch.setenv("MODE_ROUTER_API_TOKEN", "tok-1")
        yaml = (
            "chat:\n  default_retrieval: auto\n"
            "mode_router:\n  enabled: true\n"
            "  account_id: ${CLOUDFLARE_ACCOUNT_ID}\n"
            "  api_token: ${MODE_ROUTER_API_TOKEN}\n"
        )
        settings = load_settings(_write(tmp_path, yaml))
        assert settings.chat.default_retrieval == "auto"
        assert settings.mode_router.enabled is True

    def test_default_retrieval_still_rejects_bogus_values(self, tmp_path: Path):
        yaml = "chat:\n  default_retrieval: turbo\n"
        with pytest.raises(ConfigError, match="default_retrieval"):
            load_settings(_write(tmp_path, yaml))
