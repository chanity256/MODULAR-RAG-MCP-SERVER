"""IngestionPipeline：MVP 流程编排（阶段 C14）。

把阶段 C2-C13 的组件串成一条确定性流水线（spec：integrity→load→split→
transform→encode→store），对失败步骤抛出带阶段上下文的清晰异常：

1. **integrity**：``compute_sha256`` + ``should_skip``（C2）——未变更且非
   ``force`` 时零成本跳过（直接返回 ``status="skipped"``）。
2. **load**：按文件扩展名路由 Loader（``.pdf`` → ``PdfLoader``，C3；
   DOCX/PPTX/XLSX/HTML → ``MarkitdownLoader``，IR5；
   ``.md``/``.markdown`` → ``MarkdownLoader``，IR7）；
   可注入 ``BaseLoader`` 供测试/新格式复用。
3. **split**：``DocumentChunker``（C4）产出确定性 chunk id，并把本次
   ``collection`` 写入每个 chunk 的 ``metadata["collection"]``（供 D 阶段
   ``BaseVectorStore.query(filters={"collection": ...})`` 过滤）。
4. **transform**：``ChunkRefiner`` → ``MetadataEnricher`` → ``ImageCaptioner``
   （C5-C7，内部自带降级，LLM 关闭/失败不阻塞摄取）。
5. **encode**：``BatchProcessor``（C10）按 ``ingestion.batch_size`` 分批驱动
   ``DenseEncoder``（C8）与 ``SparseEncoder``（C9），保序拼接。
6. **store**：
   - BM25：已有索引文件则 ``load`` 后增量 ``add``（同 chunk_id 替换），
     否则在空索引上 ``add``；随后 ``save`` 原子落盘（C11）。
   - 向量：``VectorUpserter`` 幂等 upsert（C12）。
   - 图片：读取 Loader 落盘的 ``ImageRef.path`` 字节，经 ``ImageStorage.put``
     登记 SQLite 索引（C13）；目录键取 ``doc_hash``（对齐 C3 布局与
     DEV_SPEC"{collection}/（实际存储在 {doc_hash}/ 子目录下）"注记），
     单图缺文件仅告警跳过不阻塞。
7. **mark_success**：全链路成功后写回完整性记录（下次重跑跳过）。

失败语义：任一阶段抛出的异常统一包装为 ``IngestionPipelineError``
（携带 ``stage``），已算出 file_hash 时同步 ``mark_failed``（下次可重跑）；
Transform/LLM/Vision 的可降级失败在组件内部消化，不会触发本异常。

重试与回滚（IR4）：embed / store 阶段的可重试错误（分类见
``ingestion.errors``）按 ``ingestion.retry`` 指数退避重试，缺省
``max_attempts=1`` 即不重试；store 写入最终失败时按 ``doc_hash`` 回滚已写入的
BM25 / 向量 / 图片，回滚自身失败只记录（trace + ``error_msg``），不掩盖原始异常。

进度回调（03-tech-stack 5.3 契约）：``on_progress(stage_name, current, total)``
按 load/split/transform/embed/upsert 五个阶段名上报，embed 按批粒度、
transform 按子步骤（1..3）、upsert 按存储子步骤（1..3）递增；
回调为 ``None`` 时行为完全一致。

组件全部支持构造注入（fake loader/embedding/store/LLM 即可离线单测），
未注入时惰性按 ``Settings`` 创建（首个 ``run`` 时初始化一次）。
"""

from __future__ import annotations

import random
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from core.settings import Settings, resolve_ingestion_versions
from core.trace.persist import persist_trace
from core.trace.trace_context import TRACE_TYPE_INGESTION, TraceContext
from core.types import Chunk, Document
from ingestion.chunking import DocumentChunker
from ingestion.errors import compute_backoff, is_retryable
from ingestion.embedding import BatchProcessor, DenseEncoder, SparseEncoder
from ingestion.storage import BM25Indexer, ImageStorage, VectorUpserter
from ingestion.transform import ChunkRefiner, ImageCaptioner, MetadataEnricher
from libs.loader.base_loader import BaseLoader
from libs.loader.file_integrity import FileIntegrityChecker
from libs.loader.markitdown_loader import SUPPORTED_EXTENSIONS as MARKITDOWN_EXTENSIONS
from libs.loader.markitdown_loader import MarkitdownLoader
from libs.loader.markdown_loader import MarkdownLoader
from libs.loader.pdf_loader import PdfLoader
from observability.logger import get_logger

__all__ = [
    "IngestionPipeline",
    "IngestionPipelineError",
    "IngestionResult",
    "ProgressCallback",
    "STAGE_INTEGRITY",
    "STAGE_LOAD",
    "STAGE_SPLIT",
    "STAGE_TRANSFORM",
    "STAGE_EMBED",
    "STAGE_STORE",
    "STAGE_UPSERT",
]

# ---------------------------------------------------------------------------
# 阶段名常量（trace 打点 + on_progress 上报共用）
# ---------------------------------------------------------------------------

STAGE_INTEGRITY = "integrity"
STAGE_LOAD = "load"
STAGE_SPLIT = "split"
STAGE_TRANSFORM = "transform"
STAGE_EMBED = "embed"
STAGE_STORE = "store"

#: 向进度回调上报时使用的存储阶段名（对齐 03-tech-stack 的五个阶段名）。
STAGE_UPSERT = "upsert"

#: 进度回调签名：``on_progress(stage_name, current, total)``。
ProgressCallback = Callable[[str, int, int], None]

#: Markdown 扩展名（IR7.3 登记路由，与 ``scripts/ingest.py`` 双登记对齐）。
# Note: MarkdownLoader 不进 per-suffix 缓存（vault_root 随源变化）— 见
# .agents/notes/implemented/feature/2026-10-07-markdown-loader-vault-root-no-cache.md
_MARKDOWN_EXTENSIONS = frozenset({".md", ".markdown"})

#: 扩展名 -> Loader 构造规则（新格式在此登记或注入 ``BaseLoader``）。
#: 双登记约束：必须与 ``scripts/ingest.py`` 的 ``SUPPORTED_EXTENSIONS`` 同步。
_SUPPORTED_EXTENSIONS = {".pdf"} | set(MARKITDOWN_EXTENSIONS) | set(_MARKDOWN_EXTENSIONS)


class IngestionPipelineError(RuntimeError):
    """Pipeline 阶段失败（``stage`` 标注失败发生的阶段名）。"""

    def __init__(self, message: str, *, stage: str = "") -> None:
        self.stage = stage
        self.attempts = 1
        #: store 回滚自身失败时的描述（回滚成功或未执行时为 None）。
        self.rollback_error: str | None = None
        super().__init__(message)


@dataclass
class IngestionResult:
    """单文件摄取结果（可序列化）。

    Attributes:
        source_path: 摄取的源文件路径。
        collection: 本次写入的目标集合名。
        doc_hash: 源文件 SHA256（幂等/增量判定键）。
        status: ``"success"``（完整执行）或 ``"skipped"``（未变更跳过）；
            失败路径不返回结果而是抛出 ``IngestionPipelineError``。
        chunks: 产出并写入的 chunk 数。
        images: 成功登记到 ImageStorage 的图片数（缺文件的图片跳过不计）。
        written: 向量库返回的写入记录数（``UpsertSummary.written``）。
        trace_id: 本次摄取的 trace 标识（跳过路径也有 trace）。
        elapsed_ms: 总耗时（毫秒，保留 3 位小数）。
        error: 预留字段（成功/跳过恒为 None；失败信息在异常上）。
    """

    source_path: str
    collection: str
    doc_hash: str
    status: str
    chunks: int = 0
    images: int = 0
    written: int = 0
    trace_id: str = ""
    elapsed_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "collection": self.collection,
            "doc_hash": self.doc_hash,
            "status": self.status,
            "chunks": self.chunks,
            "images": self.images,
            "written": self.written,
            "trace_id": self.trace_id,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }


class IngestionPipeline:
    """串行执行 integrity→load→split→transform→encode→store 的编排器。

    Args:
        settings: 全局配置（驱动所有未注入组件的默认构造）。
        integrity_checker: 完整性检查器（默认 ``SQLiteIntegrityChecker``）。
        loader: 注入的文档加载器（缺省按扩展名路由，当前 ``.pdf`` →
            ``PdfLoader(images_root=ImageStorage.images_root)``）。
        chunker: ``Document -> List[Chunk]`` 切分器（默认 ``DocumentChunker``）。
        chunk_refiner / metadata_enricher / image_captioner: C5-C7 增强组件。
        dense_encoder / sparse_encoder: C8/C9 编码器。
        batch_processor: C10 批处理编排（默认 ``ingestion.batch_size``）。
        bm25_indexer: C11 倒排索引器（默认 ``data/db/bm25/index.json``）。
        vector_upserter: C12 幂等写入器（默认经 VectorStoreFactory 创建）。
        image_storage: C13 图片存储（默认 ``data/images`` + SQLite 索引）。
        sleep: 退避等待函数（默认 ``time.sleep``，测试注入 fake 时钟）。
        random_fn: 抖动随机源，返回 ``[0, 1)``（默认 ``random.random``）。

    组件惰性创建：首次 ``run`` 时统一初始化一次，注入项原样保留；
    因此构造 Pipeline 本身不触发任何 provider 连接/IO。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        integrity_checker: FileIntegrityChecker | None = None,
        loader: BaseLoader | None = None,
        chunker: DocumentChunker | None = None,
        chunk_refiner: ChunkRefiner | None = None,
        metadata_enricher: MetadataEnricher | None = None,
        image_captioner: ImageCaptioner | None = None,
        dense_encoder: DenseEncoder | None = None,
        sparse_encoder: SparseEncoder | None = None,
        batch_processor: BatchProcessor | None = None,
        bm25_indexer: BM25Indexer | None = None,
        vector_upserter: VectorUpserter | None = None,
        image_storage: ImageStorage | None = None,
        sleep: Callable[[float], None] | None = None,
        random_fn: Callable[[], float] | None = None,
    ) -> None:
        self._settings = settings
        self._sleep = sleep if sleep is not None else time.sleep
        self._random = random_fn if random_fn is not None else random.random
        self._logger = get_logger("modular-rag.ingestion.pipeline")
        self._injected = {
            "integrity_checker": integrity_checker,
            "loader": loader,
            "chunker": chunker,
            "chunk_refiner": chunk_refiner,
            "metadata_enricher": metadata_enricher,
            "image_captioner": image_captioner,
            "dense_encoder": dense_encoder,
            "sparse_encoder": sparse_encoder,
            "batch_processor": batch_processor,
            "bm25_indexer": bm25_indexer,
            "vector_upserter": vector_upserter,
            "image_storage": image_storage,
        }
        self._initialized = False

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def run(
        self,
        source_path: str,
        collection: str = "default",
        on_progress: ProgressCallback | None = None,
        *,
        force: bool = False,
        trace: TraceContext | None = None,
    ) -> IngestionResult:
        """摄取单个文件到指定集合。

        Args:
            source_path: 源文件路径（按扩展名路由 loader，或注入 loader）。
            collection: 目标集合名（写入 chunk metadata，供检索过滤）。
            on_progress: 可选进度回调 ``on_progress(stage, current, total)``。
            force: True 时跳过完整性检查强制重摄取（幂等替换）。
            trace: 可选追踪上下文（缺省新建，trace_id 随结果返回）。

        Returns:
            ``IngestionResult``（``status`` 为 ``"success"`` / ``"skipped"``）。

        Raises:
            IngestionPipelineError: 任一阶段失败（``stage`` 标注阶段名）；
                已知 file_hash 时会先 ``mark_failed`` 记录失败原因。
        """
        self._ensure_components()
        source = str(source_path)
        trace = trace if trace is not None else TraceContext(
            trace_type=TRACE_TYPE_INGESTION,
            metadata={"source_path": source, "collection": collection},
        )
        started = time.perf_counter()
        file_hash: str | None = None
        try:
            # ---- 阶段 1：integrity（SHA256 + 未变更跳过） ----
            with self._stage(STAGE_INTEGRITY, source):
                file_hash = self._integrity_checker.compute_sha256(source)
                skipped = not force and self._integrity_checker.should_skip(
                    file_hash, source_path=source, collection=collection
                )
                trace.record_stage(
                    STAGE_INTEGRITY,
                    method="pipeline",
                    details={"file_hash": file_hash, "force": bool(force), "skipped": skipped},
                )
            if skipped:
                elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
                self._logger.info(
                    "skip unchanged file %s (hash=%s..., collection=%s)",
                    source, file_hash[:12], collection,
                )
                persist_trace(trace, log_file=self._settings.observability.log_file)
                return IngestionResult(
                    source_path=source,
                    collection=collection,
                    doc_hash=file_hash,
                    status="skipped",
                    trace_id=trace.trace_id,
                    elapsed_ms=elapsed_ms,
                )
            self._logger.info(
                "ingesting %s (hash=%s..., collection=%s, force=%s)",
                source, file_hash[:12], collection, force,
            )

            # ---- 阶段 2：load ----
            with self._stage(STAGE_LOAD, source):
                document = self._resolve_loader(source).load(source)
                image_count = len(document.image_refs)
                trace.record_stage(
                    STAGE_LOAD,
                    method="pipeline",
                    details={
                        "doc_id": document.id,
                        "text_length": len(document.text),
                        "images": image_count,
                    },
                )
            self._logger.info("loaded %s: %d chars, %d images", source, len(document.text), image_count)
            self._emit_progress(on_progress, STAGE_LOAD, 1, 1)

            # ---- 阶段 3：split（+ collection 元数据标记） ----
            with self._stage(STAGE_SPLIT, source):
                chunks = self._chunker.split_document(document)
                for chunk in chunks:
                    chunk.metadata["collection"] = collection
                trace.record_stage(
                    STAGE_SPLIT, method="pipeline", details={"chunks": len(chunks)}
                )
            self._logger.info("split %s into %d chunks", source, len(chunks))
            self._emit_progress(on_progress, STAGE_SPLIT, 1, 1)

            # ---- 阶段 4：transform（refine → enrich → caption） ----
            transforms = (self._chunk_refiner, self._metadata_enricher, self._image_captioner)
            with self._stage(STAGE_TRANSFORM, source):
                for step, transform in enumerate(transforms, start=1):
                    chunks = transform.transform(chunks, trace=trace)
                    self._emit_progress(on_progress, STAGE_TRANSFORM, step, len(transforms))
                trace.record_stage(
                    STAGE_TRANSFORM,
                    method="pipeline",
                    details={"chunks": len(chunks), "steps": [t.name for t in transforms]},
                )
            self._logger.info("transformed %d chunks (rule/LLM degradation handled inside)", len(chunks))

            # ---- 阶段 5：encode（dense + sparse，按批驱动） ----
            (vectors, sparse_stats), embed_attempts = self._run_with_retry(
                STAGE_EMBED, source, trace,
                lambda: self._encode(chunks, trace=trace, on_progress=on_progress),
            )
            trace.record_stage(
                STAGE_EMBED,
                method="pipeline",
                details={
                    "chunks": len(chunks),
                    "dimension": len(vectors[0]) if vectors else None,
                    **self._attempt_details(embed_attempts),
                },
            )
            self._logger.info(
                "encoded %d chunks (dense dim=%s, sparse stats=%d)",
                len(chunks), len(vectors[0]) if vectors else None, len(sparse_stats),
            )

            # ---- 阶段 6：store（旧版本清理 → BM25 → 向量 → 图片） ----
            # 清理失败同样记为 store 阶段并中止，不进入新旧混合写入。
            replaced, _ = self._run_with_retry(
                STAGE_STORE, source, trace,
                lambda: self._prepare_version_replacement(source, collection, file_hash),
            )
            touched: set[str] = set()
            try:
                (written, images_persisted), store_attempts = self._run_with_retry(
                    STAGE_STORE, source, trace,
                    lambda: self._store(
                        document, chunks, sparse_stats, vectors,
                        collection=collection, trace=trace, on_progress=on_progress,
                        touched=touched,
                    ),
                )
            except IngestionPipelineError as exc:
                self._rollback_store(source, collection, document.id, touched, exc, trace)
                raise
            trace.record_stage(
                STAGE_STORE,
                method="pipeline",
                details={
                    "chunks": len(chunks),
                    "written": written,
                    "images": images_persisted,
                    "replaced": replaced.to_dict() if replaced is not None else None,
                    **self._attempt_details(store_attempts),
                },
            )

            # ---- 阶段 7：mark_success ----
            with self._stage(STAGE_INTEGRITY, source):
                self._integrity_checker.mark_success(
                    file_hash,
                    source,
                    collection=collection,
                    **self._version_tags(),
                )

            persist_trace(trace, log_file=self._settings.observability.log_file)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            self._logger.info(
                "ingested %s: %d chunks (written=%d), %d images (%.1fms)",
                source, len(chunks), written, images_persisted, elapsed_ms,
            )
            return IngestionResult(
                source_path=source,
                collection=collection,
                doc_hash=file_hash,
                status="success",
                chunks=len(chunks),
                images=images_persisted,
                written=written,
                trace_id=trace.trace_id,
                elapsed_ms=elapsed_ms,
            )
        except Exception as exc:
            self._record_failure(file_hash, source, exc, trace, collection=collection)
            persist_trace(trace, log_file=self._settings.observability.log_file)
            raise

    # ------------------------------------------------------------------
    # 内部：阶段执行
    # ------------------------------------------------------------------

    def _encode(
        self,
        chunks: list[Chunk],
        *,
        trace: TraceContext,
        on_progress: ProgressCallback | None,
    ) -> tuple[list[list[float]], list]:
        """按批驱动 dense/sparse 编码并拼接（embed 进度按批上报）。"""
        batch_count = len(self._batch_processor.split(chunks))
        total_batches = 2 * batch_count  # dense 一趟 + sparse 一趟
        done = 0

        def dense_fn(batch: list[Chunk]) -> list[list[float]]:
            nonlocal done
            result = self._dense_encoder.encode(batch, trace=trace)
            done += 1
            self._emit_progress(on_progress, STAGE_EMBED, done, total_batches)
            return result

        def sparse_fn(batch: list[Chunk]) -> list:
            nonlocal done
            result = self._sparse_encoder.encode_chunks(batch, trace=trace)
            done += 1
            self._emit_progress(on_progress, STAGE_EMBED, done, total_batches)
            return result

        vectors = self._batch_processor.run(chunks, dense_fn, trace=trace, stage="embedding")
        sparse_stats = self._batch_processor.run(chunks, sparse_fn, trace=trace, stage="embedding")
        return vectors, sparse_stats

    def _store(
        self,
        document: Document,
        chunks: list[Chunk],
        sparse_stats: list,
        vectors: list[list[float]],
        *,
        collection: str,
        trace: TraceContext,
        on_progress: ProgressCallback | None,
        touched: set[str] | None = None,
    ) -> tuple[int, int]:
        """三路持久化：BM25 增量索引 → 向量幂等 upsert → 图片登记。

        ``touched`` 收集已开始写入的存储名（``bm25`` / ``vectors`` / ``images``），
        供失败回滚判定范围；``bm25`` 仅在内存索引已与磁盘对齐后才登记。

        Returns:
            ``(向量写入记录数, 成功登记图片数)``。
        """
        touched = touched if touched is not None else set()
        # 6a. BM25：已有索引文件则载入后增量 add（同 chunk_id 替换），
        #     否则在空索引上 add（等价于全量构建）。
        try:
            self._bm25_indexer.load()
        except FileNotFoundError:
            pass  # 尚无持久化索引：从当前（空）内存索引起步
        touched.add("bm25")
        self._bm25_indexer.add(sparse_stats, trace=trace)
        self._bm25_indexer.save()
        self._emit_progress(on_progress, STAGE_UPSERT, 1, 3)

        # 6b. 向量库幂等 upsert（chunk.id 为幂等键）。
        touched.add("vectors")
        summary = self._vector_upserter.upsert(chunks, vectors, trace=trace)
        self._emit_progress(on_progress, STAGE_UPSERT, 2, 3)

        # 6c. 图片：读取 Loader 落盘字节，登记 C13 SQLite 索引；
        #     目录键取 doc_hash（对齐 C3 布局），缺文件告警跳过不阻塞。
        images_persisted = 0
        for ref in document.image_refs:
            image_path = Path(ref.path)
            try:
                data = image_path.read_bytes()
            except OSError as exc:
                self._logger.warning("image file unreadable, skip %s: %s", ref.id, exc)
                continue
            ext = image_path.suffix or ".png"
            touched.add("images")
            self._image_storage.put(
                ref.id,
                data,
                doc_hash=document.id,
                page_num=ref.page,
                ext=ext,
                trace=trace,
            )
            images_persisted += 1
        self._emit_progress(on_progress, STAGE_UPSERT, 3, 3)
        return summary.written, images_persisted

    def _prepare_version_replacement(self, source: str, collection: str, file_hash: str):
        """历史记录哈希与当前不同时，经 ``DocumentManager`` 清理旧版本（M2）。

        Returns:
            执行清理时为 ``DeleteResult``，无需替换时为 None。
        """
        try:
            record = self._integrity_checker.get_record(source, collection)
        except NotImplementedError:
            # 只实现 v1 最小契约的自定义检查器没有路径维度记录，无法判定版本替换。
            return None
        old_hash = (record or {}).get("file_hash")
        if not old_hash or old_hash == file_hash:
            return None
        # DocumentManager 会在共享的索引器实例上删除后整体 save；
        # 内存态必须先与磁盘对齐，否则空的内存索引会覆盖掉磁盘上的全量索引。
        try:
            self._bm25_indexer.load()
        except FileNotFoundError:
            pass
        result = self._document_manager().prepare_version_replacement(source, collection, file_hash)
        if result is not None:
            self._logger.info(
                "cleared old version of %s (old hash=%s..., collection=%s): chroma=%d bm25=%d images=%d",
                source, str(old_hash)[:12], collection,
                result.chroma_deleted, result.bm25_deleted, result.images_deleted,
            )
        return result

    def _run_with_retry(
        self,
        stage: str,
        source: str,
        trace: TraceContext,
        fn: Callable[[], Any],
    ) -> tuple[Any, int]:
        """在 ``_stage`` 包装下执行 ``fn``，可重试错误按 ``ingestion.retry`` 退避重试。

        Returns:
            ``(fn 返回值, 实际尝试次数)``。

        Raises:
            IngestionPipelineError: 不可重试错误或重试耗尽（``attempts`` 记录尝试次数）。
        """
        policy = self._settings.ingestion.retry
        max_attempts = max(1, int(policy.max_attempts))
        attempt = 1
        while True:
            try:
                with self._stage(stage, source):
                    return fn(), attempt
            except IngestionPipelineError as exc:
                exc.attempts = attempt
                if attempt >= max_attempts or not is_retryable(exc):
                    raise
                delay = compute_backoff(
                    attempt,
                    initial_backoff_s=policy.initial_backoff_s,
                    backoff_multiplier=policy.backoff_multiplier,
                    backoff_jitter=policy.backoff_jitter,
                    random_fn=self._random,
                )
                trace.record_stage(
                    "retry",
                    method="pipeline",
                    details={
                        "stage": stage,
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "delay_s": round(delay, 3),
                        "error": f"{type(exc.__cause__ or exc).__name__}: {exc.__cause__ or exc}",
                    },
                )
                self._logger.warning(
                    "retryable failure at stage %r for %s (attempt %d/%d), retrying in %.2fs: %s",
                    stage, source, attempt, max_attempts, delay, exc,
                )
                self._sleep(delay)
                attempt += 1

    def _attempt_details(self, attempts: int) -> dict[str, int]:
        """重试开启时在阶段 trace 中附带尝试次数（关闭时保持基线 trace 不变）。"""
        if self._settings.ingestion.retry.max_attempts > 1:
            return {"attempts": attempts}
        return {}

    def _rollback_store(
        self,
        source: str,
        collection: str,
        doc_hash: str,
        touched: set[str],
        exc: IngestionPipelineError,
        trace: TraceContext,
    ) -> None:
        """store 写入失败后按 ``doc_hash`` 回滚三路存储；自身失败只记录不抛出。"""
        if not touched:
            return  # BM25 载入前即失败：没有任何写入需要回滚
        details: dict[str, Any] = {"doc_hash": doc_hash, "touched": sorted(touched)}
        try:
            result = self._document_manager().rollback_ingestion(source, collection, doc_hash)
        except Exception as rollback_exc:
            exc.rollback_error = f"{type(rollback_exc).__name__}: {rollback_exc}"
            details.update(ok=False, error=exc.rollback_error)
            self._logger.error(
                "store rollback failed for %s (hash=%s..., collection=%s): %s",
                source, doc_hash[:12], collection, exc.rollback_error,
            )
        else:
            details.update(
                ok=True,
                bm25_deleted=result.bm25_deleted,
                chroma_deleted=result.chroma_deleted,
                images_deleted=result.images_deleted,
            )
            self._logger.info(
                "rolled back partial store of %s (hash=%s..., collection=%s): chroma=%d bm25=%d images=%d",
                source, doc_hash[:12], collection,
                result.chroma_deleted, result.bm25_deleted, result.images_deleted,
            )
        trace.record_stage("rollback", method="pipeline", details=details)

    def _document_manager(self):
        from ingestion.document_manager import DocumentManager

        return DocumentManager(
            chroma_store=self._vector_upserter.store,
            bm25_indexer=self._bm25_indexer,
            image_storage=self._image_storage,
            file_integrity=self._integrity_checker,
            settings=self._settings,
        )

    # ------------------------------------------------------------------
    # 内部：组件解析与工具
    # ------------------------------------------------------------------

    def _ensure_components(self) -> None:
        """惰性初始化全部组件（注入优先，只初始化一次）。"""
        if self._initialized:
            return
        injected = self._injected

        def pick(key: str, factory):
            """注入优先；禁止用真值判断（如 ``BM25Indexer`` 定义了
            ``__len__``，空索引为 falsy，``or`` 会错误丢弃注入实例）。"""
            override = injected[key]
            return override if override is not None else factory()

        self._image_storage = pick("image_storage", ImageStorage)
        self._integrity_checker = pick("integrity_checker", _default_integrity_checker)
        self._loader = injected["loader"]
        self._chunker = pick("chunker", lambda: DocumentChunker(self._settings))
        self._chunk_refiner = pick("chunk_refiner", lambda: ChunkRefiner(self._settings))
        self._metadata_enricher = pick(
            "metadata_enricher", lambda: MetadataEnricher(self._settings)
        )
        self._image_captioner = pick("image_captioner", lambda: ImageCaptioner(self._settings))
        self._dense_encoder = pick("dense_encoder", lambda: DenseEncoder(self._settings))
        self._sparse_encoder = pick("sparse_encoder", lambda: SparseEncoder(self._settings))
        self._batch_processor = pick("batch_processor", lambda: BatchProcessor(self._settings))
        self._bm25_indexer = pick("bm25_indexer", lambda: BM25Indexer(self._settings))
        self._vector_upserter = pick("vector_upserter", lambda: VectorUpserter(self._settings))
        self._initialized = True

    def _resolve_loader(self, source: str) -> BaseLoader:
        """返回注入的 loader，或按扩展名路由（``.pdf`` → ``PdfLoader``，
        DOCX/PPTX/XLSX/HTML → ``MarkitdownLoader``，
        ``.md``/``.markdown`` → ``MarkdownLoader``，IR7.3）。

        路由结果按扩展名缓存；``self._loader`` 同步指向本次使用的 loader，
        ``_version_tags`` 据此推导 parser 版本，因此判断是否注入必须看
        ``self._injected`` 而不是 ``self._loader``。
        ``MarkdownLoader`` 例外：其 ``vault_root``（相对图片引用的解析根
        目录）随源文件所在目录变化，不按后缀缓存（``run`` 的源是单文件，
        取文件所在目录；目录扫描在 CLI 层已拆成逐文件）。
        """
        injected = self._injected["loader"]
        if injected is not None:
            return injected
        suffix = Path(source).suffix.lower()
        routed: dict[str, BaseLoader] = self.__dict__.setdefault("_routed_loaders", {})
        loader = routed.get(suffix)
        if loader is None:
            if suffix == ".pdf":
                loader = PdfLoader(images_root=str(self._image_storage.images_root))
            elif suffix in MARKITDOWN_EXTENSIONS:
                loader = MarkitdownLoader()
            elif suffix in _MARKDOWN_EXTENSIONS:
                loader = MarkdownLoader(
                    vault_root=str(Path(source).parent),
                    images_root=str(self._image_storage.images_root),
                )
        if loader is not None:
            if suffix in _MARKDOWN_EXTENSIONS:
                # vault_root 随源文件目录变化，不能复用上次按后缀缓存的实例。
                self._loader = loader
                return loader
            routed[suffix] = loader
            self._loader = loader
            return loader
        raise IngestionPipelineError(
            f"stage 'load' failed for {source}: "
            f"no loader registered for extension {suffix!r} (supported: {sorted(_SUPPORTED_EXTENSIONS)})",
            stage=STAGE_LOAD,
        )

    @contextmanager
    def _stage(self, stage: str, source: str) -> Iterator[None]:
        """把阶段内异常包装为带阶段名的 ``IngestionPipelineError``。"""
        try:
            yield
        except IngestionPipelineError:
            raise
        except Exception as exc:
            raise IngestionPipelineError(
                f"stage '{stage}' failed for {source}: {exc}", stage=stage
            ) from exc

    def _record_failure(
        self,
        file_hash: str | None,
        source: str,
        exc: Exception,
        trace: TraceContext,
        *,
        collection: str = "default",
    ) -> None:
        """失败落账：integrity ``mark_failed`` + trace error 打点。"""
        stage = getattr(exc, "stage", "")
        error_msg = f"{type(exc).__name__}: {exc}"
        rollback_error = getattr(exc, "rollback_error", None)
        if rollback_error:
            error_msg = f"{error_msg}; rollback failed: {rollback_error}"
        trace.record_stage(
            "error",
            method="pipeline",
            details={"stage": stage, "error": error_msg},
        )
        if file_hash:
            try:
                self._integrity_checker.mark_failed(
                    file_hash,
                    error_msg,
                    source_path=source,
                    collection=collection,
                    failed_stage=stage or None,
                    **self._version_tags(),
                )
            except Exception as mark_exc:  # 记账失败不应吞掉原始异常
                self._logger.warning(
                    "mark_failed failed for %s (hash=%s...): %s", source, file_hash[:12], mark_exc
                )
        self._logger.error("ingestion failed for %s at stage %r: %s", source, stage, exc)

    def _version_tags(self) -> dict[str, str]:
        """当次运行的 parser/chunk/embedding 版本标识（规则见 settings）。"""
        loader = getattr(self, "_loader", None)
        return resolve_ingestion_versions(
            self._settings, loader_name=type(loader).__name__ if loader is not None else None
        )

    @staticmethod
    def _emit_progress(
        on_progress: ProgressCallback | None,
        stage: str,
        current: int,
        total: int,
    ) -> None:
        if on_progress is not None:
            try:
                on_progress(stage, current, total)
            except Exception as exc:  # 进度回调失败不阻断摄取主链路
                get_logger("modular-rag.ingestion.pipeline").warning(
                    "on_progress callback failed at %s(%s/%s): %s: %s",
                    stage, current, total, type(exc).__name__, exc,
                )


def _default_integrity_checker() -> FileIntegrityChecker:
    """创建默认完整性检查器（延迟导入避免模块级副作用）。"""
    from libs.loader.file_integrity import SQLiteIntegrityChecker

    return SQLiteIntegrityChecker()
