"""QueryProcessor：查询预处理（阶段 D1）。

对输入查询做**关键词提取**（规则分词 + 停用词过滤），并解析通用
**filters 结构**（可空实现），产出 ``core.types.ProcessedQuery`` 供
D5 HybridSearch 编排使用：

- **关键词提取**（规则实现，零外部依赖）：
  - 拉丁词 ``[a-z0-9]+`` 小写化 + CJK 逐字 unigram，与 C9
    ``SparseEncoder.default_tokenize`` 粒度精确对齐（core 层不依赖
    ingestion，单测含 drift guard 保证两侧分词不漂移，BM25 查询词
    与索引词处于同一 token 空间）；
  - 停用词过滤：默认 ``DEFAULT_STOPWORDS``（英文虚词 + 中文常用虚字），
    可注入替换；全部 token 被过滤时回退原始 token，保证
    "keywords 非空"契约（整句虚词的查询也保底有词可用于稀疏检索）；
  - 保序去重（按首次出现顺序），同一输入产出确定性结果。
- **filters 解析**（可空实现）：不做自然语言约束抽取（后续演进方向，
  见 spec 3.1.2 Metadata Filtering Strategy），仅接受调用方传入的
  结构化 dict（``None → {}``），类型校验后浅拷贝透传，由下游检索器
  （D2/D3）决定 Pre-filter / Post-filter 策略与值语义。

失败语义（fail-fast，可读报错）：空/纯空白查询、无可提取 token 的查询、
非 str 查询、非 dict filters 均立即抛错——这些属于调用方契约错误，
不属于可降级故障。
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

from core.settings import Settings
from core.trace.trace_context import TraceContext
from core.types import ProcessedQuery

__all__ = [
    "QueryProcessor",
    "QueryProcessingError",
    "default_tokenize",
    "DEFAULT_STOPWORDS",
]

# 分词正则（与 C9 ingestion.embedding.sparse_encoder.default_tokenize 保持一致）。
_LATIN_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")

Tokenizer = Callable[[str], list[str]]

#: 默认停用词（英文虚词 + 中文常用虚字；CJK 为逐字粒度故按单字过滤）。
#: 查询中的实词（实体/动词）一律保留；注入自定义集合可整体替换该策略。
DEFAULT_STOPWORDS: frozenset[str] = frozenset({
    # 英文：冠词/连词/介词/助动词/疑问词/代词等虚词
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for",
    "with", "from", "by", "as", "is", "are", "was", "were", "be", "been",
    "do", "does", "did", "how", "what", "which", "who", "whom", "when",
    "where", "why", "i", "me", "my", "we", "our", "you", "your", "it",
    "its", "this", "that", "these", "those", "there", "their", "not",
    "no", "can", "could", "should", "would", "will", "shall", "may",
    "might", "must", "about", "into", "than", "then", "so", "such",
    # 中文：常用虚字（保守集合，避免误杀可作实语素的字）
    "的", "了", "是", "在", "和", "与", "或", "及", "对", "为", "以",
    "等", "个", "中", "这", "那", "有", "不", "也", "就", "都", "而",
    "吗", "呢", "吧", "如", "何", "请",
})


class QueryProcessingError(ValueError):
    """查询预处理契约错误（空查询/无可提取关键词，fail-fast 可读报错）。"""


def default_tokenize(text: str) -> list[str]:
    """默认分词：拉丁词小写化 + CJK 逐字（与 C9 SparseEncoder 对齐）。"""
    tokens: list[str] = _LATIN_WORD_RE.findall(text.lower())
    tokens.extend(_CJK_CHAR_RE.findall(text))
    return tokens


class QueryProcessor:
    """查询预处理器：关键词提取 + filters 结构解析（规则实现，确定性）。"""

    def __init__(
        self,
        settings: Settings,
        *,
        stopwords: frozenset[str] | set[str] | None = None,
        tokenizer: Tokenizer | None = None,
    ) -> None:
        # settings 保留给后续查询扩展（同义词表等）配置使用，当前未读取。
        self._settings = settings
        self._stopwords: frozenset[str] = (
            frozenset(stopwords) if stopwords is not None else DEFAULT_STOPWORDS
        )
        self._tokenize: Tokenizer = tokenizer or default_tokenize

    @property
    def stopwords(self) -> frozenset[str]:
        """当前停用词集合（只读视图语义）。"""
        return self._stopwords

    @property
    def tokenizer(self) -> Tokenizer:
        return self._tokenize

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def process(
        self,
        query: str,
        *,
        filters: dict[str, Any] | None = None,
        trace: TraceContext | None = None,
    ) -> ProcessedQuery:
        """预处理单条查询：提取关键词 + 解析 filters。

        Args:
            query: 原始查询文本（原样保留在 ``ProcessedQuery.query``）。
            filters: 可选结构化过滤条件（如 ``{"collection": "docs"}``）；
                ``None`` 视为无约束（空 dict）。
            trace: 可选追踪上下文，记录 ``query_processing`` 阶段
                （method/keywords/filter_keys/elapsed_ms）。

        Returns:
            ``ProcessedQuery``（keywords 非空、filters 恒为 dict）。

        Raises:
            TypeError: ``query`` 非 str 或 ``filters`` 非 dict/None。
            QueryProcessingError: 查询为空/纯空白，或无可提取关键词。
        """
        started = time.perf_counter()
        if not isinstance(query, str):
            raise TypeError(f"query must be a string, got {type(query).__name__}")
        normalized_filters = self._normalize_filters(filters)
        keywords = self.extract_keywords(query)
        processed = ProcessedQuery(query=query, keywords=keywords, filters=normalized_filters)
        if trace is not None:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            trace.record_stage(
                "query_processing",
                method="query_processor",
                details={
                    "keywords": list(keywords),
                    "keyword_count": len(keywords),
                    "filter_keys": sorted(normalized_filters),
                    "elapsed_ms": elapsed_ms,
                },
            )
        return processed

    def extract_keywords(self, query: str) -> list[str]:
        """从查询提取关键词（去停用词、保序去重）。

        全部 token 被停用词过滤时回退原始 token（保证非空）；
        无任何可提取 token（标点/空白）时抛 ``QueryProcessingError``。
        """
        if not isinstance(query, str):
            raise TypeError(f"query must be a string, got {type(query).__name__}")
        if not query.strip():
            raise QueryProcessingError("query must be a non-empty, non-whitespace string")
        tokens = self._tokenize(query)
        if not tokens:
            raise QueryProcessingError(f"query contains no extractable keywords: {query!r}")
        kept = [token for token in tokens if token not in self._stopwords]
        if not kept:
            kept = tokens
        # 保序去重（dict 保序语义），同一输入确定性输出。
        return list(dict.fromkeys(kept))

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
        """校验并浅拷贝 filters（``None → {}``；值原样透传给下游检索器）。"""
        if filters is None:
            return {}
        if not isinstance(filters, dict):
            raise TypeError(f"filters must be a dict or None, got {type(filters).__name__}")
        return dict(filters)
