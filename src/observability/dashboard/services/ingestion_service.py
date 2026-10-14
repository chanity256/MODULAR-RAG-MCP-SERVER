"""IngestionService：上传保存 + 摄取编排（框架无关，WC1.2）。

Streamlit 页面与未来 FastAPI 路由共用同一套摄取编排：

- :meth:`IngestionService.save_uploads` —— 把 ``(filename, bytes)`` 安全写入
  上传目录（仅保留 basename，目录不存在时自动创建）；
- :meth:`IngestionService.ingest` —— 逐文件调用 ``IngestionPipeline.run``，
  聚合文件数 / chunk 数 / 图片数 / 失败数；单个文件失败不阻断其余文件；
- 进度回调 ``on_progress(stage, current, total)`` 原样透传给 Pipeline。

本模块不依赖 Streamlit；Pipeline 与 runner 均可注入，默认 Pipeline 在首次
摄取时才按 ``load_settings()`` 构建（单测零 Provider、零持久化目录）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

__all__ = [
    "DEFAULT_UPLOAD_DIR",
    "IngestionService",
    "IngestionSummary",
    "ProgressFn",
    "RunnerFn",
    "sanitize_filename",
]

#: 进度回调签名：``on_progress(stage, current, total)``。
ProgressFn = Callable[[str, int, int], None]

#: 整批摄取 runner：``runner(files, *, collection, force, on_progress) -> Any``。
RunnerFn = Callable[..., Any]

DEFAULT_UPLOAD_DIR = Path("data/uploads")

_FALLBACK_FILENAME = "upload.bin"


def sanitize_filename(name: str | None) -> str:
    """只保留文件名的 basename（兼容 ``/`` 与 ``\\`` 分隔符，拒绝 ``.``/``..``）。"""
    if not name:
        return _FALLBACK_FILENAME
    base = Path(str(name).replace("\\", "/")).name
    if base in ("", ".", ".."):
        return _FALLBACK_FILENAME
    return base


@dataclass(frozen=True)
class IngestionSummary:
    """一批文件的摄取聚合结果。

    ``str(summary)`` 与旧页面文案保持一致：
    ``"N files, chunks=X, images=Y, failed=Z"``。
    """

    files: int = 0
    chunks: int = 0
    images: int = 0
    failed: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.files} files, chunks={self.chunks}, images={self.images}, failed={self.failed}"


class IngestionService:
    """上传保存与摄取编排。

    Args:
        upload_dir: 上传保存目录（缺省 ``data/uploads``）。
        pipeline: 具备 ``run(path, *, collection, force, on_progress)`` 的对象
            （缺省首次摄取时构建 ``IngestionPipeline(load_settings())``）。
        runner: 整批摄取覆盖函数 ``runner(files, *, collection, force,
            on_progress)``；提供时 :meth:`ingest` 直接返回其结果，不再使用
            ``pipeline``（供页面/测试注入）。
    """

    def __init__(
        self,
        *,
        upload_dir: str | Path | None = None,
        pipeline: Any | None = None,
        runner: RunnerFn | None = None,
    ) -> None:
        self._upload_dir = Path(upload_dir) if upload_dir is not None else DEFAULT_UPLOAD_DIR
        self._pipeline = pipeline
        self._runner = runner

    @property
    def upload_dir(self) -> Path:
        return self._upload_dir

    def save_uploads(self, files: Iterable[tuple[str | None, bytes]]) -> list[Path]:
        """保存上传内容，返回落盘路径（与输入同序）。同名文件覆盖。"""
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        saved: list[Path] = []
        for name, data in files:
            dest = self._upload_dir / sanitize_filename(name)
            dest.write_bytes(data)
            saved.append(dest)
        return saved

    def ingest(
        self,
        files: Iterable[str | Path],
        *,
        collection: str,
        force: bool = False,
        on_progress: ProgressFn | None = None,
    ) -> Any:
        """逐文件摄取并聚合；单文件失败只计入 ``failed``/``errors``。"""
        paths = [Path(item) for item in files]
        if self._runner is not None:
            return self._runner(paths, collection=collection, force=force, on_progress=on_progress)

        pipeline = self._get_pipeline()
        chunks = images = 0
        errors: list[tuple[str, str]] = []
        for path in paths:
            try:
                result = pipeline.run(
                    str(path), collection=collection, force=force, on_progress=on_progress
                )
                chunks += result.chunks
                images += result.images
            except Exception as exc:
                errors.append((str(path), f"{type(exc).__name__}: {exc}"))
        return IngestionSummary(
            files=len(paths),
            chunks=chunks,
            images=images,
            failed=len(errors),
            errors=errors,
        )

    def save_and_ingest(
        self,
        uploads: Iterable[tuple[str | None, bytes]],
        *,
        collection: str,
        force: bool = False,
        on_progress: ProgressFn | None = None,
    ) -> Any:
        """:meth:`save_uploads` + :meth:`ingest` 的组合入口（供 API 路由使用）。"""
        saved = self.save_uploads(uploads)
        return self.ingest(saved, collection=collection, force=force, on_progress=on_progress)

    def _get_pipeline(self) -> Any:
        if self._pipeline is None:
            from core.settings import load_settings
            from ingestion.pipeline import IngestionPipeline

            self._pipeline = IngestionPipeline(load_settings())
        return self._pipeline
