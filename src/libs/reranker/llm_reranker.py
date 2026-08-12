"""LLM Reranker 实现（阶段 B7.7）。

读取 ``config/prompts/rerank.txt`` 构造 prompt，调用 LLM 对候选集重排，
解析 LLM 返回的排序编号列表（JSON 数组），按新顺序返回候选。

设计要点：
- LLM 实例通过依赖注入传入（测试中注入 mock LLM，生产中由调用方创建）。
- prompt 模板可通过 ``prompt_template`` 参数注入（测试中替代），或从文件读取。
- 输出严格结构化：LLM 应返回 JSON 数组（如 ``[2, 0, 1]``），解析失败时回退到原序。
- 失败时返回可回退信号：``fallback=True`` 标记，候选保持原序。
- 候选数超过 ``top_m`` 时截取前 ``top_m`` 条送入 LLM，减少 token 消耗。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Sequence

from core.settings import RerankConfig
from libs.llm.base_llm import BaseLLM
from libs.reranker.base_reranker import BaseReranker, RerankCandidate

__all__ = ["LLMReranker", "RerankerParseError", "register_llm_provider"]

_DEFAULT_PROMPT_PATH = "config/prompts/rerank.txt"


class RerankerParseError(RuntimeError):
    """LLM Reranker 响应解析错误。"""

    def __init__(self, message: str, *, raw: str | None = None, fallback: bool = False):
        self.raw = raw
        self.fallback = fallback
        super().__init__(message)


def _load_prompt_template(path: str | Path | None = None) -> str:
    """从文件加载 prompt 模板。"""
    p = Path(path) if path else Path(_DEFAULT_PROMPT_PATH)
    if not p.exists():
        raise FileNotFoundError(f"rerank prompt file not found: {p}")
    return p.read_text(encoding="utf-8")


class LLMReranker(BaseReranker):
    """LLM Rerank 实现：调用 LLM 对候选集重排。"""

    def __init__(
        self,
        rerank_config: RerankConfig,
        llm: BaseLLM,
        *,
        prompt_template: str | None = None,
        prompt_path: str | Path | None = None,
        **kwargs: Any,
    ):
        self.rerank_config = rerank_config
        self._llm = llm
        self._top_m = rerank_config.top_m
        if prompt_template is not None:
            self._prompt_template = prompt_template
        else:
            self._prompt_template = _load_prompt_template(prompt_path)
        self._extra_kwargs = kwargs
        self.last_fallback: bool = False  # 供调用方检查是否发生了回退

    # -- prompt 构建 ------------------------------------------------------

    def _format_candidates(self, candidates: Sequence[RerankCandidate]) -> str:
        lines: list[str] = []
        for i, c in enumerate(candidates):
            text = c.text.strip() if c.text else ""
            # 截断过长文本以控制 token 消耗
            max_len = self.rerank_config.extra.get("max_candidate_chars", 500)
            if len(text) > max_len:
                text = text[:max_len] + "..."
            lines.append(f"[{i}] {text}")
        return "\n".join(lines)

    def _build_prompt(self, query: str, candidates: Sequence[RerankCandidate]) -> str:
        return self._prompt_template.format(
            query=query,
            candidates=self._format_candidates(candidates),
        )

    # -- 响应解析 ---------------------------------------------------------

    def _parse_ranked_indices(self, content: str, *, expected: int) -> list[int]:
        """从 LLM 响应中解析排序后的索引列表。

        策略：用正则提取最后一个 JSON 数组 ``[...]``，解析为 int 列表。
        """
        # 尝试提取 JSON 数组
        arrays = re.findall(r"\[[\d\s,]+\]", content)
        if not arrays:
            raise RerankerParseError(
                f"no JSON array found in LLM response: {content[:200]!r}",
                raw=content,
                fallback=True,
            )
        raw_array = arrays[-1]  # 取最后一个（最可能是答案）
        try:
            indices = json.loads(raw_array)
        except json.JSONDecodeError as exc:
            raise RerankerParseError(
                f"failed to parse JSON array: {raw_array!r}: {exc}",
                raw=content,
                fallback=True,
            ) from exc
        if not isinstance(indices, list):
            raise RerankerParseError(
                f"expected a list, got {type(indices).__name__}: {raw_array!r}",
                raw=content,
                fallback=True,
            )
        # 校验每个元素
        clean: list[int] = []
        seen: set[int] = set()
        for idx in indices:
            if not isinstance(idx, int):
                # 尝试转换为 int
                try:
                    idx = int(idx)
                except (ValueError, TypeError):
                    continue
            if idx < 0 or idx >= expected:
                continue  # 跳过越界索引
            if idx in seen:
                continue  # 跳过重复索引
            seen.add(idx)
            clean.append(idx)
        if len(clean) == 0:
            raise RerankerParseError(
                f"no valid indices in parsed array: {raw_array!r}",
                raw=content,
                fallback=True,
            )
        return clean

    # -- 主流程 ------------------------------------------------------------

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

        # 构建 prompt 并调用 LLM
        prompt = self._build_prompt(query, truncated)
        try:
            content = self._llm.chat_text(prompt, trace=trace, **kwargs)
        except Exception as exc:
            # LLM 调用失败 → 回退原序
            self.last_fallback = True
            return truncated

        # 解析响应
        try:
            ranked_indices = self._parse_ranked_indices(content, expected=len(truncated))
        except RerankerParseError:
            # 解析失败 → 回退原序
            self.last_fallback = True
            return truncated

        # 按解析的索引重排，未出现在列表中的候选追加到末尾（保持原序）
        seen: set[int] = set(ranked_indices)
        remaining = [i for i in range(len(truncated)) if i not in seen]
        final_order = ranked_indices + remaining

        result: list[RerankCandidate] = []
        total = len(final_order)
        for rank, idx in enumerate(final_order):
            c = truncated[idx]
            # 更新 score：按新排名递减
            new_score = 1.0 - (rank / total) if total > 0 else 0.0
            result.append(
                RerankCandidate(
                    id=c.id,
                    text=c.text,
                    score=new_score,
                    metadata=c.metadata,
                )
            )
        return result


def register_llm_provider() -> None:
    """向 ``RerankerFactory`` 注册 llm backend。

    注意：LLMReranker 需要一个 ``BaseLLM`` 实例，工厂通过 ``kwargs["llm"]`` 传入。
    """
    from libs.reranker.reranker_factory import RerankerFactory

    @RerankerFactory.register("llm")
    def _ctor(rerank_config: RerankConfig, *, llm: BaseLLM | None = None, **kwargs: Any) -> LLMReranker:
        if llm is None:
            raise ValueError("LLMReranker requires an 'llm' keyword argument (BaseLLM instance)")
        return LLMReranker(rerank_config, llm=llm, **kwargs)
