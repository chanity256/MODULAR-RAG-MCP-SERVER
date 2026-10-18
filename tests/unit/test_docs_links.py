"""docs/ 文档链接与索引 drift guard。

锁定 docs/MOC.md「命名与流转规范」中的两条纪律：
1. docs/**/*.md、README.md、AGENTS.md 中的相对链接必须可解析（冻结/重命名后引用零残留）；
2. docs/ 下每个 .md 文档都必须登记进 docs/MOC.md（索引无遗漏）。

离线确定性：只读文件系统，零网络。
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOC = REPO_ROOT / "docs" / "MOC.md"
SCAN_PATHS = ("docs", "README.md", "AGENTS.md")

# 链接形如 [text](target)；剥离代码块/行内代码，避免把 `![](relative.png)`
# 这类 Markdown 语法示例误判为真实链接。
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`]*`")

EXTERNAL_PREFIXES = ("http://", "https://", "mailto:")


def _markdown_files() -> list[Path]:
    files: list[Path] = []
    for name in SCAN_PATHS:
        path = REPO_ROOT / name
        if path.is_dir():
            files.extend(sorted(path.rglob("*.md")))
        else:
            files.append(path)
    missing = [str(p) for p in files if not p.is_file()]
    assert not missing, f"扫描目标缺失: {missing}"
    return files


def _strip_code(text: str) -> str:
    return INLINE_CODE_RE.sub("", FENCE_RE.sub("", text))


@pytest.mark.unit
class TestDocsLinks:
    def test_relative_links_resolve(self) -> None:
        broken: list[str] = []
        for md in _markdown_files():
            text = _strip_code(md.read_text(encoding="utf-8"))
            for match in LINK_RE.finditer(text):
                target = match.group(1)
                if target.startswith(EXTERNAL_PREFIXES) or target.startswith("#"):
                    continue
                path_part = target.split("#")[0]
                if not path_part:
                    continue  # 纯锚点跳转
                if not (md.parent / path_part).exists():
                    broken.append(f"{md.relative_to(REPO_ROOT)} -> {target}")
        assert not broken, (
            "docs 存在坏链（冻结/重命名后引用未同步）：\n" + "\n".join(broken)
        )

    def test_all_docs_listed_in_moc(self) -> None:
        moc_text = MOC.read_text(encoding="utf-8")
        unlisted = [
            str(md.relative_to(REPO_ROOT / "docs"))
            for md in sorted((REPO_ROOT / "docs").rglob("*.md"))
            if md != MOC and md.stem not in moc_text
        ]
        assert not unlisted, (
            "docs/ 存在未登记进 MOC.md 的文档：\n" + "\n".join(unlisted)
        )
