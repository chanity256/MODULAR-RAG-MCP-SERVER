# Plan: [AR2] 删除文档时同步删除上传源文件

Status: implemented

## Deviations

- 无设计偏离。既有精确断言用例 `test_delete_returns_counts` 需补新字段（规格验收要求响应新增 `source_file_removed`，与全字段精确断言二取一），经用户确认后在期望 dict 中补 `"source_file_removed": False`，既有字段与计数断言全部保留。Fix-Rounds: 1。

## Files

1. `src/ingestion/document_manager.py` — `DeleteResult` 增加 `source_file_removed` 字段（只增不改）；新增 `source_referenced(source_path)`（Chroma 中是否仍有任一 collection 携带该 source_path）
2. `src/observability/dashboard/services/data_service.py` — `DataService` 增加 `upload_dir` 注入点（缺省 `DEFAULT_UPLOAD_DIR`）；`delete_document` 在四存储删除完成后联动清理
3. `src/api/schemas/tasks.py` — `DeleteDocumentResponse` 增加 `source_file_removed: bool = False`（路由经 `to_dict` 透传，无需改动）
4. `tests/unit/test_upload_source_cleanup.py`（新建）— §4 删除一致性三场景 + DeleteResult 字段透传

## Order

先 `document_manager.py`（字段 + 查询方法，纯新增），再 `data_service.py` 联动逻辑，再 schema，最后测试。

## Design

- 联动放在**服务层**（spec §3）：`DataService.delete_document` 拿到四存储结果后执行 `_remove_upload_source_file(result)`。
- 门禁顺序：四存储**有实际删除**才考虑删文件（无删除时路由按 404 拒绝，不动文件）→ `source_referenced` 为 False（同 source_path 在其他 collection 已无 Chroma 记录）→ resolved 路径确实落在 `upload_dir` 之内（resolve 后比较，`data/documents` 等 CLI 摄取文件不动）→ `unlink` 成功 → `source_file_removed=True`。
- 文件删除失败只记 warning、字段如实返回 False，不回滚四存储删除（spec §3）。
- `DeleteResult.to_dict` 带出新字段；`DeleteDocumentResponse(**payload)` 透传；既有删除用例的字段与计数口径不变（只增不改）。

## Risks

- **既有测试语义**：`test_api_write.py` 删除用例响应字段只增不改；`DeleteDocumentResponse` 默认值 False 不影响既有断言。
- **服务层分层**：只复用既有依赖（ingestion.document_manager 已是 DataService 依赖；`DEFAULT_UPLOAD_DIR` 同包 import），不碰 Pipeline。
- **误删面**：三重门禁（有删除 + 无引用 + 在上传目录内），最坏情况退化为现状（文件保留）。

## Verification

- `pytest tests/unit/test_upload_source_cleanup.py tests/unit/test_document_manager.py tests/unit/test_dashboard_data_service.py -q`（若后者存在）+ `tests/integration/test_api_write.py`
- 全量相关面：`pytest tests/unit tests/integration -q -o addopts=""`
- `python scripts/check_redlines.py`；Fix-Rounds 记入 commit trailer。

## Deviations
