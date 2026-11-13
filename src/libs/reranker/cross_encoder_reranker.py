"""Cross-Encoder Reranker 实现（阶段 B7.8）。

使用 Cross-Encoder 模型对 Top-M candidates 打分排序。
Cross-Encoder 同时接收 query 和 candidate text，输出一个相关性分数，
比 bi-encoder 更精确但更慢，适合对召回后的少量候选做精排。

设计要点：
- ``scorer`` 可注入：生产环境用 ``sentence-transformers`` CrossEncoder，
  测试用 mock scorer 保证 deterministic。
- ``rerank(query, candidates)`` 对每个 candidate 调用 scorer 得分，按分数降序排列。
- 失败/超时回退：scorer 异常时返回原序 + ``last_fallback=True``。
- ``top_m`` 截断：只对前 ``top_m`` 条候选打分，控制延迟。
- 不引入 ``sentence-transformers`` 重依赖（spec 标注"占位可跑"），scorer 由调用方注入。
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from core.settings import RerankConfig
from libs.reranker.base_reranker import BaseReranker, RerankCandidate

__all__ = ["CrossEncoderReranker", "register_cross_encoder_provider"]

# Scorer 接口：(query, texts) -> scores（越高越相关）
Scorer = Callable[[str, Sequence[str]], list[float]]


class CrossEncoderReranker(BaseReranker):
    """Cross-Encoder Rerank 实现：用可注入的 scorer 对候选打分排序。"""

    def __init__(
        self,
        rerank_config: RerankConfig,
        scorer: Scorer | None = None,
        **kwargs: Any,
    ):
        self.rerank_config = rerank_config
        self._scorer: Scorer | None = scorer
        self._top_m = rerank_config.top_m
        self._extra_kwargs = kwargs
        self.last_fallback: bool = False  # 供调用方检查是否发生了回退

    def _ensure_scorer(self) -> Scorer:
        if self._scorer is None:
            raise RuntimeError(
                "CrossEncoderReranker requires a 'scorer' callable. "
                "Inject a scorer (e.g. sentence-transformers CrossEncoder wrapper) "
                "or pass one via kwargs."
            )
        return self._scorer

    def rerank(
        self,
        query: str,
        candidates: Sequence[RerankCandidate],
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> list[RerankCandidate]:
        self.last_fallback = False
        if not isinstance(candidates, (list, tuple)):
            raise ValueError(f"candidates must be a list, got {type(candidates).__name__}")
        if len(candidates) == 0:
            return []

        # 截取 top_m
        truncated = list(candidates[: self._top_m])

        # 获取 scorer（可被 kwargs 临时覆盖）
        scorer = kwargs.pop("scorer", None) or self._ensure_scorer()

        # 准备文本列表
        texts = [c.text for c in truncated]

        # 调用 scorer 打分
        try:
            scores = scorer(query, texts)
        except Exception:
            # scorer 失败/超时 → 回退原序
            self.last_fallback = True
            return truncated

        # 校验 scorer 返回
        if not isinstance(scores, (list, tuple)):
            self.last_fallback = True
            return truncated
        if len(scores) != len(truncated):
            self.last_fallback = True
            return truncated

        # 按分数降序排列
        indexed = list(enumerate(float(s) for s in scores))
        indexed.sort(key=lambda pair: pair[1], reverse=True)

        result: list[RerankCandidate] = []
        for rank, (orig_idx, score) in enumerate(indexed):
            c = truncated[orig_idx]
            result.append(
                RerankCandidate(
                    id=c.id,
                    text=c.text,
                    score=score,
                    metadata=c.metadata,
                )
            )
        return result


def register_cross_encoder_provider() -> None:
    """向 ``RerankerFactory`` 注册 cross_encoder backend。

    注意：CrossEncoderReranker 需要一个 ``scorer`` callable，工厂通过 ``kwargs["scorer"]`` 传入。
    """
    from libs.reranker.reranker_factory import RerankerFactory

    @RerankerFactory.register("cross_encoder")
    def _ctor(rerank_config: RerankConfig, *, scorer: Scorer | None = None, **kwargs: Any) -> CrossEncoderReranker:
        return CrossEncoderReranker(rerank_config, scorer=scorer, **kwargs)
