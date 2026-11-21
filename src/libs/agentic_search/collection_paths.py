"""collection → 定界路径集推导（AGENTIC_COLLECTION_ISOLATION_SPEC AC1）。

sidecar 按文件系统路径检索、不认识 collection；本组件把「该 collection
应可见的文件」从摄取历史表 v2 的 success 记录（主键
``(source_path, collection)``，INGESTION_ROBUSTNESS_SPEC M1）推导为可发
给 sidecar 的绝对路径集：

1. 取 ``status = 'success'`` 且 ``collection`` 匹配的记录；
   ``source_path`` 为 ``None``（v1 迁移占位）的记录跳过；
2. 相对 ``source_path`` 按 ``resolve_project_root()`` 解析为绝对路径
   （ROOT_ANCHOR_SPEC：不按 CWD，与启动目录无关）；绝对路径原样；
3. 逐条调用 ``PathGuard.validate_paths([path])``（realpath 归一、白名单
   根、``denied_substrings``、符号链接逃逸、存在性五项检查；URL 形态因
   不存在于本地文件系统而剔除），捕获 ``PathGuardError`` 即剔除该条，
   单条坏记录不使整体失败；``validate_paths`` / ``filter_evidence_paths``
   均不改（PathGuard 契约冻结，S1.3）；
4. realpath 去重后按路径字符串升序输出（确定性）。

fail-closed：全部不合格时返回空列表；调用方按 D3 语义对空集零证据短路，
绝不静默退化为全量 ``allowed_roots``。定界收窄的是检索起点与引用证据，
不是安全边界（spec §8）。

与 ``libs.agentic_search`` 其余模块一致：不 ``import sirchmunk``。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

if __package__ in (None, ""):  # pragma: no cover - 直接运行兜底
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.project_root import resolve_project_root
from libs.agentic_search.path_guard import PathGuard, PathGuardError

__all__ = ["CollectionPathResolver", "IngestionHistoryStore", "normalize_history_source_path"]

logger = logging.getLogger(__name__)


class IngestionHistoryStore(Protocol):
    """摄取历史表访问句柄的最小协议（鸭子类型）。

    ``SQLiteIntegrityChecker``（``libs.loader.file_integrity``）原生满足；
    测试注入 fake。``list_processed()`` 每条记录含 ``source_path`` /
    ``collection`` / ``status`` 键（``source_path`` 对外为 ``None`` 的
    占位记录见 v1 迁移语义）。
    """

    def list_processed(self) -> list[dict]: ...


def normalize_history_source_path(source_path: str, project_root: str | Path | None = None) -> str:
    """历史表 ``source_path`` 的读时归一（AC1 定界与 AC5 上传防护同一规则）。

    绝对路径原样；相对路径按 ``project_root``（缺省
    ``resolve_project_root()``，**不按 CWD**）解析；统一 realpath
    （符号链接与 ``..`` 归一），使混合形态的存量记录可与目标绝对路径
    直接比较（AGENTIC_COLLECTION_ISOLATION_SPEC AC5）。
    """
    path = Path(source_path).expanduser()
    if not path.is_absolute():
        base = Path(project_root).expanduser().resolve() if project_root is not None else resolve_project_root()
        path = base / path
    return str(path.resolve())


class CollectionPathResolver:
    """collection 定界路径集推导器（spec §3 AC1）。

    Args:
        history: 摄取历史表访问句柄（:class:`IngestionHistoryStore`）。
        path_guard: 白名单守卫（``allowed_roots`` / ``denied_substrings``
            由调用方按配置装配，本组件不改其契约）。
        project_root: 相对 ``source_path`` 的解析基准；缺省
            ``resolve_project_root()``（RAG_PROJECT_ROOT 优先，否则按代码
            位置推导）。测试注入临时目录以覆盖默认值。

    历史句柄与守卫在构造时注入、可跨多次 ``resolve`` 复用；定界集每次
    调用现算（历史表内容变化即时生效），重复调用结果一致。
    """

    def __init__(
        self,
        history: IngestionHistoryStore,
        path_guard: PathGuard,
        project_root: str | Path | None = None,
    ) -> None:
        self._history = history
        self._guard = path_guard
        if project_root is None:
            self._project_root = resolve_project_root()
        else:
            self._project_root = Path(project_root).expanduser().resolve()

    def resolve(self, collection: str) -> list[str]:
        """返回该 collection 的定界绝对路径集（升序、realpath 去重）。

        Raises:
            ValueError: ``collection`` 非字符串或纯空白（调用方入口均已
                校验，此处兜底 fail-closed）。
        """
        if not isinstance(collection, str) or not collection.strip():
            raise ValueError(f"collection must be a non-empty string, got {collection!r}")
        scoped: set[str] = set()
        dropped = 0
        for record in self._history.list_processed():
            if record.get("status") != "success" or record.get("collection") != collection:
                continue
            source_path = record.get("source_path")
            if not isinstance(source_path, str) or not source_path.strip():
                continue  # v1 迁移占位记录（source_path 对外为 None）：跳过
            try:
                scoped.update(self._guard.validate_paths([self._normalize(source_path)]))
            except PathGuardError:
                dropped += 1
        if dropped:
            # 只记 collection 名与剔除条数，不记路径内容（spec §8 日志纪律）。
            logger.warning(
                "collection_paths: collection %r dropped %d unqualified source_path record(s)",
                collection,
                dropped,
            )
        return sorted(scoped)

    def _normalize(self, source_path: str) -> str:
        """相对路径按项目根解析为绝对路径（不按 CWD）；绝对路径原样。"""
        return normalize_history_source_path(source_path, self._project_root)
