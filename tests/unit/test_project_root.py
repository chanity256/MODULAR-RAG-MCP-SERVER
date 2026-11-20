"""``src/core/project_root.py`` 的单元测试（ROOT_ANCHOR_SPEC RA1）。

覆盖：
- 默认推导结果等于仓库根（代码位置锚定，与 ``core/chat/prompts.py`` 先例一致）。
- ``RAG_PROJECT_ROOT`` 覆盖生效（绝对 / 相对 / 带波浪号）。
- 环境变量为空串时视为未设置，回退推导。
- 与进程 CWD 无关（任意目录下解析结果不变）。
- 纯计算：不建目录、不做 IO。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.project_root import PROJECT_ROOT_ENV, resolve_project_root

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_default_derivation_is_repo_root() -> None:
    """未设置环境变量时，推导结果等于仓库根。"""
    assert resolve_project_root() == PROJECT_ROOT


def test_env_override_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``RAG_PROJECT_ROOT`` 设为绝对路径时取其值（resolve 规范化后）。"""
    monkeypatch.setenv(PROJECT_ROOT_ENV, str(tmp_path / "bundle"))
    assert resolve_project_root() == (tmp_path / "bundle").resolve()


def test_env_override_relative_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    """``RAG_PROJECT_ROOT`` 相对路径时按当前 CWD 解析为绝对路径（env 值语义与常规环境变量一致）。"""
    monkeypatch.setenv(PROJECT_ROOT_ENV, "some/rel/root")
    resolved = resolve_project_root()
    assert resolved.is_absolute()
    assert resolved.name == "root"


def test_env_override_tilde_expanded(monkeypatch: pytest.MonkeyPatch) -> None:
    """``RAG_PROJECT_ROOT`` 以 ``~`` 开头时做 expanduser。"""
    monkeypatch.setenv(PROJECT_ROOT_ENV, "~/rag-root")
    resolved = resolve_project_root()
    assert "~" not in str(resolved)


def test_env_empty_string_falls_back_to_derivation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """环境变量为空串 / 空白时视为未设置，回退代码位置推导。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(PROJECT_ROOT_ENV, "   ")
    assert resolve_project_root() == PROJECT_ROOT


def test_cwd_independent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """从任意目录调用，推导结果不变（仓库根锚定，而非 CWD 锚定）。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    assert resolve_project_root() == PROJECT_ROOT


def test_no_directories_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """纯计算契约：解析过程不在 CWD 或推导结果下新建任何目录（RA2/RA3 的建目录由调用方负责）。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    resolve_project_root()
    assert list(tmp_path.iterdir()) == []
