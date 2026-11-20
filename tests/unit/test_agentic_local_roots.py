"""AR1（AGENTIC_UPLOAD_ROOTS_SPEC）：白名单追加上传目录 + 入口预建根目录。

纯离线。覆盖规格 §4 的三组离线测试：

- ``ensure_local_roots``：enabled=false 不建目录；``data/`` 下缺失的根被创建；
  ``data/`` 外缺失的根不创建；命中 ``denied_substrings`` 的根不创建；
  已存在的根不变；OSError 只记 warning 不抛。
- PathGuard 双根：上传目录内路径通过调用前校验、证据通过调用后过滤；
  根外路径与符号链接逃逸仍被拒绝（既有契约不变）。
- 入口：``mcp_server.main`` / ``scripts/start_api.py main`` /
  ``scripts/query.py --agentic`` 在没有 ``data/uploads/`` 的临时项目根下
  预建该目录，agentic 工具可构造 PathGuard，不报 ``does not exist``。
"""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from libs.agentic_search import PathGuard, PathGuardError
from libs.agentic_search.local_roots import ensure_local_roots

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts"

_MINIMAL_YAML = dedent(
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


class _Config:
    """测试用最小 AgenticSearchConfig 鸭子类型（避免构造全套 Settings）。"""

    def __init__(
        self,
        enabled: bool = True,
        allowed_roots: list[str] | None = None,
        denied_substrings: list[str] | None = None,
    ) -> None:
        self.enabled = enabled
        self.allowed_roots = allowed_roots if allowed_roots is not None else []
        self.denied_substrings = denied_substrings if denied_substrings is not None else [".env", ".git", "data/db"]


# ---------------------------------------------------------------------------
# ensure_local_roots（spec §4 第一组）
# ---------------------------------------------------------------------------


class TestEnsureLocalRoots:
    def test_disabled_creates_nothing(self, tmp_path: Path):
        base = tmp_path / "data"
        uploads = base / "uploads"
        created = ensure_local_roots(_Config(enabled=False, allowed_roots=[str(uploads)]), base=base)
        assert created == []
        assert not uploads.exists()

    def test_creates_missing_root_under_data_base(self, tmp_path: Path):
        base = tmp_path / "data"
        uploads = base / "uploads"
        created = ensure_local_roots(_Config(allowed_roots=[str(uploads)]), base=base)
        assert created == [str(uploads)]
        assert uploads.is_dir()

    def test_skips_root_outside_base(self, tmp_path: Path):
        base = tmp_path / "data"
        outside = tmp_path / "elsewhere" / "docs"
        created = ensure_local_roots(_Config(allowed_roots=[str(outside)]), base=base)
        assert created == []
        assert not outside.exists()

    def test_skips_denied_substring_root(self, tmp_path: Path):
        base = tmp_path / "data"
        denied_root = base / "db"
        created = ensure_local_roots(_Config(allowed_roots=[str(denied_root)]), base=base)
        assert created == []
        assert not denied_root.exists()

    def test_existing_root_unchanged(self, tmp_path: Path):
        base = tmp_path / "data"
        uploads = base / "uploads"
        uploads.mkdir(parents=True)
        marker = uploads / "keep.txt"
        marker.write_text("x", encoding="utf-8")
        created = ensure_local_roots(_Config(allowed_roots=[str(uploads)]), base=base)
        assert created == []
        assert marker.read_text(encoding="utf-8") == "x"

    def test_oserror_swallowed_with_warning(self, tmp_path: Path):
        base = tmp_path / "blocker"  # base 是文件 → 其下 mkdir 必失败
        base.write_text("not a dir", encoding="utf-8")
        target = base / "uploads"
        created = ensure_local_roots(_Config(allowed_roots=[str(target)]), base=base)
        assert created == []
        assert base.read_text(encoding="utf-8") == "not a dir"

    def test_default_base_is_project_root_data(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """缺省基准为 ``<项目根>/data``（ROOT_ANCHOR_SPEC RA2，原为 CWD/data）。"""
        monkeypatch.setenv("RAG_PROJECT_ROOT", str(tmp_path))
        uploads = tmp_path / "data" / "uploads"
        created = ensure_local_roots(_Config(allowed_roots=[str(uploads)]))
        assert created == [str(uploads.resolve())]
        assert uploads.is_dir()

    def test_default_base_ignores_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """未设 ``RAG_PROJECT_ROOT`` 时基准是仓库根：CWD 下的相对根不在基准内，不建、不留 ``data/``。"""
        monkeypatch.delenv("RAG_PROJECT_ROOT", raising=False)
        monkeypatch.chdir(tmp_path)
        created = ensure_local_roots(_Config(allowed_roots=["./data/uploads"]))
        assert created == []
        assert not (tmp_path / "data").exists()


# ---------------------------------------------------------------------------
# PathGuard 双根（spec §4 第二组）：契约不变，上传目录成为合法根
# ---------------------------------------------------------------------------


class TestPathGuardDualRoots:
    def test_uploads_root_validated_and_evidence_kept(self, tmp_path: Path):
        docs = tmp_path / "data" / "documents"
        uploads = tmp_path / "data" / "uploads"
        docs.mkdir(parents=True)
        uploads.mkdir(parents=True)
        uploaded = uploads / "report.md"
        uploaded.write_text("content", encoding="utf-8")

        guard = PathGuard([str(docs), str(uploads)])
        assert guard.validate_paths([str(uploaded)]) == [str(uploaded)]
        # 调用后过滤：上传目录内证据保留
        assert guard.filter_evidence_paths([str(uploaded)]) == [str(uploaded)]

    def test_outside_root_and_symlink_escape_still_rejected(self, tmp_path: Path):
        docs = tmp_path / "data" / "documents"
        uploads = tmp_path / "data" / "uploads"
        docs.mkdir(parents=True)
        uploads.mkdir(parents=True)
        outside = tmp_path / "outside.md"
        outside.write_text("secret", encoding="utf-8")
        escape = uploads / "escape.md"
        escape.symlink_to(outside)

        guard = PathGuard([str(docs), str(uploads)])
        with pytest.raises(PathGuardError, match="outside allowed roots"):
            guard.validate_paths([str(outside)])
        with pytest.raises(PathGuardError, match="outside allowed roots"):
            guard.validate_paths([str(escape)])  # 符号链接逃逸
        # 调用后过滤：越界证据静默剔除
        assert guard.filter_evidence_paths([str(outside)]) == []


# ---------------------------------------------------------------------------
# 入口三组（spec §4 第三组）：干净临时项目根下预建 data/uploads
# ---------------------------------------------------------------------------


def _write_repo_config(root: Path, allowed_roots: list[str]) -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    roots_yaml = "\n".join(f"    - {r}" for r in allowed_roots)
    config = config_dir / "settings.yaml"
    config.write_text(
        _MINIMAL_YAML + f"agentic_search:\n  enabled: true\n  allowed_roots:\n{roots_yaml}\n",
        encoding="utf-8",
    )
    return config


class TestEntryPointsEnsureLocalRoots:
    def test_mcp_server_main_creates_uploads_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from mcp_server import server as mcp_server

        monkeypatch.chdir(tmp_path)
        # RA1/RA2 锚定：默认配置路径与 allowed_roots 都按项目根解析，
        # 注入 RAG_PROJECT_ROOT 使 fixture 目录充当项目根。
        monkeypatch.setenv("RAG_PROJECT_ROOT", str(tmp_path))
        outside = tmp_path / "elsewhere"
        _write_repo_config(tmp_path, ["./data/uploads", str(outside / "docs")])

        rc = mcp_server.main(stdin=io.StringIO(""), stdout=io.StringIO())
        assert rc == 0
        assert (tmp_path / "data" / "uploads").is_dir()  # PathGuard 不再报 does not exist
        assert not (outside / "docs").exists()  # data/ 外不碰

        # 预建后 agentic 工具的 PathGuard 可正常构造（只含实际存在的根；
        # data/ 外的根按规格不被预建，仍由 PathGuard 按原契约报错）
        settings = load_settings(tmp_path / "config" / "settings.yaml")
        assert settings.agentic_search.enabled is True
        guard = PathGuard([str(tmp_path / "data" / "uploads")])
        assert str(tmp_path / "data" / "uploads") in guard.allowed_roots

    def test_start_api_main_creates_uploads_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        spec = importlib.util.spec_from_file_location("ar_start_api", _SCRIPTS / "start_api.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        monkeypatch.chdir(tmp_path)
        # RA1/RA2 锚定：显式 --config 指向 fixture 配置，RAG_PROJECT_ROOT 指向
        # fixture 项目根（原实现靠真实仓库配置的相对根按 CWD 解析，锚定后失效）。
        monkeypatch.setenv("RAG_PROJECT_ROOT", str(tmp_path))
        config = _write_repo_config(tmp_path, ["./data/uploads"])  # api.enabled 缺省 false → 拒绝启动退出码 2

        rc = module.main(["--config", str(config)])
        assert rc == 2
        assert (tmp_path / "data" / "uploads").is_dir()  # 启动被拒前已预建

    def test_query_cli_agentic_creates_uploads_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        spec = importlib.util.spec_from_file_location("ar_query_cli", _SCRIPTS / "query.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        monkeypatch.chdir(tmp_path)
        # RA1/RA2 锚定：fixture 项目根经 RAG_PROJECT_ROOT 注入。
        monkeypatch.setenv("RAG_PROJECT_ROOT", str(tmp_path))
        config = _write_repo_config(tmp_path, ["./data/uploads"])

        # build_agentic_retriever 打桩：证明 ensure 在后端构建**之前**执行
        def _boom(_settings):
            raise AssertionError("ensure_local_roots must run before backend build")

        monkeypatch.setattr(module, "build_agentic_retriever", _boom)
        rc = module.main(["--query", "x", "--agentic", "--config", str(config)], out=lambda *_: None)
        assert rc == 2  # EXIT_USAGE：后端构建失败
        assert (tmp_path / "data" / "uploads").is_dir()

    def test_data_paths_land_in_project_root_not_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """RA3 验收：入口加载的配置中数据路径都在项目根下，CWD 不新建 ``data/``。"""
        from mcp_server import server as mcp_server

        project_root = tmp_path / "proj"
        cwd_dir = tmp_path / "cwd"
        cwd_dir.mkdir()
        (project_root / "config").mkdir(parents=True)
        monkeypatch.setenv("RAG_PROJECT_ROOT", str(project_root))
        monkeypatch.chdir(cwd_dir)
        _write_repo_config(project_root, ["./data/uploads"])

        rc = mcp_server.main(stdin=io.StringIO(""), stdout=io.StringIO())
        assert rc == 0
        assert (project_root / "data" / "uploads").is_dir()
        # 数据路径全部落在项目根下（RA3），启动目录不新建 data/（RA2 预建基准 + RA3 锚定）
        assert not (cwd_dir / "data").exists()
