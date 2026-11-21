# Plan: [AC5] 同名上传 409 防护

Status: implemented

> 实测（2026-11-21）：新测试 7 项 + 邻接套件（ingestion_service /
> upload_source_cleanup / collection_paths / api_write）106 项全绿；全量
> 禁网 pytest 3012 passed / 23 skipped / 1 xfailed；红线脚本通过；Fix-Rounds: 0。
> 实现与计划一致，无偏离。

## Files
- `src/libs/agentic_search/collection_paths.py` — 提取公共 `normalize_history_source_path`
  （AC1/AC5「同一规则」：相对按项目根、realpath），AC1 `_normalize` 改为委托
- `src/observability/dashboard/services/ingestion_service.py` — `history` 注入 +
  `upload_conflicts()`（D4 三重判定）
- `src/api/routes/ingestion.py` — `_upload_lock` 串行「预检 + 落盘」+ 409
  `upload_name_in_use` + `responses` 声明
- `tests/unit/test_upload_name_guard.py` — 新文件（AR2 既有用例零改动）

## Order
1. `collection_paths.normalize_history_source_path(source_path, project_root=None)`：
   纯路径函数；`CollectionPathResolver._normalize` 委托之（validate_paths 随后
   仍自行 realpath，AC1 既有测试结果不变）。
2. `IngestionService`：`history` 构造参数（缺省懒构造 `SQLiteIntegrityChecker`，
   仅 `upload_conflicts` 触达）；`upload_conflicts(files)` 逐文件判定：
   dest 存在 + 任一 success 记录归一后 realpath 等于 dest realpath + 新内容
   sha256 ≠ 磁盘 sha256 → `{"name", "collections"}`；同哈希/孤儿放行。
   `collections` 为引用该 dest 的去重排序集合名。
3. 路由：`_upload_lock` 进程内锁包住「conflicts 预检 + save_uploads」
   （threadpool 内单函数执行，防并发同名同时过检）；冲突 →
   `ApiError(409, code="upload_name_in_use", details={"conflicts": [...]})`，
   消息列出占用 collection 并指引先删除；`responses` 补 409。
4. 测试五组（spec §4 AC5）+ 路由级 409 体断言（TestClient + `create_app`
   注入真实 IngestionService + fake history，对齐 `test_api_write.py` 先例）。

## Risks
- **save_uploads / UploadResponse / AR2 删除联动零改动**：只在落盘前加预检，
  `save_and_ingest`（非 POST /uploads 链路）不加防护（spec Out of Scope）。
- `IngestionService` → `libs.agentic_search.collection_paths` 新 import：
  纯路径函数模块，包 `__init__` 在 API 进程本就加载；不引 sirchmunk。
- 锁粒度：只串行化上传预检+落盘，不与摄取/删除互斥（spec 明示竞态不在范围）。

## Verification
- `pytest tests/unit/test_upload_name_guard.py tests/unit/test_ingestion_service.py
  tests/unit/test_upload_source_cleanup.py tests/integration/test_api_write.py
  tests/unit/test_collection_paths.py -o addopts="" -q` 全绿。
- 全量禁网 pytest + 红线脚本全绿。

## Deviations
（实现偏离时回填）
