"""SparseEncoder：BM25 词项统计（阶段 C9）。

为每个 chunk 产出 BM25 所需的词频统计（``SparseDocStats``）：
``term_freqs``（term → 出现次数）与 ``length``（token 总数）。IDF 与
倒排索引由 C11 ``BM25Indexer`` 在全量文档统计上计算，此处只做
"单文档可独立计算"的部分，保证输出结构可直接喂给 indexer。

分词策略（默认 ``default_tokenize``，可注入自定义实现）：
- 拉丁词：``[a-z0-9]+`` 小写化；
- CJK：逐字切分（unigram），与阶段 D QueryProcessor 的关键词粒度对齐；
- 过滤空 token。

空文本行为（明确契约）：``term_freqs={}`` 且 ``length=0``，不抛错。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Callable, Sequence

from core.settings import Settings
from core.trace.trace_context import TraceContext
from core.types import Chunk
from observability.logger import get_logger

__all__ = ["SparseDocStats", "SparseEncoder", "default_tokenize"]

_LATIN_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")

Tokenizer = Callable[[str], list[str]]


def default_tokenize(text: str) -> list[str]:
    """默认分词：拉丁词小写化 + CJK 逐字。"""
    tokens: list[str] = _LATIN_WORD_RE.findall(text.lower())
    tokens.extend(_CJK_CHAR_RE.findall(text))
    return tokens


@dataclass
class SparseDocStats:
    """单 chunk 的 BM25 统计（供 ``BM25Indexer`` 构建 IDF 与倒排表）。"""

    chunk_id: str
    term_freqs: dict[str, int] = field(default_factory=dict)
    length: int = 0

    def to_dict(self) -> dict:
        """可 JSON 序列化 dict（排除 None）。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SparseDocStats":
        """从 dict 反序列化（容忍多余键，缺失键走默认值）。"""
        return cls(
            chunk_id=str(data["chunk_id"]),
            term_freqs=dict(data.get("term_freqs") or {}),
            length=int(data.get("length") or 0),
        )


class SparseEncoder:
    """chunk 稀疏编码器：词频统计 + 长度，无外部依赖、确定性。"""

    def __init__(self, settings: Settings, tokenizer: Tokenizer | None = None) -> None:
        self._settings = settings
        self._tokenize: Tokenizer = tokenizer or default_tokenize
        self._logger = get_logger("modular-rag.ingestion.sparse_encoder")

    @property
    def tokenizer(self) -> Tokenizer:
        return self._tokenize

    def encode_chunks(
        self,
        chunks: Sequence[Chunk],
        *,
        trace: TraceContext | None = None,
    ) -> list[SparseDocStats]:
        """按输入顺序产出每个 chunk 的 ``SparseDocStats``。"""
        if not chunks:
            return []
        stats = [self.encode_chunk(chunk) for chunk in chunks]
        total_terms = sum(s.length for s in stats)
        if trace is not None:
            trace.record_stage(
                "embedding",
                method="sparse_encoder",
                details={
                    "chunks": len(chunks),
                    "total_terms": total_terms,
                    "unique_terms": len({t for s in stats for t in s.term_freqs}),
                },
            )
        return stats

    def encode_chunk(self, chunk: Chunk) -> SparseDocStats:
        term_freqs, length = self.encode_text(chunk.text)
        return SparseDocStats(chunk_id=chunk.id, term_freqs=term_freqs, length=length)

    def encode_text(self, text: str) -> tuple[dict[str, int], int]:
        """统计单段文本：返回 (term_freqs, token 总数)。"""
        tokens = self._tokenize(text)
        return dict(Counter(tokens)), len(tokens)
