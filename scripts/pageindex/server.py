"""PageIndex sidecar HTTP 服务（PI2，规格 §3.4 契约）。

只做 HTTP 编排与鉴权：统一信封 ``{"success": bool, "data": ..., "error":
str | null}``、Bearer 鉴权（``hmac.compare_digest``）、127.0.0.1 绑定。
不 import pageindex / litellm —— adapter 经 ``create_app(adapter, token)``
注入（单测注入 fake adapter 跑 TestClient，规格 §4 PI2 行）；``main()``
内才延迟导入 ``pi_adapter``（sidecar venv 专属）。

端点：
- ``GET  /api/v1/status``          → ``{version, pageindex_version, model, documents}``
- ``GET  /api/v1/documents``       → ``[{source_path, file_hash, format, doc_id, node_count, indexed_at, derived_pdf?}]``
- ``POST /api/v1/index``           → ``{doc_id, format, node_count, llm_tokens, render_ms?, derived_pages?}``
- ``POST /api/v1/index/delete``    → ``{removed: bool}``（POST 子路径而非带 body 的 DELETE，§3.4）
- ``POST /api/v1/search``          → ``{evidences, llm_call_count, total_llm_tokens, telemetry}``

``/search`` 的 ``source_paths`` 三种取值（§3.4）：null 全部已索引；非空列表
只在其中检索（未索引路径由 adapter 计入 ``telemetry.unindexed``，证据归属
再由本层过滤——不信任 adapter 内部过滤）；**空列表 fail-closed** 直接空
证据、不调用 adapter 检索（主 venv 对空定界集本就不调用，此条为纵深防御）。
"""

from __future__ import annotations

import hmac
import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

try:  # 同目录导入（RenderError 为纯模块，供 /index 错误分型）
    from md_render import RenderError
except ImportError:  # pragma: no cover - 包化导入回退
    from .md_render import RenderError  # type: ignore

API_VERSION = "1.0"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

#: /search 响应证据的契约字段（TreeEvidence，§3.4；丢弃 loop 内部
#: ``truncated``——telemetry 已有 ``truncated_evidence``）
EVIDENCE_FIELDS = ("source_path", "doc_id", "node_id", "text", "score",
                   "section", "page_start", "page_end", "line_start",
                   "line_end")


def ensure_loopback(host: str) -> str:
    """校验监听地址为 loopback（§3.4「绑定 127.0.0.1」）；非法即拒绝启动。"""
    host = (host or "").strip("[]")  # IPv6 字面量去方括号
    if host not in LOOPBACK_HOSTS:
        raise ValueError(f"sidecar must bind loopback, got {host!r}")
    return host


def envelope(success: bool, data=None, error: str | None = None) -> dict:
    return {"success": success, "data": data, "error": error}


def _authorized(request: Request, token: str) -> None:
    auth = request.headers.get("authorization") or ""
    supplied = auth[7:] if auth.startswith("Bearer ") else ""
    if not supplied or not hmac.compare_digest(supplied, token):
        raise HTTPException(status_code=401, detail="invalid bearer token")


def create_app(adapter, token: str) -> FastAPI:
    """构建 FastAPI 应用；``adapter`` 注入（生产 PageIndexAdapter / 测试 fake）。"""
    if not token:
        raise ValueError("bearer token must be non-empty")
    app = FastAPI(title="pageindex-sidecar", version=API_VERSION)

    def evidence_out(ev: dict) -> dict:
        return {k: ev.get(k) for k in EVIDENCE_FIELDS}

    @app.get("/api/v1/status")
    def status(request: Request):
        _authorized(request, token)
        return envelope(True, adapter.status())

    @app.get("/api/v1/documents")
    def documents(request: Request):
        _authorized(request, token)
        return envelope(True, adapter.documents())

    @app.post("/api/v1/index")
    async def index(request: Request):
        _authorized(request, token)
        body = await request.json()
        source_path = body.get("source_path")
        file_hash = body.get("file_hash")
        if not source_path or not file_hash:
            return envelope(False, error="source_path and file_hash are required")
        if not Path(source_path).is_absolute():
            return envelope(False, error="source_path must be absolute")
        try:
            data = adapter.index(source_path, file_hash)
        except RenderError as exc:  # MD 转换失败：render: 前缀区分（§3.4 / R9）
            return envelope(False, error=f"render: {exc}")
        except (OSError, ValueError) as exc:
            return envelope(False, error=f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # 上游建树失败等，不裸抛堆栈
            print(f"[server] /index failed: {type(exc).__name__}: "
                  f"{str(exc)[:200]}", file=sys.stderr)
            return envelope(False, error=f"index failed: "
                                         f"{type(exc).__name__}: {str(exc)[:200]}")
        return envelope(True, data)

    @app.post("/api/v1/index/delete")
    async def index_delete(request: Request):
        _authorized(request, token)
        body = await request.json()
        source_path = body.get("source_path")
        if not source_path:
            return envelope(False, error="source_path is required")
        try:
            data = adapter.delete(source_path)
        except Exception as exc:
            print(f"[server] /index/delete failed: {type(exc).__name__}: "
                  f"{str(exc)[:200]}", file=sys.stderr)
            return envelope(False, error=f"delete failed: "
                                         f"{type(exc).__name__}: {str(exc)[:200]}")
        return envelope(True, data)

    @app.post("/api/v1/search")
    async def search(request: Request):
        _authorized(request, token)
        body = await request.json()
        query = body.get("query")
        source_paths = body.get("source_paths")
        if not isinstance(query, str) or not query.strip():
            return envelope(False, error="query must be a non-empty string")
        if source_paths is not None and (
                not isinstance(source_paths, list)
                or not all(isinstance(p, str) for p in source_paths)):
            return envelope(False, error="source_paths must be null or a list of strings")
        max_nodes = int(body.get("max_nodes") or 5)
        max_loops = int(body.get("max_loops") or 3)
        max_token_budget = int(body.get("max_token_budget") or 32000)
        if source_paths is not None and not source_paths:
            # fail-closed 纵深防御（§3.4）：空定界集绝不退化为全量检索
            return envelope(True, {"evidences": [], "llm_call_count": 0,
                                   "total_llm_tokens": 0,
                                   "telemetry": {"fail_closed": True}})
        try:
            result = adapter.search(query, source_paths,
                                    max_nodes=max_nodes, max_loops=max_loops,
                                    max_token_budget=max_token_budget)
        except Exception as exc:
            print(f"[server] /search failed: {type(exc).__name__}: "
                  f"{str(exc)[:200]}", file=sys.stderr)
            return envelope(False, error=f"search failed: "
                                         f"{type(exc).__name__}: {str(exc)[:200]}")
        evidences = [evidence_out(ev) for ev in (result.evidences or [])
                     if isinstance(ev, dict)]
        if source_paths is not None:
            wanted = set(source_paths)
            kept = [ev for ev in evidences if ev.get("source_path") in wanted]
            dropped = len(evidences) - len(kept)
            if dropped:
                result.telemetry["out_of_scope_dropped"] = dropped
            evidences = kept
        return envelope(True, {"evidences": evidences,
                               "llm_call_count": result.llm_call_count,
                               "total_llm_tokens": result.total_llm_tokens,
                               "telemetry": result.telemetry})

    return app


def main() -> None:  # pragma: no cover - sidecar venv 入口
    """按 §3.3 env 契约启动：HOST/PORT/TOKEN/STORAGE；绑定非 loopback 拒绝。"""
    try:
        host = ensure_loopback(os.environ.get("PAGEINDEX_HOST", "127.0.0.1"))
    except ValueError as exc:
        print(f"[server] {exc}", file=sys.stderr)
        raise SystemExit(1)
    port = int(os.environ.get("PAGEINDEX_PORT", "8585"))
    token = os.environ.get("PAGEINDEX_API_TOKEN") or ""
    if not token:
        print("[server] PAGEINDEX_API_TOKEN is required "
              "(cp scripts/pageindex/env.example .env-pageindex)", file=sys.stderr)
        raise SystemExit(1)
    from pi_adapter import PageIndexAdapter  # sidecar venv 专属（红线白名单）

    adapter = PageIndexAdapter.from_env()
    app = create_app(adapter, token)
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
