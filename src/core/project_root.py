"""项目根解析（ROOT_ANCHOR_SPEC RA1）。

为配置中的相对数据路径提供唯一的「项目根」锚点，使任意目录启动的入口
行为一致（相对路径不再按启动进程 CWD 解析）：

- 环境变量 ``RAG_PROJECT_ROOT`` 已设置时取其值（打包 / 非标准布局的覆盖出口）；
- 未设置时由代码位置推导（``Path(__file__).parents[2]``，同
  ``core/chat/prompts.py`` 与 ``scripts/start_api.py`` 的既有先例）。

纯路径计算：不做 IO（不 exists、不建目录）；建目录与落盘由调用方
（``load_settings`` / ``ensure_local_roots`` 等，RA2 / RA3 接入）负责。
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["PROJECT_ROOT_ENV", "resolve_project_root"]

PROJECT_ROOT_ENV = "RAG_PROJECT_ROOT"


def resolve_project_root() -> Path:
    """返回项目根目录：``RAG_PROJECT_ROOT`` 优先，否则按代码位置推导。

    环境变量为空串 / 空白时视为未设置，回退到代码位置推导。返回值恒为
    绝对路径；本函数不触碰文件系统（``resolve()`` 不要求路径存在）。
    """
    override = os.environ.get(PROJECT_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2]
