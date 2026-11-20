"""白名单根目录预建（AGENTIC_UPLOAD_ROOTS_SPEC AR1，D1-a 入口建目录）。

背景：``PathGuard`` 对不存在的白名单根 fail-closed（``allowed root does
not exist``，既有测试锁定）。``data/uploads/`` 要等第一次上传才由
``save_uploads`` 创建，未上传过的环境里把 ``./data/uploads`` 加入
``allowed_roots`` 会让整个 agentic 工具不可用。

本模块在**进程入口**（``scripts/start_api.py`` / ``mcp_server.server``
/ ``scripts/query.py --agentic``）预建位于项目 ``data/`` 目录下的白名单
根；只建项目自己管理的数据目录，不替用户创建任意路径。PathGuard 与
配置模型不改（spec §3：守卫保持纯函数式校验，不在构造函数里做副作用）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.project_root import resolve_project_root

__all__ = ["ensure_local_roots"]

logger = logging.getLogger(__name__)


def ensure_local_roots(agentic: Any, base: Path | None = None) -> list[str]:
    """预建位于 ``data/`` 下的白名单根，返回本次实际创建的路径列表。

    Args:
        agentic: ``AgenticSearchConfig``（或提供 ``enabled`` /
            ``allowed_roots`` / ``denied_substrings`` 属性的对象）。
            ``enabled`` 为 false 时不做任何事。
        base: 视为「本项目管理的数据目录」的基准（缺省
            ``<项目根>/data``，ROOT_ANCHOR_SPEC RA2；项目根由
            ``core.project_root.resolve_project_root`` 解析，原为按 CWD）。
            只有解析后落在该基准之下的根才会被创建；测试经此注入临时目录。

    语义（spec §3）：

    - 根按 PathGuard 相同方式解析（``Path(raw).expanduser().resolve()``）；
    - 命中 ``denied_substrings`` 的根**不建**（如 ``data/db``）；
    - 基准之外的根**不碰**，仍由 PathGuard 按原契约报错；
    - 已存在的根不变；``OSError`` 只记 warning 不抛（入口不应因预建失败
      拒绝启动，PathGuard 会在真正调用时按既有语义兜底）。
    """
    if not getattr(agentic, "enabled", False):
        return []
    roots = list(getattr(agentic, "allowed_roots", []) or [])
    if not roots:
        return []
    denied = [d for d in (getattr(agentic, "denied_substrings", []) or []) if d]
    data_base = (
        base if base is not None else resolve_project_root() / "data"
    ).resolve()
    created: list[str] = []
    for raw in roots:
        if isinstance(raw, Path):
            raw = str(raw)
        if not isinstance(raw, str) or not raw.strip():
            continue
        resolved = Path(raw).expanduser().resolve()
        if resolved != data_base and data_base not in resolved.parents:
            continue
        text = str(resolved)
        if any(d in text for d in denied):
            continue
        if resolved.exists():
            continue
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("ensure_local_roots: failed to create %s: %s", resolved, exc)
            continue
        created.append(str(resolved))
        logger.info("ensure_local_roots: created allowed root %s", resolved)
    return created
