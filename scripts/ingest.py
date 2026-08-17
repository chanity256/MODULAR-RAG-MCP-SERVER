"""命令行摄取入口（阶段 C15，离线可用）。

用法（对齐 DEV_SPEC 阶段 I5 的验收命令形态）::

    python scripts/ingest.py --path tests/fixtures/sample_documents/ --collection test
    python scripts/ingest.py --path data/documents/manual.pdf --force

行为契约（C15 验收标准）：
- ``--path``：目标文件或目录；目录时递归扫描受支持的扩展名（与
  ``ingestion.pipeline`` 的扩展名路由保持一致，当前 ``.pdf``），文件按
  路径排序保证确定性；大小写不敏感（``.PDF`` 同样受理）。
- ``--collection``：目标集合名（透传 ``IngestionPipeline.run``，写入每个
  chunk 的 ``metadata["collection"]``；默认 ``default``）。
- ``--force``：跳过完整性检查强制重摄取（幂等替换，不产生重复记录）。
- 重复运行：内容未变更的文件被完整性检查跳过（``skipped``），零成本。
- 退出码：``0`` = 全部成功或跳过（含空目录）；``1`` = 存在失败文件；
  ``2`` = 用法/配置错误（参数缺失、路径不存在、扩展名不受支持、
  配置加载失败）。

可测试性：解析/收集/聚合/渲染逻辑全部放在可导入的纯函数中，
``main(argv, *, pipeline=...)`` 支持注入已构建的 pipeline（离线 e2e
注入确定性 embedding 即可全链路离线运行，见 ``tests/e2e/test_data_ingestion.py``）。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

# 让 ``src`` 下的包在未安装时也可被直接 import（与根 ``main.py`` 同一模式，
# 支持 ``python scripts/ingest.py`` 直接运行）。
_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from core.settings import ConfigError, Settings, load_settings  # noqa: E402
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
    "build_parser",
    "parse_args",
    "collect_files",
    "build_pipeline",
    "run_ingest",
    "aggregate",
    "render_summary",
    "main",
]

#: 默认配置文件（仓库根 ``config/settings.yaml``，绝对路径不受 CWD 影响）。
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"

#: 受支持的源文件扩展名（小写；与 ``ingestion.pipeline`` 的 Loader 路由一致；
#: 单测中有 drift guard 保证两处不脱钩）。
SUPPORTED_EXTENSIONS = frozenset({".pdf"})

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
            "outcomes": [o.to_dict() for o in self.outcomes],
        }


def aggregate(collection: str, outcomes: Sequence[FileOutcome]) -> IngestSummary:
    """把逐文件结果聚合为汇总（chunks/images/written 不计失败文件）。"""
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
    )


# ---------------------------------------------------------------------------
# 参数解析与文件收集
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器（``--path`` 必填，``--collection``/``--force`` 可选）。"""
    parser = argparse.ArgumentParser(
        prog="ingest",
        description="摄取 PDF 文档到本地索引（BM25 + 向量库 + 图片存储）。",
    )
    parser.add_argument(
        "--path",
        required=True,
        help="目标文件或目录（目录时递归摄取受支持扩展名的文件）",
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
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析 argv（缺省取 ``sys.argv[1:]``；非法参数由 argparse 退出码 2 处理）。"""
    return build_parser().parse_args(argv)


def collect_files(path: str | Path) -> list[Path]:
    """把 ``--path`` 解析为待摄取文件列表。

    - 单文件：扩展名必须受支持（否则单文件场景无从跳过，直接报错）。
    - 目录：递归收集受支持扩展名的文件，按路径排序保证确定性；
      非支持扩展名（如 ``.md``/``.txt``）静默忽略。

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
        return [target]
    if target.is_dir():
        return sorted(
            p
            for p in target.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        )
    raise IngestPathError(f"path is neither a file nor a directory: {target}")


# ---------------------------------------------------------------------------
# Pipeline 构建与执行
# ---------------------------------------------------------------------------


def build_pipeline(settings: Settings) -> IngestionPipeline:
    """按 ``Settings`` 构建默认 pipeline（组件惰性创建，见 C14）。"""
    return IngestionPipeline(settings)


def run_ingest(
    pipeline: Any,
    files: Sequence[Path],
    collection: str,
    *,
    force: bool = False,
    verbose: bool = False,
    out: Emit | None = None,
) -> IngestSummary:
    """逐文件调用 ``pipeline.run`` 并聚合结果（单文件失败不中断批次）。

    Args:
        pipeline: 任何具备 ``run(source_path, collection=..., on_progress=...,
            force=...)`` 签法的对象（真 ``IngestionPipeline`` 或测试 fake）。
        files: 待摄取文件（已排序）。
        collection: 目标集合名（透传 pipeline）。
        force: 强制重摄取（透传 pipeline）。
        verbose: True 时把 ``on_progress`` 回调内容打印为缩进行。
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
                force=force,
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

    return aggregate(collection, outcomes)


def render_summary(summary: IngestSummary) -> str:
    """渲染人类可读的汇总块（失败文件逐条列出错误）。"""
    bar = "=" * 64
    lines = [
        bar,
        f"ingest summary: collection={summary.collection!r}",
        f"  files  : {summary.total} total | {summary.ingested} ingested | "
        f"{summary.skipped} skipped | {summary.failed} failed",
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


def main(
    argv: Sequence[str] | None = None,
    *,
    pipeline: Any | None = None,
    out: Emit | None = None,
) -> int:
    """CLI 入口（返回进程退出码，不直接 ``sys.exit``）。

    Args:
        argv: 参数列表（缺省 ``sys.argv[1:]``）。
        pipeline: 已构建的 pipeline（测试注入；缺省按 ``--config`` 加载
            ``Settings`` 后经 :func:`build_pipeline` 构建）。
        out: 输出函数（缺省 ``print``）。

    Returns:
        ``0`` 全部成功/跳过（含空目录）；``1`` 存在失败文件；
        ``2`` 用法/配置错误。
    """
    emit = print if out is None else out
    args = parse_args(argv)

    try:
        files = collect_files(args.path)
    except IngestPathError as exc:
        emit(f"[usage] {exc}")
        return EXIT_USAGE

    if pipeline is None:
        try:
            settings = load_settings(args.config)
        except (ConfigError, FileNotFoundError) as exc:
            emit(f"[usage] 配置加载失败: {exc}")
            return EXIT_USAGE
        pipeline = build_pipeline(settings)

    if not files:
        emit(
            f"[info] no supported files found under {args.path} "
            f"(supported: {sorted(SUPPORTED_EXTENSIONS)})"
        )
        return EXIT_OK

    summary = run_ingest(
        pipeline,
        files,
        args.collection,
        force=args.force,
        verbose=args.verbose,
        out=emit,
    )
    emit(render_summary(summary))
    return EXIT_OK if summary.failed == 0 else EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
