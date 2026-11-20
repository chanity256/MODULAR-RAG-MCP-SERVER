# Plan: [RA3] 数据层相对路径按项目根（D2-b 全量）

Status: implemented

## Deviations

- `tasks_db` / `session_db` / `static_dir` 未在 `load_settings` 规范化（偏离 D3-a 字面）：改为唯一使用点锚定（app.py / chat_wiring.py / static.py），避免 5 处 dataclass 整体等值断言破裂；落地行为等价。`test_api_security:141` / `test_chat_settings:49` 原始串断言与两处常量断言因此**无需改写**（计划原列改写，实际未动）。
- `resolve_data_path` 对 sqlite `:memory:` 哨兵原样返回（ingest CLI dry-run 传 `:memory:` 作历史库，锚定会生成名为 `:memory:` 的真实文件）。
- `tests/e2e/test_ingest_cli_subprocess.py` / `test_markdown_vault_query.py` 的查询 CLI 子进程需注入 `RAG_PROJECT_ROOT`（计划漏列：这两个文件依赖 CWD 相对的缺省存储，锚定后子进程会读写真实仓库 `data/`，实测全量时暴露）。

## Files

**新增 helper**
1. `src/core/project_root.py` — 增加 `resolve_data_path(path)`：相对路径按项目根锚定，绝对路径原样（`~` 展开，幂等）。

**Settings 层（load_settings 内锚定）**
2. `src/core/settings.py` — `_build_vector_store` 的 `persist_path` 经 `resolve_data_path` 锚定。

**使用点锚定（单消费者字段，避免 dataclass 等值断言破坏）**
3. `src/api/app.py:77` — `TaskManager(resolve_data_path(api_settings.tasks_db))`。
4. `src/api/chat_wiring.py:100` — `SessionStore(resolve_data_path(...session_db))`。
5. `src/api/static.py` — `resolve_static_dir` 用 `resolve_data_path`（去 `Path.cwd()` 拼接）。
6. `src/observability/dashboard/services/ingestion_service.py` — 默认与显式 `upload_dir` 经 `resolve_data_path`。
7. `src/observability/dashboard/services/data_service.py` — 同上。
8. `src/ingestion/storage/bm25_indexer.py` — `index_path` 经 `resolve_data_path`。
9. `src/libs/loader/file_integrity.py` — `db_path` 经 `resolve_data_path`（常量 `DEFAULT_DB_PATH` 保持原样）。
10. `src/ingestion/storage/image_storage.py` — `images_root` / `db_path` 经 `resolve_data_path`。
11. `src/observability/log_files.py` — `resolve_log_path` 未设 `RAG_LOG_ROOT` 时的回退基准 CWD → 项目根（`RAG_LOG_ROOT` 优先级不变）。

**测试改写（已批准 + §3 规格 mandate）**
12. `tests/unit/test_ingestion_service.py::test_default_upload_dir` — 断言锚定后绝对路径。
13. `tests/unit/test_upload_source_cleanup.py::test_default_upload_dir_is_data_uploads` — 注入 `RAG_PROJECT_ROOT`。
14. `tests/unit/test_sparse_retriever.py::test_default_indexer_loads_persisted_file` — 注入 `RAG_PROJECT_ROOT`。
15. `tests/integration/test_api_static.py::test_relative_static_dir_resolves_against_cwd` — 改写为项目根锚定（RA0 表原判「不改写」有误：D2-b 明确含 static_dir，非 Out of Scope 的 CLI 用户输入）。
16. `tests/unit/test_log_files.py::test_without_log_root_falls_back_to_cwd_relative` / `test_blank_log_root_is_ignored` — 回退断言改项目根（§3 明确要求，未在 RA0 chdir 清单中，按规格 mandate 改写）。

**测试新增**
17. `tests/unit/test_project_root.py` — `resolve_data_path` 用例（相对锚定 / 绝对原样 / `~` / 幂等）。
18. `tests/unit/test_config_loading.py` — `persist_path` 规范化用例（相对锚定 + env 覆盖 / 绝对原样）。
19. `tests/unit/test_agentic_local_roots.py` — 入口组新增 RA3 用例：fixture 项目根下配置的 persist_path 落在项目根、CWD 目录不新建 `data/`。

## Order

helper → settings → 各使用点 → 测试改写/新增 → 全量跑测。

## Risks

- **偏离 D3-a 字面（已识别并接受）**：`tasks_db` / `session_db` / `static_dir` 不在 load_settings 规范化，而在唯一使用点锚定。原因：`test_api_security:153` / `test_chat_settings:41,57,77,135` 共 5 处 dataclass 整体等值断言（`load_settings(...).api == ApiSettings()` 等）会因字段绝对化而破裂；两字段各只有 1 个消费者（app.py:77 / chat_wiring.py:100），使用点锚定落地行为等价、零等值断言破坏。偏差记入本计划与规格 RA3 行。
- **显式相对参数语义变化**：`BM25Indexer/SQLiteIntegrityChecker/ImageStorage/IngestionService/DataService` 的显式相对路径参数现在也锚定项目根（内部 API，非 CLI 用户输入）；传裸文件名的测试可能落在仓库根——靠全量跑测排查。
- **常量断言不改写**：`test_file_integrity:155` / `test_image_storage:337-338` 断言模块常量本身，常量保持原样（使用点锚定），两测试继续通过；RA0 批准改写清单中这两项实际无需动。
- **`test_api_security:141` / `test_chat_settings:49`**（原始字符串断言）因改为使用点锚定而继续通过，无需改写。
- 红线：不 import sirchmunk、stdout 无输出、离线确定性、`create_app` 零 IO 语义不变（锚定是纯计算）。

## Verification

- `pytest tests/unit/test_project_root.py tests/unit/test_config_loading.py tests/unit/test_log_files.py tests/unit/test_ingestion_service.py tests/unit/test_upload_source_cleanup.py tests/unit/test_sparse_retriever.py tests/unit/test_image_storage.py tests/unit/test_file_integrity.py tests/integration/test_api_static.py tests/unit/test_agentic_local_roots.py -q` 全绿。
- 全量 `pytest tests -o addopts="" -q --disable-socket --allow-unix-socket --allow-hosts=127.0.0.1,localhost,::1` 全绿（基线 2956 / 23 / 1）。
- `python scripts/check_redlines.py` OK。

## Deviations

（实现偏离时回填）
