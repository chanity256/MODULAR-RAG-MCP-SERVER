"""PageIndex 索引对账同步（阶段 PI4，PAGEINDEX_TREE_RETRIEVAL_SPEC §3.2 / §3.4）。

以摄取历史 v2 success 记录为准，把 PageIndex sidecar 的索引对齐到当前语料：
``file_hash`` 变化即重建、历史中已不存在的即删除、非 ``.pdf`` / ``.md`` /
``.markdown`` 跳过并计数（D5）。不改摄取管线与 DocumentManager；MD 的转换与
建树都在 sidecar 内完成（D3 / D11），本脚本只发原 MD 路径与 file_hash。

用法::

    python scripts/pageindex_sync.py [--collection C] [--dry-run] [--config PATH]

行为契约：

- 按物理文件去重：success 记录以 ``normalize_history_source_path`` 归一后的
  绝对路径为键；同一文件在多个 collection 各有一条记录只建一份索引。
- 同键多条记录的 ``file_hash`` 不一致时，以同步时对当前文件实算的 SHA256
  为准并以其调用 ``/index``；文件已不存在（实算失败）的键按「历史中已
  不存在」处理（不索引；全量模式下其 sidecar 索引会被删除）。
- 删除只在全量模式执行：sidecar ``/documents`` 中存在、而全部 collection
  的 success 记录归一后都不包含的路径才删除。带 ``--collection C`` 时只对
  C 内的文件做新增 / 重建 / 跳过，不删除任何索引（按 C 过滤后做差集会误删
  其他 collection 的已建树索引）。
- 单文档失败不中断；``render:`` 前缀错误单列为转换失败（与建树失败区分，
  D5）；退出码 1 表示存在失败。
- ``--dry-run`` 只打印计划，不发任何写请求。

可测试性：分类 / 执行 / 汇总全部是可导入的纯函数，``main(argv, *, settings=…,
checker=…, history=…, client_factory=…)`` 支持注入（离线单测零网络，见
``tests/unit/test_pageindex_sync.py``）。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

# 让 ``src`` 下的包在未安装时也可被直接 import（与根 ``main.py`` 同一模式，
# 支持 ``python scripts/pageindex_sync.py`` 直接运行）。
_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from libs.agentic_search.collection_paths import normalize_history_source_path  # noqa: E402
from libs.agentic_search.local_roots import ensure_local_roots  # noqa: E402
from libs.agentic_search.path_guard import PathGuard, PathGuardError  # noqa: E402
from libs.loader.file_integrity import SQLiteIntegrityChecker  # noqa: E402

__all__ = [
    "SUPPORTED_SUFFIXES",
    "RENDER_ERROR_PREFIX",
    "EXIT_OK",
    "EXIT_FAILURE",
    "EXIT_USAGE",
    "SidecarError",
    "SidecarTimeoutError",
    "SidecarBackendError",
    "SidecarResponseError",
    "SidecarClient",
    "HistoryEntry",
    "SyncAction",
    "SyncCounts",
    "SyncPlan",
    "collect_history",
    "plan_sync",
    "execute_sync",
    "render_summary",
    "build_parser",
    "main",
]

#: sidecar 支持建树的扩展名（D5：其余跳过并计数）。
SUPPORTED_SUFFIXES = (".pdf", ".md", ".markdown")
#: MD 转换失败的错误前缀（§3.4，与建树失败区分）。
RENDER_ERROR_PREFIX = "render:"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2


# ---------------------------------------------------------------------------
# sidecar HTTP 客户端（错误分型镜像 libs/tree_search/http_backend.py）
# ---------------------------------------------------------------------------


class SidecarError(Exception):
    """sidecar 同步调用失败基类。"""


class SidecarTimeoutError(SidecarError):
    """sidecar 响应超时。"""


class SidecarBackendError(SidecarError):
    """连接失败 / 4xx / 5xx / ``success=false``。"""


class SidecarResponseError(SidecarError):
    """sidecar 返回非 JSON 或结构畸形。"""


class SidecarClient:
    """PageIndex sidecar 同步端点客户端（仅 /documents、/index、/index/delete）。

    Args:
        base_url: sidecar 基地址（配置校验已限定 loopback http）。
        api_token: Bearer token（``tree_search.enabled=true`` 时必填）。
        timeout_s: 单次请求超时（统一取 ``tree_search.index_timeout_s``——
            /index 含 MD 转换与建树，是最慢端点）。
        transport: 可注入 httpx transport（测试 MockTransport 用）。
    """

    def __init__(
        self,
        base_url: str,
        api_token: str | None,
        *,
        timeout_s: float,
        transport: Any | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = api_token
        self._timeout_s = timeout_s
        self._transport = transport

    def documents(self) -> list[dict]:
        data = self._request("GET", "/api/v1/documents")
        if not isinstance(data, list):
            raise SidecarResponseError("sidecar /documents data must be a list")
        return data

    def index(self, source_path: str, file_hash: str) -> dict:
        data = self._request(
            "POST", "/api/v1/index", json={"source_path": source_path, "file_hash": file_hash}
        )
        if not isinstance(data, dict):
            raise SidecarResponseError("sidecar /index data must be an object")
        return data

    def delete(self, source_path: str) -> bool:
        data = self._request(
            "POST", "/api/v1/index/delete", json={"source_path": source_path}
        )
        if not isinstance(data, dict) or not isinstance(data.get("removed"), bool):
            raise SidecarResponseError("sidecar /index/delete data must contain removed: bool")
        return data["removed"]

    def _request(self, method: str, path: str, *, json: dict | None = None) -> Any:
        import httpx

        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        try:
            with httpx.Client(
                transport=httpx.HTTPTransport() if self._transport is None else self._transport,
                timeout=self._timeout_s,
            ) as client:
                resp = client.request(method, f"{self._base_url}{path}", json=json, headers=headers)
        except httpx.TimeoutException as exc:
            raise SidecarTimeoutError(f"sidecar timeout after {self._timeout_s}s: {exc}") from exc
        except httpx.HTTPError as exc:
            raise SidecarBackendError(f"sidecar unreachable: {exc}") from exc

        if resp.status_code >= 400:
            raise SidecarBackendError(
                f"sidecar returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise SidecarResponseError(f"sidecar returned non-JSON body: {exc}") from exc
        if not isinstance(body, dict) or body.get("success") is not True:
            error = body.get("error", "unknown error") if isinstance(body, dict) else body
            raise SidecarBackendError(f"sidecar reported failure: {error}")
        return body.get("data")


# ---------------------------------------------------------------------------
# 纯函数：历史收集 → 计划 → 执行 → 汇总
# ---------------------------------------------------------------------------


@dataclass
class HistoryEntry:
    """同一物理文件（归一路径键）合并后的 success 记录。"""

    source_path: str
    file_hashes: set[str] = field(default_factory=set)
    collections: set[str] = field(default_factory=set)


@dataclass
class SyncAction:
    kind: str  # "upsert" | "delete"
    source_path: str
    file_hash: str | None = None
    #: 计划类别（upsert 专属："added" | "rebuilt"），失败时按原类别冲正计数。
    outcome: str = "added"


@dataclass
class SyncCounts:
    added: int = 0
    rebuilt: int = 0
    skipped: int = 0
    skipped_unsupported: int = 0
    deleted: int = 0
    convert_failed: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "added": self.added,
            "rebuilt": self.rebuilt,
            "skipped": self.skipped,
            "skipped_unsupported": self.skipped_unsupported,
            "deleted": self.deleted,
            "convert_failed": self.convert_failed,
            "failed": self.failed,
        }


@dataclass
class SyncPlan:
    upserts: list[SyncAction] = field(default_factory=list)
    deletes: list[SyncAction] = field(default_factory=list)
    #: 删除保护集：全量模式下 sidecar 文档不在此集合内才删除（归一路径）。
    expected: set[str] = field(default_factory=set)
    counts: SyncCounts = field(default_factory=SyncCounts)


def collect_history(
    records: Iterable[dict],
    *,
    collection: str | None = None,
    project_root: str | Path | None = None,
) -> dict[str, HistoryEntry]:
    """把历史表记录按归一路径去重为 ``{键: HistoryEntry}``（升序遍历确定性）。

    只取 ``status == "success"``；``source_path`` 非字符串或纯空白（v1 迁移
    占位）跳过；``collection`` 给定时只取该 collection 的记录。
    """
    entries: dict[str, HistoryEntry] = {}
    for record in records:
        if record.get("status") != "success":
            continue
        if collection is not None and record.get("collection") != collection:
            continue
        source_path = record.get("source_path")
        if not isinstance(source_path, str) or not source_path.strip():
            continue  # v1 迁移占位记录：跳过
        key = normalize_history_source_path(source_path, project_root)
        entry = entries.setdefault(key, HistoryEntry(source_path=key))
        if isinstance(record.get("file_hash"), str) and record["file_hash"]:
            entry.file_hashes.add(record["file_hash"])
        if isinstance(record.get("collection"), str) and record["collection"]:
            entry.collections.add(record["collection"])
    return entries


def plan_sync(
    entries: dict[str, HistoryEntry],
    sidecar_docs: list[dict],
    *,
    checker: Any,
    path_guard: PathGuard,
    full_mode: bool,
    project_root: str | Path | None = None,
) -> SyncPlan:
    """对比历史键与 sidecar ``/documents``，产出四类动作计划（不计删除外计数）。

    Args:
        checker: 提供 ``compute_sha256(path) -> str`` 的句柄（生产为
            ``SQLiteIntegrityChecker``；文件缺失抛 ``FileNotFoundError``）。
        path_guard: ``settings.tree_search`` 装配的白名单守卫。
        full_mode: 全量模式（无 ``--collection``）才产出删除动作。
    """
    plan = SyncPlan()
    sidecar_hashes: dict[str, str] = {}
    for doc in sidecar_docs:
        if not isinstance(doc, dict):
            continue
        source_path = doc.get("source_path")
        file_hash = doc.get("file_hash")
        if not isinstance(source_path, str) or not source_path.strip():
            continue
        sidecar_hashes[normalize_history_source_path(source_path, project_root)] = (
            file_hash if isinstance(file_hash, str) else ""
        )

    for key in sorted(entries):
        entry = entries[key]
        # 实算 SHA256 为准（同键多记录 hash 冲突 / 存量记录漂移统一覆盖）。
        try:
            actual_hash = checker.compute_sha256(key)
        except (FileNotFoundError, OSError):
            # 文件已不存在：按「历史中已不存在」处理（不进删除保护集，
            # 全量模式下其 sidecar 索引会被删除）。
            continue
        plan.expected.add(key)
        if not key.lower().endswith(SUPPORTED_SUFFIXES):
            plan.counts.skipped_unsupported += 1
            continue
        try:
            path_guard.validate_paths([key])
        except PathGuardError as exc:
            print(f"skip (outside whitelist): {key}: {exc}", file=sys.stderr)
            plan.counts.skipped += 1
            continue
        sidecar_hash = sidecar_hashes.get(key)
        if sidecar_hash is None:
            plan.upserts.append(SyncAction("upsert", key, actual_hash, outcome="added"))
            plan.counts.added += 1
        elif sidecar_hash == actual_hash:
            plan.counts.skipped += 1
        else:
            plan.upserts.append(SyncAction("upsert", key, actual_hash, outcome="rebuilt"))
            plan.counts.rebuilt += 1

    if full_mode:
        for key in sorted(sidecar_hashes):
            if key not in plan.expected:
                plan.deletes.append(SyncAction("delete", key))
                plan.counts.deleted += 1
    return plan


def execute_sync(plan: SyncPlan, client: Any, *, dry_run: bool) -> SyncCounts:
    """执行计划并产出最终计数；单文档失败不中断。

    ``--dry-run`` 只计数（计划值即最终值），不发任何写请求。失败时按原
    计划类别冲正计数（added / rebuilt / deleted → failed 或 convert_failed）。
    """
    counts = SyncCounts(
        added=plan.counts.added,
        rebuilt=plan.counts.rebuilt,
        skipped=plan.counts.skipped,
        skipped_unsupported=plan.counts.skipped_unsupported,
        deleted=plan.counts.deleted,
    )
    for action in plan.upserts:
        if dry_run:
            continue
        try:
            client.index(action.source_path, action.file_hash)
        except SidecarError as exc:
            setattr(counts, action.outcome, getattr(counts, action.outcome) - 1)
            if str(exc).startswith(RENDER_ERROR_PREFIX):
                counts.convert_failed += 1
            else:
                counts.failed += 1
            print(f"index failed: {action.source_path}: {exc}", file=sys.stderr)
    for action in plan.deletes:
        if dry_run:
            continue
        try:
            client.delete(action.source_path)
        except SidecarError as exc:
            counts.deleted -= 1
            counts.failed += 1
            print(f"delete failed: {action.source_path}: {exc}", file=sys.stderr)
    return counts


def render_summary(
    counts: SyncCounts,
    *,
    collection: str | None,
    dry_run: bool,
) -> str:
    mode = f"collection={collection}" if collection else "collection=all"
    state = " (dry-run)" if dry_run else ""
    lines = [
        "pageindex_sync: "
        + " ".join(f"{k}={v}" for k, v in counts.as_dict().items())
        + f" [{mode}{state}]"
    ]
    if collection and not dry_run:
        lines.append(
            "note: --collection mode never deletes; run without --collection "
            "(full sync) to clean up removed files."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reconcile PageIndex sidecar indexes with the ingestion history (PI4)."
    )
    parser.add_argument("--collection", default=None, help="只同步该 collection（不做删除）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不发写请求")
    parser.add_argument("--config", default=None, help="配置文件路径（缺省走 load_settings 默认）")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    settings: Any | None = None,
    checker: Any | None = None,
    history: Any | None = None,
    client_factory: Callable[[Any], Any] | None = None,
) -> int:
    """入口。注入参数供离线单测使用；生产路径零注入。

    退出码：0 = 无失败；1 = 存在 failed / convert_failed；2 = 用法 / 配置错误。
    """
    args = build_parser().parse_args(argv)

    if settings is None:
        from core.settings import load_settings

        settings = load_settings(args.config)
    tree = settings.tree_search
    if not tree.enabled:
        print(
            "pageindex_sync: tree_search.enabled is false; nothing to sync "
            "(enable it in config/settings.yaml first).",
            file=sys.stderr,
        )
        return EXIT_USAGE

    # 入口预建白名单根（AR1 先例；enabled=false 时为空操作，此处已提前返回）。
    ensure_local_roots(tree)
    try:
        path_guard = PathGuard(tree.allowed_roots, tree.denied_substrings)
    except PathGuardError as exc:
        print(f"pageindex_sync: invalid tree_search config: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if checker is None:
        checker = SQLiteIntegrityChecker()
    if history is None:
        history = checker
    if client_factory is None:
        client_factory = lambda cfg: SidecarClient(  # noqa: E731
            cfg.base_url, cfg.api_token, timeout_s=cfg.index_timeout_s
        )
    client = client_factory(tree)

    entries = collect_history(history.list_processed(), collection=args.collection)
    # 归一基准用缺省 resolve_project_root()（RA 契约：不按 CWD）。
    sidecar_docs = client.documents()
    plan = plan_sync(
        entries,
        sidecar_docs,
        checker=checker,
        path_guard=path_guard,
        full_mode=args.collection is None,
    )
    counts = execute_sync(plan, client, dry_run=args.dry_run)
    print(render_summary(counts, collection=args.collection, dry_run=args.dry_run))
    if counts.failed or counts.convert_failed:
        return EXIT_FAILURE
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    sys.exit(main())
