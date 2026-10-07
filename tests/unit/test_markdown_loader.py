"""MarkdownLoader 单测（IR7.1 / IR7.2）：全离线、确定性、零网络零模型。

夹具：``tests/fixtures/vault/``（规格第 5 章「M6 测试夹具」定义的最小 vault）。
需要落盘图片的用例一律把 ``images_root`` 指向 ``tmp_path``，不触碰仓库 ``data/``。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from core.types import Document
from libs.loader.base_loader import BaseLoader
from libs.loader.markdown_loader import MarkdownLoader, MarkdownLoaderError

pytestmark = pytest.mark.unit

_VAULT = Path(__file__).resolve().parents[1] / "fixtures" / "vault"


def _load(path: Path, **kwargs) -> Document:
    kwargs.setdefault("images_root", str(path.parent / "images-out"))
    return MarkdownLoader(**kwargs).load(str(path))


def _load_vault(name: str, tmp_path: Path, **kwargs) -> Document:
    """按 vault 感知语义加载 fixtures/vault/notes 下的笔记。"""
    kwargs.setdefault("vault_root", str(_VAULT))
    kwargs.setdefault("images_root", str(tmp_path / "images"))
    return MarkdownLoader(**kwargs).load(str(_VAULT / "notes" / name))


# ---------------------------------------------------------------------------
# 契约基础
# ---------------------------------------------------------------------------


def test_is_base_loader():
    assert isinstance(MarkdownLoader(), BaseLoader)


def test_doc_type_contract_and_no_page(tmp_path):
    doc = _load_vault("frontmatter.md", tmp_path)
    assert doc.metadata["doc_type"] == "markdown"
    assert "page" not in doc.metadata
    assert "page_count" not in doc.metadata


def test_sha256_consistency(tmp_path):
    path = _VAULT / "notes" / "frontmatter.md"
    doc = _load_vault("frontmatter.md", tmp_path)
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert doc.id == expected
    assert doc.metadata["sha256"] == expected
    assert doc.metadata["source_path"] == str(path)


def test_determinism_same_input_same_output(tmp_path):
    loader_kwargs = {"vault_root": str(_VAULT), "images_root": str(tmp_path / "images")}
    doc1 = MarkdownLoader(**loader_kwargs).load(str(_VAULT / "notes" / "wikilinks.md"))
    doc2 = MarkdownLoader(**loader_kwargs).load(str(_VAULT / "notes" / "wikilinks.md"))
    assert doc1 == doc2  # Document dataclass 全字段相等（text + metadata）
    assert doc1.metadata == doc2.metadata


def test_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        MarkdownLoader().load(str(tmp_path / "missing.md"))


def test_invalid_utf8_raises_loader_error(tmp_path):
    path = tmp_path / "bad.md"
    path.write_bytes(b"\xff\xfe\x00\x81 invalid utf-8")
    with pytest.raises(MarkdownLoaderError, match="UTF-8"):
        MarkdownLoader().load(str(path))


# ---------------------------------------------------------------------------
# frontmatter
# ---------------------------------------------------------------------------


def test_frontmatter_parsed_and_removed_from_body(tmp_path):
    doc = _load_vault("frontmatter.md", tmp_path)
    assert doc.metadata["title"] == "前置元数据笔记"
    assert "frontmatter_parse_error" not in doc.metadata
    assert "---" not in doc.text
    assert "title:" not in doc.text
    assert "概述" in doc.text


def test_bad_frontmatter_degrades_without_blocking(tmp_path):
    doc = _load_vault("bad-frontmatter.md", tmp_path)
    assert doc.metadata["frontmatter_parse_error"] is True
    # frontmatter 原样拼回正文
    assert "title: [unclosed" in doc.text
    assert "---" in doc.text
    # title 降级到正文首个 H1
    assert doc.metadata["title"] == "降级笔记"
    # 后续解析管线不受阻塞
    assert "fallback" in doc.metadata["tags"]
    assert doc.metadata["heading_outline"] == ["降级笔记"]


def test_frontmatter_without_leading_delimiter_is_body(tmp_path):
    path = tmp_path / "plain.md"
    path.write_text("---\nnot at line one\n", encoding="utf-8")
    doc = _load(path)
    assert "frontmatter_parse_error" not in doc.metadata
    assert doc.text == "---\nnot at line one"


def test_unclosed_frontmatter_is_body(tmp_path):
    path = tmp_path / "unclosed.md"
    path.write_text("---\ntitle: never closed\nbody line\n", encoding="utf-8")
    doc = _load(path)
    assert "frontmatter_parse_error" not in doc.metadata
    assert "title: never closed" in doc.text


# ---------------------------------------------------------------------------
# title 三级兜底
# ---------------------------------------------------------------------------


def test_title_fallback_frontmatter_wins(tmp_path):
    doc = _load_vault("frontmatter.md", tmp_path)
    assert doc.metadata["title"] == "前置元数据笔记"


def test_title_fallback_to_first_h1(tmp_path):
    doc = _load_vault("bad-frontmatter.md", tmp_path)  # frontmatter 解析失败 → H1
    assert doc.metadata["title"] == "降级笔记"


def test_title_fallback_to_filename_stem(tmp_path):
    path = tmp_path / "我的笔记.md"
    path.write_text("没有 frontmatter 也没有 H1 的正文\n", encoding="utf-8")
    doc = _load(path)
    assert doc.metadata["title"] == "我的笔记"


# ---------------------------------------------------------------------------
# heading_outline 与行内 tags
# ---------------------------------------------------------------------------


def test_heading_outline_collects_atx_headings(tmp_path):
    doc = _load_vault("embeds-image.md", tmp_path)
    assert doc.metadata["heading_outline"] == ["嵌入图片"]


def test_inline_tags_merge_dedupe_and_exclusions(tmp_path):
    doc = _load_vault("inline-tags.md", tmp_path)
    # frontmatter tags 在前（保序），行内 tags 合并去重
    assert doc.metadata["tags"] == [
        "baseline",
        "固定标签",
        "hello",
        "中文标签",
        "nested/path",
        "trailing",
    ]


def test_tags_exclude_heading_lines_and_code_blocks(tmp_path):
    doc = _load_vault("inline-tags.md", tmp_path)
    assert "not-a-tag" not in doc.metadata["tags"]
    assert "comment_in_code" not in doc.metadata["tags"]


def test_no_tags_field_when_absent(tmp_path):
    path = tmp_path / "plain.md"
    path.write_text("# 只有标题\n\n正文无标签\n", encoding="utf-8")
    doc = _load(path)
    assert "tags" not in doc.metadata


def test_cjk_tags_from_chinese_note(tmp_path):
    doc = _load_vault("中文笔记.md", tmp_path)
    assert "项目/研发" in doc.metadata["tags"]
    assert "work" in doc.metadata["tags"]


# ---------------------------------------------------------------------------
# wikilinks
# ---------------------------------------------------------------------------


def test_wikilinks_replaced_and_outlinks_collected(tmp_path):
    doc = _load_vault("wikilinks.md", tmp_path)
    # 出链去重保序（不含扩展名）
    assert doc.metadata["wikilinks"] == ["frontmatter", "中文笔记"]
    # [[target]] / [[target|alias]] 正文替换
    assert "引用 frontmatter 与 别名形式" in doc.text
    assert "[[" not in doc.text.replace("![[note.md]]", "")


def test_wikilink_with_md_suffix_strips_extension(tmp_path):
    path = tmp_path / "links.md"
    path.write_text("见 [[target.md]] 与 [[target|显示名]]\n", encoding="utf-8")
    doc = _load(path)
    assert doc.metadata["wikilinks"] == ["target"]
    assert "见 target 与 显示名" in doc.text


# ---------------------------------------------------------------------------
# 嵌入与图片
# ---------------------------------------------------------------------------


def test_existing_image_embed_placeholder_ref_and_file(tmp_path):
    doc = _load_vault("embeds-image.md", tmp_path)
    images = doc.metadata["images"]
    assert len(images) == 1
    ref = images[0]
    doc_hash = doc.id
    # image_id 规则：{doc_hash}_{page}_{seq}，markdown 无分页 → page 段为 0
    assert ref["id"] == f"{doc_hash}_0_0"
    assert ref["page"] is None
    # 占位符出现在正文中，且偏移/长度回算正确
    placeholder = f"[IMAGE: {ref['id']}]"
    assert placeholder in doc.text
    assert doc.text[ref["text_offset"]:ref["text_offset"] + ref["text_length"]] == placeholder
    # 图片落盘（vault_root 解析到 assets/pic.png）
    stored = Path(ref["path"])
    assert stored.is_file()
    assert stored.read_bytes() == (_VAULT / "assets" / "pic.png").read_bytes()
    assert stored.parent == Path(tmp_path / "images" / doc_hash)
    assert doc.image_refs[0].id == ref["id"]


def test_missing_image_ref_kept_and_recorded(tmp_path):
    doc = _load_vault("embeds-image.md", tmp_path)
    assert doc.metadata["missing_image_refs"] == ["missing.png"]
    assert "![](missing.png)" in doc.text  # 原文保留
    assert doc.metadata.get("images", []) == [] or len(doc.metadata["images"]) == 1


def test_note_embed_not_expanded_only_recorded(tmp_path):
    doc = _load_vault("embeds-image.md", tmp_path)
    assert doc.metadata["embeds"] == ["wikilinks.md"]
    assert "![[wikilinks.md]]" in doc.text  # 文本引用保留，不递归展开


def test_default_vault_root_is_source_directory(tmp_path):
    (tmp_path / "img.png").write_bytes(b"png")
    (tmp_path / "a.md").write_text("![[img.png]]\n", encoding="utf-8")
    doc = _load(tmp_path / "a.md")
    assert len(doc.metadata["images"]) == 1
    assert "[IMAGE: " in doc.text


def test_missing_image_does_not_block_other_processing(tmp_path):
    path = tmp_path / "mixed.md"
    path.write_text(
        "# 标题\n\n![](gone.png) 正文 #tag [[目标]]\n",
        encoding="utf-8",
    )
    doc = _load(path)
    assert doc.metadata["missing_image_refs"] == ["gone.png"]
    assert doc.metadata["tags"] == ["tag"]
    assert doc.metadata["wikilinks"] == ["目标"]
    assert doc.metadata["heading_outline"] == ["标题"]


# ---------------------------------------------------------------------------
# 空文件 / 纯空白
# ---------------------------------------------------------------------------


def test_empty_file_loads_with_stem_title(tmp_path):
    path = tmp_path / "empty.md"
    path.write_text("", encoding="utf-8")
    doc = _load(path)
    assert doc.text == ""
    assert doc.metadata["title"] == "empty"
    assert doc.metadata["heading_outline"] == []
    assert doc.id == hashlib.sha256(b"").hexdigest()


def test_whitespace_only_file_loads(tmp_path):
    path = tmp_path / "blank.md"
    path.write_text("   \n\t\n", encoding="utf-8")
    doc = _load(path)
    assert doc.text.strip() == ""
    assert doc.metadata["title"] == "blank"


# ---------------------------------------------------------------------------
# fixtures/vault 冒烟遍历
# ---------------------------------------------------------------------------


def test_all_vault_notes_smoke(tmp_path):
    """遍历 vault 全部笔记：契约字段齐备、确定性、title 非空。"""
    notes = sorted((_VAULT / "notes").glob("*.md"))
    assert len(notes) == 6  # 夹具完整性
    for note in notes:
        kwargs = {"vault_root": str(_VAULT), "images_root": str(tmp_path / "images")}
        doc1 = MarkdownLoader(**kwargs).load(str(note))
        doc2 = MarkdownLoader(**kwargs).load(str(note))
        assert doc1 == doc2, note.name
        assert isinstance(doc1, Document)
        assert doc1.metadata["doc_type"] == "markdown"
        assert doc1.metadata["sha256"] == doc1.id
        assert doc1.metadata["title"]
        assert isinstance(doc1.metadata["heading_outline"], list)
        assert doc1.metadata["source_path"] == str(note)


def test_vault_smoke_expected_metadata_per_note(tmp_path):
    """逐笔记断言夹具设计意图（frontmatter/降级/标签/链接/嵌入）。"""
    expected = {
        "frontmatter.md": {"title": "前置元数据笔记", "tags": ["project/demo", "reading", "inline", "中文标签"]},
        "wikilinks.md": {"wikilinks": ["frontmatter", "中文笔记"], "embeds": ["note.md"]},
        "inline-tags.md": {"tags": ["baseline", "固定标签", "hello", "中文标签", "nested/path", "trailing"]},
        "bad-frontmatter.md": {"frontmatter_parse_error": True},
        "中文笔记.md": {"title": "中文笔记标题", "tags": ["项目/研发", "work"]},
    }
    for name, checks in expected.items():
        doc = _load_vault(name, tmp_path)
        for key, value in checks.items():
            assert doc.metadata[key] == value, f"{name}.{key}"
