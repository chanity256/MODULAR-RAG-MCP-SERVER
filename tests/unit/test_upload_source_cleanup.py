"""AR2（AGENTIC_UPLOAD_ROOTS_SPEC）：删除文档联动清理上传目录内源文件。

纯离线。覆盖规格 §4 删除一致性三场景 + DeleteResult 字段透传：

- 上传并摄取后 ``DELETE /documents/{id}``：源文件被删除、
  ``source_file_removed`` 为 True（服务层返回 + ``to_dict`` 透传）。
- 同一 ``source_path`` 仍属于另一个 collection：文件保留、字段 False。
- 源文件不在上传目录内（如 CLI 摄取的 ``data/documents/`` 文件）：不删除。
- 边界：无实际删除（404 语义）、引用检查异常、unlink 失败、
  ``DocumentManager.source_referenced`` 查询语义。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ingestion.document_manager import DeleteResult, DocumentManager
from observability.dashboard.services.data_service import DataService


class _RefFakeStore:
    """最小 Chroma store 替身：只实现 source_referenced 依赖的查询。"""

    def __init__(self, records: list[dict]) -> None:
        self._records = records

    def get_by_metadata(self, filters=None, *, trace=None, **kwargs):
        out = []
        for rec in self._records:
            if filters and not all(rec["metadata"].get(k) == v for k, v in filters.items()):
                continue
            out.append(rec)
        return out


class _FakeManager:
    """DocumentManager 替身：delete 返回预置结果，引用检查可控。"""

    def __init__(self, result: DeleteResult, referenced: bool = False, raise_on_ref: bool = False) -> None:
        self.result = result
        self.referenced = referenced
        self.raise_on_ref = raise_on_ref

    def delete_document(self, source_path: str, collection: str) -> DeleteResult:
        return self.result

    def source_referenced(self, source_path: str) -> bool:
        if self.raise_on_ref:
            raise RuntimeError("store unavailable")
        return self.referenced


def _result(tmp_path: Path, source: Path, *, chroma_deleted: int = 3) -> DeleteResult:
    return DeleteResult(
        source_path=str(source),
        collection="default",
        chroma_deleted=chroma_deleted,
    )


class TestUploadSourceCleanup:
    def test_uploaded_file_removed_with_source_file_removed_true(self, tmp_path: Path):
        uploads = tmp_path / "data" / "uploads"
        uploads.mkdir(parents=True)
        source = uploads / "report.md"
        source.write_text("content", encoding="utf-8")
        service = DataService(
            manager=_FakeManager(_result(tmp_path, source)),
            upload_dir=uploads,
        )
        result = service.delete_document(str(source), "default")
        assert result.source_file_removed is True
        assert not source.exists()
        assert result.to_dict()["source_file_removed"] is True

    def test_kept_when_still_referenced_by_other_collection(self, tmp_path: Path):
        uploads = tmp_path / "data" / "uploads"
        uploads.mkdir(parents=True)
        source = uploads / "report.md"
        source.write_text("content", encoding="utf-8")
        service = DataService(
            manager=_FakeManager(_result(tmp_path, source), referenced=True),
            upload_dir=uploads,
        )
        result = service.delete_document(str(source), "default")
        assert result.source_file_removed is False
        assert source.exists()

    def test_not_touched_when_outside_upload_dir(self, tmp_path: Path):
        uploads = tmp_path / "data" / "uploads"
        uploads.mkdir(parents=True)
        cli_doc = tmp_path / "data" / "documents" / "a.pdf"
        cli_doc.parent.mkdir(parents=True)
        cli_doc.write_text("pdf", encoding="utf-8")
        service = DataService(
            manager=_FakeManager(_result(tmp_path, cli_doc)),
            upload_dir=uploads,
        )
        result = service.delete_document(str(cli_doc), "default")
        assert result.source_file_removed is False
        assert cli_doc.exists()

    def test_not_touched_when_nothing_removed(self, tmp_path: Path):
        uploads = tmp_path / "data" / "uploads"
        uploads.mkdir(parents=True)
        source = uploads / "ghost.md"
        source.write_text("x", encoding="utf-8")
        service = DataService(
            manager=_FakeManager(_result(tmp_path, source, chroma_deleted=0)),
            upload_dir=uploads,
        )
        result = service.delete_document(str(source), "default")
        assert result.source_file_removed is False
        assert source.exists()  # 404 语义路径：文件不动

    def test_reference_check_failure_keeps_file(self, tmp_path: Path):
        uploads = tmp_path / "data" / "uploads"
        uploads.mkdir(parents=True)
        source = uploads / "report.md"
        source.write_text("content", encoding="utf-8")
        service = DataService(
            manager=_FakeManager(_result(tmp_path, source), raise_on_ref=True),
            upload_dir=uploads,
        )
        result = service.delete_document(str(source), "default")
        assert result.source_file_removed is False
        assert source.exists()

    def test_missing_source_file_reports_false(self, tmp_path: Path):
        uploads = tmp_path / "data" / "uploads"
        uploads.mkdir(parents=True)
        source = uploads / "gone.md"  # 存储有记录但文件已不在
        service = DataService(
            manager=_FakeManager(_result(tmp_path, source)),
            upload_dir=uploads,
        )
        result = service.delete_document(str(source), "default")
        assert result.source_file_removed is False

    def test_default_upload_dir_is_data_uploads(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        uploads = tmp_path / "data" / "uploads"
        uploads.mkdir(parents=True)
        source = uploads / "report.md"
        source.write_text("content", encoding="utf-8")
        service = DataService(manager=_FakeManager(_result(tmp_path, source)))
        assert service.delete_document(str(source), "default").source_file_removed is True


class TestSourceReferencedQuery:
    """DocumentManager.source_referenced 的 Chroma 查询语义。"""

    @staticmethod
    def _manager(records: list[dict]) -> DocumentManager:
        return DocumentManager(chroma_store=_RefFakeStore(records))

    @staticmethod
    def _rec(chunk_id: str, source: str, collection: str) -> dict:
        return {"id": chunk_id, "text": "t", "metadata": {"source_path": source, "collection": collection}}

    def test_true_when_other_collection_still_holds_source(self):
        manager = self._manager([self._rec("c1", "/up/a.md", "beta")])
        assert manager.source_referenced("/up/a.md") is True

    def test_false_when_no_record_left(self):
        manager = self._manager([self._rec("c1", "/up/a.md", "beta")])
        assert manager.source_referenced("/up/other.md") is False

    def test_legacy_source_key_fallback(self):
        record = {"id": "c1", "text": "t", "metadata": {"source": "/up/a.md", "collection": "beta"}}
        manager = self._manager([record])
        # 旧 metadata 只带 ``source`` 键：按 ``source`` 精确过滤同样命中，
        # 不会被误判为无引用（与 _list_documents 的 fallback 语义一致）。
        assert manager.source_referenced("/up/a.md") is True


class TestDeleteResultField:
    def test_default_false_and_dict_passthrough(self):
        result = DeleteResult(source_path="/x", collection="default")
        assert result.source_file_removed is False
        assert result.to_dict()["source_file_removed"] is False
