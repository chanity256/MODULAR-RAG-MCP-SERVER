"""Dashboard 启动脚本（阶段 G1；S4+ 增加 sidecar 托管编排）。

**已弃用（WC10.2）**：Streamlit Dashboard 进入 legacy 维护状态，主界面改为
Web UI（``python scripts/start_api.py --serve-ui``）。本脚本保持可用，启动时
向 stderr 打印弃用提示；删除条件见 docs/reference/USAGE_GUIDE.md。

用法::

    python scripts/start_dashboard.py
    python scripts/start_dashboard.py --port 8502
    python scripts/start_dashboard.py --no-sidecar   # 不托管 sidecar

Sidecar 托管（进程隔离不变，仅启动编排）：当配置 ``agentic_search.enabled``
为 true 时，启动 Dashboard 前先探活 sidecar（``GET /api/v1/search/status``）；
未启动则自动拉起 ``scripts/sirchmunk/start_sidecar.sh``（独立 venv、独立
进程）并等待健康。由本脚本拉起的 sidecar 会在 Dashboard 退出时一并停止；
已在外部运行的 sidecar 只复用、不接管。主进程永不 import sirchmunk
（SIRCHMUNK_INTEGRATION_SPEC 3.1 的进程边界红线）。
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
_APP = _SRC / "observability" / "dashboard" / "app.py"
SIDECAR_SCRIPT = _ROOT / "scripts" / "sirchmunk" / "start_sidecar.sh"
SIDECAR_STARTUP_TIMEOUT_S = 60.0
DEFAULT_CONFIG_PATH = _ROOT / "config" / "settings.yaml"

DEPRECATION_NOTICE = (
    "[dashboard] DEPRECATED: Streamlit Dashboard 已弃用（legacy，仅维护不新增功能），请改用 Web UI：\n"
    "  cd frontend && npm ci && npm run build      # 首次或前端有改动时\n"
    "  python scripts/start_api.py --serve-ui      # http://127.0.0.1:8600\n"
    "  （api.enabled 默认为 false：在 config/settings.yaml 设 api.enabled: true，或追加 --force；\n"
    "   sidecar 需另行启动：bash scripts/sirchmunk/start_sidecar.sh）\n"
    "  详见 docs/reference/USAGE_GUIDE.md「Web UI」一节。"
)

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEPRECATION_NOTICE",
    "SIDECAR_SCRIPT",
    "build_parser",
    "emit_deprecation_notice",
    "ensure_sidecar",
    "resolve_port",
    "sidecar_healthy",
    "main",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="[DEPRECATED] Start the legacy Streamlit dashboard; use scripts/start_api.py --serve-ui."
    )
    parser.add_argument("--port", type=int, default=None, help="监听端口（默认取 dashboard.port）")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="配置文件路径")
    parser.add_argument("--headless", action="store_true", help="不自动打开浏览器")
    parser.add_argument(
        "--no-sidecar",
        action="store_true",
        help="不托管 sirchmunk sidecar（即使 agentic_search.enabled=true）",
    )
    return parser


def emit_deprecation_notice(stream: object = None) -> None:
    """弃用提示只写 stderr：stdout 保留给既有的 sidecar 编排输出，且不污染管道消费者。"""
    print(DEPRECATION_NOTICE, file=stream or sys.stderr, flush=True)


def resolve_port(args: argparse.Namespace) -> int:
    if args.port is not None:
        return int(args.port)
    sys.path.insert(0, str(_SRC))
    from core.settings import load_settings

    return int(load_settings(args.config).dashboard.port)


# --------------------------------------------------------------------- #
# Sidecar 托管编排（S4+）：探活 → 拉起 → 等健康 → 随 Dashboard 退出回收
# --------------------------------------------------------------------- #
def sidecar_healthy(base_url: str, timeout: float = 2.0) -> bool:
    """探活 sidecar：任何 HTTP 响应（含 401 鉴权拒绝）都算进程存活。"""
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/v1/search/status", timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True  # 有响应即存活（无 token 时 401/500 也算）
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def ensure_sidecar(settings: object, *, root: Path = _ROOT) -> subprocess.Popen | None:
    """按配置确保 sidecar 就绪。

    Returns:
        本脚本拉起的 sidecar 进程（调用方负责在退出时终止）；
        复用外部实例 / agentic 关闭 / 启动失败时返回 ``None``。
    """
    agentic = getattr(settings, "agentic_search")
    if not getattr(agentic, "enabled", False):
        print("[dashboard] agentic_search.enabled=false，跳过 sidecar")
        return None
    base_url = getattr(agentic, "base_url")
    if sidecar_healthy(base_url):
        print(f"[dashboard] sidecar 已在运行（{base_url}），直接复用")
        return None
    if not SIDECAR_SCRIPT.exists():
        print(f"[dashboard] 警告：找不到 {SIDECAR_SCRIPT}，agentic 检索不可用，仅启动 Dashboard")
        return None

    print(f"[dashboard] 启动 sirchmunk sidecar（{base_url}）……")
    proc = subprocess.Popen(["bash", str(SIDECAR_SCRIPT)], cwd=str(root))
    deadline = time.monotonic() + SIDECAR_STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if sidecar_healthy(base_url):
            print(f"[dashboard] sidecar 就绪（pid={proc.pid}）")
            return proc
        if proc.poll() is not None:
            print(f"[dashboard] 警告：sidecar 启动脚本退出（exit={proc.returncode}），"
                  "检查 .env-sirchmunk 与 venv；agentic 检索不可用，仅启动 Dashboard")
            return None
        time.sleep(1)
    proc.terminate()
    print("[dashboard] 警告：sidecar 启动超时，已终止；仅启动 Dashboard")
    return None


def stop_sidecar(proc: subprocess.Popen | None) -> None:
    """回收本脚本拉起的 sidecar（先 TERM，10s 不退再 KILL）。"""
    if proc is None or proc.poll() is not None:
        return
    print("[dashboard] 停止 sidecar……")
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    emit_deprecation_notice()
    port = resolve_port(args)

    # SIGTERM（kill）默认直接终止、不走 finally，会留下孤儿 sidecar /
    # streamlit；转成 SystemExit 让下方 try/finally 统一回收。
    def _raise_exit(signum: int, frame: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _raise_exit)

    sidecar_proc: subprocess.Popen | None = None
    if not args.no_sidecar:
        sys.path.insert(0, str(_SRC))
        from core.settings import load_settings

        settings = load_settings(args.config)
        sidecar_proc = ensure_sidecar(settings)

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
    streamlit = subprocess.Popen(command, env=env, cwd=str(_ROOT))
    try:
        return streamlit.wait()
    finally:
        if streamlit.poll() is None:  # 启动器被 kill 时顺带回收子进程
            streamlit.terminate()
            try:
                streamlit.wait(timeout=10)
            except subprocess.TimeoutExpired:
                streamlit.kill()
        stop_sidecar(sidecar_proc)


if __name__ == "__main__":
    raise SystemExit(main())
