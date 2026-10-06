"""Agentic 第三路后置去重与上下文扩展（S2.4）单测。

全离线确定性：tmp_path 造宿主文件 + 真实 PathGuard（零网络零子进程，
不 import sirchmunk）。

覆盖（S2.4 验收标准）：
- 同文件 chunk 级命中且文本高度重叠 → agentic 条目丢弃（补位语义）。
- 同文件但文本不重叠 → agentic 条目保留。
- 文本重叠阈值边界（阈值注入，恰在阈值上下各一侧）。
- 上下文扩展成功：扩展后文本与偏移正确、metadata 保留原字段并标注
  ``context_expanded`` / ``expanded_char_start`` / ``expanded_char_end``。
- 扩展失败兜底：宿主文件不存在（不可读）→ 保留原 snippet，不抛错。
- PathGuard 越权（白名单外 / 符号链接逃逸）→ 保留原 snippet，不抛错。
- HybridSearch 零开销路径：未触发第三路时去重/扩展函数不被调用，
  输出与基线逐字节一致。
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from core.query_engine import agentic_dedup  # noqa: F401  # 模块可导入性冒烟
from core.query_engine.agentic_dedup import (
    TEXT_OVERLAP_DUPLICATE_THRESHOLD,
    dedup_and_expand,
    text_overlap_ratio,
)
from core.query_engine.fusion import RRFFusion
from core.query_engine.hybrid_search import HybridSearch
from core.settings import load_settings
from core.trace.trace_context import TRACE_TYPE_QUERY, TraceContext
from core.types import RetrievalResult
from libs.agentic_search.path_guard import PathGuard

# ---------------------------------------------------------------------------
# 构造工具
# ---------------------------------------------------------------------------


def _chunk(source_path: str, text: str, score: float = 0.9) -> RetrievalResult:
    """chunk 级结果（dense/sparse 形态：无 retrieval_route，metadata 有 source_path）。"""
    return RetrievalResult(
        chunk_id=f"chunk::{source_path}::{score}",
        score=score,
        text=text,
        metadata={"source_path": source_path},
    )


def _agentic(
    source_path: str,
    text: str,
    start: int,
    end: int,
    score: float = 0.7,
) -> RetrievalResult:
    """agentic 路结果（对齐 converter.evidence_to_retrieval_results 契约）。"""
    return RetrievalResult(
        chunk_id=f"agentic::{start}::{end}",
        score=score,
        text=text,
        metadata={
            "retrieval_route": "agentic",
            "source_path": source_path,
            "char_start": start,
            "char_end": end,
            "agentic_confidence": 0.66,
            "mode": "FAST",
        },
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """白名单根目录（真实存在；写宿主文件的测试依赖它先行创建）。"""
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def guard(workspace: Path) -> PathGuard:
    """白名单根 = tmp_path/workspace（真实存在的目录）。"""
    return PathGuard([str(workspace)])


@pytest.fixture
def host_file(workspace: Path) -> Path:
    """宿主文件：前导区(300) + 目标区(200) + 尾随区(300)，共 800 字符。"""
    content = "A" * 300 + "B" * 200 + "C" * 300
    path = workspace / "host.txt"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# text_overlap_ratio（重叠率度量）
# ---------------------------------------------------------------------------


def test_text_overlap_ratio_contains_is_one():
    """snippet 完整包含于 chunk 文本 → 重叠率 1.0。"""
    chunk_text = "A" * 300 + "B" * 200 + "C" * 300
    assert text_overlap_ratio("B" * 200, chunk_text) == pytest.approx(1.0)


def test_text_overlap_ratio_disjoint_is_zero():
    assert text_overlap_ratio("xyz", "abc") == pytest.approx(0.0)


def test_text_overlap_ratio_empty_is_zero():
    assert text_overlap_ratio("", "abc") == 0.0
    assert text_overlap_ratio("abc", "") == 0.0


# ---------------------------------------------------------------------------
# 去重：同文件 chunk 级命中 → agentic 补位语义
# ---------------------------------------------------------------------------


def test_same_file_high_overlap_drops_agentic(host_file: Path):
    """同文件且 snippet 完全落在 chunk 文本内（重叠 1.0 ≥ 0.8）→ 丢弃 agentic。"""
    chunk_text = "A" * 300 + "B" * 200 + "C" * 300
    results = [
        _chunk(str(host_file), chunk_text),
        _agentic(str(host_file), "B" * 200, 300, 500),  # chunk 文本的子串
    ]
    kept = dedup_and_expand(results, chunk_size=1000, path_guard=None)
    assert [r.chunk_id for r in kept] == [results[0].chunk_id]  # 仅剩 chunk 级


def test_same_file_no_overlap_keeps_agentic(tmp_path: Path, workspace: Path):
    """同文件但覆盖不同区域（无文本重叠）→ agentic 补位保留。"""
    path = tmp_path / "workspace" / "doc.txt"
    path.write_text("X" * 500, encoding="utf-8")
    chunk = _chunk(str(path), "A" * 400)  # 与 "X"*500 无公共字符
    agentic = _agentic(str(path), "X" * 100, 0, 100)
    kept = dedup_and_expand([chunk, agentic], chunk_size=1000, path_guard=None)
    assert len(kept) == 2
    assert kept[0].chunk_id == chunk.chunk_id
    assert kept[1].chunk_id == agentic.chunk_id


def test_different_file_never_dedups(tmp_path: Path, workspace: Path):
    """不同 source_path 不去重（即使文本完全相同）。"""
    f1 = tmp_path / "workspace" / "a.txt"
    f2 = tmp_path / "workspace" / "b.txt"
    f1.write_text("same text", encoding="utf-8")
    f2.write_text("same text", encoding="utf-8")
    kept = dedup_and_expand(
        [_chunk(str(f1), "same text"), _agentic(str(f2), "same text", 0, 9)],
        chunk_size=1000,
        path_guard=None,
    )
    assert len(kept) == 2


def test_overlap_threshold_boundary(tmp_path: Path, workspace: Path):
    """阈值边界：重叠率恰等于阈值 → 丢弃；略高于阈值 → 保留。

    构造 snippet = 'X' + chunk_region[1:]：对 100 字符区域重叠率 99/100 = 0.99。
    """
    region = "B" * 100
    chunk_text = "A" * 50 + region + "C" * 50
    snippet = "X" + region[1:]  # 替换首字符：重叠率 (100-1)/100 = 0.99
    path = tmp_path / "workspace" / "boundary.txt"
    path.write_text(chunk_text, encoding="utf-8")
    results = [_chunk(str(path), chunk_text), _agentic(str(path), snippet, 50, 150)]
    # 阈值=0.99：0.99 >= 0.99 → 丢弃（含等号）
    kept = dedup_and_expand(results, chunk_size=1000, path_guard=None, overlap_threshold=0.99)
    assert [r.chunk_id for r in kept] == [results[0].chunk_id]
    # 阈值=0.991：0.99 < 0.991 → 保留（补位）
    kept = dedup_and_expand(results, chunk_size=1000, path_guard=None, overlap_threshold=0.991)
    assert len(kept) == 2


def test_default_threshold_is_module_constant():
    """去重阈值是模块常量且默认注入。"""
    assert 0.0 < TEXT_OVERLAP_DUPLICATE_THRESHOLD < 1.0
    results = [_chunk("s", "hello"), _agentic("s", "hello", 0, 5)]
    default = dedup_and_expand(results, chunk_size=1000, path_guard=None)
    explicit = dedup_and_expand(
        results, chunk_size=1000, path_guard=None,
        overlap_threshold=TEXT_OVERLAP_DUPLICATE_THRESHOLD,
    )
    assert [r.chunk_id for r in default] == [r.chunk_id for r in explicit]


def test_chunk_level_results_never_dropped():
    """chunk 级结果永不因去重丢失（只有 agentic 条目被丢弃）。"""
    results = [
        _chunk("f1", "alpha"),
        _chunk("f1", "alpha"),  # chunk 之间重复也原样保留（去重只针对 agentic）
        _agentic("f1", "alpha", 0, 5),
    ]
    kept = dedup_and_expand(results, chunk_size=1000, path_guard=None)
    assert [r.chunk_id for r in kept] == [results[0].chunk_id, results[1].chunk_id]


# ---------------------------------------------------------------------------
# 上下文扩展
# ---------------------------------------------------------------------------


def test_expansion_success_offsets_and_metadata(host_file: Path):
    """扩展成功：文本为扩展后区间、偏移正确、原 metadata 保留并标注。"""
    content = (host_file).read_text(encoding="utf-8")
    agentic = _agentic(str(host_file), "B" * 100, 350, 450)
    kept = dedup_and_expand([agentic], chunk_size=400, path_guard=PathGuard([str(host_file.parent)]))
    assert len(kept) == 1
    meta = kept[0].metadata
    assert meta["context_expanded"] is True
    new_start, new_end = meta["expanded_char_start"], meta["expanded_char_end"]
    # span=100，目标 400：两侧各扩 150 → [200, 600)
    assert (new_start, new_end) == (200, 600)
    assert kept[0].text == content[new_start:new_end]
    assert kept[0].text.startswith("A") and kept[0].text.endswith("C")
    # 原 metadata 保留（契约字段不被覆盖）
    assert meta["retrieval_route"] == "agentic"
    assert meta["char_start"] == 350 and meta["char_end"] == 450
    assert meta["agentic_confidence"] == 0.66 and meta["mode"] == "FAST"
    # 确定性 ID 不变（trace 可关联扩展前后）
    assert kept[0].chunk_id == agentic.chunk_id


def test_expansion_extends_more_on_boundary_side(tmp_path: Path, workspace: Path):
    """snippet 贴近文件起点时：左侧余量回补给右侧，仍凑满 ≈ chunk_size。"""
    path = tmp_path / "workspace" / "head.txt"
    path.write_text("B" * 100 + "C" * 1000, encoding="utf-8")
    agentic = _agentic(str(path), "B" * 100, 0, 100)
    kept = dedup_and_expand([agentic], chunk_size=400, path_guard=PathGuard([str(path.parent)]))
    meta = kept[0].metadata
    assert (meta["expanded_char_start"], meta["expanded_char_end"]) == (0, 400)
    assert len(kept[0].text) == 400


def test_expansion_clamped_at_file_end(tmp_path: Path, workspace: Path):
    """snippet 贴近文件末尾时：右侧截断、左侧补满。"""
    path = tmp_path / "workspace" / "tail.txt"
    path.write_text("A" * 1000 + "B" * 100, encoding="utf-8")
    agentic = _agentic(str(path), "B" * 100, 1000, 1100)
    kept = dedup_and_expand([agentic], chunk_size=400, path_guard=PathGuard([str(path.parent)]))
    meta = kept[0].metadata
    assert (meta["expanded_char_start"], meta["expanded_char_end"]) == (700, 1100)
    assert len(kept[0].text) == 400


def test_no_expansion_when_span_already_at_chunk_size(tmp_path: Path, workspace: Path):
    """snippet 长度已达 chunk_size → 不扩展（无扩展标注）。"""
    path = tmp_path / "workspace" / "wide.txt"
    path.write_text("Z" * 2000, encoding="utf-8")
    agentic = _agentic(str(path), "Z" * 1000, 0, 1000)
    kept = dedup_and_expand([agentic], chunk_size=1000, path_guard=PathGuard([str(path.parent)]))
    assert kept[0].text == "Z" * 1000
    assert "context_expanded" not in kept[0].metadata


def test_no_expansion_without_char_offsets():
    """str 形态 snippet（无偏移）→ 保留原样，不扩展。"""
    result = RetrievalResult(
        chunk_id="agentic::nooffset",
        score=0.5,
        text="plain snippet",
        metadata={"retrieval_route": "agentic", "source_path": "/nowhere.txt",
                  "char_start": None, "char_end": None, "mode": "FAST"},
    )
    kept = dedup_and_expand([result], chunk_size=1000, path_guard=None)
    assert kept[0].text == "plain snippet"
    assert "context_expanded" not in kept[0].metadata


# ---------------------------------------------------------------------------
# 扩展失败兜底：文件不可读 / PathGuard 越权（fail-closed，不抛错）
# ---------------------------------------------------------------------------


def test_unreadable_file_keeps_original_snippet():
    """宿主文件不存在（不可读）→ 保留原 snippet，不抛错。"""
    agentic = _agentic("/nonexistent/host.txt", "B" * 100, 350, 450)
    guard = PathGuard([str(Path("/nonexistent").resolve().parent)])  # 根存在但文件不在
    kept = dedup_and_expand([agentic], chunk_size=400, path_guard=guard)
    assert kept[0].text == "B" * 100
    assert "context_expanded" not in kept[0].metadata


def test_path_guard_outside_roots_keeps_original_snippet(tmp_path: Path):
    """白名单外路径（fail-closed）→ 保留原 snippet，不抛错。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("S" * 900, encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agentic = _agentic(str(secret), "B" * 100, 0, 100)
    kept = dedup_and_expand([agentic], chunk_size=400, path_guard=PathGuard([str(workspace)]))
    assert kept[0].text == "B" * 100
    assert "context_expanded" not in kept[0].metadata


def test_path_guard_symlink_escape_keeps_original_snippet(tmp_path: Path):
    """符号链接逃逸（白名单内软链指向白名单外）→ 保留原 snippet。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "real.txt"
    target.write_text("R" * 900, encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    link = workspace / "link.txt"
    link.symlink_to(target)  # realpath 逃逸：resolve 后落在白名单外
    agentic = _agentic(str(link), "B" * 100, 0, 100)
    kept = dedup_and_expand([agentic], chunk_size=400, path_guard=PathGuard([str(workspace)]))
    assert kept[0].text == "B" * 100
    assert "context_expanded" not in kept[0].metadata


def test_no_guard_skips_expansion_but_keeps_results(host_file: Path):
    """path_guard=None（无白名单配置）→ 不扩展但不抛错、不丢结果。"""
    agentic = _agentic(str(host_file), "B" * 100, 350, 450)
    kept = dedup_and_expand([agentic], chunk_size=400, path_guard=None)
    assert len(kept) == 1
    assert kept[0].text == "B" * 100


def test_denied_substring_keeps_original_snippet(tmp_path: Path, workspace: Path):
    """白名单内但命中排除子串（如 .git）→ 不扩展，保留原 snippet。"""
    git_dir = tmp_path / "workspace" / ".git"
    git_dir.mkdir()
    f = git_dir / "config.txt"
    f.write_text("G" * 900, encoding="utf-8")
    agentic = _agentic(str(f), "B" * 100, 0, 100)
    kept = dedup_and_expand([agentic], chunk_size=400, path_guard=PathGuard([str(tmp_path / "workspace")]))
    assert kept[0].text == "B" * 100
    assert "context_expanded" not in kept[0].metadata


# ---------------------------------------------------------------------------
# HybridSearch 集成：零开销路径与触发路径
# ---------------------------------------------------------------------------

_VALID_YAML = dedent(
    """\
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
      persist_path: ./data/db/chroma
    retrieval:
      sparse_backend: bm25
      fusion_algorithm: rrf
      top_k_dense: 20
      top_k_sparse: 20
      top_k_final: 10
    rerank:
      backend: none
    evaluation:
      backends: [custom]
    """
)


@pytest.fixture
def settings(tmp_path: Path):
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    return load_settings(path)


class _FakeDense:
    def retrieve(self, query, top_k, filters=None, *, trace=None):
        return []


class _FakeSparse:
    def retrieve(self, keywords, top_k, *, trace=None):
        return []


class _FakeAgentic:
    def __init__(self, results: list[RetrievalResult]) -> None:
        self._results = results

    def retrieve(self, query, top_k, trace=None):
        return list(self._results)[:top_k]


def test_untriggered_route_is_zero_cost(settings, monkeypatch: pytest.MonkeyPatch):
    """未触发第三路：去重/扩展函数不被调用，输出与基线逐字节一致。"""
    from dataclasses import replace

    enabled = replace(
        settings,
        agentic_search=replace(settings.agentic_search, enabled=True, trigger="tool_only"),
    )

    def _boom(*args, **kwargs):  # 若被调用立即失败
        raise AssertionError("dedup_and_expand must not run when agentic route untriggered")

    # hybrid_search 以 `from ... import dedup_and_expand` 引入名字，须打补丁到其命名空间。
    monkeypatch.setattr("core.query_engine.hybrid_search.dedup_and_expand", _boom)
    hs = HybridSearch(
        enabled,
        dense_retriever=_FakeDense(),
        sparse_retriever=_FakeSparse(),
        fusion=RRFFusion(enabled),
        agentic_retriever=_FakeAgentic([]),
    )
    trace = TraceContext(trace_type=TRACE_TYPE_QUERY, metadata={"query": "q"})
    results = hs.search("anything", trace=trace)
    assert results == []
    assert hs.last_trigger_decision is not None and not hs.last_trigger_decision.triggered


def test_triggered_route_applies_dedup_and_expansion(settings, tmp_path: Path):
    """触发第三路：同文件高重叠 agentic 条目被丢弃、存活条目被扩展。"""
    from dataclasses import replace

    host = tmp_path / "host.txt"
    host.write_text("A" * 300 + "B" * 200 + "C" * 300, encoding="utf-8")
    enabled = replace(
        settings,
        agentic_search=replace(
            settings.agentic_search,
            enabled=True,
            trigger="always",
            allowed_roots=[str(tmp_path)],
        ),
    )
    agentic_results = [
        _agentic(str(host), "B" * 200, 300, 500),   # 与下方 chunk 高重叠 → 应被丢弃
        _agentic(str(host), "Q" * 50, 0, 50),       # 无重叠 → 保留并扩展
    ]
    chunk = _chunk(str(host), "A" * 300 + "B" * 200 + "C" * 300)

    class _Dense:
        def retrieve(self, query, top_k, filters=None, *, trace=None):
            return [chunk]

    hs = HybridSearch(
        enabled,
        dense_retriever=_Dense(),
        sparse_retriever=_FakeSparse(),
        fusion=RRFFusion(enabled),
        agentic_retriever=_FakeAgentic(agentic_results),
    )
    results = hs.search("q")
    routes = [r.metadata.get("retrieval_route") for r in results]
    assert routes.count("agentic") == 1  # 高重叠条目已被后置去重丢弃
    survivor = next(r for r in results if r.metadata.get("retrieval_route") == "agentic")
    assert survivor.metadata["context_expanded"] is True
    assert survivor.metadata["char_start"] == 0  # 原 snippet 偏移保留
    assert "expanded_char_end" in survivor.metadata
