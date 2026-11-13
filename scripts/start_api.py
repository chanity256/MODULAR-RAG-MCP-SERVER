"""Web API 启动脚本（WEBUI_CHAT_SPEC WC2.1；WS0 增加 ``--manage-sidecar``）。

用法::

    python scripts/start_api.py
    python scripts/start_api.py --host 127.0.0.1 --port 8600
    python scripts/start_api.py --serve-ui --manage-sidecar
    RAG_API_TOKEN=... python scripts/start_api.py --host 0.0.0.0

默认监听 ``api.host:api.port``（127.0.0.1:8600）。未配置 ``RAG_API_TOKEN`` 时
只允许 loopback 绑定，否则启动即失败（见 ``api.security.validate_api_binding``）。
``api.enabled`` 为 false 时拒绝启动，除非显式传 ``--force``。

``--manage-sidecar``（WS0，默认不传不托管）：``agentic_search.enabled=true`` 时
先探活 sidecar、未运行则拉起 ``scripts/sirchmunk/start_sidecar.sh`` 并等健康，
API 退出时回收由本脚本拉起的实例（已在外部运行的只复用、不接管）。
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
DEFAULT_CONFIG_PATH = _ROOT / "config" / "settings.yaml"

# sidecar 编排在同目录的共享模块（WS0）；importlib 按路径加载本文件的测试
# 不会带 scripts/ 到 sys.path，这里自行补上。
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

__all__ = ["DEFAULT_CONFIG_PATH", "build_parser", "main", "resolve_api_settings"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the Modular RAG FastAPI server.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="配置文件路径")
    parser.add_argument("--host", default=None, help="覆盖 api.host")
    parser.add_argument("--port", type=int, default=None, help="覆盖 api.port")
    parser.add_argument("--force", action="store_true", help="api.enabled=false 时仍然启动")
    parser.add_argument("--serve-ui", action="store_true", help="覆盖 api.serve_ui=true（托管 api.static_dir）")
    parser.add_argument(
        "--manage-sidecar",
        action="store_true",
        help="托管 sirchmunk sidecar（agentic_search.enabled=true 时拉起并随退出回收）",
    )
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


def _manage_sidecar(settings: object) -> subprocess.Popen | None:
    """WS0：按配置托管 sidecar，返回拉起的进程（复用外部实例/未启用/失败为 ``None``）。"""
    from sidecar_manager import ensure_sidecar

    agentic = getattr(settings, "agentic_search")
    if not getattr(agentic, "enabled", False):
        print("[api] --manage-sidecar：agentic_search.enabled=false，跳过 sidecar 托管", file=sys.stderr)
        return None
    return ensure_sidecar(settings)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sys.path.insert(0, str(_SRC))

    import uvicorn

    from api.app import create_app
    from core.settings import ConfigError, load_settings
    from observability.logger import setup_app_logging

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

    setup_app_logging(settings, "api")

    sidecar_proc: subprocess.Popen | None = None
    if args.manage_sidecar:
        sidecar_proc = _manage_sidecar(settings)

        # SIGTERM（kill）默认直接终止、不走 finally，会留下孤儿 sidecar；转成
        # SystemExit 让下方 try/finally 统一回收（uvicorn 启动后由其自身的
        # 信号处理优雅关闭、正常返回，finally 照样执行）。
        def _raise_exit(signum: int, frame: object) -> None:
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, _raise_exit)

    try:
        # log_config=None：uvicorn 默认 dictConfig 会给 "uvicorn" logger 设 propagate=False，
        # 日志到不了 root 的文件 handler；不配置时由 root（stderr + 按日文件）统一输出。
        uvicorn.run(app, host=api.host, port=api.port, log_level="info", log_config=None)
        return 0
    finally:
        if args.manage_sidecar:
            from sidecar_manager import stop_sidecar

            stop_sidecar(sidecar_proc)


if __name__ == "__main__":
    raise SystemExit(main())
