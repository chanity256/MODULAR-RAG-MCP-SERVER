"""Modular RAG MCP Server — 启动入口。

阶段 A（骨架）：仅做最小可运行入口。
- 尝试加载 ``config/settings.yaml``，失败时打印可读错误并退出。
- 真正的 MCP Server Stdio 入口在阶段 E 实现（``src/mcp_server/server.py``）。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 让 ``src`` 下的包在未安装时也可被直接 import（方便开发期 ``python main.py``）。
_SRC_DIR = Path(__file__).resolve().parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# 占位 logger：stderr 输出，避免污染未来 MCP 的 stdout 通道。
try:
    from observability.logger import get_logger  # type: ignore
    logger = get_logger(__name__)
except Exception:  # pragma: no cover - logger 尚未实现时的兜底
    import logging
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    logger = logging.getLogger(__name__)


def main() -> int:
    """骨架入口：加载配置并打印就绪信息。

    阶段 A3 会把 ``load_settings`` 的 fail-fast 校验接进来；当前只做最小占位。
    """
    config_path = Path(__file__).resolve().parent / "config" / "settings.yaml"
    logger.info("Modular RAG MCP Server skeleton starting (阶段 A)...")
    logger.info("Config path: %s (exists=%s)", config_path, config_path.exists())

    if not config_path.exists():
        logger.error("配置文件缺失: %s", config_path)
        return 1

    # 占位：A3 将替换为 load_settings(config_path) 并做 fail-fast 校验。
    logger.info("Skeleton ready. MCP Server 入口将在阶段 E 实现。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
