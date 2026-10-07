"""Markdown Loader（IR7.1 / IR7.2，Vault 感知解析器）。

# Note: vault_root 取值 / 不进 per-suffix 缓存 / 空文档与 URL 图片降级口径的取舍
# — 见 .agents/notes/implemented/feature/2026-10-07-markdown-loader-vault-root-no-cache.md

按 INGESTION_ROBUSTNESS_SPEC 3.8.1 的契约实现 ``BaseLoader``：解析 Markdown 为
与 ``PdfLoader`` 同构的 ``Document``（``id`` 为文件内容 SHA256），解析管线
有序、逐段降级不阻塞：

1. UTF-8 读取；编码错误抛可读 ``MarkdownLoaderError``（不静默替换）。
2. YAML frontmatter：``---`` 行界定；优先零依赖行级解析（``key: value`` /
   ``key: [a, b]`` / 多行 ``- item`` 列表），行级解析失败且环境已有 PyYAML
   时用其解析（import 在 try 内，零新增主依赖）。解析失败降级：frontmatter
   原样拼回正文，``metadata["frontmatter_parse_error"]=True``。
3. title 三级兜底：frontmatter.title > 正文首个 ``# H1`` > 文件名 stem。
4. heading_outline：全部 ATX 标题行（``#``~``######``，不含代码块内）。
5. 行内 tags：匹配 ``(^|\\s)#([\\w\\u4e00-\\u9fff][\\w\\u4e00-\\u9fff/-]*)``，
   排除标题行与代码块内；与 frontmatter tags 合并去重保序。
6. wikilinks：``[[target]]`` / ``[[target|alias]]`` 正文替换为 alias 或
   target（去掉双方括号与 ``.md`` 扩展名），target 记入 metadata（去重保序）。
7. 嵌入引用 ``![[target]]`` 与 ``![alt](path)``：图片扩展名时按 ``vault_root``
   解析；存在 → ``make_image_id`` + ``[IMAGE: {image_id}]`` 占位符 + ImageRef
   落盘 ``images_root``（对齐 pdf_loader 的 C3 图片机制）；不存在 → 保留原文
   并记入 ``metadata["missing_image_refs"]``，告警不阻塞。目标是笔记
   （``![[note.md]]``）→ 不递归展开，保留文本引用并记入 ``metadata["embeds"]``。
8. ``metadata["doc_type"]="markdown"``；无 ``page`` 字段（不适用）。

确定性：同输入同输出（无随机性、无时间戳入 metadata）；单图失败降级不阻塞
整体。``__init__.py`` 保持既有空文件模式（pdf/markitdown loader 同样不从包
``__init__`` 再导出，测试/管线按模块路径导入）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.types import (
    Document,
    ImageRef,
    image_placeholder,
    iter_image_placeholders,
    make_image_id,
)
from libs.loader.base_loader import BaseLoader
from observability.logger import get_logger

__all__ = ["MarkdownLoader", "MarkdownLoaderError"]

# 流式读取块大小（与 pdf_loader / file_integrity 保持一致）。
_CHUNK_SIZE = 64 * 1024

#: 视为图片引用的扩展名（规格 3.8.1 第 7 步）。
IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
)

_MARKDOWN_SUFFIXES = (".md", ".markdown")

_FRONTMATTER_DELIM = "---"

# 围栏代码块（``` 或 ~~~）开合行。
_FENCE_PREFIXES = ("```", "~~~")

# ATX 标题行（1-6 个 # + 空格 + 标题文本；容忍最多 3 个前导空格，去尾部闭合 #）。
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(?P<text>.*?)\s*#*\s*$")

# 行内标签：(^|\s)#([\w\u4e00-\u9fff][\w\u4e00-\u9fff/-]*)，用零宽断言避免消耗分隔符。
_INLINE_TAG_RE = re.compile(r"(?:^|(?<=\s))#(?P<tag>[\w\u4e00-\u9fff][\w\u4e00-\u9fff/-]*)")

# 行内引用单趟扫描：![[target]] 优先于 ![alt](path) 优先于 [[target|alias]]，
# 保证 ![[...]] 不会被 wikilink 分支吞掉。
_INLINE_REF_RE = re.compile(
    r"!\[\[(?P<embed_target>[^\[\]]+?)\]\]"
    r"|!\[(?P<md_alt>[^\]]*)\]\((?P<md_path>[^)]+)\)"
    r"|\[\[(?P<wiki_raw>[^\[\]]+?)\]\]"
)

# frontmatter 行级解析的 key: value 行。
_FM_KEY_VALUE_RE = re.compile(r"^(?P<key>[\w\u4e00-\u9fff][\w\u4e00-\u9fff .-]*?)\s*:\s*(?P<value>.*)$")


class MarkdownLoaderError(RuntimeError):
    """MarkdownLoader 解析失败（编码错误等不可恢复错误）。"""


def _strip_markdown_suffix(target: str) -> str:
    """去掉出链目标尾部的 ``.md`` / ``.markdown`` 扩展名（规格：文件名不含扩展名）。"""
    return re.sub(r"\.(?:md|markdown)$", "", target, flags=re.IGNORECASE)


def _parse_scalar(text: str) -> Any | None:
    """行级标量解析：``[a, b]`` 列表 / 引号字符串 / 原样字符串。

    Returns:
        解析结果；无法解析时返回 ``None``（由调用方降级）。
    """
    text = text.strip()
    if not text:
        return None
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        items = [_parse_scalar(part) for part in inner.split(",")]
        if any(item is None for item in items):
            return None
        return items
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def _parse_frontmatter_lines(block: str) -> dict[str, Any] | None:
    """零依赖行级解析：``key: value`` / ``key: [a, b]`` / 多行 ``- item`` 列表。

    Returns:
        解析结果 dict；遇到无法识别的行返回 ``None``（交给 PyYAML 或降级）。
    """
    result: dict[str, Any] = {}
    current_key: str | None = None
    for raw_line in block.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            # 空行与 YAML 注释不参与解析。
            continue
        if stripped.startswith("- "):
            if current_key is None:
                return None
            item = _parse_scalar(stripped[2:])
            if item is None:
                return None
            result[current_key].append(item)
            continue
        match = _FM_KEY_VALUE_RE.match(stripped)
        if match is None:
            return None
        key = match.group("key").strip()
        value_text = match.group("value")
        if value_text.strip():
            value = _parse_scalar(value_text)
            if value is None:
                return None
            result[key] = value
            current_key = None
        else:
            result[key] = []
            current_key = key
    return result


def _parse_frontmatter_with_yaml(block: str) -> dict[str, Any] | None:
    """PyYAML 兜底解析（可选依赖，import 放 try 内，零新增主依赖）。"""
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        data = yaml.safe_load(block)
    except Exception:  # noqa: BLE001 - 任何 YAML 解析失败都走降级，不阻塞摄取
        return None
    return data if isinstance(data, dict) else None


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str, bool]:
    """提取文件头部的 YAML frontmatter。

    Returns:
        ``(frontmatter dict, 去除 frontmatter 后的正文, 是否解析失败)``；
        解析失败时 frontmatter 原样保留在正文中并置 ``parse_error=True``；
        文件不以 ``---`` 开头或围栏未闭合时视为无 frontmatter。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_DELIM:
        return {}, text, False
    for idx in range(1, len(lines)):
        if lines[idx].strip() == _FRONTMATTER_DELIM:
            block = "\n".join(lines[1:idx])
            parsed = _parse_frontmatter_lines(block)
            if parsed is None:
                parsed = _parse_frontmatter_with_yaml(block)
            if parsed is None:
                return {}, text, True
            return parsed, "\n".join(lines[idx + 1:]), False
    return {}, text, False  # 未闭合的 --- 围栏视为普通正文


@dataclass
class _ParseState:
    """单次 ``load`` 的解析累积状态。"""

    doc_hash: str
    vault_root: Path
    headings: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    wikilinks: list[str] = field(default_factory=list)
    embeds: list[str] = field(default_factory=list)
    image_refs: list[ImageRef] = field(default_factory=list)
    missing_image_refs: list[str] = field(default_factory=list)

    def add_unique(self, bucket: list[str], value: str) -> None:
        if value and value not in bucket:
            bucket.append(value)


class MarkdownLoader(BaseLoader):
    """Markdown -> ``Document`` 加载器（Vault 感知，frontmatter/tags/wikilinks/embeds）。

    Args:
        vault_root: 解析 ``![[image.png]]`` / ``![](relative.png)`` 相对引用的
            根目录；缺省按 ``source`` 所在目录解析。
        images_root: 图片落盘根目录（对齐 ``PdfLoader`` 的
            ``{images_root}/{doc_hash}/{image_id}.{ext}`` 约定，默认 ``data/images``）。
    """

    def __init__(
        self,
        *,
        vault_root: str | None = None,
        images_root: str | None = None,
    ) -> None:
        self._vault_root = Path(vault_root) if vault_root is not None else None
        self._images_root = Path(images_root) if images_root is not None else Path("data/images")
        self._logger = get_logger("modular-rag.loader.markdown")

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def load(self, path: str) -> Document:
        """解析 Markdown 为 ``Document``（frontmatter/嵌入/图片逐段降级不阻塞）。"""
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"file not found or not a regular file: {file_path}")

        doc_hash = self._compute_sha256(file_path)
        try:
            raw_text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise MarkdownLoaderError(
                f"failed to decode {file_path} as UTF-8 (encoding errors are not "
                f"silently replaced): {exc}"
            ) from exc

        frontmatter, body, parse_error = _split_frontmatter(raw_text)
        state = _ParseState(
            doc_hash=doc_hash,
            vault_root=self._vault_root if self._vault_root is not None else file_path.parent,
        )

        title = self._resolve_title(frontmatter, body, file_path)
        text = self._process_body(body, state)

        metadata: dict[str, Any] = {
            "source_path": str(path),
            "doc_type": "markdown",
            "sha256": doc_hash,
            "title": title,
            "heading_outline": state.headings,
        }
        tags = self._merged_tags(frontmatter, state.tags)
        if tags:
            metadata["tags"] = tags
        if state.wikilinks:
            metadata["wikilinks"] = state.wikilinks
        if state.embeds:
            metadata["embeds"] = state.embeds
        if parse_error:
            metadata["frontmatter_parse_error"] = True
        if state.missing_image_refs:
            metadata["missing_image_refs"] = state.missing_image_refs
        if state.image_refs:
            self._backfill_offsets(text, state.image_refs)
            metadata["images"] = [ref.to_dict() for ref in state.image_refs]
        return Document(id=doc_hash, text=text, metadata=metadata)

    # ------------------------------------------------------------------
    # frontmatter / title / tags
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_title(frontmatter: dict[str, Any], body: str, file_path: Path) -> str:
        """title 三级兜底：frontmatter.title > 正文首个 ``# H1`` > 文件名 stem。"""
        raw_title = frontmatter.get("title")
        if raw_title is not None and not isinstance(raw_title, (list, dict)):
            title = str(raw_title).strip()
            if title:
                return title
        for line in body.splitlines():
            match = _HEADING_RE.match(line)
            if match is not None and match.group(1) == "#":
                return match.group("text").strip()
        return file_path.stem

    @staticmethod
    def _frontmatter_tags(frontmatter: dict[str, Any]) -> list[str]:
        raw = frontmatter.get("tags")
        if raw is None:
            return []
        if isinstance(raw, str):
            candidates = [raw]
        elif isinstance(raw, (list, tuple)):
            candidates = [str(item) for item in raw]
        else:
            return []
        return [item.strip() for item in candidates if str(item).strip()]

    def _merged_tags(self, frontmatter: dict[str, Any], inline_tags: list[str]) -> list[str]:
        """frontmatter tags + 行内 tags 合并，去重保序。"""
        merged: list[str] = []
        for tag in [*self._frontmatter_tags(frontmatter), *inline_tags]:
            if tag not in merged:
                merged.append(tag)
        return merged

    # ------------------------------------------------------------------
    # 正文处理（逐行：引用替换 + 标题/行内标签收集，跳过围栏代码块）
    # ------------------------------------------------------------------

    def _process_body(self, body: str, state: _ParseState) -> str:
        out_lines: list[str] = []
        in_fence = False
        for line in body.splitlines():
            stripped = line.strip()
            if any(stripped.startswith(prefix) for prefix in _FENCE_PREFIXES):
                in_fence = not in_fence
                out_lines.append(line)
                continue
            if in_fence:
                out_lines.append(line)
                continue
            new_line = self._transform_line(line, state)
            self._collect_heading_and_tags(new_line, state)
            out_lines.append(new_line)
        return "\n".join(out_lines)

    def _collect_heading_and_tags(self, line: str, state: _ParseState) -> None:
        match = _HEADING_RE.match(line)
        if match is not None:
            state.headings.append(match.group("text").strip())
            return  # 标题行整体不计行内标签
        for tag_match in _INLINE_TAG_RE.finditer(line):
            state.tags.append(tag_match.group("tag"))

    def _transform_line(self, line: str, state: _ParseState) -> str:
        """单趟替换嵌入/图片/链接引用（嵌入优先于链接，避免 ![[...]] 被吞）。"""

        def replace(match: re.Match[str]) -> str:
            if match.group("embed_target") is not None:
                return self._handle_embed(match, state)
            if match.group("md_path") is not None:
                return self._handle_md_image(match, state)
            return self._handle_wikilink(match, state)

        return _INLINE_REF_RE.sub(replace, line)

    # ------------------------------------------------------------------
    # 引用处理（图片 / 笔记嵌入 / wikilink）
    # ------------------------------------------------------------------

    def _handle_embed(self, match: re.Match[str], state: _ParseState) -> str:
        """``![[target]]``：图片引用走图片链路，笔记引用不展开仅记录。"""
        original = match.group(0)
        target = match.group("embed_target").strip()
        if Path(target).suffix.lower() in IMAGE_EXTENSIONS:
            return self._resolve_image(target, original, state)
        state.add_unique(state.embeds, target)
        return original  # 笔记嵌入保留文本引用，不递归展开

    def _handle_md_image(self, match: re.Match[str], state: _ParseState) -> str:
        """``![alt](path)``：本地图片引用走图片链路，其余保留原文。"""
        original = match.group(0)
        target = match.group("md_path").strip()
        if "://" in target:
            return original  # 外部 URL 不做本地解析
        if Path(target).suffix.lower() in IMAGE_EXTENSIONS:
            return self._resolve_image(target, original, state)
        state.add_unique(state.embeds, target)  # 指向笔记的嵌入引用
        return original

    def _handle_wikilink(self, match: re.Match[str], state: _ParseState) -> str:
        """``[[target]]`` / ``[[target|alias]]``：替换为 alias 或 target 并记录出链。"""
        raw = match.group("wiki_raw").strip()
        if "|" in raw:
            target, alias = raw.split("|", 1)
            target = target.strip()
            display = alias.strip() or _strip_markdown_suffix(target)
        else:
            target = raw
            display = _strip_markdown_suffix(target)
        if not target:
            return match.group(0)
        state.add_unique(state.wikilinks, _strip_markdown_suffix(target))
        return display or match.group(0)

    def _resolve_image(self, target: str, original: str, state: _ParseState) -> str:
        """按 vault_root 解析图片引用：存在 → 占位符 + 落盘；缺失 → 保留原文并记录。"""
        resolved = state.vault_root / target
        if not resolved.is_file():
            self._logger.warning(
                "missing image reference %r in a markdown document (kept as-is)", target
            )
            state.add_unique(state.missing_image_refs, target)
            return original
        try:
            ref = self._store_image(state.doc_hash, len(state.image_refs), resolved)
        except Exception as exc:  # noqa: BLE001 - 单图失败降级不阻塞（对齐 C3 语义）
            self._logger.warning("skip image reference %r: %s", target, exc)
            state.add_unique(state.missing_image_refs, target)
            return original
        state.image_refs.append(ref)
        return image_placeholder(ref.id)

    def _store_image(self, doc_hash: str, seq: int, source_path: Path) -> ImageRef:
        """把 vault 内图片复制到 ``{images_root}/{doc_hash}/`` 并返回 ImageRef。

        Markdown 无分页语义，ID 的 page 段固定为 0；``ImageRef.page`` 保持 None。
        """
        image_id = make_image_id(doc_hash, 0, seq)
        out_dir = self._images_root / doc_hash
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{image_id}{source_path.suffix.lower()}"
        out_path.write_bytes(source_path.read_bytes())
        return ImageRef(id=image_id, path=str(out_path), page=None)

    @staticmethod
    def _backfill_offsets(text: str, refs: list[ImageRef]) -> None:
        """回填占位符在最终文本中的偏移（对齐 pdf_loader 的偏移回算）。"""
        offsets = dict(iter_image_placeholders(text))
        for ref in refs:
            offset = offsets.get(ref.id)
            if offset is not None:
                ref.text_offset = offset
                ref.text_length = len(image_placeholder(ref.id))

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_sha256(file_path: Path) -> str:
        digest = hashlib.sha256()
        with file_path.open("rb") as fh:
            while True:
                chunk = fh.read(_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()
