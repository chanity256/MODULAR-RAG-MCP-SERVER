"""scripts/start_dashboard.py 参数解析（阶段 G1）。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from textwrap import dedent

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "start_dashboard.py"
_MODULE_NAME = "scripts_start_dashboard"


def _load():
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


cli = _load()


def test_parse_headless_and_explicit_port():
    args = cli.build_parser().parse_args(["--port", "8502", "--headless"])
    assert args.port == 8502
    assert args.headless is True
    assert cli.resolve_port(args) == 8502


def test_resolve_port_from_settings(tmp_path):
    config = tmp_path / "settings.yaml"
    config.write_text(
        dedent(
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
            dashboard:
              port: 8601
            """
        ),
        encoding="utf-8",
    )
    args = cli.build_parser().parse_args(["--config", str(config)])
    assert cli.resolve_port(args) == 8601
