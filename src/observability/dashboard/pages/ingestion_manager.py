"""Ingestion 管理页面（阶段 G4）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import streamlit as st

from observability.dashboard.services.data_service import DataService
from observability.dashboard.services.ingestion_service import IngestionService

__all__ = ["render"]

RunIngestFn = Callable[..., Any]


def render(
    *,
    data_service: DataService | None = None,
    run_ingest: RunIngestFn | None = None,
    upload_dir: str | Path | None = None,
) -> None:
    data_service = data_service or DataService()
    st.header("Ingestion 管理")
    st.caption("上传文件触发摄取、观察进度，并删除已有文档。")

    documents = []
    try:
        documents = data_service.list_documents()
    except Exception as exc:
        st.warning(f"读取文档列表失败：{exc}")

    existing = sorted({item.collection for item in documents})
    collection_choice = st.selectbox("目标集合", existing + ["新建集合"])
    if collection_choice == "新建集合":
        collection = st.text_input("新集合名", value="default").strip() or "default"
    else:
        collection = collection_choice

    uploaded = st.file_uploader("选择要摄取的文件", accept_multiple_files=True)
    force = st.checkbox("强制重摄取（--force）", value=False)
    if st.button("开始摄取", type="primary"):
        if not uploaded:
            st.warning("请先选择至少一个文件。")
        else:
            _run_upload(
                uploaded,
                collection=collection,
                force=force,
                run_ingest=run_ingest,
                upload_dir=upload_dir,
            )

    st.divider()
    st.subheader("已摄入文档")
    if not documents:
        st.info("暂无文档。")
        return
    for document in documents:
        cols = st.columns([4, 1])
        cols[0].write(
            f"`{document.source_path}` · {document.collection} · "
            f"{document.chunk_count} chunks / {document.image_count} images"
        )
        if cols[1].button("删除", key=f"delete::{document.collection}::{document.source_path}"):
            result = data_service.delete_document(document.source_path, document.collection)
            st.success(
                f"已删除 {result.source_path}（chroma={result.chroma_deleted}, "
                f"bm25={result.bm25_deleted}, images={result.images_deleted}）"
            )
            st.rerun()


def _run_upload(
    uploaded: list[Any],
    *,
    collection: str,
    force: bool,
    run_ingest: RunIngestFn | None,
    upload_dir: str | Path | None,
) -> None:
    service = IngestionService(upload_dir=upload_dir, runner=run_ingest)
    saved = service.save_uploads(
        (getattr(item, "name", None), item.getvalue()) for item in uploaded
    )

    progress = st.progress(0.0, text="准备摄取…")
    status = st.empty()

    def on_progress(stage: str, current: int, total: int) -> None:
        ratio = 0.0 if total <= 0 else min(1.0, current / total)
        progress.progress(ratio, text=f"{stage} {current}/{total}")
        status.caption(f"当前阶段：{stage} ({current}/{total})")

    try:
        summary = service.ingest(saved, collection=collection, force=force, on_progress=on_progress)
    except Exception as exc:
        st.error(f"摄取失败：{exc}")
        return
    progress.progress(1.0, text="完成")
    st.success(f"摄取完成：{summary}")


if __name__ == "__main__":
    render()
