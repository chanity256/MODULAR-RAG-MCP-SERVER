"""评估面板占位页（阶段 G；H4 再启用）。"""

from __future__ import annotations

import streamlit as st

__all__ = ["render"]


def render() -> None:
    st.header("评估面板")
    st.info("评估模块尚未启用。该页面将在阶段 H（RagasEvaluator / CompositeEvaluator / EvalRunner）落地。")


if __name__ == "__main__":
    render()
