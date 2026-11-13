# Agent Note: drift guard 对 .md 的断言翻转与哨兵迁移（而非删除）

Status: implemented

## Problem

IR5（M5 markitdown）交付时，drift guard 与基线守卫共三处断言 `.md` **显式不在**扩展名清单（当时是规格要求的 Out of Scope 锁定）。IR7.3 把 `.md`/`.markdown` 登记进双清单后，这些断言全部过期。同时，「不支持扩展名」的测试哨兵大量使用 `note.md` / `notes.md`——`.md` 合法化后这些用例的语义也失效了。

## Decision

**翻转与迁移，不删除断言**：

- 三处断言翻转为「两处清单相等 **且** 均含 `.md`/`.markdown`」：
  1. `tests/unit/test_ingest_entry.py::test_supported_extensions_matches_pipeline_routing`；
  2. `tests/unit/test_ingestion_loader_routing.py::test_dual_registration_includes_markitdown_and_markdown_extensions`（原 `..._excludes_markdown`）；
  3. `tests/unit/test_ingestion_robustness_baseline.py::test_snapshot_pdf_and_markdown_registered`（原 `..._markdown_excluded`，IR7.3 实施时发现的第四处守卫）。
- 「不支持扩展名」哨兵从 `note.md` / `notes.md` 迁移为 `.txt`（`test_ingest_entry.py` 两例、`test_ingest_sync.py`、`tests/e2e/test_data_ingestion.py`、`tests/e2e/test_ingest_cli_subprocess.py`），并把 `test_cli_rejects_single_markdown_file` 翻转为 `test_cli_accepts_single_markdown_file`。
- 一致性约束本身（两处清单必须相等）零放松。

## Alternatives considered

- **删除旧排除断言，只留通用一致性检查**：最强论据是断言数最少。被否：排除断言翻转后仍锁定「清单必须显式含 `.md`」，防止未来有人顺手把 `.md` 挤出清单而无测试报警；通用一致性检查只锁相等，不锁内容。
- **哨兵保持 `.md` 并把用例改为「应成功」**：最强论据是少改文件。被否：「不支持扩展名报错」这条路径本身仍需测试覆盖（`usage 报错` 是既有契约），必须保留一个真正不支持的扩展名当哨兵。

## Consequences

- **收益**：双登记纪律在 `.md` 转正后依然由测试锁死；未来再新增格式（如 `.py` Code Loader，见路线图 6.8）时，本 Note 记录了完整操作面：3 处断言 + 5 个文件的哨兵迁移。
- **代价**：每次新增格式都要重演「翻转 + 迁哨兵」的连锁修改（本次涉及 5 个测试文件）；未参数化，因为格式新增低频、参数化会让哨兵语义变隐晦。
- **已知事实**：`.txt` 哨兵是当前唯一的「非法扩展名」代表；若未来纯文本也支持，需再迁移哨兵。

## Verification

- `pytest tests/unit/test_ingest_entry.py tests/unit/test_ingestion_loader_routing.py tests/unit/test_ingest_sync.py tests/unit/test_ingestion_robustness_baseline.py -q` 全绿（IR7.3 收口时 147 passed 含路由用例）。
- 过滤语义本身的取舍见 [source_filters 过滤先于扩展名筛选](../feature/2026-10-07-source-filters-before-extension.md)。
