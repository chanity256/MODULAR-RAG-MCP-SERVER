"""Cross-Encoder scorer 适配层（CROSS_ENCODER_RERANKER_SPEC CER1）。

把 ``sentence_transformers.CrossEncoder`` 包装为 B7.8 ``Scorer`` 协议
（``(query, texts) -> scores``，越高越相关），供 ``CrossEncoderReranker`` 打分。

设计要点（对齐 spec §3.1）：
- **构造零 IO**：``__init__`` 只存参数，不 import ``sentence_transformers``、不加载
  模型（对齐 MCP tools 懒初始化先例；CI 无 torch 环境导入 ``libs.reranker`` 包不炸）。
- **惰性加载**：首次 ``ensure_loaded()`` / ``__call__`` 才校验 ``model_name``、
  import 依赖并加载模型；加载成功后缓存，进程内只加载一次。
- **``loader`` 注入点**：默认实现经惰性 import 构建真模型；单测注入 fake loader
  零网络零 torch 锁定语义。
- **失败语义**：``model_name`` 缺失 / 依赖未安装 / 模型下载加载失败，统一包装为带
  修复指引的可读 ``RuntimeError``（经 ``_ensure_scorer()`` 冒出后走 D6 通道①，
  ``pip install -e ".[rerank]"`` / ``HF_ENDPOINT`` 提示完整保留在
  ``last_error`` 与 trace ``details["error"]``）；适配层自己不吞异常、不做重试，
  模型就绪后的推理期异常原样传播（由 backend ``try/except`` 走 B7.8 内部回退）。
- ``sentence-transformers`` 不进主依赖：未安装时本模块导入与构造均合法，
  仅 ``ensure_loaded()`` 抛带 ``pip install -e ".[rerank]"`` 提示的错误。
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

__all__ = ["CrossEncoderScorer"]

#: 默认 ``loader`` 构建出的对象需具备 ``predict(pairs) -> scores``（上游 CrossEncoder 契约）。
ScorerLike = Any

#: 依赖缺失时的修复指引（与 pyproject 可选依赖组 ``rerank`` 对应）。
_INSTALL_HINT = 'pip install -e ".[rerank]"'

#: 模型下载失败时的镜像提示（国内网络环境）。
_MIRROR_HINT = "HF_ENDPOINT"


class CrossEncoderScorer:
    """包装 ``sentence_transformers.CrossEncoder`` 为 B7.8 Scorer 协议。"""

    def __init__(
        self,
        model_name: str | None,
        *,
        loader: Callable[[str], ScorerLike] | None = None,
        device: str | None = None,
        batch_size: int = 32,
    ) -> None:
        """Args:
            model_name: HF 模型名（来自 ``rerank.model``）；缺失不在此报错，
                延迟到 ``ensure_loaded()``（构造零 IO、零校验）。
            loader: 模型构建注入点；缺省用真 ``sentence_transformers.CrossEncoder``。
            device: 透传 ``CrossEncoder(device=...)``（None 交由上游自动选择）。
            batch_size: 透传 ``predict(batch_size=...)``。
        """
        self._model_name = model_name
        self._loader = loader
        self._device = device
        self._batch_size = batch_size
        self._model: ScorerLike | None = None  # 惰性缓存：None = 未加载

    def ensure_loaded(self) -> ScorerLike:
        """惰性加载模型并缓存；失败抛带修复指引的 ``RuntimeError``。

        供工厂默认 ``scorer_factory`` 在 ``_ensure_scorer()`` 阶段显式触发，
        确保加载失败走 D6 通道①（Core 层记录 ``last_error`` / trace error）
        而非被 ``rerank()`` 内部 ``try/except`` 吞掉。
        """
        if self._model is not None:
            return self._model

        model_name = (self._model_name or "").strip() if isinstance(self._model_name, str) else self._model_name
        if not model_name:
            raise RuntimeError(
                "CrossEncoderScorer requires a model name. "
                "Set 'rerank.model' in config/settings.yaml "
                "(e.g. cross-encoder/ms-marco-MiniLM-L-6-v2)."
            )

        if self._loader is not None:
            loader = self._loader
        else:
            loader = self._default_loader
        try:
            self._model = loader(str(model_name))
        except ImportError as exc:
            self._model = None
            raise RuntimeError(
                f"sentence-transformers is not installed (required for rerank backend "
                f"'cross_encoder'). Install the optional dependency group: {_INSTALL_HINT}. "
                f"Original error: {exc}"
            ) from exc
        except Exception as exc:
            self._model = None
            raise RuntimeError(
                f"Failed to load cross-encoder model '{model_name}' "
                f"(first use downloads it from Hugging Face; set {_MIRROR_HINT} to use a mirror). "
                f"Original error: {exc}"
            ) from exc

        if self._model is None:  # 防御：loader 返回 None 视为契约破坏
            raise RuntimeError(f"CrossEncoder loader returned None for model '{model_name}'.")
        return self._model

    def _default_loader(self, model_name: str) -> ScorerLike:
        """默认模型构建：惰性 import + ``CrossEncoder(model_name, device=device)``。"""
        from sentence_transformers import CrossEncoder

        return CrossEncoder(model_name, device=self._device)

    def __call__(self, query: str, texts: Sequence[str]) -> list[float]:
        """B7.8 Scorer 协议：``(query, texts) -> scores``（越高越相关）。

        推理期异常不在此捕获（backend 内部回退契约负责），只包装加载期失败。
        """
        model = self.ensure_loaded()
        pairs = [(query, text) for text in texts]
        scores = model.predict(pairs, batch_size=self._batch_size)
        return [float(s) for s in scores]
