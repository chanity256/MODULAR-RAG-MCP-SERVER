"""数据浏览器页面（阶段 G3）。"""

from __future__ import annotations

from typing import Any

import streamlit as st

from observability.dashboard.services.data_service import DataService

__all__ = ["render"]


def render(*, data_service: DataService | None = None) -> None:
    data_service = data_service or DataService()
    st.header("数据浏览器")
    st.caption("浏览已摄入文档、Chunk 详情与关联图片。")

    try:
        documents = data_service.list_documents()
    except Exception as exc:
        st.error(f"读取文档列表失败：{exc}")
        return

    collections = sorted({item.collection for item in documents})
    selected_collection = st.selectbox("集合筛选", ["全部"] + collections)
    keyword = st.text_input("关键词搜索（source_path / title）", value="")

    filtered = documents
    if selected_collection != "全部":
        filtered = [item for item in filtered if item.collection == selected_collection]
    needle = keyword.strip().lower()
    if needle:
        filtered = [
            item
            for item in filtered
            if needle in item.source_path.lower() or needle in str(item.title or "").lower()
        ]

    if not filtered:
        st.info("没有匹配的文档。")
        return

    st.write(f"共 {len(filtered)} 篇文档")
    for document in filtered:
        with st.expander(
            f"{document.title or document.source_path} · {document.collection} · {document.chunk_count} chunks",
            expanded=False,
        ):
            st.write(f"- **Source**: `{document.source_path}`")
            st.write(f"- **Collection**: `{document.collection}`")
            st.write(f"- **Chunks**: {document.chunk_count}")
            st.write(f"- **Images**: {document.image_count}")
            if document.ingested_at:
                st.write(f"- **Ingested at**: {document.ingested_at}")
            detail = data_service.get_document_detail(document.source_path, document.collection)
            if detail is None:
                st.warning("无法读取文档详情。")
                continue
            for index, chunk in enumerate(detail.chunks):
                metadata = chunk.get("metadata") or {}
                st.markdown(f"**Chunk {index + 1}** `{chunk.get('id')}`")
                st.text((chunk.get("text") or "")[:800] or "(empty)")
                st.json(
                    {
                        key: metadata.get(key)
                        for key in ("title", "summary", "tags", "page", "image_refs", "collection")
                        if metadata.get(key) is not None
                    }
                )
            if detail.images:
                st.markdown("**关联图片**")
                for image in detail.images:
                    payload = data_service.image_bytes(image["image_id"])
                    if payload is None:
                        st.caption(f"{image['image_id']}（文件缺失）")
                        continue
                    data, _mime = payload
                    st.image(data, caption=image["image_id"], width=240)


if __name__ == "__main__":
    render()
