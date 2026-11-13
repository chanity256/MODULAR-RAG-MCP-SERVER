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
    """入口：加载配置（fail-fast）并打印就绪信息。

    阶段 A3：调用 ``load_settings()``，缺关键字段直接 fail-fast 退出。
    真正的 MCP Server Stdio 入口在阶段 E 实现（``src/mcp_server/server.py``）。
    """
    from core.settings import ConfigError, load_settings

    config_path = Path(__file__).resolve().parent / "config" / "settings.yaml"
    logger.info("Modular RAG MCP Server starting (阶段 A)...")
    logger.info("Config path: %s (exists=%s)", config_path, config_path.exists())

    try:
        settings = load_settings(config_path)
    except ConfigError as exc:
        logger.error("配置校验失败: %s", exc)
        return 1
    except FileNotFoundError as exc:
        logger.error("配置文件缺失: %s", exc)
        return 1

    logger.info(
        "Settings loaded: llm.provider=%s | embedding.provider=%s | vector_store.backend=%s",
        settings.llm.provider,
        settings.embedding.provider,
        settings.vector_store.backend,
    )
    logger.info("Skeleton ready. MCP Server 入口将在阶段 E 实现。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
