"""Web API 启动脚本（WEBUI_CHAT_SPEC WC2.1）。

用法::

    python scripts/start_api.py
    python scripts/start_api.py --host 127.0.0.1 --port 8600
    RAG_API_TOKEN=... python scripts/start_api.py --host 0.0.0.0

默认监听 ``api.host:api.port``（127.0.0.1:8600）。未配置 ``RAG_API_TOKEN`` 时
只允许 loopback 绑定，否则启动即失败（见 ``api.security.validate_api_binding``）。
``api.enabled`` 为 false 时拒绝启动，除非显式传 ``--force``。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
DEFAULT_CONFIG_PATH = _ROOT / "config" / "settings.yaml"

__all__ = ["DEFAULT_CONFIG_PATH", "build_parser", "main", "resolve_api_settings"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the Modular RAG FastAPI server.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="配置文件路径")
    parser.add_argument("--host", default=None, help="覆盖 api.host")
    parser.add_argument("--port", type=int, default=None, help="覆盖 api.port")
    parser.add_argument("--force", action="store_true", help="api.enabled=false 时仍然启动")
    parser.add_argument("--serve-ui", action="store_true", help="覆盖 api.serve_ui=true（托管 api.static_dir）")
    return parser


def resolve_api_settings(settings: object, args: argparse.Namespace) -> object:
    """命令行参数覆盖配置，返回新的 ``Settings``（不修改入参）。"""
    api = settings.api  # type: ignore[attr-defined]
    overrides: dict[str, object] = {}
    if args.host is not None:
        overrides["host"] = args.host
    if args.port is not None:
        overrides["port"] = int(args.port)
    if getattr(args, "serve_ui", False):
        overrides["serve_ui"] = True
    return replace(settings, api=replace(api, **overrides)) if overrides else settings  # type: ignore[type-var]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sys.path.insert(0, str(_SRC))

    import uvicorn

    from api.app import create_app
    from core.settings import ConfigError, load_settings

    try:
        settings = resolve_api_settings(load_settings(args.config), args)
        api = settings.api  # type: ignore[attr-defined]
        if not api.enabled and not args.force:
            print("api.enabled is false; set api.enabled: true or pass --force", file=sys.stderr)
            return 2
        app = create_app(settings)  # type: ignore[arg-type]
    except ConfigError as exc:
        print(f"[api] configuration error: {exc}", file=sys.stderr)
        return 2

    uvicorn.run(app, host=api.host, port=api.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
