"""Loader 抽象基类（阶段 C3）。

Loader 负责把原始文件解析为统一的 ``Document`` 对象（``text`` + ``metadata``，
类型契约见 ``core.types``），不负责切分。当前阶段仅实现 PDF 格式
（``libs.loader.pdf_loader.PdfLoader``）；后续新增格式只需实现本接口并按
文件扩展名注册到 Pipeline。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.types import Document

__all__ = ["BaseLoader"]


class BaseLoader(ABC):
    """文档加载抽象接口，所有格式实现需继承并实现 ``load``。"""

    @abstractmethod
    def load(self, path: str) -> Document:
        """解析文件为 ``Document``。

        Args:
            path: 文件路径。

        Returns:
            ``Document``（``metadata`` 至少包含 ``source_path``）。

        Raises:
            FileNotFoundError: 文件不存在。
        """
        raise NotImplementedError
