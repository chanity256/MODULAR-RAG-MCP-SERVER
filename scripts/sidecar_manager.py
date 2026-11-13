"""Sidecar 托管编排（WEBUI_SIDECAR_SPEC WS0）：探活 → 拉起 → 等健康 → 随主进程退出回收。

Web UI 唯一托管入口（``start_api.py --manage-sidecar``）。只做 subprocess 拉起 +
urllib 探活，主 venv 永不 import sirchmunk（SIRCHMUNK_INTEGRATION_SPEC 3.1 进程隔离红线不变）。
"""

from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
SIDECAR_SCRIPT = _ROOT / "scripts" / "sirchmunk" / "start_sidecar.sh"
SIDECAR_STARTUP_TIMEOUT_S = 60.0

__all__ = [
    "SIDECAR_SCRIPT",
    "SIDECAR_STARTUP_TIMEOUT_S",
    "ensure_sidecar",
    "sidecar_healthy",
    "stop_sidecar",
]


def sidecar_healthy(base_url: str, timeout: float = 2.0) -> bool:
    """探活 sidecar：任何 HTTP 响应（含 401 鉴权拒绝）都算进程存活。"""
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/v1/search/status", timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True  # 有响应即存活（无 token 时 401/500 也算）
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def ensure_sidecar(
    settings: object,
    *,
    root: Path = _ROOT,
    probe: Callable[[str], bool] | None = None,
    startup_timeout_s: float | None = None,
) -> subprocess.Popen | None:
    """按配置确保 sidecar 就绪。

    Args:
        settings: 已加载的 ``Settings``。
        root: 仓库根（start_sidecar.sh 的工作目录）。
        probe: 探活函数，默认 :func:`sidecar_healthy`；调用方可注入替代实现。
        startup_timeout_s: 健康等待上限，默认 :data:`SIDECAR_STARTUP_TIMEOUT_S`。

    Returns:
        本调用拉起的 sidecar 进程（调用方负责在退出时终止）；
        复用外部实例 / agentic 关闭 / 启动失败时返回 ``None``。
    """
    _probe: Callable[[str], bool] = sidecar_healthy if probe is None else probe
    _timeout_s = SIDECAR_STARTUP_TIMEOUT_S if startup_timeout_s is None else startup_timeout_s
    agentic = getattr(settings, "agentic_search")
    if not getattr(agentic, "enabled", False):
        print("[sidecar] agentic_search.enabled=false，跳过 sidecar")
        return None
    base_url = getattr(agentic, "base_url")
    if _probe(base_url):
        print(f"[sidecar] sidecar 已在运行（{base_url}），直接复用")
        return None
    if not SIDECAR_SCRIPT.exists():
        print(f"[sidecar] 警告：找不到 {SIDECAR_SCRIPT}，agentic 检索不可用，仅启动主服务")
        return None

    print(f"[sidecar] 启动 sirchmunk sidecar（{base_url}）……")
    proc = subprocess.Popen(["bash", str(SIDECAR_SCRIPT)], cwd=str(root))
    deadline = time.monotonic() + _timeout_s
    while time.monotonic() < deadline:
        if _probe(base_url):
            print(f"[sidecar] sidecar 就绪（pid={proc.pid}）")
            return proc
        if proc.poll() is not None:
            print(f"[sidecar] 警告：sidecar 启动脚本退出（exit={proc.returncode}），"
                  "检查 .env-sirchmunk 与 venv；agentic 检索不可用，仅启动主服务")
            return None
        time.sleep(1)
    proc.terminate()
    print("[sidecar] 警告：sidecar 启动超时，已终止；仅启动主服务")
    return None


def stop_sidecar(proc: subprocess.Popen | None) -> None:
    """回收本入口拉起的 sidecar（先 TERM，10s 不退再 KILL）；``None`` 为无操作。"""
    if proc is None or proc.poll() is not None:
        return
    print("[sidecar] 停止 sidecar……")
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
