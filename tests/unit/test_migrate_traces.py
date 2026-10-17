"""LOGGING_SPEC LG6：``scripts/migrate_traces.py`` 历史 trace 按日拆分迁移。"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrate_traces.py"
_spec = importlib.util.spec_from_file_location("migrate_traces_script", _SCRIPT)
migrate_traces = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = migrate_traces  # dataclass 解析注解时需从 sys.modules 找到模块
_spec.loader.exec_module(migrate_traces)


def _utc_day(started_at):
    # 用 UTC 日期，避免断言依赖运行机器的时区。
    return date.fromisoformat(started_at[:10]) if isinstance(started_at, str) and started_at else None


def _record(trace_id: str, started_at: str, trace_type: str = "query", source_path: str | None = None) -> dict:
    if trace_type == "query":
        metadata = {"query": f"question {trace_id}"}
    else:
        metadata = {} if source_path is None else {"source_path": source_path, "collection": "c"}
    return {"trace_id": trace_id, "trace_type": trace_type, "started_at": started_at, "metadata": metadata, "stages": []}


def _mislabeled(trace_id: str, started_at: str) -> dict:
    """默认类型 TraceContext() 被注入 pipeline 的测试产物。"""
    record = _record(trace_id, started_at)
    record["metadata"] = {}
    record["stages"] = [{"stage": "integrity", "elapsed_ms": 0.1}, {"stage": "load", "elapsed_ms": 0.2}]
    return record


@pytest.fixture
def source(tmp_path: Path) -> Path:
    duplicate = _record("q1", "2026-10-06T01:00:00+00:00")
    duplicate["stages"] = [{"stage": "late-rewrite"}]
    records = [
        _record("q1", "2026-10-06T01:00:00+00:00"),
        _record("i-real", "2026-10-06T02:00:00+00:00", "ingestion", "data/documents/a.pdf"),
        _record("i-tmp", "2026-10-06T03:00:00+00:00", "ingestion", "/private/var/folders/x/pytest-of-u/t/a.md"),
        _record("i-fixture", "2026-10-07T03:00:00+00:00", "ingestion", "tests/fixtures/sample.md"),
        _record("i-empty", "2026-10-07T03:00:00+00:00", "ingestion"),
        _mislabeled("q-mis", "2026-10-07T03:30:00+00:00"),
        _record("q2", "2026-10-07T04:00:00+00:00"),
        duplicate,
    ]
    lines = [json.dumps(r) for r in records] + ["not json", json.dumps({"trace_id": "no-time"}), ""]
    path = tmp_path / "traces.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _ids(path: Path) -> list[str]:
    return [json.loads(line)["trace_id"] for line in path.read_text(encoding="utf-8").splitlines()]


def test_splits_by_day_drops_test_records_and_backs_up(source, tmp_path):
    dest = tmp_path / "traces"
    report = migrate_traces.migrate(source, dest, to_day=_utc_day)
    assert (report.total, report.kept, report.dropped_test, report.invalid, report.duplicates) == (10, 3, 4, 2, 1)
    assert _ids(dest / "traces-2026-10-06.jsonl") == ["q1", "i-real"]
    first_q1 = json.loads((dest / "traces-2026-10-06.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert first_q1["stages"] == []  # 重复 trace_id 保留首条
    assert _ids(dest / "traces-2026-10-07.jsonl") == ["q2"]
    assert not source.exists() and report.backup == tmp_path / "traces.jsonl.migrated"
    assert report.backup.is_file()


def test_dry_run_writes_nothing(source, tmp_path):
    dest = tmp_path / "traces"
    report = migrate_traces.migrate(source, dest, dry_run=True, to_day=_utc_day)
    assert report.kept == 3 and dict(report.per_day) == {"2026-10-06": 2, "2026-10-07": 1}
    assert source.is_file() and not dest.exists() and report.backup is None


def test_keep_test_records(source, tmp_path):
    report = migrate_traces.migrate(source, tmp_path / "traces", keep_test_records=True, to_day=_utc_day)
    assert (report.kept, report.dropped_test, report.duplicates) == (7, 0, 1)


def test_rerun_skips_existing_trace_ids(source, tmp_path):
    dest = tmp_path / "traces"
    dest.mkdir()
    (dest / "traces-2026-10-06.jsonl").write_text(json.dumps(_record("q1", "2026-10-06T01:00:00+00:00")) + "\n")
    report = migrate_traces.migrate(source, dest, to_day=_utc_day)
    assert report.already_present == 1
    assert _ids(dest / "traces-2026-10-06.jsonl") == ["q1", "i-real"]


def test_existing_backup_aborts_before_writing(source, tmp_path):
    (tmp_path / "traces.jsonl.migrated").write_text("", encoding="utf-8")
    with pytest.raises(FileExistsError):
        migrate_traces.migrate(source, tmp_path / "traces", to_day=_utc_day)
    assert not (tmp_path / "traces").exists() and source.is_file()


def test_is_test_record_rules():
    temp_roots = ("/custom/tmp",)
    assert migrate_traces.is_test_record(_record("i", "t", "ingestion", "/custom/tmp/x.md"), temp_roots)
    assert not migrate_traces.is_test_record(_record("i", "t", "ingestion", "/home/u/notes/x.md"), temp_roots)
    assert not migrate_traces.is_test_record(_record("q", "t", "query"), temp_roots)
    assert migrate_traces.is_test_record(_mislabeled("q", "t"), temp_roots)
    no_text_real_stages = _mislabeled("q", "t")
    no_text_real_stages["stages"] = [{"stage": "dense_retrieval"}]
    assert not migrate_traces.is_test_record(no_text_real_stages, temp_roots)


def test_local_day_converts_utc_to_local_date():
    assert migrate_traces.local_day("bad") is None
    assert migrate_traces.local_day(None) is None
    assert isinstance(migrate_traces.local_day("2026-10-06T23:30:00+00:00"), date)


def test_cli_reports_missing_source(tmp_path):
    lines: list[str] = []
    assert migrate_traces.main(["--src", str(tmp_path / "nope.jsonl")], out=lines.append) == 2
    assert "source not found" in lines[0]


def test_cli_dry_run_output(source, tmp_path):
    lines: list[str] = []
    argv = ["--src", str(source), "--dest", str(tmp_path / "traces"), "--dry-run"]
    assert migrate_traces.main(argv, out=lines.append) == 0
    assert lines[0].startswith("[dry-run] total=10")
