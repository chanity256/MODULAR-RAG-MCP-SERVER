"""PathGuard 白名单守卫 — 阶段 S1.3。

sirchmunk 的 ``paths`` 参数**不是安全边界**：ReAct 工具 ``file_read``
可读 LLM 请求的任意绝对路径，且 sidecar ``/api/v1/search`` 不执行
``SIRCHMUNK_ALLOWED_PATHS`` 校验（SIRCHMUNK_INTEGRATION_SPEC 4.4）。
因此白名单由本项目在调用方执行，双层防线：

1. **调用前**（:meth:`PathGuard.validate_paths`）：realpath 归一、
   符号链接逃逸拒绝、白名单根校验、``denied_substrings`` 强制排除；
   任一路径越权即整体拒绝（fail-closed，宁可误拒），**不发请求**。
2. **调用后**（:meth:`PathGuard.filter_evidence_paths`）：过滤证据/
   引用文件中落在白名单外的条目，防 prompt injection 经 ``file_read``
   读出的路径泄露。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Sequence

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

__all__ = ["PathGuard", "PathGuardError"]


class PathGuardError(ValueError):
    """路径白名单校验失败（fail-closed）。"""


class PathGuard:
    """路径白名单守卫（双层防线，spec 4.4）。

    Args:
        allowed_roots: 允许检索的根目录列表（必须是存在路径）。
        denied_substrings: 在白名单内仍强制排除的子串（对 realpath
            字符串匹配，例如 ``.env`` / ``.git`` / ``data/db``）。
    """

    def __init__(
        self,
        allowed_roots: Sequence[str],
        denied_substrings: Sequence[str] = (".env", ".git", "data/db"),
    ) -> None:
        if not allowed_roots:
            raise PathGuardError("allowed_roots must be non-empty (fail-closed)")
        self._denied = [d for d in denied_substrings if d]
        self._roots: list[Path] = []
        for raw in allowed_roots:
            if isinstance(raw, Path):
                raw = str(raw)
            if not isinstance(raw, str) or not raw.strip():
                raise PathGuardError(f"allowed root must be a non-empty string, got {raw!r}")
            root = Path(raw).expanduser().resolve()
            if not root.exists():
                raise PathGuardError(f"allowed root does not exist: {root}")
            self._roots.append(root)

    @property
    def allowed_roots(self) -> list[str]:
        return [str(r) for r in self._roots]

    # ------------------------------------------------------------------
    # 调用前校验
    # ------------------------------------------------------------------
    def validate_paths(self, paths: Sequence[str]) -> list[str]:
        """校验请求路径并返回 realpath 归一后的绝对路径列表。

        Raises:
            PathGuardError: 任一路径越权（白名单外 / 符号链接逃逸 /
                命中排除子串 / 非字符串）。
        """
        if not paths:
            raise PathGuardError("paths must be non-empty")
        validated: list[str] = []
        for raw in paths:
            if isinstance(raw, Path):
                raw = str(raw)
            if not isinstance(raw, str) or not raw.strip():
                raise PathGuardError(f"path must be a non-empty string, got {raw!r}")
            # expanduser + resolve()（realpath 语义）：符号链接指向白名单外
            # 时 resolve 后必然落在根外，从而被拒绝（逃逸防护）。
            resolved = Path(raw).expanduser().resolve()
            if not self._under_any_root(resolved):
                raise PathGuardError(
                    f"path is outside allowed roots: {raw!r} (resolved: {resolved})"
                )
            if self._denied_match(resolved):
                raise PathGuardError(f"path hits denied substring: {raw!r} (resolved: {resolved})")
            if not resolved.exists():
                raise PathGuardError(f"path does not exist: {raw!r} (resolved: {resolved})")
            normalized = str(resolved)
            if normalized not in validated:
                validated.append(normalized)
        return validated

    # ------------------------------------------------------------------
    # 调用后过滤
    # ------------------------------------------------------------------
    def filter_evidence_paths(self, paths: Iterable[str]) -> list[str]:
        """返回落在白名单内且不命中排除子串的路径（保序去重）。

        与 :meth:`validate_paths` 不同：越权条目**静默剔除**而非报错
        （调用已发生，过滤即第二层防线）。
        """
        kept: list[str] = []
        for raw in paths:
            if isinstance(raw, Path):
                raw = str(raw)
            if not isinstance(raw, str) or not raw.strip():
                continue
            # http(s):// 等 URL 证据保留（spec 4.4：sirchmunk 接受 URL 路径）。
            if raw.startswith(("http://", "https://", "ftp://")):
                if raw not in kept:
                    kept.append(raw)
                continue
            resolved = Path(raw).expanduser().resolve()
            if not self._under_any_root(resolved) or self._denied_match(resolved):
                continue
            normalized = str(resolved)
            if normalized not in kept:
                kept.append(normalized)
        return kept

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _under_any_root(self, resolved: Path) -> bool:
        return any(resolved == root or root in resolved.parents for root in self._roots)

    def _denied_match(self, resolved: Path) -> bool:
        text = str(resolved)
        return any(d in text for d in self._denied)
