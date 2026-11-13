"""``ingestion.storage.ImageStorage`` 契约测试（阶段 C13）。

覆盖：
- put 落盘 + 索引登记：文件存在、字节保真、file_path/collection/doc_hash/
  page_num/created_at 元数据完整；
- 目录布局：collection 优先（spec ``data/images/{collection}/``），
  缺省回退 doc_hash（对齐 C3 PdfLoader），两者皆缺 fail-fast；
- 幂等性：同 image_id 重复 put → 覆盖文件 + 替换行，无重复行；
  目录变更的重复 put 清理旧文件；
- 查询：get（miss 返回 None）、list_images 按 collection/doc_hash
  过滤 + image_id 升序确定性排序 + limit、count；
- 删除：delete 单张（行 + 文件）、delete_images 批量（需过滤条件）；
- 契约破坏 fail-fast：空/含路径分隔符的 id、空/非 bytes 数据、
  非法 page_num/ext/limit；
- 持久化：关闭后重开（同一路径）索引与 schema 仍在；
- trace storage 打点（put / replaced / delete_images）；
- spec 默认路径常量契约。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.trace.trace_context import TraceContext
from ingestion.storage import ImageRecord, ImageStorage, ImageStorageError
from ingestion.storage.image_storage import DEFAULT_DB_PATH, DEFAULT_IMAGES_ROOT

_PNG = b"\x89PNG\r\n\x1a\nfake-image-payload"


def _storage(tmp_path: Path) -> ImageStorage:
    """基于 tmp_path 的存储实例（不触碰仓库 data/ 目录）。"""
    return ImageStorage(
        images_root=str(tmp_path / "images"),
        db_path=str(tmp_path / "db" / "image_index.db"),
    )


# ---------------------------------------------------------------------------
# put / get 基本契约
# ---------------------------------------------------------------------------


class TestPutAndGet:
    def test_put_creates_file_and_record(self, tmp_path: Path) -> None:
        storage = _storage(tmp_path)
        record = storage.put(
            "abc123_0_1",
            _PNG,
            collection="docs",
            doc_hash="abc123",
            page_num=0,
        )
        expected = tmp_path / "images" / "docs" / "abc123_0_1.png"
        assert expected.is_file()
        assert expected.read_bytes() == _PNG  # 字节保真
        assert record.image_id == "abc123_0_1"
        assert record.file_path == str(expected)
        assert record.collection == "docs"
        assert record.doc_hash == "abc123"
        assert record.page_num == 0
        assert isinstance(record.created_at, str) and record.created_at

    def test_get_returns_correct_path(self, tmp_path: Path) -> None:
        storage = _storage(tmp_path)
        storage.put("h1_2_0", _PNG, collection="c1", doc_hash="h1", page_num=2)
        found = storage.get("h1_2_0")
        assert found is not None
        assert found.file_path == str(tmp_path / "images" / "c1" / "h1_2_0.png")
        assert found.page_num == 2

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        storage = _storage(tmp_path)
        assert storage.get("nope") is None
        assert storage.get("") is None
        assert storage.get(None) is None  # type: ignore[arg-type]

    def test_directory_falls_back_to_doc_hash(self, tmp_path: Path) -> None:
        """collection 缺省时按 C3 布局归档到 {images_root}/{doc_hash}/。"""
        storage = _storage(tmp_path)
        record = storage.put("h2_0_0", _PNG, doc_hash="h2")
        expected = tmp_path / "images" / "h2" / "h2_0_0.png"
        assert expected.is_file()
        assert record.file_path == str(expected)
        assert record.collection is None
        assert record.doc_hash == "h2"

    def test_put_requires_collection_or_doc_hash(self, tmp_path: Path) -> None:
        storage = _storage(tmp_path)
        with pytest.raises(ImageStorageError, match="collection or doc_hash"):
            storage.put("h3_0_0", _PNG)
        assert storage.count() == 0
        assert list((tmp_path / "images").iterdir()) == []

    def test_extension_normalized_and_honored(self, tmp_path: Path) -> None:
        storage = _storage(tmp_path)
        no_dot = storage.put("h4_0_0", _PNG, collection="c", ext="png")
        jpg = storage.put("h4_0_1", _PNG, collection="c", ext=".jpg")
        assert no_dot.file_path.endswith("h4_0_0.png")
        assert jpg.file_path.endswith("h4_0_1.jpg")

    def test_bytearray_data_accepted(self, tmp_path: Path) -> None:
        storage = _storage(tmp_path)
        record = storage.put("h5_0_0", bytearray(_PNG), collection="c")
        assert Path(record.file_path).read_bytes() == _PNG


# ---------------------------------------------------------------------------
# 幂等性
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_reput_replaces_without_duplicate_rows(self, tmp_path: Path) -> None:
        storage = _storage(tmp_path)
        first = storage.put("h1_0_0", _PNG, collection="c", doc_hash="h1")
        updated_payload = _PNG + b"-v2"
        second = storage.put("h1_0_0", updated_payload, collection="c", doc_hash="h1")
        assert storage.count() == 1
        assert len(storage.list_images()) == 1
        assert Path(second.file_path).read_bytes() == updated_payload  # 文件被覆盖
        assert Path(first.file_path).read_bytes() == updated_payload  # 同一路径

    def test_reput_directory_change_moves_file(self, tmp_path: Path) -> None:
        """目录变更的重复 put：新文件写入新目录，旧文件被清理。"""
        storage = _storage(tmp_path)
        first = storage.put("h2_0_0", _PNG, doc_hash="h2old")
        old_path = Path(first.file_path)
        assert old_path.is_file()
        second = storage.put("h2_0_0", _PNG, collection="h2new")
        assert not old_path.exists()  # 旧文件清理
        assert Path(second.file_path).is_file()
        assert storage.count() == 1
        assert storage.get("h2_0_0").file_path == second.file_path

    def test_delete_then_put_recreates(self, tmp_path: Path) -> None:
        storage = _storage(tmp_path)
        storage.put("h3_0_0", _PNG, collection="c")
        assert storage.delete("h3_0_0") is True
        record = storage.put("h3_0_0", _PNG, collection="c")
        assert storage.count() == 1
        assert Path(record.file_path).is_file()


# ---------------------------------------------------------------------------
# 查询：list_images / count
# ---------------------------------------------------------------------------


class TestQueries:
    def _seed(self, tmp_path: Path) -> ImageStorage:
        storage = _storage(tmp_path)
        storage.put("b_0_0", b"1", collection="c1", doc_hash="h1")
        storage.put("a_0_0", b"2", collection="c1", doc_hash="h2")
        storage.put("a_0_1", b"3", collection="c2", doc_hash="h2")
        return storage

    def test_list_by_collection(self, tmp_path: Path) -> None:
        storage = self._seed(tmp_path)
        ids = [r.image_id for r in storage.list_images(collection="c1")]
        assert ids == ["a_0_0", "b_0_0"]  # image_id 升序（确定性）

    def test_list_by_doc_hash(self, tmp_path: Path) -> None:
        storage = self._seed(tmp_path)
        ids = [r.image_id for r in storage.list_images(doc_hash="h2")]
        assert ids == ["a_0_0", "a_0_1"]

    def test_list_combined_filters(self, tmp_path: Path) -> None:
        storage = self._seed(tmp_path)
        ids = [r.image_id for r in storage.list_images(collection="c2", doc_hash="h2")]
        assert ids == ["a_0_1"]

    def test_list_all_when_no_filter(self, tmp_path: Path) -> None:
        storage = self._seed(tmp_path)
        assert len(storage.list_images()) == 3

    def test_list_limit(self, tmp_path: Path) -> None:
        storage = self._seed(tmp_path)
        assert [r.image_id for r in storage.list_images(limit=2)] == ["a_0_0", "a_0_1"]
        with pytest.raises(ImageStorageError, match="limit"):
            storage.list_images(limit=0)
        with pytest.raises(ImageStorageError, match="limit"):
            storage.list_images(limit=True)  # type: ignore[arg-type]

    def test_count_with_filters(self, tmp_path: Path) -> None:
        storage = self._seed(tmp_path)
        assert storage.count() == 3
        assert storage.count(collection="c1") == 2
        assert storage.count(doc_hash="h2") == 2
        assert storage.count(collection="c1", doc_hash="h2") == 1


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_single_removes_row_and_file(self, tmp_path: Path) -> None:
        storage = _storage(tmp_path)
        record = storage.put("h_0_0", _PNG, collection="c")
        file_path = Path(record.file_path)
        assert storage.delete("h_0_0") is True
        assert storage.get("h_0_0") is None
        assert not file_path.exists()
        assert storage.count() == 0

    def test_delete_missing_returns_false(self, tmp_path: Path) -> None:
        storage = _storage(tmp_path)
        assert storage.delete("ghost") is False
        assert storage.delete("") is False

    def test_delete_images_bulk(self, tmp_path: Path) -> None:
        storage = _storage(tmp_path)
        paths = [
            Path(storage.put(f"h_{i}_0", _PNG, collection="bulk", doc_hash="h").file_path)
            for i in range(3)
        ]
        storage.put("keep_0_0", _PNG, collection="other")
        deleted = storage.delete_images(collection="bulk")
        assert deleted == 3
        assert storage.count() == 1
        assert all(not p.exists() for p in paths)

    def test_delete_images_by_doc_hash(self, tmp_path: Path) -> None:
        storage = _storage(tmp_path)
        storage.put("h1_0_0", _PNG, collection="c", doc_hash="h1")
        storage.put("h1_0_1", _PNG, collection="c", doc_hash="h1")
        storage.put("h2_0_0", _PNG, collection="c", doc_hash="h2")
        assert storage.delete_images(doc_hash="h1") == 2
        assert storage.count() == 1

    def test_delete_images_requires_filter(self, tmp_path: Path) -> None:
        storage = _storage(tmp_path)
        storage.put("h1_0_0", _PNG, collection="c")
        with pytest.raises(ImageStorageError, match="at least one"):
            storage.delete_images()
        assert storage.count() == 1  # 未误删


# ---------------------------------------------------------------------------
# 契约破坏 fail-fast
# ---------------------------------------------------------------------------


class TestContractViolations:
    @pytest.mark.parametrize("bad_id", ["", None, 123, "../evil", "a/b", "a\\b", ".", ".."])
    def test_invalid_image_id(self, tmp_path: Path, bad_id) -> None:
        storage = _storage(tmp_path)
        with pytest.raises(ImageStorageError):
            storage.put(bad_id, _PNG, collection="c")  # type: ignore[arg-type]
        assert storage.count() == 0

    @pytest.mark.parametrize("bad_data", [b"", "", "not-bytes", 42, None, bytearray()])
    def test_invalid_data(self, tmp_path: Path, bad_data) -> None:
        storage = _storage(tmp_path)
        with pytest.raises(ImageStorageError):
            storage.put("h_0_0", bad_data, collection="c")  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad_page", [-1, "3", 1.5, True])
    def test_invalid_page_num(self, tmp_path: Path, bad_page) -> None:
        storage = _storage(tmp_path)
        with pytest.raises(ImageStorageError):
            storage.put("h_0_0", _PNG, collection="c", page_num=bad_page)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad_ext", ["", "  ", ".p/ng", 3])
    def test_invalid_ext(self, tmp_path: Path, bad_ext) -> None:
        storage = _storage(tmp_path)
        with pytest.raises(ImageStorageError):
            storage.put("h_0_0", _PNG, collection="c", ext=bad_ext)  # type: ignore[arg-type]

    def test_invalid_collection_separator(self, tmp_path: Path) -> None:
        storage = _storage(tmp_path)
        with pytest.raises(ImageStorageError, match="path separators"):
            storage.put("h_0_0", _PNG, collection="../escape")
        with pytest.raises(ImageStorageError, match="path separators"):
            storage.put("h_0_0", _PNG, doc_hash="a/b")
        assert storage.count() == 0


# ---------------------------------------------------------------------------
# 持久化与 trace
# ---------------------------------------------------------------------------


class TestPersistenceAndTrace:
    def test_reopen_preserves_index_and_schema(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "db" / "image_index.db")
        root = str(tmp_path / "images")
        storage = ImageStorage(images_root=root, db_path=db_path)
        storage.put("h1_0_0", _PNG, collection="c1", doc_hash="h1", page_num=1)
        storage.put("h1_0_1", _PNG, collection="c1", doc_hash="h1", page_num=1)
        storage.close()

        reopened = ImageStorage(images_root=root, db_path=db_path)  # schema 幂等重建
        assert reopened.count() == 2
        found = reopened.get("h1_0_0")
        assert found is not None and found.collection == "c1"
        assert [r.image_id for r in reopened.list_images(collection="c1")] == ["h1_0_0", "h1_0_1"]
        assert Path(found.file_path).is_file()
        reopened.close()

    def test_trace_records_storage_stage(self, tmp_path: Path) -> None:
        storage = _storage(tmp_path)
        trace = TraceContext()
        storage.put("h1_0_0", _PNG, collection="c", trace=trace)
        stage = trace.get_stage("storage")
        assert stage is not None
        assert stage["method"] == "image_storage"
        assert stage["details"]["action"] == "put"
        assert stage["details"]["replaced"] is False
        storage.put("h1_0_0", b"v2", collection="c", trace=trace)
        latest = trace.get_stage("storage")
        assert latest["details"]["replaced"] is True
        deleted = storage.delete_images(collection="c", trace=trace)
        final = trace.get_stage("storage")
        assert deleted == 1
        assert final["details"]["action"] == "delete_images"
        assert final["details"]["deleted"] == 1

    def test_record_to_dict_roundtrip(self, tmp_path: Path) -> None:
        storage = _storage(tmp_path)
        record = storage.put("h1_0_0", _PNG, collection="c", doc_hash="h1", page_num=3)
        data = record.to_dict()
        assert data["image_id"] == "h1_0_0"
        assert data["collection"] == "c"
        assert data["doc_hash"] == "h1"
        assert data["page_num"] == 3
        assert ImageRecord.from_row(
            (data["image_id"], data["file_path"], data["collection"], data["doc_hash"],
             data["page_num"], data["created_at"])
        ) == record

    def test_default_constants_match_spec(self) -> None:
        assert DEFAULT_IMAGES_ROOT == "data/images"
        assert DEFAULT_DB_PATH == "data/db/image_index.db"
