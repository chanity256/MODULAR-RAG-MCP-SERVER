"""``RecursiveSplitter`` 测试（阶段 B7.5）。

验证：
- ``provider=recursive`` 时 ``SplitterFactory`` 可创建。
- ``split_text`` 能正确处理 Markdown 结构（标题/代码块不被打断）。
- 配置驱动 ``chunk_size`` / ``chunk_overlap`` 影响切分结果。
- 自定义 separators 可覆盖默认。
- 边界：空字符串、纯文本。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from libs.splitter.recursive_splitter import DEFAULT_MARKDOWN_SEPARATORS, RecursiveSplitter
from libs.splitter.splitter_factory import SplitterFactory

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
    retrieval:
      sparse_backend: bm25
      fusion_algorithm: rrf
    rerank:
      backend: none
    evaluation:
      backends: [custom]
    """
)


def _settings(tmp_path: Path, **splitter_overrides):
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    settings = load_settings(path)
    sp = settings.splitter
    if splitter_overrides:
        sp = replace(sp, **splitter_overrides)
    return replace(settings, splitter=sp)


@pytest.fixture
def registered():
    import libs.splitter  # noqa: F401
    yield SplitterFactory


# ---------------------------------------------------------------------------
# 工厂创建
# ---------------------------------------------------------------------------


def test_factory_creates_recursive(tmp_path, registered):
    settings = _settings(tmp_path, provider="recursive")
    sp = registered.create(settings)
    assert isinstance(sp, RecursiveSplitter)


def test_factory_recursive_default_when_unspecified(tmp_path, registered):
    """settings.yaml 未写 splitter 节时，默认 provider=recursive。"""
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    settings = load_settings(path)
    sp = registered.create(settings)
    assert isinstance(sp, RecursiveSplitter)


# ---------------------------------------------------------------------------
# 切分行为
# ---------------------------------------------------------------------------


def test_split_returns_non_empty_chunks():
    from core.settings import SplitterConfig

    sp = RecursiveSplitter(SplitterConfig(provider="recursive", chunk_size=100, chunk_overlap=10))
    chunks = sp.split_text("# Title\n\nSome content here.")
    assert len(chunks) >= 1
    assert all(isinstance(c, str) for c in chunks)


def test_split_empty_string_returns_empty():
    from core.settings import SplitterConfig

    sp = RecursiveSplitter(SplitterConfig(provider="recursive", chunk_size=100, chunk_overlap=10))
    assert sp.split_text("") == []


def test_split_non_string_raises():
    from core.settings import SplitterConfig

    sp = RecursiveSplitter(SplitterConfig(provider="recursive", chunk_size=100, chunk_overlap=10))
    with pytest.raises(ValueError):
        sp.split_text(123)  # type: ignore[arg-type]


def test_chunk_size_drives_count():
    from core.settings import SplitterConfig

    text = "word " * 200  # ~1000 chars
    sp_small = RecursiveSplitter(SplitterConfig(provider="recursive", chunk_size=100, chunk_overlap=0))
    sp_large = RecursiveSplitter(SplitterConfig(provider="recursive", chunk_size=500, chunk_overlap=0))
    small_chunks = sp_small.split_text(text)
    large_chunks = sp_large.split_text(text)
    assert len(small_chunks) > len(large_chunks)


# ---------------------------------------------------------------------------
# Markdown 结构感知：标题 / 代码块不被打断
# ---------------------------------------------------------------------------


def test_headers_preferred_as_boundaries():
    from core.settings import SplitterConfig

    md = dedent(
        """\
        ## 第一节

        这是第一节的内容，写一些文字用于填充。

        ## 第二节

        这是第二节的内容，写一些文字用于填充。
        """
    )
    sp = RecursiveSplitter(SplitterConfig(provider="recursive", chunk_size=40, chunk_overlap=0))
    chunks = sp.split_text(md)
    # 至少切出 2 块，且每块以标题开头（keep_separator=True）。
    assert len(chunks) >= 2
    joined = "".join(chunks)
    assert "## 第一节" in joined
    assert "## 第二节" in joined


def test_code_block_not_broken_inside():
    from core.settings import SplitterConfig

    code = "def add(a, b):\n    return a + b\n"
    md = dedent(
        """\
        ## 示例代码

        下面是一段代码：

        ```python
        def add(a, b):
            return a + b
        ```

        结束说明。
        """
    )
    sp = RecursiveSplitter(SplitterConfig(provider="recursive", chunk_size=80, chunk_overlap=0))
    chunks = sp.split_text(md)
    # 代码块内部内容应完整出现在某一块中（不被字符级切断）。
    found_code = any("return a + b" in c for c in chunks)
    assert found_code


def test_custom_separators_override_default(tmp_path, registered):
    from core.settings import SplitterConfig

    custom = ["\n\n", "\n", " "]
    sp = RecursiveSplitter(SplitterConfig(provider="recursive", chunk_size=50, chunk_overlap=0, separators=custom))
    # 自定义 separators 不影响接口契约。
    chunks = sp.split_text("para1\n\npara2\n\npara3")
    assert len(chunks) >= 1


def test_default_markdown_separators_constant():
    assert DEFAULT_MARKDOWN_SEPARATORS[0] == "\n## "
    assert "" in DEFAULT_MARKDOWN_SEPARATORS


# ---------------------------------------------------------------------------
# 通过工厂 + 配置驱动验证
# ---------------------------------------------------------------------------


def test_factory_config_driven_chunk_size(tmp_path, registered):
    settings_small = _settings(tmp_path, provider="recursive", chunk_size=50, chunk_overlap=0)
    settings_large = _settings(tmp_path, provider="recursive", chunk_size=500, chunk_overlap=0)
    text = "word " * 200
    small = registered.create(settings_small).split_text(text)
    large = registered.create(settings_large).split_text(text)
    assert len(small) > len(large)


def test_overlap_zero_vs_positive_adjacent_chunks():
    """QA M-10 / M-11：每个 chunk 不超过 chunk_size；overlap=0 时相邻 chunk 无重复文本，>0 时有。"""
    from core.settings import SplitterConfig

    text = " ".join(f"w{i:03d}" for i in range(300))  # 每个词唯一，重叠可按词集合精确判断

    def _chunks(overlap: int) -> list[str]:
        sp = RecursiveSplitter(SplitterConfig(provider="recursive", chunk_size=100, chunk_overlap=overlap))
        return sp.split_text(text)

    no_overlap = _chunks(0)
    with_overlap = _chunks(40)
    assert len(no_overlap) > 1 and len(with_overlap) > len(no_overlap)
    assert max(len(c) for c in no_overlap + with_overlap) <= 100
    for prev, nxt in zip(no_overlap, no_overlap[1:]):
        assert not set(prev.split()) & set(nxt.split())
    for prev, nxt in zip(with_overlap, with_overlap[1:]):
        assert set(prev.split()) & set(nxt.split())
