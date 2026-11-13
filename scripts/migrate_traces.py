"""把单文件 ``logs/traces.jsonl`` 按本地日期拆分到 ``logs/traces/``（LOGGING_SPEC LG6）。

用法::

    python scripts/migrate_traces.py --dry-run          # 只统计，不写盘
    python scripts/migrate_traces.py                    # 执行；原文件改名为 traces.jsonl.migrated
    python scripts/migrate_traces.py --keep-test-records

默认剔除测试产生的记录：

- ingestion：``metadata.source_path`` 为空，或位于系统临时目录 / ``pytest-of-*`` /
  ``tests/fixtures`` 下；
- query：无 ``metadata.query`` 且首阶段为摄取 ``integrity``（向 pipeline 注入默认类型
  trace 的用例产物）。其余 query 记录无可靠来源标识，全部保留。

源文件内重复的 ``trace_id`` 只保留首条。目标日文件已存在时追加，并跳过其中已有的
``trace_id``，可重复执行。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Sequence

_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from observability.log_files import TRACE_FILE_PREFIX, TRACE_FILE_SUFFIX, daily_path  # noqa: E402

__all__ = ["MigrationReport", "is_mislabeled_ingestion", "is_test_record", "local_day", "main", "migrate"]

DEFAULT_SRC = _ROOT / "logs" / "traces.jsonl"
DEFAULT_DEST = _ROOT / "logs" / "traces"
BACKUP_SUFFIX = ".migrated"

_TEST_PATH_MARKERS = ("/tmp/", "/var/folders/", "pytest-of-", "tests/fixtures")


@dataclass
class MigrationReport:
    total: int = 0
    kept: int = 0
    dropped_test: int = 0
    invalid: int = 0
    #: 源文件内重复的 trace_id（保留首条）。
    duplicates: int = 0
    #: 目标日文件中已存在的 trace_id（重复执行迁移时）。
    already_present: int = 0
    per_day: Counter[str] = field(default_factory=Counter)
    backup: Path | None = None


def _temp_roots() -> tuple[str, ...]:
    base = tempfile.gettempdir()
    return tuple({base.replace("\\", "/"), os.path.realpath(base).replace("\\", "/")})


def is_mislabeled_ingestion(record: dict[str, Any]) -> bool:
    """``trace_type=query`` 却无查询文本、首阶段为摄取的 ``integrity``：只有测试会产生。

    生产链路的 query trace 都带 ``metadata.query``；这类记录来自向 pipeline 注入
    默认类型 ``TraceContext()`` 的用例。
    """
    if record.get("trace_type") != "query" or (record.get("metadata") or {}).get("query"):
        return False
    stages = record.get("stages") or []
    return bool(stages) and isinstance(stages[0], dict) and stages[0].get("stage") == "integrity"


def is_test_record(record: dict[str, Any], temp_roots: Sequence[str] | None = None) -> bool:
    if is_mislabeled_ingestion(record):
        return True
    if record.get("trace_type") != "ingestion":
        return False
    source = str((record.get("metadata") or {}).get("source_path") or "").replace("\\", "/")
    if not source:
        return True
    roots = _temp_roots() if temp_roots is None else temp_roots
    if any(root and source.startswith(root) for root in roots):
        return True
    return any(marker in source for marker in _TEST_PATH_MARKERS)


def local_day(started_at: Any) -> date | None:
    """UTC ISO 时间戳转本地日期；无时区的按本地时间处理。"""
    if not isinstance(started_at, str) or not started_at:
        return None
    try:
        return datetime.fromisoformat(started_at).astimezone().date()
    except ValueError:
        return None


def _existing_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            trace_id = json.loads(line).get("trace_id")
        except (json.JSONDecodeError, AttributeError):
            continue
        if trace_id:
            ids.add(str(trace_id))
    return ids


def migrate(
    src: Path,
    dest: Path,
    *,
    dry_run: bool = False,
    keep_test_records: bool = False,
    to_day: Callable[[Any], date | None] = local_day,
) -> MigrationReport:
    backup = src.with_name(src.name + BACKUP_SUFFIX)
    if not dry_run and backup.exists():
        raise FileExistsError(f"backup already exists: {backup}")

    report = MigrationReport()
    buckets: dict[date, list[tuple[str, str]]] = {}
    source_ids: set[str] = set()
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        report.total += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            report.invalid += 1
            continue
        day = to_day(record.get("started_at")) if isinstance(record, dict) else None
        if day is None or not record.get("trace_id"):
            report.invalid += 1
            continue
        trace_id = str(record["trace_id"])
        if trace_id in source_ids:
            report.duplicates += 1
            continue
        source_ids.add(trace_id)
        if not keep_test_records and is_test_record(record):
            report.dropped_test += 1
            continue
        buckets.setdefault(day, []).append((trace_id, line.strip()))

    for day in sorted(buckets):
        target = daily_path(dest, TRACE_FILE_PREFIX, TRACE_FILE_SUFFIX, day)
        seen = _existing_ids(target)
        fresh: list[str] = []
        for trace_id, line in buckets[day]:
            if trace_id in seen:
                report.already_present += 1
                continue
            seen.add(trace_id)
            fresh.append(line)
        report.kept += len(fresh)
        if fresh:
            report.per_day[day.isoformat()] += len(fresh)
        if fresh and not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(fresh) + "\n")

    if not dry_run:
        src.rename(backup)
        report.backup = backup
    return report


def render(report: MigrationReport, *, dry_run: bool) -> str:
    lines = [
        f"{'[dry-run] ' if dry_run else ''}total={report.total} kept={report.kept} "
        f"dropped_test={report.dropped_test} invalid={report.invalid} "
        f"duplicates={report.duplicates} already_present={report.already_present}",
    ]
    lines += [f"  {day}: {count}" for day, count in sorted(report.per_day.items())]
    if report.backup is not None:
        lines.append(f"backup: {report.backup}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, *, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(description="Split a single traces.jsonl into daily trace files.")
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC, help=f"源文件（默认 {DEFAULT_SRC}）")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help=f"目标目录（默认 {DEFAULT_DEST}）")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写盘")
    parser.add_argument("--keep-test-records", action="store_true", help="保留测试产生的 ingestion 记录")
    args = parser.parse_args(argv)
    if not args.src.is_file():
        out(f"error: source not found: {args.src}")
        return 2
    try:
        report = migrate(args.src, args.dest, dry_run=args.dry_run, keep_test_records=args.keep_test_records)
    except FileExistsError as exc:
        out(f"error: {exc}")
        return 2
    out(render(report, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
