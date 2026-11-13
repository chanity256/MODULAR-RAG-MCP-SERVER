"""全局测试夹具。

日志隔离（LOGGING_SPEC LG1）：大量用例用不含 ``observability`` 段的临时配置，
默认相对日志路径会相对 cwd（仓库根）解析而写进真实 ``logs/``。会话级把
``RAG_LOG_ROOT`` 指向临时目录，相对日志路径一律落在这里；子进程继承该环境变量。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from observability.log_files import LOG_ROOT_ENV
from observability.logger import DailyFileHandler


@pytest.fixture(scope="session", autouse=True)
def isolated_log_root(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("log_root")
    previous = os.environ.get(LOG_ROOT_ENV)
    os.environ[LOG_ROOT_ENV] = str(root)
    try:
        yield root
    finally:
        if previous is None:
            os.environ.pop(LOG_ROOT_ENV, None)
        else:
            os.environ[LOG_ROOT_ENV] = previous


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    """入口用例会调用 ``setup_app_logging``（改 root 级别、挂文件 handler），用例结束复原。

    只移除本项目的 ``DailyFileHandler``：pytest 自己的捕获 handler 由其插件管理。
    """
    root = logging.getLogger()
    level = root.level
    before = set(root.handlers)
    yield
    for handler in list(root.handlers):
        if isinstance(handler, DailyFileHandler) and handler not in before:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)
