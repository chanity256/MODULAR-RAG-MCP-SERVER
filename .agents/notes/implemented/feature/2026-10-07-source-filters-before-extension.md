# Agent Note: source_filters 过滤先于扩展名筛选（filtered 计数口径）

Status: implemented

## Problem

IR7.4 给目录扫描加 include/exclude glob 过滤（`ingestion.source_filters`）时，有一个顺序问题规格没有明说：`collect_files` 先按支持扩展名筛、再应用过滤，还是先过滤、再筛扩展名？这决定 `filtered` 计数里包不包含「被目录规则排除的不支持格式文件」，而 IR7.4 的验收标准锁死了 vault 场景 `filtered==2`——其中 `.obsidian/app.json` 的扩展名本就不受支持。

## Decision

**过滤先于扩展名筛选**，在 `scripts/ingest.py` 的 `collect_files_detailed()` 中执行：

- 匹配语义：对扫描根目录的 POSIX 相对路径做 `fnmatch`（目录段参与匹配，`.obsidian/attachments/x.png` 命中 `.obsidian/**`）；include 空 = 全部受理；include 与 exclude 同时命中时 **exclude 优先**。
- `filtered` 计数含**任意扩展名**的被排除文件；被过滤文件不产生逐文件行，汇总块输出 `filtered: N (excluded by source_filters)`（恒输出，含 N=0）。
- **显式单文件 `--path` 不过滤**（用户明确意图；扩展名不支持时维持既有 usage 报错）。
- **`--sync` / `--prune` 同一套语义**：被过滤路径视为不存在（不摄取）；已入库但被过滤且仍在磁盘的路径，经 `_filtered_source` 走既有 deleted 语义清理，不再触发「exists but not scanned」警告。
- 配置校验 fail-fast：非 list、元素非非空字符串、空 glob 串拒绝；空白串（strip 后为空）与空串同判，比规格略严。

## Alternatives considered

- **先扩展名后过滤**：最强论据是「不支持格式本来就不会摄取，先筛掉可以少跑匹配」。被否：`.obsidian/app.json` 不会进 filtered 计数，vault 场景 filtered==1 ≠ 验收标准的 2；且用户看到「某目录没进来」却在汇总里找不到解释。
- **`--sync` 不应用过滤（只影响普通摄取）**：最强论据是 sync 的 diff 基准是历史表全量。被否：被过滤文件会被 sync 当作新增重新摄取，直接违背配置意图；过滤必须在「目录 vs 存储态」对比前收窄目录侧。
- **被过滤的已入库路径只告警不清理**：最强论据是删除动作危险。被否：清理复用 `delete_document` 单一实现（IR2/IR3 已有幂等语义），且 `--prune` 本就是显式清理通道；只告警会让「排除目录后存储不减」成为静默漂移。
- **filtered 行仅 N>0 时输出**：最强论据是输出干净。被否：恒输出让「过滤生效且为 0」可验证，避免用户怀疑配置没读进去；已确认不破坏既有断言。

## Consequences

- **收益**：vault/Obsidian 场景的过滤行为可验收、可诊断；sync 与普通摄取共享同一过滤判定，不会出现「同一配置两种目录视图」。
- **代价**：`--sync` 的 plan 输出是计划形态而非 `IngestSummary`，sync 模式下 filtered 计数不单独展示（被过滤路径从计划中直接消失，dry-run 计划即过滤后清单）。
- **已知缺口**：`--sync` 在「注入 pipeline 且不注入 settings」的测试装配路径下不应用过滤（生产 CLI 无影响），见 [sync 注入路径的过滤回退提案](../../proposed/process/2026-10-07-sync-filter-fallback-injected-pipeline.md)。

## Verification

- `tests/unit/test_ingest_source_filters.py`（31 例）：pattern×path 参数化矩阵、exclude 优先、默认排除经真实 Settings 验证、显式单文件绕过、`--sync` 不摄取/清理被过滤路径、`--dry-run` 零写入。
- `tests/unit/test_ingestion_config.py`（31 例）：配置解析与 fail-fast 矩阵。
- 集成：`tests/fixtures/vault/` 过滤后 `filtered==2`，`.obsidian/`、`templates/` 未入库。
