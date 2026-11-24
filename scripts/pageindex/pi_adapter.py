"""PageIndex 上游适配层（PI2，运行期唯一 import pageindex / litellm 的文件）。

主 venv 永不 import 本模块（红线 ``no-pageindex-import`` 白名单成员，
规格 §3.2 / §3.6 第 5 条）；仅 ``server.py``（sidecar venv 内）与预研存档
``poc_compare.py`` 同侧使用。全部上游依赖**懒加载在函数体内**，模块导入
零副作用。

职责（规格 §3.2 sidecar 服务段）：
- 建树：PDF 直接 ``PageIndexClient.submit_document``；MD 先经 ``md_render``
  转派生 PDF（D3 / D11，产物 ``<storage>/derived/<md_sha256>.pdf``，D12）
  再同 PDF 路径提交。显式传 ``storage_path`` 与低 ``summary_concurrency``
  （防上游默认 128 并发触发低 RPM 账户 429）并保留单节点 400 内容审核
  拒绝降级保护；``usage["total_tokens"]`` 采集进 /index 响应。
- 检索：组装 ``tree_loop.LoopDoc``，注入 ``complete``（litellm）与
  ``read_node``（D12 证据回映射：MD 证据 ``source_path`` 永为原 MD 路径，
  读前核对文件哈希，漂移 / 越界安全回退 ``get_page_content``）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

try:  # 同目录导入（sidecar venv 直跑；测试不导入本模块，规格 §4）
    from heading_index import HeadingIndex, build_heading_index
    from md_render import render
    from tree_loop import (LoopDoc, LoopResult, NodeRead, flat_nodes,
                           search, section_titles_for)
    from tree_store import TreeStore
except ImportError:  # pragma: no cover - 包化导入回退
    from .heading_index import HeadingIndex, build_heading_index  # type: ignore
    from .md_render import render  # type: ignore
    from .tree_loop import (LoopDoc, LoopResult, NodeRead, flat_nodes,  # type: ignore
                            search, section_titles_for)
    from .tree_store import TreeStore  # type: ignore

API_VERSION = "1.0"

#: D11 终裁（2026-11-22 用户拍板）：submit 建树默认 optimize="merge" 档。
SUBMIT_OPTIMIZE = "merge"

MD_SUFFIXES = (".md", ".markdown")


def format_for(path: Path) -> str:
    """按扩展名定格式（D5：对账同步只发 .pdf / .md / .markdown）。"""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in MD_SUFFIXES:
        return "md"
    raise ValueError(f"unsupported source format: {path.suffix or path}")


def parse_extra_headers(raw: str | None) -> dict[str, str] | None:
    """``LLM_EXTRA_HEADERS``：JSON 对象（str→str），空值返回 None；非法即抛错。"""
    if not raw or not raw.strip():
        return None
    try:
        headers = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM_EXTRA_HEADERS must be a JSON object: {exc}") from exc
    if not isinstance(headers, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise RuntimeError("LLM_EXTRA_HEADERS must be a JSON object of string values")
    return headers


def patch_ratelimit(concurrency: int = 3) -> None:
    """上游限流补丁（POC ``patch_pageindex_ratelimit`` 移植，幂等）。

    上游对全部节点 ``asyncio.gather`` 并发发 LLM 请求，大文档会瞬时打爆
    供应商 RPM（实测智谱 429）。全局信号量限在飞请求数之外的第二道保险
    （``summary_concurrency`` 之外的运行期兜底），并在上游重试耗尽后以
    指数退避整体重试；单节点 400 内容审核拒绝降级为 600 字符摘录当摘要
    （对齐上游 fallback_summary 口径，3.2 要求保留）。
    """
    import litellm
    import pageindex.utils as pi_utils

    if getattr(pi_utils._acompletion, "_pi_ratelimit_wrapped", False):
        return  # 幂等：重复调用不再嵌套包装
    sem = asyncio.Semaphore(concurrency)
    original = pi_utils._acompletion
    retryable = (
        litellm.RateLimitError,
        litellm.InternalServerError,
        litellm.Timeout,
        litellm.APIConnectionError,
        pi_utils.LLMRetriesExhausted,
    )

    async def limited(model, messages, sent):
        delay = 5.0
        for attempt in range(10):
            async with sem:
                try:
                    return await original(model, messages, sent)
                except retryable:
                    if attempt == 9:
                        raise
                except litellm.BadRequestError as exc:
                    limited.moderation_skips += 1
                    prompt = ""
                    if messages and isinstance(messages[0], dict):
                        prompt = messages[0].get("content") or ""
                    text = prompt.split("Partial Document Text:", 1)[1] if \
                        "Partial Document Text:" in prompt else prompt
                    excerpt = " ".join(text.strip().split())[:600]
                    print(f"[pi_adapter] moderation fallback "
                          f"#{limited.moderation_skips}: {str(exc)[:80]}",
                          file=sys.stderr)
                    return excerpt
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)
        raise RuntimeError("unreachable: retry loop exited")

    limited.moderation_skips = 0
    limited._pi_ratelimit_wrapped = True
    pi_utils._acompletion = limited


class PageIndexAdapter:
    """上游 PageIndexClient 编排：index / delete / documents / search / status。"""

    def __init__(self, storage: str | os.PathLike[str], model: str, *,
                 api_base: str | None = None, api_key: str | None = None,
                 extra_headers: dict[str, str] | None = None,
                 summary_concurrency: int = 3,
                 render_timeout_s: float = 600.0) -> None:
        self.storage = Path(storage)
        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.extra_headers = extra_headers
        self.summary_concurrency = summary_concurrency
        self.render_timeout_s = render_timeout_s
        self.store = TreeStore(self.storage / "tree_store.json")
        self._client_obj = None
        # MD 读取缓存：(source_path, file_hash) -> (lines, HeadingIndex)；
        # 哈希核对通过才复用（D12④ 漂移防护）
        self._md_cache: dict[tuple[str, str], tuple[list[str], HeadingIndex]] = {}

    @classmethod
    def from_env(cls) -> "PageIndexAdapter":
        """按 §3.3 env 契约装配（``.env-pageindex`` 由 start_sidecar.sh 导出）。"""
        storage = os.environ.get("PAGEINDEX_STORAGE") or "./data/db/pageindex"
        model = os.environ.get("LLM_MODEL") or ""
        if not model:
            raise RuntimeError("LLM_MODEL is required (see scripts/pageindex/env.example)")
        return cls(
            storage,
            model,
            api_base=os.environ.get("LLM_API_BASE") or None,
            api_key=os.environ.get("LLM_API_KEY") or None,
            extra_headers=parse_extra_headers(os.environ.get("LLM_EXTRA_HEADERS")),
            summary_concurrency=int(os.environ.get("PAGEINDEX_SUMMARY_CONCURRENCY", "3")),
            render_timeout_s=float(os.environ.get("PAGEINDEX_RENDER_TIMEOUT_S", "600")),
        )

    # -- 上游 client（懒加载） ----------------------------------------------

    def _backend(self) -> dict:
        """建树与检索共用的 LiteLLM 连接参数（只含已配置项）。"""
        backend = {"api_base": self.api_base, "api_key": self.api_key,
                   "extra_headers": self.extra_headers}
        return {k: v for k, v in backend.items() if v}

    def _client(self):
        from pageindex import PageIndexClient

        if self._client_obj is None:
            # 上游建树走自己的 litellm 调用，端点 / 会话头须经 index_backend 传入，
            # 否则 openai/<model> 落到 OPENAI_API_BASE 缺省值（api.openai.com）。
            self._client_obj = PageIndexClient(
                index_model=self.model, chat_model=self.model,
                index_backend=self._backend() or None,
                storage_path=str(self.storage),
                summary_concurrency=self.summary_concurrency,
                optimize=SUBMIT_OPTIMIZE)
        return self._client_obj

    def _submit(self, pdf_path: str) -> tuple[dict, str | None, int]:
        """提交 PDF 建树：返回 (tree, doc_id, llm_tokens)。"""
        patch_ratelimit(self.summary_concurrency)
        client = self._client()
        submitted = client.submit_document(pdf_path)
        doc_id = submitted.get("doc_id") if isinstance(submitted, dict) else None
        usage = submitted.get("usage") or {} if isinstance(submitted, dict) else {}
        tokens = int(usage.get("total_tokens") or 0)
        structure = client.get_document_structure(doc_id)
        tree = structure if isinstance(structure, dict) else {"structure": structure}
        return tree, doc_id, tokens

    def _structure(self, doc_id: str | None) -> dict:
        structure = self._client().get_document_structure(doc_id)
        return structure if isinstance(structure, dict) else {"structure": structure}

    def _page_content(self, doc_id: str | None, page: int) -> str:
        """上游 ``get_page_content(doc_id, pages) -> list[{page_index, markdown}]``。"""
        if not doc_id:
            return ""
        try:
            result = self._client().get_page_content(doc_id, str(page))
        except Exception as exc:
            print(f"[pi_adapter] get_page_content failed: "
                  f"{type(exc).__name__}: {str(exc)[:120]}", file=sys.stderr)
            return ""
        if isinstance(result, list):
            return "\n\n".join(item.get("markdown") or "" for item in result
                               if isinstance(item, dict))
        return result if isinstance(result, str) else ""

    # -- LLM（检索循环注入） -------------------------------------------------

    def _complete(self, prompt: str) -> dict:
        """litellm 调用（连接类错误 3 次指数退避，POC 同款）；返回
        ``{"text", "tokens"}``（D7 1.3 修订：token 记账需要）。"""
        import litellm

        started = time.perf_counter()
        resp = None
        for attempt in range(3):
            try:
                resp = litellm.completion(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    **self._backend(),
                )
                break
            except Exception as exc:
                if attempt == 2 or "Connection error" not in str(exc):
                    raise
                time.sleep(2 ** attempt)
        usage = getattr(resp, "usage", None)
        return {
            "text": resp.choices[0].message.content or "",
            "tokens": getattr(usage, "total_tokens", 0) or 0,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    # -- read_node（D12 证据回映射） -----------------------------------------

    def _md_snapshot(self, source_path: str, file_hash: str
                     ) -> tuple[list[str], HeadingIndex] | None:
        """原 MD 行与标题索引；文件缺失 / 解码失败 / 哈希漂移返回 None。"""
        key = (source_path, file_hash)
        if key in self._md_cache:
            return self._md_cache[key]
        try:
            data = Path(source_path).read_bytes()
        except OSError:
            return None
        if hashlib.sha256(data).hexdigest() != file_hash:
            print(f"[pi_adapter] md drift detected, fallback to derived PDF: "
                  f"{source_path}", file=sys.stderr)
            return None
        try:
            md_text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None
        lines = md_text.split("\n")
        snapshot = (lines, HeadingIndex(md_text))
        self._md_cache[key] = snapshot
        return snapshot

    def _read_md_node(self, doc: LoopDoc, node: dict) -> NodeRead:
        rec = self.store.get(doc.source_path) or {}
        snapshot = self._md_snapshot(doc.source_path, rec.get("file_hash") or "")
        title = node.get("title", "")
        if snapshot is not None:
            lines, heading_index = snapshot
            # 父路径不含节点自身标题（HeadingIndex 消歧按祖先对齐）
            parents = section_titles_for(doc.tree, node)[:-1]
            span = heading_index.locate(title, parents)
            if span:
                start, end = span
                if 1 <= start <= end <= len(lines):  # 行越界 → 回退
                    return NodeRead(text="\n".join(lines[start - 1:end]),
                                    line_start=start, line_end=end,
                                    how="relocated")
        # 回定位失败 / 文件漂移：回退派生 PDF 页文本（页码对用户无意义，
        # 置 None；D12④ 安全回退 get_page_content）
        start = int(node.get("start_index") or 1)
        end = int(node.get("end_index") or start)
        pages = [self._page_content(doc.doc_id, p)
                 for p in range(start, min(end, start + 5) + 1)]
        how = "unlocated" if snapshot is not None else "page_fallback"
        return NodeRead(text="\n\n".join(t for t in pages if t), how=how)

    def _read_pdf_node(self, doc: LoopDoc, node: dict) -> NodeRead:
        # PDF 树节点页码字段为 start_index / end_index（物理页，1 起计）
        start = int(node.get("start_index") or 1)
        end = int(node.get("end_index") or start)
        pages = [self._page_content(doc.doc_id, p)
                 for p in range(start, min(end, start + 5) + 1)]
        return NodeRead(text="\n\n".join(t for t in pages if t),
                        page_start=start, page_end=end, how="pdf")

    def _read_node(self, doc: LoopDoc, node: dict) -> NodeRead:
        if doc.format == "md":
            return self._read_md_node(doc, node)
        return self._read_pdf_node(doc, node)

    # -- HTTP 端点底层（server.py 编排） -------------------------------------

    def status(self) -> dict:
        pageindex_version = "unknown"
        try:
            from importlib.metadata import version
            pageindex_version = version("pageindex")
        except Exception:  # pragma: no cover - 元数据缺失不阻断
            pass
        return {"version": API_VERSION, "pageindex_version": pageindex_version,
                "model": self.model, "documents": len(self.store.list_all())}

    def documents(self) -> list[dict]:
        out = []
        for rec in self.store.list_all():
            item = {k: rec[k] for k in
                    ("source_path", "file_hash", "format", "doc_id",
                     "node_count", "indexed_at") if k in rec}
            if rec.get("format") == "md" and rec.get("derived_pdf"):
                item["derived_pdf"] = rec["derived_pdf"]  # 仅供运维排查（§3.4）
            out.append(item)
        return out

    def index(self, source_path: str, file_hash: str) -> dict:
        src = Path(source_path)
        fmt = format_for(src)
        rec = self.store.get(str(src))
        if rec and rec.get("file_hash") == file_hash:
            return self._index_response(rec)  # 同 file_hash 幂等返回（§3.4）
        if fmt == "md":
            derived = self.storage / "derived" / f"{file_hash}.pdf"  # D12①
            result = render(str(src), str(derived),
                            timeout_s=self.render_timeout_s)
            render_ms = result.elapsed_ms
            derived_pages = result.pages
            tree, doc_id, tokens = self._submit(str(derived))
            rec = {
                "source_path": str(src), "file_hash": file_hash, "format": "md",
                "doc_id": doc_id, "derived_pdf": str(derived),
                "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "node_count": len(flat_nodes(tree)),
                "headings": build_heading_index(
                    src.read_text(encoding="utf-8-sig")),
            }
        else:
            tree, doc_id, tokens = self._submit(str(src))
            render_ms = None
            derived_pages = None
            rec = {
                "source_path": str(src), "file_hash": file_hash, "format": "pdf",
                "doc_id": doc_id,
                "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "node_count": len(flat_nodes(tree)),
            }
        self.store.upsert(rec)
        return self._index_response(rec, llm_tokens=tokens,
                                    render_ms=render_ms,
                                    derived_pages=derived_pages)

    @staticmethod
    def _index_response(rec: dict, llm_tokens: int = 0,
                        render_ms: float | None = None,
                        derived_pages: int | None = None) -> dict:
        out = {"doc_id": rec.get("doc_id"), "format": rec.get("format"),
               "node_count": rec.get("node_count"), "llm_tokens": llm_tokens}
        if rec.get("format") == "md":  # 后两项仅 MD（§3.4）
            if render_ms is not None:
                out["render_ms"] = round(render_ms, 1)
            if derived_pages is not None:
                out["derived_pages"] = derived_pages
        return out

    def delete(self, source_path: str) -> dict:
        rec = self.store.remove(source_path)
        if rec and rec.get("derived_pdf"):  # MD 连带删派生 PDF（D5）
            try:
                Path(rec["derived_pdf"]).unlink(missing_ok=True)
            except OSError as exc:
                print(f"[pi_adapter] derived pdf unlink failed: {exc}",
                      file=sys.stderr)
        return {"removed": rec is not None}

    def search(self, query: str, source_paths: list[str] | None, *,
               max_nodes: int = 5, max_loops: int = 3,
               max_token_budget: int = 32000) -> LoopResult:
        records = self.store.list_all()
        unindexed = 0
        if source_paths is not None:
            wanted = {str(p) for p in source_paths}
            indexed = {r["source_path"] for r in records}
            unindexed = len(wanted - indexed)
            records = [r for r in records if r["source_path"] in wanted]
        if not records:
            return LoopResult(telemetry={"unindexed": unindexed} if unindexed else {})
        docs = [
            LoopDoc(source_path=r["source_path"], format=r["format"],
                    tree=self._structure(r["doc_id"]), doc_id=r["doc_id"],
                    title=Path(r["source_path"]).stem)
            for r in records
        ]
        result = search(query, docs, complete=self._complete,
                        read_node=self._read_node,
                        max_nodes=max_nodes, max_loops=max_loops,
                        max_token_budget=max_token_budget)
        if unindexed:
            result.telemetry["unindexed"] = unindexed
        return result
