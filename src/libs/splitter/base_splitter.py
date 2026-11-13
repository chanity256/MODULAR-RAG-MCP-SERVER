"""Splitter 抽象基类（阶段 B3）。

定义全链路共用的 ``BaseSplitter`` 接口，屏蔽不同切分策略
（Recursive / Semantic / FixedLength）的实现差异。

设计要点：
- ``split_text(text)`` 为统一入口，返回 ``List[str]``。
- 预留 ``trace: TraceContext | None`` 参数（阶段 F 打点时使用，当前仅占位）。
- 不在此处做任何网络/IO 的"业务初始化"，仅定义契约。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

__all__ = ["BaseSplitter"]


class BaseSplitter(ABC):
    """Splitter 抽象基类，所有切分策略实现需继承并实现 ``split_text``。"""

    @abstractmethod
    def split_text(
        self,
        text: str,
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> list[str]:
        """将文本切分为若干片段。

        Args:
            text: 待切分的文本（通常是 Loader 产出的规范化 Markdown）。
            trace: 可选的 ``TraceContext``，用于阶段 F 打点（当前占位）。
            **kwargs: 策略特定的扩展参数（如临时覆盖 ``chunk_size``）。

        Returns:
            切分后的文本片段列表。

        Raises:
            ValueError: 输入非字符串或为空时。
        """
        raise NotImplementedError
