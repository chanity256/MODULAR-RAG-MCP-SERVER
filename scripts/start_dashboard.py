"""Dashboard 启动脚本（阶段 G1）。

用法::

    python scripts/start_dashboard.py
    python scripts/start_dashboard.py --port 8502
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
_APP = _SRC / "observability" / "dashboard" / "app.py"
DEFAULT_CONFIG_PATH = _ROOT / "config" / "settings.yaml"

__all__ = ["DEFAULT_CONFIG_PATH", "build_parser", "resolve_port", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the Modular RAG Streamlit dashboard.")
    parser.add_argument("--port", type=int, default=None, help="监听端口（默认取 dashboard.port）")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="配置文件路径")
    parser.add_argument("--headless", action="store_true", help="不自动打开浏览器")
    return parser


def resolve_port(args: argparse.Namespace) -> int:
    if args.port is not None:
        return int(args.port)
    sys.path.insert(0, str(_SRC))
    from core.settings import load_settings

    return int(load_settings(args.config).dashboard.port)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    port = resolve_port(args)
    env = os.environ.copy()
    pythonpath = str(_SRC)
    if env.get("PYTHONPATH"):
        pythonpath = pythonpath + os.pathsep + env["PYTHONPATH"]
    env["PYTHONPATH"] = pythonpath
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(_APP),
        "--server.port",
        str(port),
        "--server.headless",
        "true" if args.headless else "false",
    ]
    return subprocess.call(command, env=env, cwd=str(_ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
