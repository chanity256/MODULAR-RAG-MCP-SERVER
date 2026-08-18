"""Dashboard 入口：Streamlit 多页面导航（阶段 G1）。

启动：

    streamlit run src/observability/dashboard/app.py
    python scripts/start_dashboard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parents[2]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import streamlit as st

from observability.dashboard.pages import (
    data_browser,
    evaluation_panel,
    ingestion_manager,
    ingestion_traces,
    overview,
    query_traces,
)

st.set_page_config(page_title="Modular RAG Dashboard", layout="wide")

pages = {
    "总览": [
        st.Page(overview.render, title="系统总览", icon="📊", default=True, url_path="overview"),
    ],
    "数据与摄取": [
        st.Page(data_browser.render, title="数据浏览器", icon="📁", url_path="data-browser"),
        st.Page(ingestion_manager.render, title="Ingestion 管理", icon="⬆️", url_path="ingestion"),
    ],
    "追踪": [
        st.Page(ingestion_traces.render, title="Ingestion 追踪", icon="📈", url_path="ingestion-traces"),
        st.Page(query_traces.render, title="Query 追踪", icon="🔍", url_path="query-traces"),
    ],
    "评估": [
        st.Page(evaluation_panel.render, title="评估面板", icon="🧪", url_path="evaluation"),
    ],
}

st.navigation(pages, position="top").run()
