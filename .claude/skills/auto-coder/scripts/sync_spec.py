#!/usr/bin/env python3
"""
Spec Sync — splits spec markdown into chapter files under auto-coder/references/.

Supports multiple specs so incremental specs (docs/specs/*.md) get their own
prefixed chapter files instead of clobbering the main DEV_SPEC chapters.

Usage:
    python scripts/sync_spec.py [--force]
        Sync the main spec (docs/frozen/01_DEV_SPEC_frozen.md), files like 01-overview.md.

    python scripts/sync_spec.py --spec docs/specs/SIRCHMUNK_EXTENSION_SPEC.md --prefix sx [--force]
        Sync an extra spec; chapter files become sx-01-overview.md, sx-07-schedule.md, ...
        Without --prefix, the spec filename stem (lowercased) is used as prefix.

Each spec keeps its own hash file (.spec_hash / .spec_hash.<prefix>) so re-runs
skip work when the source is unchanged.
"""

import hashlib
import re
import sys
from pathlib import Path
from typing import List, Tuple, NamedTuple, Optional


class Chapter(NamedTuple):
    number: int
    cn_title: str
    filename: str
    start_line: int
    end_line: int
    line_count: int


# Chapter number -> English slug (encoding-independent)
NUMBER_SLUG_MAP = {
    1: "overview",
    2: "features",
    3: "tech-stack",
    4: "testing",
    5: "architecture",
    6: "schedule",
    7: "future",
}

# Title-keyword slugs, checked in order before the number-based map. Keeps the
# pipeline stable when an incremental spec's chapter numbers don't line up with
# DEV_SPEC's (e.g. SX spec chapter 6 = 配置变更汇总 -> *-config.md, not schedule).
KEYWORD_SLUGS = (
    (("schedule", "排期", "进度"), "schedule"),
    (("testing", "测试"), "testing"),
    (("architecture", "架构", "模块设计"), "architecture"),
    (("config", "配置"), "config"),
    (("scope", "范围"), "scope"),
    (("evidence", "源码实证"), "source-evidence"),
    (("overview", "概述"), "overview"),
    (("features", "功能", "特性"), "features"),
)


def _slug(chapter_num: int, title: str) -> str:
    lower = title.lower()
    for keywords, slug in KEYWORD_SLUGS:
        if any(k in lower for k in keywords):
            return slug
    if chapter_num in NUMBER_SLUG_MAP:
        return NUMBER_SLUG_MAP[chapter_num]
    # Fallback: sanitize whatever title text we have
    clean = re.sub(r'[^\w]+', '-', title, flags=re.ASCII).strip('-').lower()
    return clean or f"chapter-{chapter_num}"


def detect_chapters(content: str) -> List[Chapter]:
    lines = content.split('\n')
    starts: List[Tuple[int, str, int]] = []
    for i, line in enumerate(lines):
        m = re.match(r'^## (\d+)\.\s+(.+)$', line)
        if m:
            starts.append((int(m.group(1)), m.group(2).strip(), i))
    if not starts:
        raise ValueError("No chapters found. Expected '## N. Title'")
    chapters = []
    for idx, (num, title, start) in enumerate(starts):
        end = starts[idx + 1][2] if idx + 1 < len(starts) else len(lines)
        chapters.append(Chapter(num, title, f"{num:02d}-{_slug(num, title)}.md", start, end, end - start))
    return chapters


def _default_spec() -> Path:
    skill_dir = Path(__file__).parent.parent          # auto-coder/
    repo_root = skill_dir.parent.parent.parent        # project root
    candidates = (
        repo_root / "docs/frozen/01_DEV_SPEC_frozen.md",  # frozen spec (docs reorg)
        repo_root / "docs/specs/01_DEV_SPEC_frozen.md",   # legacy location 1
        repo_root / "01_DEV_SPEC_frozen.md",              # legacy location 2
    )
    for cand in candidates:
        if cand.exists():
            return cand
    return candidates[0]  # for the error message


def sync(spec: Path, prefix: str, force: bool = False):
    skill_dir = Path(__file__).parent.parent          # auto-coder/
    specs_dir = skill_dir / "references"
    hash_file = skill_dir / (".spec_hash" if not prefix else f".spec_hash.{prefix}")

    if not spec.exists():
        print(f"ERROR: {spec} not found"); sys.exit(1)

    # Hash check
    current_hash = hashlib.sha256(spec.read_bytes()).hexdigest()
    if not force and hash_file.exists() and hash_file.read_text().strip() == current_hash:
        print(f"specs up-to-date: {spec.name}"); return

    content = spec.read_text(encoding='utf-8')
    chapters = detect_chapters(content)
    lines = content.split('\n')

    specs_dir.mkdir(parents=True, exist_ok=True)

    def out_name(ch: Chapter) -> str:
        return f"{prefix}-{ch.filename}" if prefix else ch.filename

    # Clean orphans, scoped to this spec's own naming pattern so a prefixed
    # spec's files never collide with (or get cleaned by) the main spec.
    if prefix:
        pattern = re.compile(rf"^{re.escape(prefix)}-\d{{2}}-.*\.md$")
    else:
        pattern = re.compile(r"^\d{2}-.*\.md$")
    old = {f.name for f in specs_dir.glob("*.md") if pattern.match(f.name)}
    new = {out_name(ch) for ch in chapters}
    for f in sorted(old - new):
        (specs_dir / f).unlink()

    # Write chapters
    for ch in chapters:
        (specs_dir / out_name(ch)).write_text('\n'.join(lines[ch.start_line:ch.end_line]), encoding='utf-8')

    hash_file.write_text(current_hash)
    tag = f" (prefix '{prefix}')" if prefix else ""
    print(f"synced {len(chapters)} chapters from {spec.name}{tag}")


def _parse_args(argv: List[str]) -> Tuple[Optional[Path], str, bool]:
    force = "--force" in argv
    spec: Optional[Path] = None
    prefix = ""
    if "--spec" in argv:
        i = argv.index("--spec")
        spec = Path(argv[i + 1])
    if "--prefix" in argv:
        i = argv.index("--prefix")
        prefix = argv[i + 1].strip('-').lower()
    elif spec is not None:
        prefix = re.sub(r'[^\w]+', '-', spec.stem).strip('-').lower()
    return spec, prefix, force


if __name__ == "__main__":
    _spec, _prefix, _force = _parse_args(sys.argv[1:])
    sync(_spec or _default_spec(), _prefix, _force)
