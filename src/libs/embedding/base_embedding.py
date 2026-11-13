"""Embedding 抽象基类（阶段 B2）。

定义全链路共用的 ``BaseEmbedding`` 接口，屏蔽不同 Embedding Provider
（OpenAI / Azure / Ollama）的认证与请求格式差异。

设计要点：
- ``embed(texts)`` 为统一批量入口，返回 ``list[list[float]]``。
- 预留 ``trace: TraceContext | None`` 参数（阶段 F 打点时使用，当前仅占位）。
- 不在此处做任何网络/IO 的"业务初始化"，仅定义契约。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Sequence

__all__ = ["BaseEmbedding"]


class BaseEmbedding(ABC):
    """Embedding 抽象基类，所有 provider 实现需继承并实现 ``embed``。"""

    @property
    def dimension(self) -> int | None:
        """输出向量维度（若已知）。默认 ``None``，由子类按需覆盖。"""
        return None

    @abstractmethod
    def embed(
        self,
        texts: Sequence[str],
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> list[list[float]]:
        """对一批文本生成稠密向量。

        Args:
            texts: 待向量化的文本序列。
            trace: 可选的 ``TraceContext``，用于阶段 F 打点（当前占位）。
            **kwargs: provider 特定的扩展参数。

        Returns:
            与 ``texts`` 等长的向量列表，每个向量是 ``list[float]``。

        Raises:
            ValueError: 输入为空或包含非法项时。
        """
        raise NotImplementedError

    def embed_one(
        self,
        text: str,
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> list[float]:
        """便捷方法：对单条文本生成向量并返回单个向量。"""
        if not isinstance(text, str) or text == "":
            raise ValueError("embed_one requires a non-empty string")
        return self.embed([text], trace=trace, **kwargs)[0]
