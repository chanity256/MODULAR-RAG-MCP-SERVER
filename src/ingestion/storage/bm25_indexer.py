"""BM25Indexer：倒排索引构建与持久化（阶段 C11）。

接收 ``SparseEncoder`` 的 ``SparseDocStats`` 输出（阶段 C9），在全量文档
统计上计算 IDF 并构建倒排索引，序列化到 ``data/db/bm25/``：

- IDF 公式（spec 精确约定，自然对数）::

      IDF(term) = log((N - df + 0.5) / (df + 0.5))

  其中 ``N`` 为索引文档数（chunk 计数），``df`` 为包含该 term 的文档数。
  按该公式，``df > N/2`` 的高频词 IDF 为负（不额外加 1 或截断）。

- 倒排索引结构（spec 约定）::

      {term: {idf, postings: [{chunk_id, tf, doc_length}]}}

- BM25 打分（Okapi，参数 ``k1`` / ``b``，默认 1.5 / 0.75）::

      score(D, Q) = Σ_t qtf(t) · IDF(t) · tf(t,D)·(k1+1)
                    ─────────────────────────────────────
                    tf(t,D) + k1·(1 − b + b·|D|/avgdl)

  ``qtf`` 为关键词在查询中出现的次数；``|D|`` 取 posting 的
  ``doc_length``，``avgdl = total_length / num_docs``。

更新与持久化：
- ``build(stats)``：清空重建（rebuild）；
- ``add(stats)``：增量更新，同 ``chunk_id`` 视为替换（旧 postings 移除，
  ``df == 0`` 的死词项整体删除，IDF 全量重算——``N`` 变化会影响所有词）；
- ``save(path?)`` / ``load(path?)``：JSON 序列化（``version=1``），原子写入
  （tmp + ``os.replace``），``load`` 采纳文件中的 ``k1``/``b`` 并校验
  ``num_docs`` / ``total_length`` 与 postings 的一致性，加载后可继续增量。

空文档（``term_freqs={}``）不进入倒排索引、不计入 ``N``（无 term 可匹配，
对打分无影响，保证 build/add/save/load 往返一致）。
"""

from __future__ import annotations

import json
import math
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from core.settings import Settings
from core.trace.trace_context import TraceContext
from ingestion.embedding import SparseDocStats
from observability.logger import get_logger

__all__ = [
    "Posting",
    "TermEntry",
    "BM25Index",
    "BM25Indexer",
    "BM25IndexError",
    "DEFAULT_INDEX_PATH",
    "DEFAULT_K1",
    "DEFAULT_B",
    "INDEX_VERSION",
]

#: 默认索引文件路径（spec 约定 data/db/bm25/ 目录）。
DEFAULT_INDEX_PATH = "data/db/bm25/index.json"

#: BM25 默认参数（Okapi 常用值）。
DEFAULT_K1 = 1.5
DEFAULT_B = 0.75

#: 索引序列化格式版本。
INDEX_VERSION = 1


class BM25IndexError(RuntimeError):
    """索引输入/持久化契约被破坏（属于需要暴露的 bug，而非可降级故障）。"""


@dataclass
class Posting:
    """倒排表条目：某 term 在某 chunk 中的一次出现。"""

    chunk_id: str
    tf: int
    doc_length: int

    def to_dict(self) -> dict[str, Any]:
        """可 JSON 序列化 dict。"""
        return {"chunk_id": self.chunk_id, "tf": self.tf, "doc_length": self.doc_length}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Posting":
        """从 dict 反序列化（容忍多余键；缺 chunk_id 视为损坏）。"""
        if not isinstance(data, dict) or "chunk_id" not in data:
            raise BM25IndexError(f"invalid posting entry: {data!r}")
        try:
            return cls(
                chunk_id=str(data["chunk_id"]),
                tf=int(data.get("tf", 0)),
                doc_length=int(data.get("doc_length", 0)),
            )
        except (TypeError, ValueError) as exc:
            raise BM25IndexError(f"invalid posting entry: {data!r} ({exc})") from exc


@dataclass
class TermEntry:
    """单 term 的索引项：IDF + 倒排表（spec 结构 {idf, postings}）。"""

    idf: float
    postings: list[Posting] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """可 JSON 序列化 dict。"""
        return {
            "idf": self.idf,
            "postings": [posting.to_dict() for posting in self.postings],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TermEntry":
        """从 dict 反序列化（容忍多余键）。"""
        if not isinstance(data, dict):
            raise BM25IndexError(f"invalid term entry: {data!r}")
        raw_postings = data.get("postings") or []
        if not isinstance(raw_postings, list):
            raise BM25IndexError(f"invalid postings for term entry: {data!r}")
        try:
            idf = float(data.get("idf", 0.0))
        except (TypeError, ValueError) as exc:
            raise BM25IndexError(f"invalid idf in term entry: {data!r} ({exc})") from exc
        return cls(idf=idf, postings=[Posting.from_dict(p) for p in raw_postings])


@dataclass
class BM25Index:
    """倒排索引数据结构（可序列化、可增量维护）。"""

    k1: float = DEFAULT_K1
    b: float = DEFAULT_B
    num_docs: int = 0
    total_length: int = 0
    terms: dict[str, TermEntry] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.k1, (int, float)) or isinstance(self.k1, bool) or self.k1 < 0:
            raise ValueError(f"k1 must be a non-negative number, got {self.k1!r}")
        if not isinstance(self.b, (int, float)) or isinstance(self.b, bool) or not 0.0 <= self.b <= 1.0:
            raise ValueError(f"b must be within [0, 1], got {self.b!r}")

    @property
    def avgdl(self) -> float:
        """平均文档长度（``total_length / num_docs``，空索引为 0.0）。"""
        return self.total_length / self.num_docs if self.num_docs else 0.0

    def __len__(self) -> int:
        return self.num_docs

    def doc_frequency(self, term: str) -> int:
        """文档频率 ``df``（term 不在索引中返回 0）。"""
        entry = self.terms.get(term)
        return len(entry.postings) if entry is not None else 0

    def get_postings(self, term: str) -> list[Posting]:
        """term 的倒排表副本（term 不存在返回空列表）。"""
        entry = self.terms.get(term)
        return list(entry.postings) if entry is not None else []

    def idf(self, term: str) -> float | None:
        """term 的 IDF（不在索引中返回 None）。"""
        entry = self.terms.get(term)
        return entry.idf if entry is not None else None

    def to_dict(self) -> dict[str, Any]:
        """可 JSON 序列化 dict（version + 参数 + 统计 + 倒排表）。"""
        return {
            "version": INDEX_VERSION,
            "k1": self.k1,
            "b": self.b,
            "num_docs": self.num_docs,
            "total_length": self.total_length,
            "terms": {term: entry.to_dict() for term, entry in self.terms.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BM25Index":
        """从 dict 反序列化（容忍多余键；结构损坏抛 ``BM25IndexError``）。"""
        if not isinstance(data, dict):
            raise BM25IndexError(f"invalid index payload: expected mapping, got {type(data).__name__}")
        version = data.get("version", INDEX_VERSION)
        if version != INDEX_VERSION:
            raise BM25IndexError(f"unsupported index version: {version!r} (expected {INDEX_VERSION})")
        raw_terms = data.get("terms") or {}
        if not isinstance(raw_terms, dict):
            raise BM25IndexError("invalid index payload: 'terms' must be a mapping")
        try:
            return cls(
                k1=float(data.get("k1", DEFAULT_K1)),
                b=float(data.get("b", DEFAULT_B)),
                num_docs=int(data.get("num_docs", 0)),
                total_length=int(data.get("total_length", 0)),
                terms={str(term): TermEntry.from_dict(entry) for term, entry in raw_terms.items()},
            )
        except (TypeError, ValueError) as exc:
            raise BM25IndexError(f"invalid index payload: {exc}") from exc


class BM25Indexer:
    """BM25 倒排索引器：build 重建 / add 增量 / save-load 持久化 / query 打分。

    Args:
        settings: 全局配置（保留给后续 ``ingestion.bm25`` 节使用，当前可选）。
        index_path: 索引文件路径，默认 ``data/db/bm25/index.json``；
            保存时父目录不存在会自动创建。
        k1: BM25 饱和参数（≥ 0，默认 1.5）。
        b: BM25 长度归一化参数（[0, 1]，默认 0.75）。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        index_path: str | Path | None = None,
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
    ) -> None:
        self._settings = settings
        self._index_path = Path(index_path) if index_path is not None else Path(DEFAULT_INDEX_PATH)
        self._index = BM25Index(k1=k1, b=b)  # __post_init__ 校验 k1/b
        self._docs: dict[str, SparseDocStats] = {}
        self._logger = get_logger("modular-rag.ingestion.bm25_indexer")

    # ------------------------------------------------------------------
    # 只读视图
    # ------------------------------------------------------------------

    @property
    def index(self) -> BM25Index:
        """当前索引（只读视图语义，外部勿直接修改）。"""
        return self._index

    @property
    def num_docs(self) -> int:
        return self._index.num_docs

    @property
    def avgdl(self) -> float:
        return self._index.avgdl

    @property
    def terms(self) -> dict[str, TermEntry]:
        return self._index.terms

    def __len__(self) -> int:
        return self._index.num_docs

    # ------------------------------------------------------------------
    # 构建：重建与增量
    # ------------------------------------------------------------------

    def build(self, stats: Sequence[SparseDocStats], *, trace: TraceContext | None = None) -> "BM25Indexer":
        """清空后从 ``stats`` 全量重建索引。"""
        self._index = BM25Index(k1=self._index.k1, b=self._index.b)
        self._docs = {}
        self._add(stats, mode="build", trace=trace)
        return self

    def add(self, stats: Sequence[SparseDocStats], *, trace: TraceContext | None = None) -> "BM25Indexer":
        """增量更新：新 chunk 追加，同 ``chunk_id`` 替换（幂等重摄取）。"""
        self._add(stats, mode="add", trace=trace)
        return self

    def _add(
        self,
        stats: Sequence[SparseDocStats],
        *,
        mode: str,
        trace: TraceContext | None = None,
    ) -> None:
        started = time.perf_counter()
        docs, replaced = self._prepare(stats)
        for doc in docs:
            old = self._docs.get(doc.chunk_id)
            if old is not None:
                self._remove_doc(old)
                replaced += 1
            self._insert_doc(doc)
        self._recompute_idf()
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        if trace is not None and docs:
            trace.record_stage(
                "storage",
                method="bm25_indexer",
                details={
                    "mode": mode,
                    "docs": len(docs),
                    "replaced": replaced,
                    "num_docs": self._index.num_docs,
                    "terms": len(self._index.terms),
                    "elapsed_ms": elapsed_ms,
                },
            )
        self._logger.info(
            "bm25 index %s: %d docs (+%d replaced) -> %d docs, %d terms (%.1fms)",
            mode, len(docs), replaced, self._index.num_docs, len(self._index.terms), elapsed_ms,
        )

    def _prepare(self, stats: Sequence[SparseDocStats]) -> tuple[list[SparseDocStats], int]:
        """校验输入并剔除空文档；返回 (待写入 docs, 已存在的重复 chunk 数)。"""
        if isinstance(stats, (SparseDocStats,)):
            raise BM25IndexError("expected a sequence of SparseDocStats, got a single instance")
        docs: list[SparseDocStats] = []
        seen: set[str] = set()
        replaced = 0
        for item in stats:
            self._validate(item)
            if not item.term_freqs:
                continue  # 空文档：不入索引、不计入 N
            if item.chunk_id in seen:
                raise BM25IndexError(f"duplicate chunk_id in input stats: {item.chunk_id!r}")
            seen.add(item.chunk_id)
            if item.chunk_id in self._docs:
                replaced += 1
            docs.append(item)
        return docs, replaced

    @staticmethod
    def _validate(item: SparseDocStats) -> None:
        chunk_id = getattr(item, "chunk_id", None)
        if not isinstance(chunk_id, str) or not chunk_id:
            raise BM25IndexError(f"chunk_id must be a non-empty string, got {chunk_id!r}")
        length = getattr(item, "length", None)
        if not isinstance(length, int) or isinstance(length, bool) or length < 0:
            raise BM25IndexError(f"length must be a non-negative int, got {length!r} (chunk {chunk_id!r})")
        term_freqs = getattr(item, "term_freqs", None)
        if not isinstance(term_freqs, dict):
            raise BM25IndexError(f"term_freqs must be a mapping, got {type(term_freqs).__name__} (chunk {chunk_id!r})")
        for term, tf in term_freqs.items():
            if not isinstance(term, str) or not term:
                raise BM25IndexError(f"term must be a non-empty string, got {term!r} (chunk {chunk_id!r})")
            if not isinstance(tf, int) or isinstance(tf, bool) or tf < 1:
                raise BM25IndexError(f"term frequency must be a positive int, got {tf!r} (term {term!r})")

    def _insert_doc(self, doc: SparseDocStats) -> None:
        for term, tf in doc.term_freqs.items():
            entry = self._index.terms.setdefault(term, TermEntry(idf=0.0))
            entry.postings.append(Posting(chunk_id=doc.chunk_id, tf=tf, doc_length=doc.length))
        self._index.num_docs += 1
        self._index.total_length += doc.length
        self._docs[doc.chunk_id] = doc

    def _remove_doc(self, old: SparseDocStats) -> None:
        for term in old.term_freqs:
            entry = self._index.terms.get(term)
            if entry is None:
                continue
            entry.postings = [p for p in entry.postings if p.chunk_id != old.chunk_id]
            if not entry.postings:
                del self._index.terms[term]
        self._index.num_docs -= 1
        self._index.total_length -= old.length
        del self._docs[old.chunk_id]

    def _recompute_idf(self) -> None:
        """按当前 ``N`` 与各词 ``df`` 全量重算 IDF（spec 公式，自然对数）。"""
        n = self._index.num_docs
        for entry in self._index.terms.values():
            df = len(entry.postings)
            entry.idf = math.log((n - df + 0.5) / (df + 0.5))

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def save(self, path: str | Path | None = None) -> Path:
        """序列化索引为 JSON（tmp + ``os.replace`` 原子落盘）。"""
        target = Path(path) if path is not None else self._index_path
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self._index.to_dict(), fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
        self._logger.info("bm25 index saved: %s (%d docs, %d terms)", target, self._index.num_docs, len(self._index.terms))
        return target

    def load(self, path: str | Path | None = None) -> "BM25Indexer":
        """从 JSON 载入索引（采纳文件中的 ``k1``/``b``，校验统计一致性）。"""
        source = Path(path) if path is not None else self._index_path
        if not source.is_file():
            raise FileNotFoundError(f"bm25 index file not found: {source}")
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BM25IndexError(f"failed to parse bm25 index {source}: {exc}") from exc
        index = BM25Index.from_dict(data)
        docs = self._docs_from(index, source)
        self._index = index
        self._docs = docs
        self._logger.info("bm25 index loaded: %s (%d docs, %d terms)", source, index.num_docs, len(index.terms))
        return self

    @staticmethod
    def _docs_from(index: BM25Index, source: Path) -> dict[str, SparseDocStats]:
        """由倒排表反推 doc 级视图，并校验持久化统计的一致性。"""
        docs: dict[str, SparseDocStats] = {}
        for term, entry in index.terms.items():
            for posting in entry.postings:
                doc = docs.setdefault(
                    posting.chunk_id,
                    SparseDocStats(chunk_id=posting.chunk_id, term_freqs={}, length=posting.doc_length),
                )
                doc.term_freqs[term] = posting.tf
        if len(docs) != index.num_docs:
            raise BM25IndexError(
                f"corrupt bm25 index {source}: num_docs={index.num_docs} but postings cover {len(docs)} chunks"
            )
        total = sum(doc.length for doc in docs.values())
        if total != index.total_length:
            raise BM25IndexError(
                f"corrupt bm25 index {source}: total_length={index.total_length} but postings sum to {total}"
            )
        return docs

    # ------------------------------------------------------------------
    # 查询（D3 SparseRetriever 契约：[{chunk_id, score}]）
    # ------------------------------------------------------------------

    def query(
        self,
        keywords: Sequence[str],
        top_k: int,
        *,
        trace: TraceContext | None = None,
    ) -> list[dict[str, Any]]:
        """BM25 打分并返回 top_k 结果（score 降序，同分按 chunk_id 升序）。"""
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
            raise ValueError(f"top_k must be a positive integer, got {top_k!r}")
        qtf = Counter(k for k in keywords if isinstance(k, str) and k and k in self._index.terms)
        if not qtf:
            return []
        index = self._index
        avgdl = index.avgdl
        scores: dict[str, float] = {}
        for term, q in qtf.items():
            entry = index.terms[term]
            for posting in entry.postings:
                norm = posting.doc_length / avgdl if avgdl > 0 else 0.0
                denominator = posting.tf + index.k1 * (1.0 - index.b + index.b * norm)
                contribution = entry.idf * (posting.tf * (index.k1 + 1.0)) / denominator
                scores[posting.chunk_id] = scores.get(posting.chunk_id, 0.0) + q * contribution
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return [{"chunk_id": chunk_id, "score": score} for chunk_id, score in ranked[:top_k]]
