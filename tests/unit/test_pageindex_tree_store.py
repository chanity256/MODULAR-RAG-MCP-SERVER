"""PI1 tree_store 单测（原子写 / 幂等 / 删除返回派生 PDF / 哈希查找）。"""

import json
import sys
import threading
from pathlib import Path

PAGEINDEX_DIR = Path(__file__).resolve().parents[2] / "scripts" / "pageindex"
sys.path.insert(0, str(PAGEINDEX_DIR))

import pytest  # noqa: E402

from tree_store import TreeStore  # noqa: E402


def rec(source="/docs/a.md", file_hash="h1", fmt="md", **extra):
    base = {"source_path": source, "file_hash": file_hash, "format": fmt,
            "doc_id": "pi-1", "indexed_at": "2026-11-23T00:00:00", "node_count": 10}
    if fmt == "md":
        base["derived_pdf"] = "/data/db/pageindex/derived/h1.pdf"
        base["headings"] = [{"level": 1, "title": "A", "line": 1, "parents": []}]
    base.update(extra)
    return base


def test_upsert_get_roundtrip(tmp_path):
    store = TreeStore(tmp_path / "store.json")
    store.upsert(rec())
    assert store.get("/docs/a.md")["doc_id"] == "pi-1"
    on_disk = json.loads((tmp_path / "store.json").read_text(encoding="utf-8"))
    assert on_disk["/docs/a.md"]["file_hash"] == "h1"


def test_upsert_replaces_same_source_path(tmp_path):
    store = TreeStore(tmp_path / "store.json")
    store.upsert(rec(file_hash="h1"))
    store.upsert(rec(file_hash="h2", node_count=20))
    got = store.get("/docs/a.md")
    assert got["file_hash"] == "h2" and got["node_count"] == 20


def test_atomic_write_no_tmp_leftover(tmp_path):
    store = TreeStore(tmp_path / "store.json")
    store.upsert(rec())
    assert not (tmp_path / "store.json.tmp").exists()
    assert (tmp_path / "store.json").exists()


def test_missing_required_field_raises(tmp_path):
    store = TreeStore(tmp_path / "store.json")
    with pytest.raises(ValueError):
        store.upsert({"source_path": "/x", "format": "md"})
    with pytest.raises(ValueError):
        store.upsert(rec(fmt="txt"))
    assert store.list_all() == []


def test_remove_returns_record_then_idempotent(tmp_path):
    store = TreeStore(tmp_path / "store.json")
    store.upsert(rec())
    removed = store.remove("/docs/a.md")
    assert removed["derived_pdf"].endswith("h1.pdf")  # 调用方连带删除派生 PDF（D5）
    assert store.get("/docs/a.md") is None
    assert store.remove("/docs/a.md") is None  # 幂等


def test_find_by_hash(tmp_path):
    store = TreeStore(tmp_path / "store.json")
    store.upsert(rec(source="/docs/a.md", file_hash="h1"))
    store.upsert(rec(source="/docs/b.pdf", file_hash="h2", fmt="pdf"))
    assert store.find_by_hash("h2")["source_path"] == "/docs/b.pdf"
    assert store.find_by_hash("missing") is None


def test_corrupt_file_treated_as_empty(tmp_path):
    p = tmp_path / "store.json"
    p.write_text("{not json", encoding="utf-8")
    store = TreeStore(p)
    assert store.list_all() == []
    store.upsert(rec())  # 下次写入覆盖损坏内容
    assert store.get("/docs/a.md") is not None


def test_concurrent_upserts_produce_valid_store(tmp_path):
    store = TreeStore(tmp_path / "store.json")

    def worker(i: int) -> None:
        store.upsert(rec(source=f"/docs/{i}.md", file_hash=f"h{i}"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(store.list_all()) == 8
    # 重新加载磁盘文件：内容完整合法
    reloaded = TreeStore(tmp_path / "store.json")
    assert len(reloaded.list_all()) == 8


def test_existing_store_loaded_on_init(tmp_path):
    p = tmp_path / "store.json"
    p.write_text(json.dumps({"/docs/a.md": rec()}), encoding="utf-8")
    store = TreeStore(p)
    assert store.get("/docs/a.md")["format"] == "md"
