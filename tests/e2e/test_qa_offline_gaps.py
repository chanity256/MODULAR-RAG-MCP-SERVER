"""QA 计划可离线缺口的端到端补测（AI_NATIVE_SDLC_SPEC SD3.3）。

对照表见 ``.factory/skills/qa-tester/QA_PYTEST_MAP.md``：本文件承接 N、O 两段与 H-07 中
「能离线且此前无对应 pytest」的用例，用例 ID 写在每个测试的 docstring 里。

组装与 ``tests/e2e/test_data_ingestion.py`` 同法：真 PdfLoader（仓库自带的
``tests/fixtures/sample_documents/*.pdf``）、真 Chroma / BM25 / SQLite 历史表 / 图片库，
全部落在 ``tmp_path``；摄取侧唯一注入确定性哈希 embedding。查询侧的 embedding
一律抛错，HybridSearch 降级为 BM25 单路，与离线子进程用例（embedding 指向关闭端口）
行为一致，所以断言只依赖关键词命中，不依赖语义排序。删除走 ``DataService``，
与 Web UI Data Browser、``qa_multistep.py`` 同一入口。
"""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

import pytest

from core.query_engine.dense_retriever import DenseRetriever
from core.query_engine.fusion import RRFFusion
from core.query_engine.hybrid_search import HybridSearch
from core.query_engine.query_processor import QueryProcessor
from core.query_engine.sparse_retriever import SparseRetriever
from core.settings import Settings, load_settings
from ingestion.document_manager import DocumentManager
from ingestion.embedding import BatchProcessor, DenseEncoder, SparseEncoder
from ingestion.pipeline import IngestionPipeline
from ingestion.storage import BM25Indexer, ImageStorage, VectorUpserter
from libs.embedding.base_embedding import BaseEmbedding
from libs.loader.file_integrity import SQLiteIntegrityChecker
from libs.vector_store.vector_store_factory import VectorStoreFactory
from observability.dashboard.services.data_service import DataService

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sample_documents"


class _HashEmbedding(BaseEmbedding):
    """确定性哈希向量（dim=16），只用于摄取侧写入 Chroma。"""

    @property
    def dimension(self) -> int:
        return 16

    def embed(self, texts, *, trace=None, **kwargs):
        return [
            [int(hashlib.sha256(t.encode()).hexdigest()[i * 2 : i * 2 + 2], 16) / 255.0 for i in range(16)]
            for t in texts
        ]


class _DownEmbedding(BaseEmbedding):
    """查询侧 embedding 不可用：Dense 路失败，HybridSearch 降级为 BM25。"""

    @property
    def dimension(self) -> int:
        return 16

    def embed(self, texts, *, trace=None, **kwargs):
        raise RuntimeError("embedding offline")


def _settings(root: Path) -> Settings:
    path = root / "settings.yaml"
    path.write_text(
        dedent(
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
              persist_path: {root / "db" / "chroma"}
            retrieval:
              sparse_backend: bm25
              fusion_algorithm: rrf
            rerank:
              backend: none
            evaluation:
              backends: [custom]
            splitter:
              provider: recursive
              chunk_size: 400
              chunk_overlap: 50
            ingestion:
              chunk_refiner:
                use_llm: false
              metadata_enricher:
                use_llm: false
              image_captioner:
                enabled: false
              batch_size: 16
            """
        ),
        encoding="utf-8",
    )
    return load_settings(path)


@dataclass
class Stack:
    settings: Settings
    store: object
    bm25: BM25Indexer
    integrity: SQLiteIntegrityChecker
    images: ImageStorage
    pipeline: IngestionPipeline
    data: DataService

    def ingest(self, name: str, collection: str = "default", *, force: bool = False):
        return self.pipeline.run(str(FIXTURES / name), collection=collection, force=force)

    def hybrid(self) -> HybridSearch:
        return HybridSearch(
            self.settings,
            query_processor=QueryProcessor(self.settings),
            dense_retriever=DenseRetriever(self.settings, embedding_client=_DownEmbedding(), vector_store=self.store),
            sparse_retriever=SparseRetriever(self.settings, bm25_indexer=self.bm25, vector_store=self.store),
            fusion=RRFFusion(self.settings),
        )

    def search(self, query: str, collection: str, top_k: int = 10):
        return self.hybrid().search(query, top_k=top_k, filters={"collection": collection})

    def sources(self, query: str, collection: str, top_k: int = 10) -> list[str]:
        return [Path(r.metadata.get("source_path", "")).name for r in self.search(query, collection, top_k)]

    def chunks(self, name: str, collection: str) -> list[dict]:
        detail = self.data.get_document_detail(str(FIXTURES / name), collection)
        return [] if detail is None else detail.chunks


def _build(root: Path) -> Stack:
    settings = _settings(root)
    store = VectorStoreFactory.create(settings)
    bm25 = BM25Indexer(settings, index_path=root / "db" / "bm25" / "index.json")
    integrity = SQLiteIntegrityChecker(db_path=root / "db" / "ingestion_history.db")
    images = ImageStorage(images_root=str(root / "images"), db_path=str(root / "db" / "image_index.db"))
    pipeline = IngestionPipeline(
        settings,
        integrity_checker=integrity,
        dense_encoder=DenseEncoder(settings, _HashEmbedding()),
        sparse_encoder=SparseEncoder(settings),
        batch_processor=BatchProcessor(settings),
        bm25_indexer=bm25,
        vector_upserter=VectorUpserter(settings, store=store),
        image_storage=images,
    )
    manager = DocumentManager(chroma_store=store, bm25_indexer=bm25, image_storage=images, file_integrity=integrity)
    return Stack(settings, store, bm25, integrity, images, pipeline, DataService(manager=manager, image_storage=images))


@pytest.fixture()
def stack(tmp_path: Path) -> Stack:
    return _build(tmp_path)


SIMPLE_QUERY = "Sample Document PDF loader"


# ---------------------------------------------------------------------------
# N. 数据生命周期闭环
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_n01_ingest_query_delete_query(self, stack: Stack):
        """N-01：摄取 → 查询命中 → 删除 → 查询不再命中。"""
        assert stack.ingest("simple.pdf").status == "success"
        assert "simple.pdf" in stack.sources(SIMPLE_QUERY, "default")

        result = stack.data.delete_document(str(FIXTURES / "simple.pdf"), "default")
        assert result.chroma_deleted > 0 and result.bm25_deleted > 0 and result.integrity_removed

        assert "simple.pdf" not in stack.sources(SIMPLE_QUERY, "default")
        assert stack.data.list_documents("default") == []

    def test_n02_reingest_after_delete_is_not_skipped(self, stack: Stack):
        """N-02：删除后重新摄取不会被 SHA256 去重跳过，查询重新命中。"""
        stack.ingest("simple.pdf")
        stack.data.delete_document(str(FIXTURES / "simple.pdf"), "default")

        again = stack.ingest("simple.pdf")
        assert again.status == "success"
        assert "simple.pdf" in stack.sources(SIMPLE_QUERY, "default")

    def test_n03_collections_are_isolated(self, stack: Stack):
        """N-03：两份文档进两个集合，各自查询只返回本集合的文档。"""
        stack.ingest("simple.pdf", "isolate_a")
        stack.ingest("complex_technical_doc.pdf", "isolate_b")

        a = stack.sources(SIMPLE_QUERY, "isolate_a")
        b = stack.sources("Retrieval-Augmented Generation", "isolate_b")
        assert a and set(a) == {"simple.pdf"}
        assert b and set(b) == {"complex_technical_doc.pdf"}

    def test_n04_clear_all_then_recover(self, tmp_path: Path, stack: Stack):
        """N-04：清空全部存储（等价 ``qa_bootstrap.py clear``）后查询为空，重新摄取后恢复。"""
        stack.ingest("simple.pdf")
        assert "simple.pdf" in stack.sources(SIMPLE_QUERY, "default")

        import chromadb

        # 每次 CLI 调用都是新进程；进程内需清掉 chromadb 按路径缓存的客户端，才能模拟「清空后重启」。
        chromadb.api.client.SharedSystemClient.clear_system_cache()
        shutil.rmtree(tmp_path / "db")
        shutil.rmtree(tmp_path / "images", ignore_errors=True)

        fresh = _build(tmp_path)
        assert fresh.search(SIMPLE_QUERY, "default") == []
        assert fresh.ingest("simple.pdf").status == "success"
        assert "simple.pdf" in fresh.sources(SIMPLE_QUERY, "default")

    def test_n06_delete_in_one_collection_keeps_the_other(self, stack: Stack):
        """N-06：删除 col_1 中的文档后，col_2 中同一文件仍可查询命中，摄取历史仍在。

        受 N-05 缺陷影响，col_1 的 Chroma 记录在摄取 col_2 时已被覆盖，这里只锁定 col_2 不受删除波及。
        """
        stack.ingest("simple.pdf", "col_1")
        stack.ingest("simple.pdf", "col_2")

        stack.data.delete_document(str(FIXTURES / "simple.pdf"), "col_1")
        assert "simple.pdf" in stack.sources(SIMPLE_QUERY, "col_2")
        assert stack.ingest("simple.pdf", "col_2").status == "skipped"

    @pytest.mark.xfail(
        strict=True,
        reason="已知缺陷：chunk_id 不含 collection，同一文件进第二个集合会覆盖第一个集合的 Chroma 记录"
        "（04_INGESTION_ROBUSTNESS_SPEC_frozen.md「已知遗留问题」）；修复后此用例转绿，strict 迫使同步去掉 xfail",
    )
    def test_n05_same_file_in_two_collections_both_searchable(self, stack: Stack):
        """N-05：同一文件摄取到两个集合，两个集合各自可查询命中。"""
        stack.ingest("simple.pdf", "col_1")
        stack.ingest("simple.pdf", "col_2")
        assert "simple.pdf" in stack.sources(SIMPLE_QUERY, "col_2")
        assert "simple.pdf" in stack.sources(SIMPLE_QUERY, "col_1")


# ---------------------------------------------------------------------------
# O. 文档替换与多场景验证（仓库自带的中英文 PDF）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> Stack:
    """每份文档进以文件名命名的独立集合，查询按集合过滤，互不干扰。"""
    s = _build(tmp_path_factory.mktemp("qa_corpus"))
    for name in (
        "chinese_technical_doc.pdf",
        "chinese_table_chart_doc.pdf",
        "chinese_long_doc.pdf",
        "complex_technical_doc.pdf",
        "blogger_intro.pdf",
    ):
        assert s.ingest(name, Path(name).stem).status == "success"
    return s


def _hit_text(stack: Stack, query: str, collection: str) -> str:
    return "\n".join(r.text for r in stack.search(query, collection, top_k=5))


class TestDocuments:
    def test_o01_chinese_technical_doc_sparse_recall(self, corpus: Stack):
        """O-01：纯中文技术文档，中文关键词经 CJK 分词走 BM25 召回。"""
        for query, keyword in (("混合检索 RRF", "混合检索"), ("Transformer 注意力", "注意力")):
            assert keyword in _hit_text(corpus, query, "chinese_technical_doc")

    def test_o02_table_content_is_searchable(self, corpus: Stack):
        """O-02：中文表格内容被解析进 chunk，可按表格数据关键词命中。"""
        for keyword in ("BGE-large-zh", "Cross-Encoder"):
            assert keyword in _hit_text(corpus, keyword, "chinese_table_chart_doc")

    def test_o04_long_document_front_and_back_recall(self, corpus: Stack):
        """O-04：30+ 页长文档全部处理，前半部分与后半部分内容都能召回。"""
        chunks = corpus.chunks("chinese_long_doc.pdf", "chinese_long_doc")
        assert chunks and chunks[0]["metadata"].get("page_count", 0) >= 30
        assert "位置编码" in _hit_text(corpus, "Transformer 位置编码", "chinese_long_doc")
        assert "项目实战" in _hit_text(corpus, "项目实战经验", "chinese_long_doc")

    def test_o05_technical_terms_survive_chunking(self, corpus: Stack):
        """O-05：技术术语保留在 chunk 中，按 "ChromaDB" / "ada-002" 可召回对应段落。"""
        text = _hit_text(corpus, "ChromaDB text-embedding-ada-002 vector storage", "complex_technical_doc")
        assert "ChromaDB" in text or "ada-002" in text

    def test_o09_short_non_technical_doc(self, corpus: Stack):
        """O-09：博客类短文档 chunk 数明显少于长文档，查询可命中。"""
        short = corpus.chunks("blogger_intro.pdf", "blogger_intro")
        long = corpus.chunks("chinese_long_doc.pdf", "chinese_long_doc")
        assert 0 < len(short) < len(long) / 5
        assert "介绍" in _hit_text(corpus, "自我介绍", "blogger_intro")
        assert "博主" in _hit_text(corpus, "博客", "blogger_intro")


# ---------------------------------------------------------------------------
# H. CLI 查询（真实离线查询栈注入 scripts/query.py 的 main）
# ---------------------------------------------------------------------------

_QUERY_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "query.py"


def _query_cli():
    name = "scripts_query"  # 与 tests/unit/test_query_entry.py 共用同一模块对象
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, _QUERY_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


LONG_QUERY = (
    "Transformer 模型中的自注意力机制如何工作，包括 Multi-Head Attention 和 RoPE 位置编码的原理，以及 KV Cache "
    "优化策略。同时请解释 RAG 系统中混合检索的工作流程，包括 Dense Retrieval、BM25 Sparse Retrieval 和 RRF "
    "融合算法的具体实现方式。还有 Cross-Encoder Reranker 和 LLM Reranker 的对比分析，以及在生产环境中如何选择"
    "合适的向量数据库（如 ChromaDB、FAISS、Milvus）来存储和检索 Embedding 向量。请详细说明每个组件的优缺点和适用场景。"
)


def test_h07_long_query_cli_completes(stack: Stack):
    """H-07：约 250 字符的超长中英混合查询经 query.py 正常返回结果，不崩溃。"""
    stack.ingest("complex_technical_doc.pdf")
    assert len(LONG_QUERY) >= 240
    lines: list[str] = []
    code = _query_cli().main(
        ["--query", LONG_QUERY, "--no-rerank", "--collection", "default"],
        search=stack.hybrid(),
        out=lines.append,
    )
    out = "\n".join(lines)
    assert code == 0
    assert "complex_technical_doc.pdf" in out
