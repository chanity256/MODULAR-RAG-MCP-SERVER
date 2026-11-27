"""CrossEncoderScorer 适配层单测（CER1）。

全部离线确定性：loader / sentence_transformers 均以 fake 替换，
零网络、零 torch、零真实模型（对齐 spec §4 测试方案）。
"""

from __future__ import annotations

import sys
import types

import pytest

from libs.reranker.cross_encoder_scorer import CrossEncoderScorer


class _FakeModel:
    """fake CrossEncoder：记录 predict 调用，返回构造时给定的分数。"""

    def __init__(self, scores: list[float], *, fail: Exception | None = None) -> None:
        self.scores = scores
        self.fail = fail
        self.calls: list[tuple[list[tuple[str, str]], int]] = []

    def predict(self, pairs: list[tuple[str, str]], batch_size: int = 32) -> list[float]:
        if self.fail is not None:
            raise self.fail
        self.calls.append((list(pairs), batch_size))
        return self.scores


# ---------------------------------------------------------------------------
# 构造零 IO
# ---------------------------------------------------------------------------


def test_constructor_is_zero_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """构造零 IO：不调 loader、不 import sentence_transformers。"""
    calls: list[str] = []

    def loader(name: str) -> object:
        calls.append(name)
        return object()

    # 「未 import」的环境无关证明：屏蔽真实导入路径后，构造若触发
    # ``import sentence_transformers`` 会立即抛 ImportError。不能以
    # ``sys.modules`` 是否含该键作为判据——安装可选组 ``[rerank]`` 后，
    # langchain_text_splitters 会在自身模块级传递性 import 该包（受
    # try/except 保护），任何先经 query_engine / ingestion 链路的测试
    # 都会把它提前放进 sys.modules，与构造行为无关。
    class _BlockImport:
        def find_spec(self, name, path=None, target=None):
            if name == "sentence_transformers" or name.startswith("sentence_transformers."):
                raise ImportError("blocked: constructor must not import sentence_transformers")
            return None

    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_BlockImport(), *sys.meta_path])

    scorer = CrossEncoderScorer("cross-encoder/test-model", loader=loader)

    assert calls == []  # 构造不触发加载
    assert scorer._model is None  # 惰性缓存未填充
    assert scorer is not None


# ---------------------------------------------------------------------------
# 惰性加载与缓存
# ---------------------------------------------------------------------------


def test_ensure_loaded_builds_once_and_caches() -> None:
    """首次 ensure_loaded 才构建，且只构建一次（二次调用复用缓存）。"""
    calls: list[str] = []
    model = _FakeModel([0.5])

    def loader(name: str) -> object:
        calls.append(name)
        return model

    scorer = CrossEncoderScorer("cross-encoder/test-model", loader=loader)

    first = scorer.ensure_loaded()
    second = scorer.ensure_loaded()

    assert calls == ["cross-encoder/test-model"]  # 只构建一次
    assert first is model and second is model


def test_call_triggers_lazy_load_and_passthrough_scores() -> None:
    """首次 __call__ 惰性构建；分数原样透传并 float 化。"""
    model = _FakeModel([0.3, 0.9])
    scorer = CrossEncoderScorer("cross-encoder/test-model", loader=lambda name: model)

    scores = scorer("azure auth", ["text a", "text b"])

    assert scores == [0.3, 0.9]
    pairs, batch_size = model.calls[0]
    assert pairs == [("azure auth", "text a"), ("azure auth", "text b")]
    assert batch_size == 32  # 默认 batch_size
    # 二次调用不重复构建（同一次 predict 记录）
    scorer("azure auth", ["text a"])
    assert len(model.calls) == 2  # 推理重复执行
    assert model.calls[1][0] == [("azure auth", "text a")]  # 但模型对象复用


def test_call_forwards_batch_size() -> None:
    """batch_size 透传到 predict。"""
    model = _FakeModel([0.1])
    scorer = CrossEncoderScorer("m", loader=lambda name: model, batch_size=7)

    scorer("q", ["t"])

    assert model.calls[0][1] == 7


# ---------------------------------------------------------------------------
# 默认 loader（经 fake sentence_transformers 模块验证 device 透传与惰性 import）
# ---------------------------------------------------------------------------


def test_default_loader_passes_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认 loader 惰性 import sentence_transformers 并透传 device。"""
    captured: dict[str, object] = {}

    class _FakeCrossEncoder:
        def __init__(self, model_name: str, device: str | None = None) -> None:
            captured["model_name"] = model_name
            captured["device"] = device

        def predict(self, pairs: list[tuple[str, str]], batch_size: int = 32) -> list[float]:
            return [0.5] * len(pairs)

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.CrossEncoder = _FakeCrossEncoder  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    scorer = CrossEncoderScorer("cross-encoder/test-model", device="cpu")

    scores = scorer("q", ["t"])

    assert captured == {"model_name": "cross-encoder/test-model", "device": "cpu"}
    assert scores == [0.5]


# ---------------------------------------------------------------------------
# model_name 缺失契约
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_name", [None, "", "   "])
def test_missing_model_name_raises_readable_error(bad_name: str | None) -> None:
    """model_name 为 None / 空白时抛带 rerank.model 指引的 RuntimeError。"""
    scorer = CrossEncoderScorer(bad_name, loader=lambda name: object())

    with pytest.raises(RuntimeError, match="rerank\\.model"):
        scorer("q", ["t"])


# ---------------------------------------------------------------------------
# 失败包装
# ---------------------------------------------------------------------------


def test_import_error_wrapped_with_install_hint() -> None:
    """loader 抛 ImportError（依赖未装）→ 包装为带 pip install 提示的 RuntimeError。"""

    def loader(name: str) -> object:
        raise ImportError("No module named 'sentence_transformers'")

    scorer = CrossEncoderScorer("m", loader=loader)

    with pytest.raises(RuntimeError, match='pip install -e "\\.\\[rerank\\]"') as exc_info:
        scorer.ensure_loaded()
    assert "No module named" in str(exc_info.value)  # 原始错误保留


def test_load_failure_wrapped_with_mirror_hint() -> None:
    """模型下载/加载失败 → 包装为带 HF_ENDPOINT 镜像提示的 RuntimeError。"""

    def loader(name: str) -> object:
        raise ConnectionError("download failed")

    scorer = CrossEncoderScorer("m", loader=loader)

    with pytest.raises(RuntimeError, match="HF_ENDPOINT") as exc_info:
        scorer.ensure_loaded()
    assert "download failed" in str(exc_info.value)
    assert "m" in str(exc_info.value)  # 模型名保留


def test_loader_returning_none_is_contract_violation() -> None:
    """loader 返回 None 视为契约破坏，抛 RuntimeError 而非缓存 None。"""
    scorer = CrossEncoderScorer("m", loader=lambda name: None)

    with pytest.raises(RuntimeError, match="None"):
        scorer.ensure_loaded()


def test_inference_error_propagates_raw() -> None:
    """模型就绪后的推理期异常原样传播（不包装、不吞），交由 backend 内部回退。"""
    boom = ValueError("predict exploded")
    model = _FakeModel([0.5], fail=boom)
    scorer = CrossEncoderScorer("m", loader=lambda name: model)

    with pytest.raises(ValueError) as exc_info:
        scorer("q", ["t"])

    assert exc_info.value is boom


def test_empty_texts_returns_empty() -> None:
    """空 texts → 空 pairs → 空分数（backend 上游已挡，适配层不重复处理）。"""
    model = _FakeModel([])
    scorer = CrossEncoderScorer("m", loader=lambda name: model)

    assert scorer("q", []) == []
