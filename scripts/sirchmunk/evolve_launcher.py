"""Sirchmunk sidecar 知识演化启动器（KE1，SIRCHMUNK_KNOWLEDGE_EVOLUTION_SPEC 方案 B）。

只在 sidecar 独立 venv 内运行（由 start_sidecar.sh 的演化开启分支调用）；
主 venv 永不 import 本文件，也永不 import sirchmunk（进程隔离红线，S 系列 3.1）。

职责（对齐上游 ``sirchmunk serve`` 的初始化顺序，见 cli/cli.py cmd_serve）：
  1. 开关与组合 fail-fast：演化依赖知识簇复用（embedding_client 仅在
     reuse_knowledge=True 时创建，上游 search.py），复用关闭时演化必然无效。
  2. 复现 CLI 的 work path 解析与 work_path/.env 加载（先绝对化、回写环境，
     再加载 .env，override=False 与上游一致）。
  3. 在 import sirchmunk.api.main 之前包装 ``AgenticSearch.__init__``：仅注入
     ``enable_knowledge_evolution=True``（未显式传递时），其余参数原样转发；
     同步与 SSE 构造路径、上游 Chat 单例均经过同一已补丁类（Chat 副作用由
     门控验证记录，KE1 不缩小补丁作用域）。
  4. 以同一 ASGI app 启动 uvicorn：显式传 host / port / log_level，reload=False。

边界：不修改上游源码；签名不兼容即 fail-fast 退出（不静默降级为普通 serve）。
启动时只验证签名 / 补丁 / 开关组合；同步与 SSE 是否真正创建非空 evolver，
必须由各自首个门控 FAST 请求验证（RAG_AGENTIC_INTEGRATION=1，KE 规格第 7.2 节）。
"""

from __future__ import annotations

import argparse
import functools
import inspect
import os
import sys
from pathlib import Path

EVOLUTION_FLAG = "SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION"
REUSE_FLAG = "SIRCHMUNK_ENABLE_CLUSTER_REUSE"

EXIT_FLAG_OFF = 3
EXIT_FLAG_INVALID_COMBINATION = 4
EXIT_SIGNATURE_INCOMPATIBLE = 5


def _resolve_work_path() -> Path:
    """复现 cli.cli.cmd_serve 的 work path 解析：环境变量缺省 ~/.sirchmunk，绝对化后回写。"""
    work_path = Path(
        os.getenv("SIRCHMUNK_WORK_PATH", str(Path.home() / ".sirchmunk"))
    ).expanduser().resolve()
    os.environ["SIRCHMUNK_WORK_PATH"] = str(work_path)
    return work_path


def _load_work_path_env(work_path: Path) -> bool:
    """复现 cli.cli 的 .env 加载：work_path/.env 存在则加载（override=False）。"""
    from sirchmunk.cli.cli import _load_env_file

    env_file = work_path / ".env"
    if not env_file.exists():
        return False
    return bool(_load_env_file(env_file))


def _check_flags() -> int | None:
    """开关与组合 fail-fast：演化未显式开启、或演化开启但复用显式关闭时拒绝启动。"""
    if os.getenv(EVOLUTION_FLAG, "false").lower() != "true":
        print(
            f"[evolve-launcher] {EVOLUTION_FLAG} is not 'true'; "
            "start_sidecar.sh should not route here",
            file=sys.stderr,
        )
        return EXIT_FLAG_OFF
    if os.getenv(REUSE_FLAG, "true").lower() != "true":
        # 上游 embedding_client 只在 reuse_knowledge=True 时创建（search.py）；
        # 复用关闭 = 演化必然创建空 evolver，属不兼容组合，fail-fast 而非静默空转。
        print(
            f"[evolve-launcher] {EVOLUTION_FLAG}=true requires {REUSE_FLAG} "
            "not 'false' (knowledge evolution depends on the embedding client, "
            "which is only created when cluster reuse is enabled)",
            file=sys.stderr,
        )
        return EXIT_FLAG_INVALID_COMBINATION
    return None


def _patch_agentic_search() -> None:
    """包装 AgenticSearch.__init__：仅注入 enable_knowledge_evolution=True，其余原样转发。

    必须在 import sirchmunk.api.main 之前完成，使同步端点单例、SSE 每请求
    实例与上游 Chat 单例的构造都经过同一已补丁类。
    """
    from sirchmunk.search import AgenticSearch

    params = inspect.signature(AgenticSearch.__init__).parameters
    if "enable_knowledge_evolution" not in params:
        raise RuntimeError(
            "AgenticSearch.__init__ has no 'enable_knowledge_evolution' parameter; "
            "upstream signature drifted from the pinned baseline (b314e11). "
            "Re-run KE0/KE1 verification before enabling evolution."
        )

    original_init = AgenticSearch.__init__

    @functools.wraps(original_init)
    def _patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("enable_knowledge_evolution", True)
        return original_init(self, *args, **kwargs)

    AgenticSearch.__init__ = _patched_init  # type: ignore[method-assign]
    print(
        "[evolve-launcher] AgenticSearch patched: enable_knowledge_evolution=True "
        "(default; explicit caller values still win)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Start the sirchmunk sidecar with knowledge evolution enabled "
        "(same ASGI app as `sirchmunk serve`; sidecar venv only)."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8584)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    flag_error = _check_flags()
    if flag_error is not None:
        return flag_error

    work_path = _resolve_work_path()
    loaded = _load_work_path_env(work_path)
    print(
        f"[evolve-launcher] work_path={work_path} (.env {'loaded' if loaded else 'not found'})"
    )

    _patch_agentic_search()

    # 补丁完成后才 import 上游 ASGI 应用：api.main 内部的 AgenticSearch 引用
    # 指向同一已补丁类；reload=False 保证不另起 reloader 进程（补丁不进入子进程）。
    import uvicorn

    from sirchmunk import __version__
    from sirchmunk.api.main import app

    print(
        f"[evolve-launcher] sirchmunk {__version__} starting with knowledge "
        f"evolution: host={args.host} port={args.port} log_level={args.log_level} "
        "reload=False"
    )
    uvicorn.run(app, host=args.host, port=args.port, reload=False,
                log_level=args.log_level.lower())
    return 0


if __name__ == "__main__":
    sys.exit(main())
