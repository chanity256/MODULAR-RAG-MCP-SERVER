"""全局测试夹具。

日志隔离（LOGGING_SPEC LG1）：大量用例用不含 ``observability`` 段的临时配置，
默认相对日志路径会相对 cwd（仓库根）解析而写进真实 ``logs/``。会话级把
``RAG_LOG_ROOT`` 指向临时目录，相对日志路径一律落在这里；子进程继承该环境变量。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from observability.log_files import LOG_ROOT_ENV


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
