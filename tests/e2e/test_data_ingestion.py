"""数据摄取端到端测试（阶段 C15，同时承接 C14 遗留的 e2e 验收）。

C14 的 spec 原定 ``tests/integration/test_ingestion_pipeline.py`` 需要真实
Chroma/嵌入网络，按离线确定性纪律当时改成了纯单元；本文件按 C15 spec
（``tests/e2e/test_data_ingestion.py``，尽量用临时目录）补齐真实端到端：

- **真实组件**：真 PdfLoader（pypdf 解析真 PDF 字节流，含嵌入图片）、
  DocumentChunker、C5-C7 规则 transform（LLM 关闭）、BatchProcessor +
  SparseEncoder、BM25Indexer（JSON 落盘）、SQLiteIntegrityChecker、
  ImageStorage（SQLite + 文件）、ChromaStore（嵌入式 PersistentClient，
  离线可用），全部持久化落在 ``tmp_path``。
- **唯一注入的边界**：DenseEncoder 的 embedding client 用确定性哈希向量
  （真 embedding provider 需网络/API Key）。
- **入口**：直接调 ``scripts/ingest.py`` 的 ``main(argv, pipeline=...)``，
  覆盖参数解析 → 目录收集 → 逐文件 dispatch → 聚合 → 退出码全链路。

环境门控（对齐 ``RAG_LLM_INTEGRATION`` 先例）：
- 默认（离线）：全部测试确定性运行，无网络。
- ``RAG_E2E_INTEGRATION=1`` 时额外运行真实 provider 的子进程 CLI 冒烟
  （真 ``python scripts/ingest.py``，使用仓库默认配置的真实 embedding），
  会在临时 CWD 下的 ``data/db`` 产生真实产物。
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from ingestion.embedding import BatchProcessor, DenseEncoder, SparseEncoder
from ingestion.pipeline import IngestionPipeline
from ingestion.storage import BM25Indexer, ImageStorage, VectorUpserter
from libs.embedding.base_embedding import BaseEmbedding
from libs.loader.file_integrity import SQLiteIntegrityChecker
from libs.vector_store.vector_store_factory import VectorStoreFactory

pytestmark = pytest.mark.e2e

_INTEGRATION_ENABLED = os.environ.get("RAG_E2E_INTEGRATION") == "1"

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ingest.py"


def _load_cli():
    name = "scripts_ingest_e2e"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = _load_cli()

# ---------------------------------------------------------------------------
# 极简 PDF 构造器（与 tests/unit/test_loader_pdf_contract.py 同法：正确计算
# xref 偏移的最小合法 PDF，pypdf 可解析；仓库无预置 PDF fixture）
# ---------------------------------------------------------------------------

_FONT = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
_IMAGE_PIXELS = b"\xff\x00\x00\x00\xff\x00\x00\x00\xff\xff\xff\xff"


def _build_pdf(objects: list[bytes]) -> bytes:
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF"
    ).encode("latin-1")
    return bytes(out)


def _content_stream(text: str) -> bytes:
    payload = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    return b"<< /Length %d >>\nstream\n" % len(payload) + payload + b"\nendstream"


def _page(parent: int, contents: int, resources: bytes) -> bytes:
    return (
        f"<< /Type /Page /Parent {parent} 0 R /MediaBox [0 0 612 792] "
        f"/Contents {contents} 0 R /Resources {resources.decode('latin-1')} >>"
    ).encode("latin-1")


def _image_xobject() -> bytes:
    return (
        b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 "
        b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Length 12 >>\n"
        b"stream\n" + _IMAGE_PIXELS + b"\nendstream"
    )


def _write_simple_pdf(path: Path) -> Path:
    path.write_bytes(
        _build_pdf(
            [
                b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                _page(2, 4, b"<< /Font << /F1 5 0 R >> >>"),
                _content_stream("Hello ingestion e2e simple document"),
                _FONT,
            ]
        )
    )
    return path


def _write_pdf_with_images(path: Path) -> Path:
    path.write_bytes(
        _build_pdf(
            [
                b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>",
                _page(2, 4, b"<< /Font << /F1 5 0 R >> /XObject << /Im0 7 0 R >> >>"),
                _content_stream("First page about system architecture overview"),
                _FONT,
                _page(2, 8, b"<< /Font << /F1 5 0 R >> >>"),
                _image_xobject(),
                _content_stream("Second page conclusion with embedded figure"),
            ]
        )
    )
    return path


# ---------------------------------------------------------------------------
# 确定性离线 embedding + 真实组件组装
# ---------------------------------------------------------------------------


class HashEmbedding(BaseEmbedding):
    """确定性哈希向量（同文本同向量，dim=16），零网络。"""

    def __init__(self, dimension: int = 16) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts, *, trace=None, **kwargs):
        return [
            [
                round(
                    int(hashlib.sha256(t.encode()).hexdigest()[i * 2 : i * 2 + 2], 16) % 97 / 97.0,
                    6,
                )
                for i in range(self._dimension)
            ]
            for t in texts
        ]


def _settings_yaml(tmp_path: Path) -> str:
    return dedent(
        f"""\
        llm:
          provider: azure
          model: gpt-4o
        embedding:
          provider: openai
          model: text-embedding-3-small
        vision_llm:
          provider: azure
          model: gpt-4o
        vector_store:
          backend: chroma
          persist_path: {tmp_path / "db" / "chroma"}
        retrieval:
          sparse_backend: bm25
          fusion_algorithm: rrf
        rerank:
          backend: none
        evaluation:
          backends: [custom]
        splitter:
          provider: recursive
          chunk_size: 60
          chunk_overlap: 10
        ingestion:
          chunk_refiner:
            use_llm: false
          metadata_enricher:
            use_llm: false
          image_captioner:
            enabled: false
          batch_size: 4
        """
    )


def _build_real_pipeline(tmp_path: Path):
    """全真实组件（loader 走 pipeline 扩展名路由 = 真 PdfLoader），
    唯一注入 HashEmbedding；所有持久化落在 tmp_path。"""
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(_settings_yaml(tmp_path), encoding="utf-8")
    settings = load_settings(settings_path)

    store = VectorStoreFactory.create(settings)  # 真 ChromaStore（嵌入式，离线）
    embedding = HashEmbedding()
    bm25 = BM25Indexer(settings, index_path=tmp_path / "db" / "bm25" / "index.json")
    integrity = SQLiteIntegrityChecker(db_path=tmp_path / "db" / "ingestion_history.db")
    image_storage = ImageStorage(
        images_root=str(tmp_path / "images"),
        db_path=str(tmp_path / "db" / "image_index.db"),
    )
    pipeline = IngestionPipeline(
        settings,
        integrity_checker=integrity,
        dense_encoder=DenseEncoder(settings, embedding),
        sparse_encoder=SparseEncoder(settings),
        batch_processor=BatchProcessor(settings),
        bm25_indexer=bm25,
        vector_upserter=VectorUpserter(settings, store=store),
        image_storage=image_storage,
    )
    return pipeline, {"settings": settings, "store": store, "bm25": bm25, "image_storage": image_storage}


def _docs_dir(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir()
    _write_simple_pdf(docs / "simple.pdf")
    _write_pdf_with_images(docs / "with_images.pdf")
    # IR7.3 起 .md 已登记，不受支持扩展名的哨兵改用 .txt
    (docs / "notes.txt").write_text("忽略的非支持扩展名文件")
    return docs


def _run(argv: list[str], pipeline) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = cli.main(argv, pipeline=pipeline, out=lines.append)
    return code, lines


# ---------------------------------------------------------------------------
# 验收：命令行可运行并在 data/db 产生产物（tmp_path 扮演 data 根）
# ---------------------------------------------------------------------------


def test_cli_directory_ingest_end_to_end_artifacts(tmp_path):
    pipeline, parts = _build_real_pipeline(tmp_path)
    docs = _docs_dir(tmp_path)

    code, lines = _run(["--path", str(docs), "--collection", "e2e-docs", "--verbose"], pipeline)

    assert code == 0
    joined = "\n".join(lines)
    assert "2 total | 2 ingested | 0 skipped | 0 failed" in joined
    # 逐文件行 + 进度行（verbose 透传 on_progress）
    assert "success" in joined and "load 1/1" in joined

    # 向量库：真 ChromaDB 持久化 + collection 元数据过滤可用（D 阶段契约）
    store = parts["store"]
    vector_count = store.count()
    assert vector_count > 0
    probe = HashEmbedding().embed(["system architecture overview"])[0]
    hits = store.query(probe, top_k=vector_count)
    assert hits, "expected queryable vectors after ingestion"
    assert all(h.metadata.get("collection") == "e2e-docs" for h in hits)

    # BM25：索引文件落盘且可重载，docs 数与向量数一致（每个 chunk 一条）
    bm25_path = tmp_path / "db" / "bm25" / "index.json"
    assert bm25_path.exists()
    reloaded = BM25Indexer(parts["settings"], index_path=bm25_path).load()
    assert reloaded.num_docs == vector_count

    # 图片：提取字节已登记 SQLite 索引且文件存在
    storage = parts["image_storage"]
    assert storage.count() == 1
    record = storage.list_images()[0]
    assert Path(record.file_path).exists()
    assert record.file_path.startswith(str(tmp_path / "images"))

    # trace：两文件均有 trace id（CLI 输出行内）
    assert joined.count("trace=") == 2


def test_cli_rerun_skips_unchanged_and_force_reingests(tmp_path):
    pipeline, parts = _build_real_pipeline(tmp_path)
    docs = _docs_dir(tmp_path)
    first_code, first_lines = _run(["--path", str(docs), "--collection", "e2e"], pipeline)
    assert first_code == 0
    count_after_first = parts["store"].count()

    # 重跑（从磁盘状态全新构建组件，验证持久化驱动跳过）：全部 skipped。
    fresh_pipeline, fresh_parts = _build_real_pipeline(tmp_path)
    second_code, second_lines = _run(["--path", str(docs), "--collection", "e2e"], fresh_pipeline)
    assert second_code == 0
    assert "2 total | 0 ingested | 2 skipped | 0 failed" in "\n".join(second_lines)
    assert fresh_parts["store"].count() == count_after_first  # 无重复写入
    fresh_bm25 = BM25Indexer(fresh_parts["settings"], index_path=tmp_path / "db" / "bm25" / "index.json").load()
    assert fresh_bm25.num_docs == count_after_first

    # --force：强制重摄取，幂等替换（计数不增长）。
    force_code, force_lines = _run(
        ["--path", str(docs), "--collection", "e2e", "--force"], fresh_pipeline
    )
    assert force_code == 0
    assert "2 total | 2 ingested | 0 skipped | 0 failed" in "\n".join(force_lines)
    assert fresh_parts["store"].count() == count_after_first


def test_cli_single_file_path(tmp_path):
    pipeline, parts = _build_real_pipeline(tmp_path)
    pdf = _write_simple_pdf(tmp_path / "solo.pdf")

    code, lines = _run(["--path", str(pdf), "--collection", "solo"], pipeline)

    assert code == 0
    assert "1 total | 1 ingested | 0 skipped | 0 failed" in "\n".join(lines)
    assert parts["store"].count() > 0


def test_cli_broken_pdf_maps_to_failed_file_not_crash(tmp_path):
    pipeline, parts = _build_real_pipeline(tmp_path)
    docs = _docs_dir(tmp_path)
    (docs / "broken.pdf").write_bytes(b"%PDF-1.4\nthis is not a real pdf body\n%%EOF")

    code, lines = _run(["--path", str(docs), "--collection", "e2e"], pipeline)

    assert code == 1
    joined = "\n".join(lines)
    assert "2 ingested" in joined and "1 failed" in joined
    assert "broken.pdf" in joined
    # 好文件仍然完整落库
    assert parts["store"].count() > 0
    assert parts["image_storage"].count() == 1


# ---------------------------------------------------------------------------
# 真实 provider 的子进程 CLI 冒烟（默认跳过，RAG_E2E_INTEGRATION=1 启用）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _INTEGRATION_ENABLED, reason="requires real embedding provider; set RAG_E2E_INTEGRATION=1")
def test_real_provider_subprocess_cli(tmp_path):
    """真 ``python scripts/ingest.py``：默认配置 + 真 embedding provider。

    CWD 切到 tmp_path，使相对路径产物（data/db/...）落在临时目录，
    同时默认配置路径是绝对路径不受 CWD 影响。
    """
    docs = tmp_path / "docs"
    docs.mkdir()
    _write_simple_pdf(docs / "sample.pdf")
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "--path", str(docs), "--collection", "gated"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert (tmp_path / "data" / "db" / "bm25" / "index.json").exists()
    assert (tmp_path / "data" / "db" / "chroma").exists()
