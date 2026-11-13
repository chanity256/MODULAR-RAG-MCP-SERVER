"""Skill 镜像同步（AI_NATIVE_SDLC_SPEC SD6.2）：``.factory/skills/`` 是单一来源。

从来源目录生成 ``.claude/skills/`` 与 ``.github/skills/`` 下的同名 skill，每份镜像自包含：
文本中的 ``.factory/skills/`` 路径改写为镜像自己的目录。两类内容不进镜像：

- 单一状态文件（``qa-tester/QA_*.md``）：QA 计划 / 进度 / 对照表只在来源目录保留一份，
  镜像里指向它们的路径也不改写，三份 skill 读写的是同一份状态；
- 生成物与缓存（``auto-coder/references/``、``.spec_hash*``、``__pycache__``）：由各副本自己的
  ``sync_spec.py`` 运行时生成，已被 ``.gitignore`` 忽略。

只同步 ``SYNCED_SKILLS``；``review-guidelines`` 只存在于 ``.factory/``（Droid 自动 review 的读取位置），
``.github/skills/`` 独有的 5 个 skill 不在范围内（规格 §2.2）。

用法::

    python scripts/sync_skills.py            # 重新生成镜像
    python scripts/sync_skills.py --check    # 只校验，有漂移时退出码 1
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

SOURCE = ".factory/skills"
MIRRORS = (".claude/skills", ".github/skills")
SYNCED_SKILLS = ("auto-coder", "docs-governance", "qa-tester", "resume-writer", "skill-creator")

SKIP_DIR_NAMES = {"__pycache__"}
SKIP_FILE_NAMES = {".DS_Store"}

#: 仅存在于来源目录的路径前缀（相对 skill 根，POSIX 风格）。
SOURCE_ONLY_PREFIXES = ("auto-coder/references/",)
SOURCE_ONLY_PATTERNS = (
    re.compile(r"^auto-coder/\.spec_hash"),
    re.compile(r"^qa-tester/QA_[^/]*\.md$"),
)

#: 改写 ``.factory/skills``（带或不带结尾斜杠），但指向单一状态文件的路径保持原样。
_REWRITE = re.compile(r"\.factory/skills(?![\w.-])(?!/qa-tester/QA_)")


@dataclass(frozen=True)
class Drift:
    kind: str  # "missing" | "stale" | "differs"
    path: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.path}"


def _is_source_only(rel: str) -> bool:
    return rel.startswith(SOURCE_ONLY_PREFIXES) or any(p.match(rel) for p in SOURCE_ONLY_PATTERNS)


def _iter_skill_files(base: Path, skill: str) -> dict[str, Path]:
    """返回 ``{skill 相对路径: 文件}``，已排除缓存与来源独有内容。"""
    files: dict[str, Path] = {}
    skill_dir = base / skill
    if not skill_dir.is_dir():
        return files
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file() or path.name in SKIP_FILE_NAMES:
            continue
        if any(part in SKIP_DIR_NAMES for part in path.relative_to(base).parts):
            continue
        rel = path.relative_to(base).as_posix()
        if _is_source_only(rel):
            continue
        files[rel] = path
    return files


def render(text: str, mirror: str) -> str:
    """把来源文本改写为 ``mirror``（如 ``.claude/skills``）下的版本。"""
    return _REWRITE.sub(mirror, text)


def expected_mirror(root: Path, mirror: str) -> dict[str, str]:
    source = root / SOURCE
    expected: dict[str, str] = {}
    for skill in SYNCED_SKILLS:
        if not (source / skill).is_dir():
            raise FileNotFoundError(f"source skill missing: {SOURCE}/{skill}")
        for rel, path in _iter_skill_files(source, skill).items():
            expected[rel] = render(path.read_text(encoding="utf-8"), mirror)
    return expected


def check(root: Path = _ROOT) -> list[Drift]:
    drifts: list[Drift] = []
    for mirror in MIRRORS:
        expected = expected_mirror(root, mirror)
        actual: dict[str, Path] = {}
        for skill in SYNCED_SKILLS:
            actual.update(_iter_skill_files(root / mirror, skill))
        for rel, content in expected.items():
            shown = f"{mirror}/{rel}"
            if rel not in actual:
                drifts.append(Drift("missing", shown))
            elif actual[rel].read_text(encoding="utf-8") != content:
                drifts.append(Drift("differs", shown))
        for rel in sorted(actual.keys() - expected.keys()):
            drifts.append(Drift("stale", f"{mirror}/{rel}"))
    return drifts


def sync(root: Path = _ROOT) -> list[Drift]:
    """把镜像写成期望状态，返回修正前的漂移列表。"""
    drifts = check(root)
    for mirror in MIRRORS:
        expected = expected_mirror(root, mirror)
        for skill in SYNCED_SKILLS:
            for rel, path in _iter_skill_files(root / mirror, skill).items():
                if rel not in expected:
                    path.unlink()
        for rel, content in expected.items():
            target = root / mirror / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="" 保留来源的换行符，避免 Windows 上写成 CRLF 导致假漂移。
            with target.open("w", encoding="utf-8", newline="") as fh:
                fh.write(content)
            shutil.copymode(root / SOURCE / rel, target)
    return drifts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    parser.add_argument("--root", type=Path, default=_ROOT, help="仓库根目录")
    args = parser.parse_args(argv)

    if args.check:
        drifts = check(args.root)
        for drift in drifts:
            print(drift)
        if drifts:
            print(
                f"{len(drifts)} mirror drift(s): edit {SOURCE}/ (never the mirrors), "
                "then run: python scripts/sync_skills.py",
                file=sys.stderr,
            )
            return 1
        print("skill mirrors in sync")
        return 0

    drifts = sync(args.root)
    for drift in drifts:
        print(f"fixed {drift}")
    print(f"synced {len(SYNCED_SKILLS)} skills into {', '.join(MIRRORS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
