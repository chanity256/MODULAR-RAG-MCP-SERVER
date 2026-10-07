"""命令行摄取入口（阶段 C15，离线可用）。

用法（对齐 DEV_SPEC 阶段 I5 的验收命令形态）::

    python scripts/ingest.py --path tests/fixtures/sample_documents/ --collection test
    python scripts/ingest.py --path data/documents/manual.pdf --force

行为契约（C15 验收标准 + IR7.3/IR7.4 扩展）：
- ``--path``：目标文件或目录；目录时递归扫描受支持的扩展名（与
  ``ingestion.pipeline`` 的扩展名路由保持一致，含 ``.pdf``/``.md`` 等），
  文件按路径排序保证确定性；大小写不敏感（``.PDF`` 同样受理）。
- 目录扫描遵守 ``ingestion.source_filters``（IR7.4）：对扫描根目录的
  POSIX 相对路径做 fnmatch 匹配（目录段参与匹配），include 空 = 全部
  受理，exclude 优先；被过滤文件计入 ``filtered`` 且不摄取；
  ``--path`` 显式指向单文件时不过滤（用户明确意图）。
- ``--sync``/``--prune`` 的目录对比遵守同一套过滤（被过滤路径视为
  不存在；已入库的此类路径按既有 prune/deleted 语义清理）。
- ``--collection``：目标集合名（透传 ``IngestionPipeline.run``，写入每个
  chunk 的 ``metadata["collection"]``；默认 ``default``）。
- ``--force``：跳过完整性检查强制重摄取（幂等替换，不产生重复记录）。
- 重复运行：内容未变更的文件被完整性检查跳过（``skipped``），零成本。
- 退出码：``0`` = 全部成功或跳过（含空目录）；``1`` = 存在失败文件；
  ``2`` = 用法/配置错误（参数缺失、路径不存在、扩展名不受支持、
  配置加载失败）。

同步模式（INGESTION_ROBUSTNESS_SPEC M3，``--sync`` / ``--prune`` 互斥）::

    python scripts/ingest.py --path data/documents/ --collection c --sync [--dry-run]
    python scripts/ingest.py --path data/documents/ --collection c --prune [--dry-run]

- 对比基准 = 历史表 v2（``(source_path, collection)``）∪ Chroma
  ``metadata.source_path`` 反查，限定在 ``--path`` 范围内（目录前缀 / 单文件
  相等，按 ``abspath`` 归一比较，不做 realpath 展开）。
- 分类：``new``（无历史）/ ``modified``（哈希变化，或上次失败，或历史成功但
  存储无数据）/ ``unchanged`` / ``deleted``（磁盘上已不存在）/ ``stale``
  （历史 ``embedding_version`` 与当前配置不同，按 ``version_policy`` 告警并
  跳过，不自动重建）。
- ``--sync`` 先摄取 new + modified，再对 deleted 调
  ``DocumentManager.delete_document``；``--prune`` 只做删除，零摄取。
- ``--dry-run`` 只打印计划，不写任何存储；必须与 ``--sync``/``--prune`` 组合。

重试模式（M4 ``--retry-failed``）：重跑该集合历史表中 ``status="failed"``
的记录（``--path`` 可选，给出时限定范围），日志标注 ``failed_stage``。

可测试性：解析/收集/聚合/渲染逻辑全部放在可导入的纯函数中，
``main(argv, *, pipeline=...)`` 支持注入已构建的 pipeline（离线 e2e
注入确定性 embedding 即可全链路离线运行，见 ``tests/e2e/test_data_ingestion.py``）；
同步/重试模式还可注入 ``integrity_checker`` / ``document_manager`` / ``settings``。
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Collection, Iterable, Sequence

# 让 ``src`` 下的包在未安装时也可被直接 import（与根 ``main.py`` 同一模式，
# 支持 ``python scripts/ingest.py`` 直接运行）。
_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from core.settings import (  # noqa: E402
    ConfigError,
    Settings,
    load_settings,
    resolve_ingestion_versions,
)
from ingestion.pipeline import IngestionPipeline, IngestionResult  # noqa: E402

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "SUPPORTED_EXTENSIONS",
    "EXIT_OK",
    "EXIT_FAILURE",
    "EXIT_USAGE",
    "FileOutcome",
    "IngestSummary",
    "IngestPathError",
    "FileScan",
    "build_parser",
    "parse_args",
    "collect_files",
    "collect_files_detailed",
    "build_pipeline",
    "run_ingest",
    "aggregate",
    "render_summary",
    "SyncEntry",
    "SyncPlan",
    "PruneOutcome",
    "collect_and_diff",
    "render_plan",
    "run_prune",
    "render_prune_summary",
    "select_failed_records",
    "main",
]

#: 默认配置文件（仓库根 ``config/settings.yaml``，绝对路径不受 CWD 影响）。
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"

#: 受支持的源文件扩展名（小写；与 ``ingestion.pipeline`` 的 Loader 路由一致；
#: 单测中有 drift guard 保证两处不脱钩；``.md``/``.markdown`` 为 IR7.3 登记）。
SUPPORTED_EXTENSIONS = frozenset({".pdf", ".docx", ".pptx", ".xlsx", ".html", ".md", ".markdown"})

# 退出码语义。
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

#: 输出函数签名（默认 ``print``；测试注入 list.append 收集器）。
Emit = Callable[[str], None]


class IngestPathError(ValueError):
    """``--path`` 无效（不存在 / 不是文件或目录 / 单文件扩展名不受支持）。"""


# ---------------------------------------------------------------------------
# 结果模型
# ---------------------------------------------------------------------------


@dataclass
class FileOutcome:
    """单文件处置结果（成功/跳过复用 ``IngestionResult`` 字段，失败记录错误）。"""

    path: str
    status: str  # "success" | "skipped" | "failed"
    chunks: int = 0
    images: int = 0
    written: int = 0
    trace_id: str = ""
    elapsed_ms: float = 0.0
    error: str | None = None

    @classmethod
    def from_result(cls, result: IngestionResult) -> "FileOutcome":
        return cls(
            path=result.source_path,
            status=result.status,
            chunks=result.chunks,
            images=result.images,
            written=result.written,
            trace_id=result.trace_id,
            elapsed_ms=result.elapsed_ms,
        )

    @classmethod
    def from_error(cls, path: str, exc: Exception) -> "FileOutcome":
        return cls(path=path, status="failed", error=f"{type(exc).__name__}: {exc}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "chunks": self.chunks,
            "images": self.images,
            "written": self.written,
            "trace_id": self.trace_id,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }


@dataclass
class IngestSummary:
    """一次 CLI 运行的聚合结果（人读摘要 + ``to_dict`` 机读）。"""

    collection: str
    outcomes: list[FileOutcome] = field(default_factory=list)
    ingested: int = 0
    skipped: int = 0
    failed: int = 0
    chunks: int = 0
    images: int = 0
    written: int = 0
    #: 被目录扫描 source_filters 过滤掉的文件数（IR7.4；不出现在逐文件行中）。
    filtered: int = 0

    @property
    def total(self) -> int:
        return len(self.outcomes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "collection": self.collection,
            "total": self.total,
            "ingested": self.ingested,
            "skipped": self.skipped,
            "failed": self.failed,
            "chunks": self.chunks,
            "images": self.images,
            "written": self.written,
            "filtered": self.filtered,
            "outcomes": [o.to_dict() for o in self.outcomes],
        }


def aggregate(
    collection: str, outcomes: Sequence[FileOutcome], *, filtered: int = 0
) -> IngestSummary:
    """把逐文件结果聚合为汇总（chunks/images/written 不计失败文件）。

    ``filtered`` 是目录扫描阶段被 ``source_filters`` 排除的文件数
    （IR7.4），不计入 ``total``/``outcomes``。
    """
    outcome_list = list(outcomes)
    ok = [o for o in outcome_list if o.status == "success"]
    return IngestSummary(
        collection=collection,
        outcomes=outcome_list,
        ingested=len(ok),
        skipped=sum(1 for o in outcome_list if o.status == "skipped"),
        failed=sum(1 for o in outcome_list if o.status == "failed"),
        chunks=sum(o.chunks for o in ok),
        images=sum(o.images for o in ok),
        written=sum(o.written for o in ok),
        filtered=filtered,
    )


# ---------------------------------------------------------------------------
# 参数解析与文件收集
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器。

    ``--path`` 在语法层可选，由 :func:`parse_args` 校验：仅 ``--retry-failed``
    可省略。``--sync`` / ``--prune`` / ``--retry-failed`` 三者互斥。
    """
    parser = argparse.ArgumentParser(
        prog="ingest",
        description="摄取文档到本地索引（BM25 + 向量库 + 图片存储）。",
        epilog=f"supported extensions: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
    )
    parser.add_argument(
        "--path",
        default=None,
        help="目标文件或目录（目录时递归摄取受支持扩展名的文件；--retry-failed 时可省略）",
    )
    parser.add_argument(
        "--collection",
        default="default",
        help="目标集合名（写入 chunk metadata，默认 default）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="跳过完整性检查强制重摄取（幂等替换）",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"配置文件路径（默认 {DEFAULT_CONFIG_PATH}）",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="输出逐阶段进度（pipeline on_progress 回调内容）",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--sync",
        action="store_true",
        help="目录与存储对比：摄取新增/修改文件，清理磁盘上已消失的文档",
    )
    mode.add_argument(
        "--prune",
        action="store_true",
        help="只清理磁盘上已消失的文档（不摄取任何文件）",
    )
    mode.add_argument(
        "--retry-failed",
        action="store_true",
        help="重跑该集合历史表中 status=failed 的记录（--path 可选，用于限定范围）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="与 --sync/--prune 组合：只打印计划动作，不写任何存储",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析 argv（缺省取 ``sys.argv[1:]``；非法参数/组合由 argparse 退出码 2 处理）。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.path is None and not args.retry_failed:
        parser.error("the following arguments are required: --path")
    if args.dry_run and not (args.sync or args.prune):
        parser.error("--dry-run must be combined with --sync or --prune")
    if args.force and (args.sync or args.prune):
        parser.error("--force cannot be combined with --sync/--prune (sync decides per file)")
    return args


@dataclass
class FileScan:
    """目录/单文件扫描结果（IR7.4）。

    ``files`` 是过滤后待摄取的文件清单；``filtered`` 是被
    ``source_filters`` 排除的文件数（不管扩展名是否受支持——被过滤路径
    视为不存在，先过滤后选扩展名）。
    """

    files: list[Path] = field(default_factory=list)
    filtered: int = 0


def _rel_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _hit_any(rel: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for pattern in patterns)


# Note: 过滤先于扩展名筛选 / sync 同语义 / filtered 计数口径 — 见
# .agents/notes/implemented/feature/2026-10-07-source-filters-before-extension.md
def _is_filtered(rel: str, include: Sequence[str], exclude: Sequence[str]) -> bool:
    """source_filters 匹配语义（IR7.4，对扫描根目录的 POSIX 相对路径）。

    - include 空 = 全部受理；非空时至少命中一个 include 才受理。
    - exclude 命中即排除（与 include 同时命中时 exclude 优先）。
    - 目录段参与匹配：``fnmatch`` 的 ``*`` 可跨 ``/``，因此
      ``.obsidian/attachments/x.png`` 命中 ``.obsidian/**``。
    """
    if include and not _hit_any(rel, include):
        return True
    return bool(exclude) and _hit_any(rel, exclude)


def collect_files_detailed(
    path: str | Path,
    *,
    include_patterns: Sequence[str] | None = None,
    exclude_patterns: Sequence[str] | None = None,
) -> FileScan:
    """把 ``--path`` 解析为待摄取文件列表 + 被过滤计数（IR7.4）。

    - 单文件：扩展名必须受支持（否则单文件场景无从跳过，直接报错）；
      **不做 source_filters 过滤**（用户显式指定的路径视为明确意图）。
    - 目录：先按 ``source_filters`` 过滤（include/exclude 对扫描根目录的
      POSIX 相对路径做 fnmatch），再按扩展名收集；被过滤文件计入
      ``filtered``（无论扩展名是否受支持），其余非支持扩展名静默忽略；
      按路径排序保证确定性。

    Args:
        path: 目标文件或目录。
        include_patterns: 受理 glob（None/空 = 全部受理）。
        exclude_patterns: 排除 glob（None/空 = 不排除）。

    Raises:
        IngestPathError: 路径不存在、既非文件也非目录、或单文件扩展名
            不受支持。
    """
    target = Path(path).expanduser()
    if not target.exists():
        raise IngestPathError(f"path does not exist: {target}")
    if target.is_file():
        if target.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise IngestPathError(
                f"unsupported file extension {target.suffix!r} for {target} "
                f"(supported: {sorted(SUPPORTED_EXTENSIONS)})"
            )
        return FileScan(files=[target])
    if target.is_dir():
        include = list(include_patterns or ())
        exclude = list(exclude_patterns or ())
        files: list[Path] = []
        filtered = 0
        for candidate in target.rglob("*"):
            if not candidate.is_file():
                continue
            if _is_filtered(_rel_posix(candidate, target), include, exclude):
                filtered += 1
                continue
            if candidate.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(candidate)
        return FileScan(files=sorted(files), filtered=filtered)
    raise IngestPathError(f"path is neither a file nor a directory: {target}")


def collect_files(
    path: str | Path,
    *,
    include_patterns: Sequence[str] | None = None,
    exclude_patterns: Sequence[str] | None = None,
) -> list[Path]:
    """:func:`collect_files_detailed` 的薄封装（只要文件清单）。

    - 单文件：扩展名必须受支持（否则单文件场景无从跳过，直接报错）。
    - 目录：递归收集受支持扩展名的文件，按路径排序保证确定性；
      非 ``source_filters`` 过滤、非支持扩展名（如 ``.txt``）静默忽略。

    Raises:
        IngestPathError: 路径不存在、既非文件也非目录、或单文件扩展名
            不受支持。
    """
    return collect_files_detailed(
        path,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
    ).files


def _source_filter_args(settings: Settings | None) -> dict[str, list[str] | None]:
    """从配置提取 ``ingestion.source_filters`` 的 collect 关键字参数。

    ``settings`` 为 None（未加载配置，如注入 fake pipeline 的单测）时返回
    None 表示不过滤（对齐 IR7.4 前「全受理」基线）。
    """
    if settings is None:
        return {"include_patterns": None, "exclude_patterns": None}
    filters = settings.ingestion.source_filters
    return {
        "include_patterns": list(filters.include_patterns),
        "exclude_patterns": list(filters.exclude_patterns),
    }


# ---------------------------------------------------------------------------
# Pipeline 构建与执行
# ---------------------------------------------------------------------------


@dataclass
class StorageComponents:
    """同步/重试模式下 pipeline 与 ``DocumentManager`` 共享的存储实例。"""

    integrity_checker: Any
    bm25_indexer: Any
    vector_upserter: Any
    image_storage: Any


def build_storage(settings: Settings) -> StorageComponents:
    """按配置构建四个存储组件（不触碰 embedding/LLM provider）。"""
    from ingestion.storage import BM25Indexer, ImageStorage, VectorUpserter
    from libs.loader.file_integrity import SQLiteIntegrityChecker

    return StorageComponents(
        integrity_checker=SQLiteIntegrityChecker(),
        bm25_indexer=BM25Indexer(settings),
        vector_upserter=VectorUpserter(settings),
        image_storage=ImageStorage(),
    )


def build_pipeline(
    settings: Settings, *, storage: StorageComponents | None = None
) -> IngestionPipeline:
    """按 ``Settings`` 构建默认 pipeline（组件惰性创建，见 C14）。

    给出 ``storage`` 时注入共享的存储实例，使 CLI 的同步/删除动作与摄取
    操作同一组连接。
    """
    if storage is None:
        return IngestionPipeline(settings)
    return IngestionPipeline(
        settings,
        integrity_checker=storage.integrity_checker,
        bm25_indexer=storage.bm25_indexer,
        vector_upserter=storage.vector_upserter,
        image_storage=storage.image_storage,
    )


def run_ingest(
    pipeline: Any,
    files: Sequence[Path | str],
    collection: str,
    *,
    force: bool = False,
    forced: Collection[str] = (),
    verbose: bool = False,
    filtered: int = 0,
    out: Emit | None = None,
) -> IngestSummary:
    """逐文件调用 ``pipeline.run`` 并聚合结果（单文件失败不中断批次）。

    Args:
        pipeline: 任何具备 ``run(source_path, collection=..., on_progress=...,
            force=...)`` 签法的对象（真 ``IngestionPipeline`` 或测试 fake）。
        files: 待摄取文件（已排序、已按 ``source_filters`` 过滤）。
        collection: 目标集合名（透传 pipeline）。
        force: 强制重摄取（透传 pipeline）。
        forced: 额外按文件强制重摄取的路径集合（``str(path)`` 命中即 force）。
        verbose: True 时把 ``on_progress`` 回调内容打印为缩进行。
        filtered: 目录扫描被 ``source_filters`` 排除的文件数（IR7.4，
            仅计入汇总，不产生逐文件行）。
        out: 输出函数（缺省 ``print``）。

    Returns:
        聚合后的 :class:`IngestSummary`。
    """
    emit = print if out is None else out
    outcomes: list[FileOutcome] = []
    total = len(files)

    for index, file_path in enumerate(files, start=1):
        prefix = f"[{index}/{total}]"

        def on_progress(stage: str, current: int, stage_total: int) -> None:
            if verbose:
                emit(f"  {prefix} {stage} {current}/{stage_total}")

        try:
            result = pipeline.run(
                str(file_path),
                collection=collection,
                on_progress=on_progress,
                force=force or str(file_path) in forced,
            )
        except Exception as exc:  # 单文件失败映射为 failed，不中断批次
            outcome = FileOutcome.from_error(str(file_path), exc)
            emit(f"{prefix} failed  {file_path}  error={outcome.error}")
        else:
            outcome = FileOutcome.from_result(result)
            if outcome.status == "skipped":
                emit(
                    f"{prefix} skipped {file_path}  "
                    f"(unchanged; use --force to re-ingest)"
                )
            else:
                emit(
                    f"{prefix} success {file_path}  chunks={outcome.chunks} "
                    f"images={outcome.images} written={outcome.written} "
                    f"trace={outcome.trace_id} ({outcome.elapsed_ms:.1f}ms)"
                )
        outcomes.append(outcome)

    return aggregate(collection, outcomes, filtered=filtered)


def render_summary(summary: IngestSummary) -> str:
    """渲染人类可读的汇总块（失败文件逐条列出错误）。"""
    bar = "=" * 64
    lines = [
        bar,
        f"ingest summary: collection={summary.collection!r}",
        f"  files  : {summary.total} total | {summary.ingested} ingested | "
        f"{summary.skipped} skipped | {summary.failed} failed",
        f"  filtered: {summary.filtered} (excluded by source_filters)",
        f"  chunks : {summary.chunks} | images: {summary.images} | "
        f"vectors written: {summary.written}",
    ]
    if summary.failed:
        lines.append("  failed files:")
        for outcome in summary.outcomes:
            if outcome.status == "failed":
                lines.append(f"    - {outcome.path}: {outcome.error}")
    lines.append(bar)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 同步模式（M3：--sync / --prune / --dry-run）
# ---------------------------------------------------------------------------

ACTION_NEW = "new"
ACTION_MODIFIED = "modified"
ACTION_UNCHANGED = "unchanged"
ACTION_DELETED = "deleted"
ACTION_STALE = "stale"

_DEFAULT_COLLECTION = "default"
# 迁移自 v1 的记录版本列为该占位值，无法判定是否不兼容，不视为 stale。
_LEGACY_VERSION = "legacy"


@dataclass(frozen=True)
class SyncEntry:
    """计划中的单条动作。

    ``path`` 是交给 ``pipeline.run`` / ``delete_document`` 的原始路径串：
    有历史记录时取记录里的 ``source_path``，保证与各存储的键逐字一致。
    """

    path: str
    action: str
    reason: str = ""
    force: bool = False


@dataclass
class SyncPlan:
    """目录 vs 存储态的对比结果。"""

    collection: str
    path: str
    entries: list[SyncEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    on_embedding_mismatch: str = "stale"

    def by_action(self, action: str) -> list[SyncEntry]:
        return [entry for entry in self.entries if entry.action == action]


@dataclass
class PruneOutcome:
    """单个已消失文档的删除结果。"""

    path: str
    chroma_deleted: int = 0
    bm25_deleted: int = 0
    images_deleted: int = 0
    integrity_removed: bool = False
    error: str | None = None


def _norm(path: str | Path) -> str:
    """比较用的路径键：``abspath`` + ``normpath``，不展开符号链接。"""
    return os.path.normpath(os.path.abspath(os.path.expanduser(str(path))))


def _scope_of(path: str | Path) -> tuple[str, bool | None]:
    """``(归一路径, 是否目录)``；路径已不存在时第二项为 None。"""
    target = Path(path).expanduser()
    if not target.exists():
        return _norm(target), None
    return _norm(target), target.is_dir()


def _in_scope(source: str, scope: str, scope_is_dir: bool | None) -> bool:
    key = _norm(source)
    under = key.startswith(scope.rstrip(os.sep) + os.sep)
    if scope_is_dir is None:
        # 已从磁盘消失的范围无法判定原先是文件还是目录，两种都匹配。
        return key == scope or under
    if scope_is_dir:
        return under
    return key == scope


def _history_in_scope(
    integrity: Any, collection: str, scope: str, scope_is_dir: bool
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in integrity.list_processed():
        source = record.get("source_path")
        if not source:
            continue
        if (record.get("collection") or _DEFAULT_COLLECTION) != collection:
            continue
        if not _in_scope(source, scope, scope_is_dir):
            continue
        grouped.setdefault(_norm(source), []).append(record)
    return grouped


def _filtered_source(
    raw: str,
    scope: str,
    scope_is_dir: bool | None,
    include: Sequence[str],
    exclude: Sequence[str],
) -> bool:
    """判断历史/存储里的 ``source_path`` 是否命中 source_filters（IR7.4）。

    被过滤路径视为不存在：``--sync``/``--prune`` 的对比把它按既有
    deleted 语义清理。仅当扫描范围是目录时参与（单文件范围是用户显式
    指定，不过滤）；范围已从磁盘消失时无法判定原布局，不过滤（保持
    「prune 一切」语义）。
    """
    if scope_is_dir is not True or not (include or exclude):
        return False
    try:
        rel = Path(os.path.relpath(raw, scope)).as_posix()
    except ValueError:  # Windows 跨盘符等不可比较场景：按不过滤处理
        return False
    if rel == "." or rel.startswith("../"):
        return False
    return _is_filtered(rel, include, exclude)


def collect_and_diff(
    path: str | Path,
    collection: str,
    *,
    integrity: Any,
    files: Sequence[Path] | None = None,
    store_sources: Iterable[str] | None = None,
    current_embedding_version: str | None = None,
    on_embedding_mismatch: str = "stale",
    include_ingest: bool = True,
    include_patterns: Sequence[str] | None = None,
    exclude_patterns: Sequence[str] | None = None,
) -> SyncPlan:
    """对比 ``--path`` 下的文件与该集合的存储态，产出只读计划（不写任何存储）。

    Args:
        path: 扫描范围（文件或目录）。
        collection: 目标集合。
        integrity: 历史表 v2（``list_processed`` / ``compute_sha256``）。
        files: 已收集的文件（缺省调 :func:`collect_files`）。
        store_sources: Chroma 反查得到的该集合 ``source_path`` 清单；
            None 表示未反查（跳过「历史成功但存储缺失」软校验）。
        current_embedding_version: 当前配置的 embedding 版本；None 时不做
            stale 判定。
        on_embedding_mismatch: ``stale`` / ``rebuild``（只影响提示文案，
            两者都不自动摄取）。
        include_ingest: False 时只判定 ``deleted``（``--prune`` 不需要哈希）。
        include_patterns / exclude_patterns: ``source_filters`` glob（IR7.4，
            与 ``collect_files`` 同语义）；已入库但被过滤的路径按 deleted
            清理。

    Raises:
        IngestPathError: ``path`` 无效（同 :func:`collect_files`）。
    """
    if files is not None:
        scanned_files = list(files)
    elif not include_ingest and not Path(path).expanduser().exists():
        scanned_files = []
    else:
        scanned_files = collect_files(
            path,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
        )
    scope, scope_is_dir = _scope_of(path)
    include = list(include_patterns or ())
    exclude = list(exclude_patterns or ())
    plan = SyncPlan(
        collection=collection, path=str(path), on_embedding_mismatch=on_embedding_mismatch
    )
    scanned = {_norm(f): str(f) for f in scanned_files}
    history = _history_in_scope(integrity, collection, scope, scope_is_dir)
    stored: dict[str, set[str]] | None = None
    if store_sources is not None:
        stored = {}
        for source in store_sources:
            if source and _in_scope(source, scope, scope_is_dir):
                stored.setdefault(_norm(source), set()).add(str(source))

    if include_ingest:
        for key, raw in sorted(scanned.items(), key=lambda kv: kv[1]):
            records = history.get(key, [])
            if not records:
                if stored is not None and key in stored:
                    plan.warnings.append(
                        f"{raw}: store has chunks but no history record; re-ingesting"
                    )
                plan.entries.append(SyncEntry(raw, ACTION_NEW, "not in history"))
                continue
            record = records[0]
            run_path = str(record["source_path"])
            recorded = record.get("embedding_version")
            if (
                current_embedding_version
                and recorded
                and recorded != _LEGACY_VERSION
                and recorded != current_embedding_version
            ):
                plan.entries.append(
                    SyncEntry(
                        run_path,
                        ACTION_STALE,
                        f"embedding_version recorded={recorded!r} current={current_embedding_version!r}",
                    )
                )
                continue
            file_hash = integrity.compute_sha256(raw)
            if record.get("file_hash") != file_hash:
                plan.entries.append(SyncEntry(run_path, ACTION_MODIFIED, "content changed"))
            elif record.get("status") != "success":
                stage = record.get("failed_stage")
                detail = f" at stage {stage!r}" if stage else ""
                plan.entries.append(
                    SyncEntry(
                        run_path,
                        ACTION_MODIFIED,
                        f"last run {record.get('status')}{detail}",
                    )
                )
            elif stored is not None and key not in stored:
                plan.warnings.append(
                    f"{run_path}: history says success but store has no chunks; re-ingesting"
                )
                plan.entries.append(
                    SyncEntry(run_path, ACTION_MODIFIED, "missing in store", force=True)
                )
            else:
                plan.entries.append(SyncEntry(run_path, ACTION_UNCHANGED))

    known = set(history) | set(stored or {})
    for key in sorted(known):
        if key in scanned:
            continue
        raws = {str(r["source_path"]) for r in history.get(key, [])}
        raws |= (stored or {}).get(key, set())
        for raw in sorted(raws):
            if os.path.exists(raw):
                if _filtered_source(raw, scope, scope_is_dir, include, exclude):
                    pass  # 被过滤路径视为不存在 → 走既有 deleted 清理（IR7.4）
                else:
                    plan.warnings.append(
                        f"{raw}: exists on disk but was not scanned "
                        "(unsupported extension?); left untouched"
                    )
                    continue
            in_history = key in history
            in_store = stored is not None and key in stored
            where = "+".join(n for n, hit in (("history", in_history), ("store", in_store)) if hit)
            plan.entries.append(SyncEntry(raw, ACTION_DELETED, f"missing on disk ({where})"))
    return plan


def render_plan(plan: SyncPlan, *, mode: str = "sync", dry_run: bool = False) -> str:
    """渲染人类可读的计划清单（``mode`` 为 ``sync`` 或 ``prune``）。"""
    bar = "=" * 64
    suffix = " (dry-run, nothing written)" if dry_run else ""
    lines = [
        bar,
        f"{mode} plan{suffix}: collection={plan.collection!r} path={plan.path!r}",
    ]

    def section(label: str, action: str, marker: str) -> None:
        entries = plan.by_action(action)
        lines.append(f"  {label}: {len(entries)}")
        for entry in entries:
            reason = f"  ({entry.reason})" if entry.reason else ""
            lines.append(f"    {marker} {entry.path}{reason}")

    if mode == "sync":
        section("ingest  (new)      ", ACTION_NEW, "+")
        section("replace (modified) ", ACTION_MODIFIED, "~")
        section("skip    (unchanged)", ACTION_UNCHANGED, "=")
        stale = plan.by_action(ACTION_STALE)
        if stale:
            section(f"stale   (policy={plan.on_embedding_mismatch})", ACTION_STALE, "!")
            if plan.on_embedding_mismatch == "rebuild":
                lines.append(
                    "    -> embedding config changed: full rebuild required "
                    "(delete the collection and re-ingest with --force; not automatic)"
                )
            else:
                lines.append("    -> skipped: embedding config differs from the recorded version")
    section("prune   (deleted)  ", ACTION_DELETED, "-")
    if plan.warnings:
        lines.append("  warnings:")
        lines.extend(f"    * {warning}" for warning in plan.warnings)
    lines.append(bar)
    return "\n".join(lines)


def run_prune(
    manager: Any,
    entries: Sequence[SyncEntry],
    collection: str,
    *,
    out: Emit | None = None,
) -> list[PruneOutcome]:
    """逐条调 ``manager.delete_document``（单条失败不中断批次）。"""
    emit = print if out is None else out
    outcomes: list[PruneOutcome] = []
    total = len(entries)
    for index, entry in enumerate(entries, start=1):
        prefix = f"[prune {index}/{total}]"
        try:
            result = manager.delete_document(entry.path, collection)
        except Exception as exc:  # 单条失败记录后继续
            outcome = PruneOutcome(path=entry.path, error=f"{type(exc).__name__}: {exc}")
            emit(f"{prefix} failed  {entry.path}  error={outcome.error}")
        else:
            outcome = PruneOutcome(
                path=entry.path,
                chroma_deleted=int(getattr(result, "chroma_deleted", 0) or 0),
                bm25_deleted=int(getattr(result, "bm25_deleted", 0) or 0),
                images_deleted=int(getattr(result, "images_deleted", 0) or 0),
                integrity_removed=bool(getattr(result, "integrity_removed", False)),
            )
            emit(
                f"{prefix} deleted {entry.path}  chroma={outcome.chroma_deleted} "
                f"bm25={outcome.bm25_deleted} images={outcome.images_deleted} "
                f"history={'removed' if outcome.integrity_removed else 'none'}"
            )
        outcomes.append(outcome)
    return outcomes


def render_prune_summary(outcomes: Sequence[PruneOutcome]) -> str:
    ok = [o for o in outcomes if o.error is None]
    failed = [o for o in outcomes if o.error is not None]
    lines = [
        f"prune summary: {len(ok)} deleted | {len(failed)} failed | "
        f"chroma={sum(o.chroma_deleted for o in ok)} bm25={sum(o.bm25_deleted for o in ok)} "
        f"images={sum(o.images_deleted for o in ok)}"
    ]
    for outcome in failed:
        lines.append(f"    - {outcome.path}: {outcome.error}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 重试模式（M4：--retry-failed）
# ---------------------------------------------------------------------------


def select_failed_records(
    integrity: Any, collection: str, path: str | Path | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    """选出该集合 ``status="failed"`` 的历史记录（按 ``source_path`` 排序）。

    ``path`` 给出时限定在其范围内（目录前缀 / 单文件相等）。路径未知的占位
    记录无法重跑，计入告警返回。

    Returns:
        ``(records, warnings)``。
    """
    scope = _scope_of(path) if path is not None else None
    selected: list[dict[str, Any]] = []
    warnings: list[str] = []
    for record in integrity.list_processed():
        if record.get("status") != "failed":
            continue
        if (record.get("collection") or _DEFAULT_COLLECTION) != collection:
            continue
        source = record.get("source_path")
        if not source:
            warnings.append(
                f"failed record with unknown source path (hash={str(record.get('file_hash'))[:12]}...) "
                "cannot be retried"
            )
            continue
        if scope is not None and not _in_scope(source, *scope):
            continue
        selected.append(record)
    selected.sort(key=lambda r: str(r["source_path"]))
    return selected, warnings


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


@dataclass
class _Resources:
    pipeline: Any
    integrity: Any
    manager: Any | None
    bm25: Any | None
    settings: Settings | None


def _resolve_resources(
    args: argparse.Namespace,
    *,
    pipeline: Any | None,
    integrity_checker: Any | None,
    document_manager: Any | None,
    settings: Settings | None,
    need_manager: bool,
    emit: Emit,
) -> _Resources | int:
    """装配同步/重试模式所需组件；失败时返回 ``EXIT_USAGE``。"""
    if pipeline is None:
        if settings is None:
            try:
                settings = load_settings(args.config)
            except (ConfigError, FileNotFoundError) as exc:
                emit(f"[usage] 配置加载失败: {exc}")
                return EXIT_USAGE
        try:
            storage = build_storage(settings)
        except Exception as exc:
            emit(f"[usage] 存储初始化失败: {type(exc).__name__}: {exc}")
            return EXIT_USAGE
        if integrity_checker is not None:
            storage.integrity_checker = integrity_checker
        pipeline = build_pipeline(settings, storage=storage)
        manager = document_manager
        if manager is None and need_manager:
            from ingestion.document_manager import DocumentManager

            manager = DocumentManager(
                chroma_store=storage.vector_upserter.store,
                bm25_indexer=storage.bm25_indexer,
                image_storage=storage.image_storage,
                file_integrity=storage.integrity_checker,
                settings=settings,
            )
        return _Resources(
            pipeline, storage.integrity_checker, manager, storage.bm25_indexer, settings
        )

    if settings is None:
        candidate = getattr(pipeline, "_settings", None)
        settings = candidate if isinstance(candidate, Settings) else None
    if integrity_checker is not None and (document_manager is not None or not need_manager):
        return _Resources(pipeline, integrity_checker, document_manager, None, settings)

    # 注入的真 IngestionPipeline：复用其存储实例，避免同进程内多份连接互相覆盖。
    ensure = getattr(pipeline, "_ensure_components", None)
    if ensure is None:
        emit(
            "[usage] injected pipeline exposes no storage components; "
            "inject integrity_checker/document_manager"
        )
        return EXIT_USAGE
    ensure()
    integrity = integrity_checker or pipeline._integrity_checker
    manager = document_manager
    bm25 = None
    if manager is None and need_manager:
        from ingestion.document_manager import DocumentManager

        bm25 = pipeline._bm25_indexer
        manager = DocumentManager(
            chroma_store=pipeline._vector_upserter.store,
            bm25_indexer=bm25,
            image_storage=pipeline._image_storage,
            file_integrity=integrity,
            settings=settings,
        )
    return _Resources(pipeline, integrity, manager, bm25, settings)


@dataclass
class _StoredDocument:
    source_path: str


class _ReadOnlyCatalog:
    """dry-run 的 ``list_documents`` 替身：只读向量库，缺失时视为空。

    ``DocumentManager.list_documents`` 会顺带懒建图片存储（新建 SQLite 与
    图片目录），dry-run 不能有这种副作用。
    """

    def __init__(self, store: Any | None) -> None:
        self._store = store

    def list_documents(self, collection: str | None = None) -> list[_StoredDocument]:
        if self._store is None:
            return []
        filters = {"collection": collection} if collection else None
        sources: set[str] = set()
        for record in self._store.get_by_metadata(filters):
            metadata = record.get("metadata") or {}
            source = metadata.get("source_path") or metadata.get("source")
            if source:
                sources.add(str(source))
        return [_StoredDocument(source) for source in sorted(sources)]


def _resolve_dry_run_resources(
    args: argparse.Namespace, *, settings: Settings | None, emit: Emit
) -> _Resources | int:
    """dry-run 装配：只打开已存在的存储，不存在的按空处理，绝不新建文件。"""
    if settings is None:
        try:
            settings = load_settings(args.config)
        except (ConfigError, FileNotFoundError) as exc:
            emit(f"[usage] 配置加载失败: {exc}")
            return EXIT_USAGE
    from libs.loader.file_integrity import DEFAULT_DB_PATH, SQLiteIntegrityChecker
    from libs.vector_store import VectorStoreFactory

    try:
        history_db = DEFAULT_DB_PATH if Path(DEFAULT_DB_PATH).is_file() else ":memory:"
        integrity = SQLiteIntegrityChecker(history_db)
        vs = settings.vector_store
        persist = vs.persist_path or "./data/db/chroma"
        if vs.backend == "chroma" and not Path(persist).exists():
            store = None
        else:
            store = VectorStoreFactory.create(settings)
    except Exception as exc:
        emit(f"[usage] 存储初始化失败: {type(exc).__name__}: {exc}")
        return EXIT_USAGE
    return _Resources(None, integrity, _ReadOnlyCatalog(store), None, settings)


def _reload_bm25(bm25: Any | None) -> None:
    # 共享的 BM25 实例是「整份内存态 + 整体 save」：删除前必须与磁盘对齐，
    # 否则空的内存索引会在 save 时覆盖掉磁盘上的全量索引。
    if bm25 is None:
        return
    try:
        bm25.load()
    except FileNotFoundError:
        pass


# Note: 注入 pipeline 未注入 settings 时过滤配置的回退缺口 — 见
# .agents/notes/proposed/process/2026-10-07-sync-filter-fallback-injected-pipeline.md
def _main_sync(
    args: argparse.Namespace, emit: Emit, **injected: Any
) -> int:
    mode = "sync" if args.sync else "prune"
    # 目录扫描过滤（IR7.4）需要配置；未注入 pipeline 时在此统一加载一次，
    # 并回填 injected 供资源装配复用（避免二次读取配置）。
    settings = injected.get("settings")
    if settings is None and injected.get("pipeline") is None:
        try:
            settings = load_settings(args.config)
        except (ConfigError, FileNotFoundError) as exc:
            emit(f"[usage] 配置加载失败: {exc}")
            return EXIT_USAGE
        injected = {**injected, "settings": settings}

    if args.prune and not Path(args.path).expanduser().exists():
        # 整个文件/目录已被删除时 --prune 仍需能清理它留下的记录。
        emit(f"[info] {args.path} no longer exists on disk; pruning everything recorded under it")
        files: list[Path] = []
    else:
        try:
            scan = collect_files_detailed(args.path, **_source_filter_args(settings))
            files = scan.files
        except IngestPathError as exc:
            emit(f"[usage] {exc}")
            return EXIT_USAGE

    if args.dry_run and all(
        injected.get(key) is None
        for key in ("pipeline", "integrity_checker", "document_manager")
    ):
        resources = _resolve_dry_run_resources(
            args, settings=injected.get("settings"), emit=emit
        )
    else:
        resources = _resolve_resources(args, need_manager=True, emit=emit, **injected)
    if isinstance(resources, int):
        return resources

    try:
        store_sources: list[str] | None = [
            str(doc.source_path) for doc in resources.manager.list_documents(args.collection)
        ]
    except Exception as exc:
        emit(f"[warn] store reverse lookup failed, comparing against history only: {exc}")
        store_sources = None

    current_embedding: str | None = None
    policy = "stale"
    if resources.settings is not None:
        current_embedding = resolve_ingestion_versions(resources.settings)["embedding_version"]
        policy = resources.settings.ingestion.version_policy.on_embedding_mismatch

    try:
        plan = collect_and_diff(
            args.path,
            args.collection,
            integrity=resources.integrity,
            files=files,
            store_sources=store_sources,
            current_embedding_version=current_embedding,
            on_embedding_mismatch=policy,
            include_ingest=args.sync,
            **_source_filter_args(settings),
        )
    except NotImplementedError:
        emit("[usage] integrity checker does not support history listing (list_processed)")
        return EXIT_USAGE

    emit(render_plan(plan, mode=mode, dry_run=args.dry_run))
    if args.dry_run:
        return EXIT_OK

    failed = False
    if args.sync:
        to_ingest = plan.by_action(ACTION_NEW) + plan.by_action(ACTION_MODIFIED)
        if to_ingest:
            summary = run_ingest(
                resources.pipeline,
                [entry.path for entry in to_ingest],
                args.collection,
                forced={entry.path for entry in to_ingest if entry.force},
                verbose=args.verbose,
                out=emit,
            )
            emit(render_summary(summary))
            failed = summary.failed > 0

    deleted = plan.by_action(ACTION_DELETED)
    if deleted:
        _reload_bm25(resources.bm25)
        outcomes = run_prune(resources.manager, deleted, args.collection, out=emit)
        emit(render_prune_summary(outcomes))
        failed = failed or any(o.error is not None for o in outcomes)
    return EXIT_FAILURE if failed else EXIT_OK


def _main_retry(args: argparse.Namespace, emit: Emit, **injected: Any) -> int:
    if args.path is not None and not Path(args.path).expanduser().exists():
        emit(f"[usage] path does not exist: {Path(args.path).expanduser()}")
        return EXIT_USAGE

    resources = _resolve_resources(args, need_manager=False, emit=emit, **injected)
    if isinstance(resources, int):
        return resources

    try:
        records, warnings = select_failed_records(
            resources.integrity, args.collection, args.path
        )
    except NotImplementedError:
        emit("[usage] integrity checker does not support history listing (list_processed)")
        return EXIT_USAGE
    for warning in warnings:
        emit(f"[warn] {warning}")
    scope = f" under {args.path}" if args.path is not None else ""

    present: list[dict[str, Any]] = []
    for record in records:
        if os.path.isfile(str(record["source_path"])):
            present.append(record)
        else:
            emit(
                f"[warn] {record['source_path']}: failed record but file no longer on disk; "
                "not retried (use --prune to clean it up)"
            )
    records = present

    if not records:
        emit(f"[info] no retryable failed records in collection {args.collection!r}{scope}")
        return EXIT_OK

    emit(f"[retry] {len(records)} failed record(s) in collection {args.collection!r}{scope}")
    for record in records:
        stage = record.get("failed_stage")
        stage_note = f"previously failed at stage {stage!r}" if stage else "failed stage unknown"
        error = record.get("error_msg")
        emit(f"  [retry] {record['source_path']}  ({stage_note}{': ' + error if error else ''})")

    summary = run_ingest(
        resources.pipeline,
        [str(record["source_path"]) for record in records],
        args.collection,
        force=args.force,
        verbose=args.verbose,
        out=emit,
    )
    emit(render_summary(summary))
    return EXIT_OK if summary.failed == 0 else EXIT_FAILURE


def main(
    argv: Sequence[str] | None = None,
    *,
    pipeline: Any | None = None,
    out: Emit | None = None,
    integrity_checker: Any | None = None,
    document_manager: Any | None = None,
    settings: Settings | None = None,
) -> int:
    """CLI 入口（返回进程退出码，不直接 ``sys.exit``）。

    Args:
        argv: 参数列表（缺省 ``sys.argv[1:]``）。
        pipeline: 已构建的 pipeline（测试注入；缺省按 ``--config`` 加载
            ``Settings`` 后经 :func:`build_pipeline` 构建）。
        out: 输出函数（缺省 ``print``）。
        integrity_checker / document_manager / settings: 同步/重试模式的
            注入点（缺省从注入的 pipeline 或配置推导）。

    Returns:
        ``0`` 全部成功/跳过（含空目录、dry-run）；``1`` 存在失败文件或删除
        失败；``2`` 用法/配置错误。
    """
    emit = print if out is None else out
    args = parse_args(argv)

    injected = {
        "pipeline": pipeline,
        "integrity_checker": integrity_checker,
        "document_manager": document_manager,
        "settings": settings,
    }
    if args.sync or args.prune:
        return _main_sync(args, emit, **injected)
    if args.retry_failed:
        return _main_retry(args, emit, **injected)

    # 目录扫描过滤（IR7.4）需要配置：注入 pipeline 时优先复用其 Settings，
    # 否则在此加载（供 source_filters 与默认 pipeline 构建共用）。
    if settings is None and pipeline is not None:
        candidate = getattr(pipeline, "_settings", None)
        settings = candidate if isinstance(candidate, Settings) else None
    if settings is None and pipeline is None:
        try:
            settings = load_settings(args.config)
        except (ConfigError, FileNotFoundError) as exc:
            emit(f"[usage] 配置加载失败: {exc}")
            return EXIT_USAGE

    try:
        scan = collect_files_detailed(args.path, **_source_filter_args(settings))
        files = scan.files
    except IngestPathError as exc:
        emit(f"[usage] {exc}")
        return EXIT_USAGE

    if pipeline is None:
        pipeline = build_pipeline(settings)

    if not files:
        emit(
            f"[info] no supported files found under {args.path} "
            f"(supported: {sorted(SUPPORTED_EXTENSIONS)})"
        )
        if scan.filtered:
            emit(f"[info] filtered: {scan.filtered} (excluded by source_filters)")
        return EXIT_OK

    summary = run_ingest(
        pipeline,
        files,
        args.collection,
        force=args.force,
        verbose=args.verbose,
        filtered=scan.filtered,
        out=emit,
    )
    emit(render_summary(summary))
    return EXIT_OK if summary.failed == 0 else EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
