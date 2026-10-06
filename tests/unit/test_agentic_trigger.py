"""S2.2: AgenticTrigger 触发策略单元测试（离线、确定性）。"""

from __future__ import annotations

import pytest

from core.query_engine.agentic_trigger import (
    DEFAULT_TRIGGER_MIN_RESULTS,
    DEFAULT_TRIGGER_SCORE_THRESHOLD,
    TRIGGER_ALWAYS,
    TRIGGER_FALLBACK,
    TRIGGER_TOOL_ONLY,
    VALID_TRIGGERS,
    AgenticTrigger,
    AgenticTriggerError,
    TriggerDecision,
)
from core.settings import ConfigError, load_settings

# tests/unit/test_agentic_config.py 中的最小合法 YAML（复用其加载方式）。
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


def _write(tmp_path, agentic_section: str):
    path = tmp_path / "settings.yaml"
    path.write_text(_MINIMAL_YAML + agentic_section, encoding="utf-8")
    return path


class TestStrategyMatrix:
    """三策略 × 信号充足/不足 矩阵。"""

    def test_tool_only_never_triggers(self):
        trigger = AgenticTrigger(TRIGGER_TOOL_ONLY)
        assert trigger.should_trigger(0.01, 0).triggered is False
        assert trigger.should_trigger(0.99, 100).triggered is False
        assert trigger.should_trigger(None, 0).triggered is False

    def test_always_always_triggers(self):
        trigger = AgenticTrigger(TRIGGER_ALWAYS)
        assert trigger.should_trigger(0.99, 100).triggered is True
        assert trigger.should_trigger(None, 0).triggered is True

    def test_fallback_insufficient_count_triggers(self):
        trigger = AgenticTrigger(TRIGGER_FALLBACK, score_threshold=0.5, min_results=3)
        # 结果数低于下限：即使分数很高也触发。
        assert trigger.should_trigger(0.99, 2).triggered is True

    def test_fallback_low_score_triggers(self):
        trigger = AgenticTrigger(TRIGGER_FALLBACK, score_threshold=0.5, min_results=1)
        assert trigger.should_trigger(0.49, 10).triggered is True

    def test_fallback_no_results_triggers(self):
        # min_results=0 时数量检查不拦，top_score=None 仍视为信号不足。
        trigger = AgenticTrigger(TRIGGER_FALLBACK, min_results=0)
        decision = trigger.should_trigger(None, 0)
        assert decision.triggered is True
        assert "None" in decision.reason

    def test_fallback_sufficient_signal_no_trigger(self):
        trigger = AgenticTrigger(TRIGGER_FALLBACK, score_threshold=0.5, min_results=3)
        assert trigger.should_trigger(0.9, 5).triggered is False

    def test_fallback_no_results_but_min_results_zero(self):
        # min_results=0 时空结果不算数量不足，但 top_score=None 仍触发。
        trigger = AgenticTrigger(TRIGGER_FALLBACK, min_results=0)
        assert trigger.should_trigger(None, 0).triggered is True
        assert trigger.should_trigger(0.9, 0).triggered is False


class TestThresholdBoundaries:
    """阈值边界：等于阈值不触发（严格小于才触发）。"""

    @pytest.mark.parametrize(
        "score,threshold,expected",
        [
            (0.5, 0.5, False),  # 恰好等于：信号充足
            (0.4999, 0.5, True),
            (0.0, 0.0, False),  # 下边界
            (0.0, 1.0, True),
            (1.0, 1.0, False),  # 上边界
        ],
    )
    def test_score_boundary(self, score: float, threshold: float, expected: bool):
        trigger = AgenticTrigger(TRIGGER_FALLBACK, score_threshold=threshold, min_results=0)
        assert trigger.should_trigger(score, 10).triggered is expected


class TestInvalidConfig:
    """非法策略名 / 参数 fail-fast。"""

    def test_invalid_strategy_name(self):
        with pytest.raises(AgenticTriggerError, match="strategy"):
            AgenticTrigger("auto")

    @pytest.mark.parametrize("threshold", [-0.1, 1.1, "0.5", None, True])
    def test_invalid_threshold(self, threshold):
        with pytest.raises(AgenticTriggerError, match="score_threshold"):
            AgenticTrigger(TRIGGER_FALLBACK, score_threshold=threshold)

    @pytest.mark.parametrize("min_results", [-1, 1.5, "3", None, True])
    def test_invalid_min_results(self, min_results):
        with pytest.raises(AgenticTriggerError, match="min_results"):
            AgenticTrigger(TRIGGER_FALLBACK, min_results=min_results)

    def test_tool_only_default_is_safe(self):
        assert TRIGGER_TOOL_ONLY == "tool_only"
        assert DEFAULT_TRIGGER_SCORE_THRESHOLD == 0.5  # 未校准占位值
        assert DEFAULT_TRIGGER_MIN_RESULTS == 1
        assert VALID_TRIGGERS == {"tool_only", "fallback", "always"}


class TestDecision:
    def test_decision_carries_reason(self):
        decision = AgenticTrigger(TRIGGER_FALLBACK, score_threshold=0.5).should_trigger(0.2, 5)
        assert isinstance(decision, TriggerDecision)
        assert "0.2000" in decision.reason


class TestTriggerSettings:
    """agentic_search.trigger* 配置字段解析与 fail-fast。"""

    def test_defaults(self, tmp_path):
        config = load_settings(_write(tmp_path, "")).agentic_search
        assert config.trigger == "tool_only"
        assert config.trigger_score_threshold == 0.5
        assert config.trigger_min_results == 1

    def test_explicit_values_load(self, tmp_path):
        yaml = (
            "agentic_search:\n"
            "  trigger: fallback\n"
            "  trigger_score_threshold: 0.72\n"
            "  trigger_min_results: 3\n"
        )
        config = load_settings(_write(tmp_path, yaml)).agentic_search
        assert config.trigger == "fallback"
        assert config.trigger_score_threshold == 0.72
        assert config.trigger_min_results == 3

    @pytest.mark.parametrize(
        "section,fragment",
        [
            ("agentic_search:\n  trigger: auto\n", "trigger"),
            ("agentic_search:\n  trigger: ''\n", "trigger"),
            ("agentic_search:\n  trigger: 3\n", "trigger"),
            ("agentic_search:\n  trigger_score_threshold: -0.1\n", "trigger_score_threshold"),
            ("agentic_search:\n  trigger_score_threshold: 1.5\n", "trigger_score_threshold"),
            ("agentic_search:\n  trigger_score_threshold: abc\n", "trigger_score_threshold"),
            ("agentic_search:\n  trigger_min_results: -1\n", "trigger_min_results"),
            ("agentic_search:\n  trigger_min_results: 1.5\n", "trigger_min_results"),
        ],
    )
    def test_invalid_values_fail_fast(self, tmp_path, section: str, fragment: str):
        with pytest.raises(ConfigError, match=fragment):
            load_settings(_write(tmp_path, section))
