"""日志工具。

- 阶段 A：``get_logger`` 返回输出到 stderr 的 logger，避免污染 MCP 的 stdout 通道。
- 阶段 F2：JSON Lines 结构化 trace 日志（遗留，生产写入走 ``core.trace.persist``）。
- LOGGING_SPEC LG4：``setup_app_logging`` 在进程入口为 root logger 追加按日文件
  handler（``<app_log_dir>/<component>-YYYY-MM-DD.log``）。库代码与测试不调用它，
  因此不产生日志文件。
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import IO, Any

from observability.log_files import (
    TRACE_FILE_PREFIX,
    TRACE_FILE_SUFFIX,
    cleanup_expired,
    daily_path,
    is_single_file,
    local_today,
    resolve_log_path,
)

_CONFIGURED = False
_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

#: 会写应用日志的进程；保留期清理覆盖全部组件，避免少跑的组件旧文件无人清理。
APP_COMPONENTS: tuple[str, ...] = ("api", "mcp", "ingest", "query", "evaluate")
APP_LOG_SUFFIX = ".log"
_COMPONENT_PATTERN = re.compile(r"^[a-z0-9_-]+$")
#: 访问日志量大且已在 stderr，可从文件中排除。
_EXCLUDED_FROM_FILE = ("uvicorn.access",)


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root = logging.getLogger()
    # 仅在 root logger 尚无 handler 时配置，避免重复输出。
    if not root.handlers:
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    _CONFIGURED = True


def get_logger(name: str = "modular-rag") -> logging.Logger:
    """返回一个输出到 stderr 的 logger。"""
    _configure()
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# F2：JSON Lines 结构化日志 + trace 持久化
# ---------------------------------------------------------------------------

import json as _json
from pathlib import Path as _Path

from core.settings import Settings as _Settings

__all__ = [
    "APP_COMPONENTS",
    "DailyFileHandler",
    "JSONFormatter",
    "get_logger",
    "get_trace_logger",
    "setup_app_logging",
    "setup_cli_logging",
    "write_trace",
]

_TRACES_LOGGER_NAME = "modular-rag.traces"


class JSONFormatter(logging.Formatter):
    """把 LogRecord 渲染为单行 JSON（JSON Lines 格式）。

    固定字段：``time`` / ``level`` / ``logger`` / ``message``；
    额外字段合并 ``record.extra``（若有）。
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "time": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return _json.dumps(payload, ensure_ascii=False, default=str)


def get_trace_logger(log_file: str | None = None) -> logging.Logger:
    """返回配置了 JSONFormatter + FileHandler 的 trace logger。

    Args:
        log_file: 目标 JSONL 文件路径；``None`` 取
            ``settings.observability.log_file``（默认 ``logs/traces.jsonl``）。
    """
    _configure()
    logger = logging.getLogger(_TRACES_LOGGER_NAME)
    if logger.handlers:  # 已配置（多进程/重复调用幂等，取首路径）
        return logger
    path = _Path(log_file or _Settings().observability.log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def write_trace(trace_dict: dict, log_file: str | None = None) -> None:
    """把 ``trace.to_dict()`` 结果追加为一行 JSON（JSON Lines）。

    Args:
        trace_dict: 可 JSON 序列化的 trace 字典（含 ``trace_type``）。
        log_file: 目标文件路径；``None`` 取配置默认值。
    """
    logger = get_trace_logger(log_file)
    logger.info("trace", extra={"extra": dict(trace_dict)})


# ---------------------------------------------------------------------------
# LG4：应用运行日志按日落盘
# ---------------------------------------------------------------------------

CleanupTarget = tuple[Path, str, str]


class DailyFileHandler(logging.Handler):
    """按本地日期写 ``<directory>/<component>-YYYY-MM-DD.log``。

    不用 ``TimedRotatingFileHandler``：它靠重命名轮转，多进程同写时会互相覆盖。
    这里每天换一个新文件名、以追加方式写。打开新一天的文件时执行一次保留期清理。
    """

    def __init__(
        self,
        directory: str | Path,
        component: str,
        *,
        retention_days: int = 0,
        cleanup_targets: list[CleanupTarget] | None = None,
        today: Callable[[], date] = local_today,
    ) -> None:
        super().__init__()
        if not _COMPONENT_PATTERN.match(component):
            raise ValueError(f"invalid log component name: {component!r}")
        self._directory = Path(directory)
        self._component = component
        self._retention_days = retention_days
        self._cleanup_targets = list(cleanup_targets or [])
        self._today = today
        self._day: date | None = None
        self._stream: IO[str] | None = None
        self.addFilter(lambda record: not record.name.startswith(_EXCLUDED_FROM_FILE))

    @property
    def current_path(self) -> Path:
        return daily_path(self._directory, self._component, APP_LOG_SUFFIX, self._day or self._today())

    def open_today(self) -> Path:
        """确保当天文件已打开（入口调用一次，触发启动时清理）。"""
        self.acquire()
        try:
            self._ensure_day(self._today())
        finally:
            self.release()
        return self.current_path

    def _ensure_day(self, day: date) -> None:
        if self._stream is not None and day == self._day:
            return
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        self._day = day
        path = daily_path(self._directory, self._component, APP_LOG_SUFFIX, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = path.open("a", encoding="utf-8")
        for directory, prefix, suffix in self._cleanup_targets:
            cleanup_expired(directory, prefix, suffix, self._retention_days, day)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            self._ensure_day(self._today())
            assert self._stream is not None
            self._stream.write(message + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        self.acquire()
        try:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
        finally:
            self.release()
        super().close()


def setup_app_logging(
    settings: Any,
    component: str,
    *,
    today: Callable[[], date] = local_today,
) -> Path | None:
    """进程入口调用：root 级别取 ``log_level``，并追加按日文件 handler。

    幂等：重复调用会替换此前挂上的 ``DailyFileHandler``。stderr 输出不变。

    Returns:
        当天应用日志文件路径；目录不可写时为 ``None``（仅保留 stderr）。
    """
    obs = settings.observability
    _configure()
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(obs.log_level).upper(), logging.INFO))
    for existing in [h for h in root.handlers if isinstance(h, DailyFileHandler)]:
        root.removeHandler(existing)
        existing.close()

    app_dir = resolve_log_path(obs.app_log_dir)
    targets: list[CleanupTarget] = [(app_dir, name, APP_LOG_SUFFIX) for name in APP_COMPONENTS]
    trace_path = resolve_log_path(obs.log_file)
    if not is_single_file(trace_path):
        targets.append((trace_path, TRACE_FILE_PREFIX, TRACE_FILE_SUFFIX))
    handler = DailyFileHandler(
        app_dir,
        component,
        retention_days=int(obs.retention_days),
        cleanup_targets=targets,
        today=today,
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    try:
        path = handler.open_today()
    except OSError as exc:
        # 日志落盘属于旁路：目录不可写时只保留 stderr，不阻止进程启动。
        handler.close()
        logging.getLogger("modular-rag.app-logging").warning(
            "app log file disabled: %s: %s", type(exc).__name__, exc
        )
        return None
    root.addHandler(handler)
    return path


def setup_cli_logging(component: str, argv: list[str], default_config: str | Path) -> Path | None:
    """CLI ``__main__`` 用：只解析 ``--config`` 并启用应用日志。

    配置加载失败时静默返回 ``None``，错误由 CLI 的 ``main`` 照常报告。
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", default=str(default_config))
    args, _ = parser.parse_known_args(argv)
    from core.settings import load_settings

    try:
        settings = load_settings(args.config)
    except Exception:
        return None
    return setup_app_logging(settings, component)
