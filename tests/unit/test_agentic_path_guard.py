"""S1.3: PathGuard 白名单守卫单元测试（离线）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from libs.agentic_search.path_guard import PathGuard, PathGuardError


@pytest.fixture()
def roots(tmp_path: Path) -> dict[str, Path]:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("hello", encoding="utf-8")
    (docs / ".env").write_text("KEY=1", encoding="utf-8")
    (docs / ".git").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    return {"docs": docs, "outside": outside}


class TestValidatePaths:
    def test_non_empty_string(self, roots):
        guard = PathGuard([roots["docs"]])
        assert guard.validate_paths([roots["docs"]]) == [str(roots["docs"])]

    def test_empty_roots_fail_closed(self, roots):
        with pytest.raises(PathGuardError, match="fail-closed"):
            PathGuard([])

    def test_missing_root_fails_fast(self, roots):
        with pytest.raises(PathGuardError, match="does not exist"):
            PathGuard([roots["docs"] / "nope-missing"])

    @pytest.mark.parametrize(
        "bad",
        [
            pytest.param([""], id="empty-string"),
            pytest.param(["/etc/passwd"], id="outside-root"),
            pytest.param([None], id="not-str"),
            pytest.param([42], id="int"),
        ],
    )
    def test_rejects(self, roots, bad):
        guard = PathGuard([roots["docs"]])
        with pytest.raises(PathGuardError):
            guard.validate_paths(bad)

    def test_symlink_escape_rejected(self, roots, tmp_path):
        link = tmp_path / "link-out"
        link.symlink_to(roots["outside"])
        guard = PathGuard([roots["docs"]])
        with pytest.raises(PathGuardError, match="outside allowed roots"):
            guard.validate_paths([link])

    def test_denied_substring_rejected(self, roots):
        guard = PathGuard([roots["docs"]], denied_substrings=[".env"])
        with pytest.raises(PathGuardError, match="denied substring"):
            guard.validate_paths([roots["docs"] / ".env"])

    def test_default_denied_covers_git_dir(self, roots):
        guard = PathGuard([roots["docs"]])
        with pytest.raises(PathGuardError, match="denied substring"):
            guard.validate_paths([roots["docs"] / ".git"])

    def test_relative_path_resolved_against_cwd(self, roots, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.chdir(roots["docs"])
        guard = PathGuard([roots["docs"]])
        assert guard.validate_paths(["a.md"]) == [str(roots["docs"] / "a.md")]

    def test_dedup_preserves_order(self, roots):
        guard = PathGuard([roots["docs"]])
        paths = [str(roots["docs"]), str(roots["docs"] / "a.md"), str(roots["docs"])]
        assert guard.validate_paths(paths) == [str(roots["docs"]), str(roots["docs"] / "a.md")]


class TestFilterEvidencePaths:
    def test_keeps_allowed_drops_outside(self, roots):
        guard = PathGuard([roots["docs"]])
        kept = guard.filter_evidence_paths(
            [str(roots["docs"] / "a.md"), str(roots["outside"] / "x.md")]
        )
        assert kept == [str(roots["docs"] / "a.md")]

    def test_keeps_urls(self, roots):
        guard = PathGuard([roots["docs"]])
        assert guard.filter_evidence_paths(["https://example.com/doc"]) == ["https://example.com/doc"]

    def test_drops_denied(self, roots):
        guard = PathGuard([roots["docs"]], denied_substrings=[".env"])
        assert guard.filter_evidence_paths([str(roots["docs"] / ".env")]) == []

    def test_non_string_and_garbage_skipped(self, roots):
        guard = PathGuard([roots["docs"]])
        assert guard.filter_evidence_paths(["", None, "   "]) == []  # type: ignore[list-item]

    def test_preserves_order_and_dedup(self, roots):
        guard = PathGuard([roots["docs"]])
        p = str(roots["docs"] / "a.md")
        assert guard.filter_evidence_paths([p, str(roots["outside"]), p]) == [p]
