"""Splitter 抽象层包。

导入本包即触发默认 ``recursive`` provider 的注册。
"""

from libs.splitter.base_splitter import BaseSplitter
from libs.splitter.splitter_factory import SplitterFactory

# 触发 provider 注册。
from libs.splitter import recursive_splitter as _recursive_splitter  # noqa: F401

_recursive_splitter.register_recursive_provider()

__all__ = ["BaseSplitter", "SplitterFactory"]
