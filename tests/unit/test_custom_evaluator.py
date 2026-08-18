"""``CustomEvaluator`` 指标测试与 ``EvaluatorFactory`` 路由测试（阶段 B6）。

验证：
- 输入 query + retrieved_ids + golden_ids 输出稳定 metrics（hit_rate / mrr / hit_at_k）。
- 指标对同一输入确定性可复现。
- 工厂按名称路由，``custom`` 默认注册，未知名称报错。
- ``create_from_settings`` 批量创建。
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from libs.evaluator.base_evaluator import BaseEvaluator
from libs.evaluator.custom_evaluator import CustomEvaluator
from libs.evaluator.evaluator_factory import EvaluatorBackendError, EvaluatorFactory

_VALID_YAML = dedent(
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
    evaluation:
      backends: [custom]
    """
)


def _settings(tmp_path: Path, backends: list[str] | None = None):
    path = tmp_path / "settings.yaml"
    yaml_text = _VALID_YAML
    if backends is not None:
        yaml_text = yaml_text.replace("backends: [custom]", f"backends: {backends}")
    path.write_text(yaml_text, encoding="utf-8")
    return load_settings(path)


# ---------------------------------------------------------------------------
# CustomEvaluator 指标
# ---------------------------------------------------------------------------


def test_hit_and_mrr_first_position():
    ev = CustomEvaluator()
    m = ev.evaluate("q", ["c1", "c2", "c3"], ["c1"])
    assert m["hit_rate"] == 1.0
    assert m["mrr"] == 1.0
    assert m["hit_at_k"] == 1.0


def test_hit_and_mrr_later_position():
    ev = CustomEvaluator()
    m = ev.evaluate("q", ["c1", "c2", "c3"], ["c2"])
    assert m["hit_rate"] == 1.0
    assert m["mrr"] == pytest.approx(0.5)
    assert m["hit_at_k"] == 1.0


def test_no_hit():
    ev = CustomEvaluator()
    m = ev.evaluate("q", ["c1", "c2"], ["c9"])
    assert m["hit_rate"] == 0.0
    assert m["mrr"] == 0.0
    assert m["hit_at_k"] == 0.0


def test_multiple_golden_hit_at_k():
    ev = CustomEvaluator()
    m = ev.evaluate("q", ["c1", "c2", "c3"], ["c2", "c3", "c9"])
    # 2/3 golden 命中
    assert m["hit_at_k"] == pytest.approx(2 / 3)
    # mrr 取第一个命中（c2 在 rank 2）
    assert m["mrr"] == 0.5


def test_top_k_truncation():
    ev = CustomEvaluator()
    # top_k=2 时，c3 被截断，golden=[c3] 不命中
    m = ev.evaluate("q", ["c1", "c2", "c3"], ["c3"], top_k=2)
    assert m["hit_rate"] == 0.0
    assert m["hit_at_k"] == 0.0
    # mrr 仍基于全量 retrieved
    assert m["mrr"] == pytest.approx(1 / 3)


def test_metrics_deterministic():
    ev = CustomEvaluator()
    args = ("q", ["c1", "c2", "c3"], ["c2"])
    assert ev.evaluate(*args) == ev.evaluate(*args)


def test_empty_golden_returns_zeros():
    ev = CustomEvaluator()
    m = ev.evaluate("q", ["c1", "c2"], [])
    assert m == {"hit_rate": 0.0, "mrr": 0.0, "hit_at_k": 0.0}


def test_evaluator_name():
    ev = CustomEvaluator()
    assert ev.name == "custom"


# ---------------------------------------------------------------------------
# EvaluatorFactory 路由
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_registry():
    saved = dict(EvaluatorFactory._registry)
    EvaluatorFactory._registry = {"custom": saved["custom"]}
    yield EvaluatorFactory
    EvaluatorFactory._registry = saved


def test_factory_create_custom(clean_registry):
    ev = clean_registry.create("custom")
    assert isinstance(ev, CustomEvaluator)
    assert isinstance(ev, BaseEvaluator)


def test_factory_custom_always_registered(clean_registry):
    assert clean_registry.is_registered("custom")
    assert "custom" in clean_registry.available_backends()


def test_factory_unknown_name_raises(clean_registry):
    with pytest.raises(EvaluatorBackendError) as exc_info:
        clean_registry.create("nonexistent")
    assert "nonexistent" in str(exc_info.value)


def test_factory_unknown_name_lists_registered(clean_registry):
    with pytest.raises(EvaluatorBackendError) as exc_info:
        clean_registry.create("bad")
    assert "custom" in str(exc_info.value)


def test_factory_register_fake(clean_registry):
    class _FakeEv(BaseEvaluator):
        def evaluate(self, query, retrieved_ids, golden_ids, *, top_k=None, trace=None, **kwargs):
            return {"x": 1.0}

    clean_registry.register("fake")(lambda **kw: _FakeEv())
    ev = clean_registry.create("fake")
    assert isinstance(ev, _FakeEv)
    assert ev.evaluate("q", [], []) == {"x": 1.0}


def test_create_from_settings_single(tmp_path, clean_registry):
    settings = _settings(tmp_path, backends=["custom"])
    evs = clean_registry.create_from_settings(settings)
    assert len(evs) == 1
    assert isinstance(evs[0], CustomEvaluator)


def test_create_from_settings_multiple(tmp_path, clean_registry):
    class _FakeEv2(BaseEvaluator):
        def evaluate(self, query, retrieved_ids, golden_ids, *, top_k=None, trace=None, **kwargs):
            return {}

    clean_registry.register("fake2")(lambda **kw: _FakeEv2())
    settings = _settings(tmp_path, backends=["custom", "fake2"])
    evs = clean_registry.create_from_settings(settings)
    assert len(evs) == 2
    assert isinstance(evs[0], CustomEvaluator)
    assert isinstance(evs[1], _FakeEv2)


def test_create_from_settings_unknown_raises(tmp_path, clean_registry):
    settings = _settings(tmp_path, backends=["nonexistent"])
    with pytest.raises(EvaluatorBackendError):
        clean_registry.create_from_settings(settings)


# ---------------------------------------------------------------------------
# 契约补齐（I4）：输入边界
# ---------------------------------------------------------------------------


def test_empty_retrieved_no_hit():
    ev = CustomEvaluator()
    m = ev.evaluate("q", [], ["c1"])
    assert m == {"hit_rate": 0.0, "mrr": 0.0, "hit_at_k": 0.0}


def test_duplicate_golden_ids_do_not_inflate_hit_at_k():
    ev = CustomEvaluator()
    # golden 去重语义：["c1","c1"] 等价 ["c1"]
    m = ev.evaluate("q", ["c1"], ["c1", "c1"])
    assert m["hit_at_k"] == 1.0
    assert m["hit_rate"] == 1.0


def test_duplicate_retrieved_ids_counted_once_for_hit_at_k():
    ev = CustomEvaluator()
    m = ev.evaluate("q", ["c1", "c1", "c2"], ["c1", "c2"])
    # 命中 c1/c2 各一次（重复 c1 不重复计数）→ 2/2
    assert m["hit_at_k"] == 1.0


def test_top_k_zero_considered_empty():
    ev = CustomEvaluator()
    m = ev.evaluate("q", ["c1", "c2"], ["c2"], top_k=0)
    # top_k=0 → considered 为空 → hit_rate/hit_at_k=0；mrr 按全量惯例仍计
    assert m["hit_rate"] == 0.0
    assert m["hit_at_k"] == 0.0
    assert m["mrr"] == pytest.approx(0.5)


def test_golden_later_than_top_k_only_mrr_counts():
    ev = CustomEvaluator()
    m = ev.evaluate("q", ["c1", "c2", "c3"], ["c3"], top_k=1)
    assert m["hit_rate"] == 0.0
    assert m["hit_at_k"] == 0.0
    assert m["mrr"] == pytest.approx(1 / 3)


def test_metrics_always_contain_exactly_three_keys():
    ev = CustomEvaluator()
    for args in (("q", ["c1"], ["c1"]), ("q", [], []), ("q", ["a"], ["z"])):
        keys = ev.evaluate(*args).keys()
        assert set(keys) == {"hit_rate", "mrr", "hit_at_k"}
    assert set(ev.evaluate("q", ["c1"], ["c1"], top_k=3).keys()) == {"hit_rate", "mrr", "hit_at_k"}


def test_trace_and_kwargs_ignored():
    ev = CustomEvaluator()
    m = ev.evaluate("q", ["c1"], ["c1"], top_k=None, trace=None, extra="ignored")
    assert m["hit_rate"] == 1.0
