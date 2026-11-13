"""Recursive Splitter 默认实现（阶段 B7.5）。

封装 LangChain 的 ``RecursiveCharacterTextSplitter``，作为默认切分器。

设计要点：
- 用 Markdown 结构感知的 separators（标题 / 代码块 / 段落 / 行 / 空格），
  优先在语义边界切分，尽量不打断标题与代码块。
- 通过 ``SplitterConfig`` 驱动 ``chunk_size`` / ``chunk_overlap`` / ``separators``。
- ``provider=recursive`` 时由 ``SplitterFactory`` 创建。
"""

from __future__ import annotations

from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.settings import SplitterConfig
from libs.splitter.base_splitter import BaseSplitter

__all__ = ["RecursiveSplitter", "register_recursive_provider", "DEFAULT_MARKDOWN_SEPARATORS"]


# Markdown 结构感知的默认分隔符（按优先级递降）。
DEFAULT_MARKDOWN_SEPARATORS: list[str] = [
    "\n## ",  # 二级标题
    "\n### ",  # 三级标题
    "\n#### ",  # 四级标题
    "\n```",  # 代码块边界
    "\n\n",  # 段落
    "\n",  # 行
    " ",  # 空格
    "",  # 字符兜底
]


class RecursiveSplitter(BaseSplitter):
    """基于 LangChain ``RecursiveCharacterTextSplitter`` 的默认切分器。"""

    def __init__(self, splitter_config: SplitterConfig, **kwargs: Any):
        self.splitter_config = splitter_config
        separators = splitter_config.separators
        if separators is None:
            separators = list(DEFAULT_MARKDOWN_SEPARATORS)
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=splitter_config.chunk_size,
            chunk_overlap=splitter_config.chunk_overlap,
            separators=separators,
            keep_separator=True,
            **kwargs,
        )

    def split_text(
        self,
        text: str,
        *,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> list[str]:
        if not isinstance(text, str):
            raise ValueError("split_text requires a string")
        if text == "":
            return []
        return self._splitter.split_text(text)


def register_recursive_provider() -> None:
    """向 ``SplitterFactory`` 注册 recursive provider。"""
    from libs.splitter.splitter_factory import SplitterFactory

    @SplitterFactory.register("recursive")
    def _ctor(splitter_config: SplitterConfig, **kwargs: Any) -> RecursiveSplitter:
        return RecursiveSplitter(splitter_config, **kwargs)
