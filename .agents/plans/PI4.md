# Plan: [PI4] scripts/pageindex_sync.py 对账同步

Status: planned

## Scope

摄取历史 v2 success 记录 → PageIndex sidecar 索引的幂等对账（规格 §3.2「索引同步」/ §3.4 契约）。
不改摄取管线、DocumentManager、`src/` 任何模块；sidecar 侧（server / pi_adapter）不动。
门控部分（对真实 sidecar 跑两次、第二次新增 / 重建为 0）不在本次离线交付内，需用户确认后执行。

## Files

新建：

1. `scripts/pageindex_sync.py` — 对账同步 CLI（主 venv，不 import pageindex / litellm）：
   - `SidecarClient(base_url, api_token, *, timeout_s, transport=None)`：`GET /api/v1/documents`、
     `POST /api/v1/index {source_path, file_hash}`、`POST /api/v1/index/delete {source_path}`；
     Bearer 头；错误分型镜像 `libs/tree_search/http_backend.py`：超时 → `SidecarTimeoutError`、
     连接失败 / 4xx / 5xx / `success=false` → `SidecarBackendError`、非 JSON →
     `SidecarResponseError`；httpx 懒加载 + transport 可注入（MockTransport）。
   - `collect_history(records, *, collection=None) -> dict[str, HistoryEntry]`：
     只取 `status == "success"`、`source_path` 非空（v1 占位跳过）；经
     `normalize_history_source_path` 归一后按路径去重，合并 file_hash 集合与 collection 集合。
   - `plan_sync(entries, sidecar_docs, *, checker, path_guard, full_mode) -> SyncPlan`：
     按归一键排序确定性遍历——扩展名不在 {`.pdf`, `.md`, `.markdown`} → `skipped_unsupported`；
     `checker.compute_sha256` 实算（FileNotFoundError/OSError → 视为「历史中已不存在」，
     从删除保护集剔除）；`PathGuard.validate_paths` 逐条校验（越权 → `skipped`）；
     对比 sidecar `/documents`（同样归一）：未索引 → 新增、hash 一致 → 跳过、hash 不同 → 重建；
     全量模式另产出删除动作（sidecar 文档不在保护集内）；`--collection` 模式零删除动作。
   - `execute_sync(plan, client, *, dry_run) -> SyncCounts`：dry-run 只计数不发请求；
     单文档失败不中断；`render:` 前缀错误单列 `convert_failed`（MD 转换失败，D5）。
   - `main(argv, *, settings=None, checker=None, history=None, client_factory=None)`：
     `load_settings()` → `tree_search.enabled` 为假 → 退出码 2；`ensure_local_roots(tree)`
     （AR1 先例）；PathGuard 构造失败 → 退出码 2；退出码 1 = 存在 failed / convert_failed。
2. `tests/unit/test_pageindex_sync.py` — 离线单测（fake history / fake checker / fake client，
   零网络零真实 sidecar）。

## Order

plan → 脚本（client → 纯函数 → main）→ 单测 → 全量回归 + redlines → 规格回填。

## Risks

- **既有测试语义**：不改任何现有文件；`libs/agentic_search` / `libs/tree_search` 契约不动。
- **红线**：脚本不 import pageindex / litellm；`check_redlines.py` 须保持 0 违规。
- **删除安全**：删除只在全量模式执行且只删「归一后不在任何 collection success 记录」的
  sidecar 文档； vanished 文件（实算失败）按「历史中已不存在」处理（规格明文）。
- **determinism**：遍历按归一路径排序；同一物理文件多 collection 记录只建一份索引。

## Verification

- `pytest tests/unit/test_pageindex_sync.py -q` 全绿（覆盖四类动作、--dry-run、单条失败不中断、
  双 collection 去重、hash 冲突实算优先、collection 模式零删除、入口 ensure_local_roots、
  tree_search.enabled=false 退出码 2、`sys.modules` 无 pageindex / litellm）。
- `pytest tests -o addopts="" -q` 全量不回退；`python scripts/check_redlines.py` 0 违规。
- 门控（待用户确认）：真实 sidecar 跑两次 `pageindex_sync.py`，第二次新增 / 重建为 0。
