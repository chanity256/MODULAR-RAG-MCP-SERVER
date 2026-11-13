"""IngestionService 单测（WC1.2）：全部注入 fake Pipeline，不触碰 Provider 与持久化目录。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from observability.dashboard.services.ingestion_service import (
    IngestionService,
    IngestionSummary,
    sanitize_filename,
)


@dataclass
class _Result:
    chunks: int = 0
    images: int = 0


class FakePipeline:
    def __init__(self, results=None, fail_on=()):
        self.calls: list[dict] = []
        self._results = results or {}
        self._fail_on = set(fail_on)

    def run(self, path, *, collection, force, on_progress=None):
        self.calls.append(
            {"path": path, "collection": collection, "force": force, "on_progress": on_progress}
        )
        name = Path(path).name
        if name in self._fail_on:
            raise RuntimeError(f"boom {name}")
        if on_progress is not None:
            on_progress("load", 1, 1)
        return self._results.get(name, _Result(chunks=1, images=0))


class TestSaveUploads:
    def test_saves_multiple_files_in_order(self, tmp_path):
        service = IngestionService(upload_dir=tmp_path / "up", pipeline=FakePipeline())
        saved = service.save_uploads([("a.txt", b"AAA"), ("b.md", b"BBB")])
        assert [p.name for p in saved] == ["a.txt", "b.md"]
        assert saved[0].read_bytes() == b"AAA"
        assert saved[1].read_bytes() == b"BBB"
        assert all(p.parent == tmp_path / "up" for p in saved)

    def test_creates_missing_upload_dir(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "uploads"
        assert not target.exists()
        IngestionService(upload_dir=target).save_uploads([("x.txt", b"x")])
        assert (target / "x.txt").read_bytes() == b"x"

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("../../etc/passwd", "passwd"),
            ("/abs/path/report.pdf", "report.pdf"),
            ("..\\..\\evil.txt", "evil.txt"),
            ("sub/dir/file.md", "file.md"),
            ("plain.txt", "plain.txt"),
            ("", "upload.bin"),
            (None, "upload.bin"),
            ("..", "upload.bin"),
            (".", "upload.bin"),
        ],
    )
    def test_filename_sanitized(self, raw, expected):
        assert sanitize_filename(raw) == expected

    def test_traversal_name_stays_inside_upload_dir(self, tmp_path):
        service = IngestionService(upload_dir=tmp_path / "up")
        [dest] = service.save_uploads([("../escape.txt", b"x")])
        assert dest == tmp_path / "up" / "escape.txt"
        assert not (tmp_path / "escape.txt").exists()

    def test_default_upload_dir(self):
        assert IngestionService().upload_dir == Path("data/uploads")


class TestIngest:
    def test_params_passed_through(self, tmp_path):
        pipeline = FakePipeline()
        service = IngestionService(upload_dir=tmp_path, pipeline=pipeline)
        service.ingest([tmp_path / "a.txt"], collection="docs", force=True)
        assert pipeline.calls[0]["path"] == str(tmp_path / "a.txt")
        assert pipeline.calls[0]["collection"] == "docs"
        assert pipeline.calls[0]["force"] is True

    def test_force_defaults_false(self, tmp_path):
        pipeline = FakePipeline()
        IngestionService(pipeline=pipeline).ingest(["a.txt"], collection="c")
        assert pipeline.calls[0]["force"] is False

    def test_aggregates_multi_file_results(self, tmp_path):
        pipeline = FakePipeline(
            results={"a.txt": _Result(3, 1), "b.txt": _Result(4, 2), "c.txt": _Result(0, 0)}
        )
        summary = IngestionService(pipeline=pipeline).ingest(
            ["a.txt", "b.txt", "c.txt"], collection="c"
        )
        assert isinstance(summary, IngestionSummary)
        assert (summary.files, summary.chunks, summary.images, summary.failed) == (3, 7, 3, 0)
        assert summary.errors == []

    def test_single_failure_does_not_block_others(self):
        pipeline = FakePipeline(
            results={"a.txt": _Result(2, 1), "c.txt": _Result(5, 0)}, fail_on={"b.txt"}
        )
        summary = IngestionService(pipeline=pipeline).ingest(
            ["a.txt", "b.txt", "c.txt"], collection="c"
        )
        assert [Path(c["path"]).name for c in pipeline.calls] == ["a.txt", "b.txt", "c.txt"]
        assert (summary.files, summary.chunks, summary.images, summary.failed) == (3, 7, 1, 1)
        assert summary.errors == [("b.txt", "RuntimeError: boom b.txt")]

    def test_progress_callback_passed_through(self):
        pipeline = FakePipeline()
        events: list[tuple[str, int, int]] = []

        def cb(stage, current, total):
            events.append((stage, current, total))

        IngestionService(pipeline=pipeline).ingest(["a.txt", "b.txt"], collection="c", on_progress=cb)
        assert all(call["on_progress"] is cb for call in pipeline.calls)
        assert events == [("load", 1, 1), ("load", 1, 1)]

    def test_summary_string_matches_legacy_format(self):
        pipeline = FakePipeline(results={"a.txt": _Result(2, 1)}, fail_on={"b.txt"})
        summary = IngestionService(pipeline=pipeline).ingest(["a.txt", "b.txt"], collection="c")
        assert str(summary) == "2 files, chunks=2, images=1, failed=1"

    def test_empty_input(self):
        summary = IngestionService(pipeline=FakePipeline()).ingest([], collection="c")
        assert str(summary) == "0 files, chunks=0, images=0, failed=0"

    def test_runner_overrides_pipeline(self):
        seen: dict = {}

        def runner(files, *, collection, force, on_progress):
            seen.update(files=files, collection=collection, force=force, on_progress=on_progress)
            return "custom-summary"

        pipeline = FakePipeline()
        service = IngestionService(pipeline=pipeline, runner=runner)
        out = service.ingest(["a.txt"], collection="docs", force=True, on_progress=None)
        assert out == "custom-summary"
        assert seen == {
            "files": [Path("a.txt")],
            "collection": "docs",
            "force": True,
            "on_progress": None,
        }
        assert pipeline.calls == []

    def test_runner_exception_propagates(self):
        def runner(files, **kwargs):
            raise RuntimeError("runner down")

        with pytest.raises(RuntimeError, match="runner down"):
            IngestionService(runner=runner).ingest(["a.txt"], collection="c")


class TestSaveAndIngest:
    def test_saves_then_ingests_saved_paths(self, tmp_path):
        pipeline = FakePipeline(results={"a.txt": _Result(2, 0)})
        service = IngestionService(upload_dir=tmp_path / "up", pipeline=pipeline)
        summary = service.save_and_ingest([("../a.txt", b"hello")], collection="docs")
        assert pipeline.calls[0]["path"] == str(tmp_path / "up" / "a.txt")
        assert (tmp_path / "up" / "a.txt").read_bytes() == b"hello"
        assert summary.chunks == 2


def test_service_has_no_streamlit_import():
    import observability.dashboard.services.ingestion_service as module

    assert "import streamlit" not in Path(module.__file__).read_text(encoding="utf-8")
