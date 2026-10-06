"""在线检索页面（S4.2）：Normal / Fast / Deep 三模式在线查询。

交互：先选模式（三按钮，默认 Normal），再点「检索」。门控严格遵守
settings（Fast 需 ``agentic_search.enabled``；Deep 另需 ``allow_deep``），
不可用按钮置灰并以 help 悬停提示开启方法。检索写入 trace，可在
「Query 追踪」页查看各阶段耗时（S3.2 已支持渲染 agentic 阶段）。
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from observability.dashboard.services.search_service import SearchService, SearchOutcome

__all__ = ["render"]

_MODES: tuple[tuple[str, str], ...] = (("normal", "Normal"), ("fast", "Fast"), ("deep", "Deep"))

_MODE_BTN_KEY = "qp_mode"
_OUTCOME_KEY = "qp_outcome"
_SERVICE_KEY = "_search_service"


def render(*, settings: Any | None = None, search_service: SearchService | None = None) -> None:
    """渲染「在线检索」页。

    Args:
        settings: 传给缺省 ``SearchService`` 的配置（注入 service 时忽略）。
        search_service: 注入检索服务（离线测试用 fake 实现）。
    """
    service = search_service
    if service is None:
        # 会话内缓存服务实例 → 工具（HybridSearch 等）只在首次构建。
        if _SERVICE_KEY not in st.session_state:
            st.session_state[_SERVICE_KEY] = SearchService(settings=settings)
        service = st.session_state[_SERVICE_KEY]

    st.header("在线检索")
    st.caption("三模式在线查询；每次检索都会写入 trace，可到「Query 追踪」页查看各阶段耗时。")

    availability = service.availability()
    mode = _render_mode_buttons(availability)
    _render_query_form(service, mode, availability)
    _render_outcome(st.session_state.get(_OUTCOME_KEY))


# --------------------------------------------------------------------- #
# 模式选择：三按钮，默认 Normal，选中 primary 高亮，不可用置灰
# --------------------------------------------------------------------- #
def _set_mode(mode: str) -> None:
    # on_click 回调在脚本重跑前执行，保证单击当轮即以新模式渲染按钮。
    st.session_state[_MODE_BTN_KEY] = mode


def _render_mode_buttons(availability: dict) -> str:
    if st.session_state.get(_MODE_BTN_KEY) not in dict(_MODES):
        _set_mode("normal")
    cols = st.columns(len(_MODES))
    for col, (mode, label) in zip(cols, _MODES):
        with col:
            state = availability[mode]
            if st.button(
                label,
                type="primary" if st.session_state[_MODE_BTN_KEY] == mode else "secondary",
                disabled=not state.available,
                help=state.reason,
                use_container_width=True,
                key=f"qp_btn_{mode}",
                on_click=_set_mode,
                args=(mode,),
            ):
                pass  # 状态经 on_click 回调更新，此处无需处理
    return st.session_state[_MODE_BTN_KEY]


# --------------------------------------------------------------------- #
# 查询表单与检索按钮
# --------------------------------------------------------------------- #
def _render_query_form(service: SearchService, mode: str, availability: dict) -> None:
    query = st.text_input(
        "查询文本",
        value="",
        key="qp_query",
        help=availability[mode].reason if not availability[mode].available else None,
    )
    top_k = int(st.number_input("Top-K", min_value=1, value=5, step=1, key="qp_top_k"))
    collection: str | None = None
    if mode == "normal":
        collection = st.text_input("Collection（可选，留空用默认）", value="", key="qp_collection").strip() or None

    if not st.button("检索", type="primary", key="qp_run"):
        return
    if not query.strip():
        st.warning("请先输入查询文本。")
        return

    spinner = "Deep 模式可能需要数十秒，检索中……" if mode == "deep" else "检索中……"
    with st.spinner(spinner):
        outcome = service.run(mode, query.strip(), top_k=top_k, collection=collection)
    st.session_state[_OUTCOME_KEY] = outcome


# --------------------------------------------------------------------- #
# 结果区：共有（Markdown 答案 + 引用表）；Fast/Deep 额外展示 agentic 元数据
# --------------------------------------------------------------------- #
def _render_outcome(outcome: SearchOutcome | None) -> None:
    if outcome is None:
        return

    st.subheader("结果")
    if outcome.is_error:
        st.error(f"检索失败：{outcome.error or '未知错误'}")
        return

    if outcome.markdown:
        st.markdown(outcome.markdown)
    else:
        st.info("无结果。若使用 Fast/Deep 模式，请确认 sidecar 已启动且检索路径下有语料。")

    if outcome.agentic_meta:
        _render_agentic_meta(outcome.agentic_meta)

    if outcome.citations:
        st.subheader("引用")
        st.table(
            pd.DataFrame(
                [
                    {
                        "编号": f"[{c.get('id')}]",
                        "来源": c.get("source"),
                        "页码": c.get("page"),
                        "分数": c.get("score"),
                    }
                    for c in outcome.citations
                ]
            )
        )
    st.caption(f"耗时 {outcome.elapsed_ms:.0f} ms · 已写入 trace（见「Query 追踪」页）")


def _render_agentic_meta(meta: dict[str, Any]) -> None:
    st.subheader("Agentic 检索详情")
    evidence_files = meta.get("evidence_files") or []
    metrics = st.columns(4)
    with metrics[0]:
        st.metric("模式", meta.get("mode") or "—")
    with metrics[1]:
        st.metric("Token", meta.get("total_llm_tokens") if meta.get("total_llm_tokens") is not None else "—")
    with metrics[2]:
        st.metric("LLM 调用", meta.get("llm_call_count") if meta.get("llm_call_count") is not None else "—")
    with metrics[3]:
        elapsed = meta.get("elapsed_ms")
        st.metric("耗时", f"{elapsed:.0f} ms" if isinstance(elapsed, (int, float)) else "—")

    agentic_answer = meta.get("agentic_answer")
    if agentic_answer:
        with st.expander("Agentic 答案", expanded=False):
            st.markdown(str(agentic_answer))
    if evidence_files:
        with st.expander(f"证据文件（{len(evidence_files)}）", expanded=False):
            st.write("\n".join(f"- `{path}`" for path in evidence_files))
