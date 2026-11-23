# Plan: [PI1.1] PI1 代码审查修复（规格 10.9 #1–#26）

Status: done（2026-11-23；修复记录落盘规格 10.9.1，单测 79 例全绿）

## 任务来源

`docs/specs/PAGEINDEX_TREE_RETRIEVAL_SPEC.md` §10.9（commit `2e39a95` 的代码审查发现）+
第 5 章 PI1.1 行。目标：P1（#1–#2）与 P2（#3–#13）全部修复；P3（#14–#26）逐条修复或记不修
理由；#13 契约偏离逐项二选一；每项修复配「修复前会失败」的回归单测。

## 修复决策（逐条）

### heading_index.py

- **#7 ATX 闭合井号**：`ATX_RE` 改懒惰标题 + 可选闭合序列 `(?:\s+#+\s*)?`（CommonMark：
  闭合序列前须有空白、后只允许空白；`C#` 不受影响）。
- **#6 消歧后仍歧义**：父路径尾部对齐改为「最长匹配优先」——对每个候选计算匹配的后缀
  长度 n，取 n 最大者；并列 >1 → 返回 `[]`（`None`），不再取 `cands[0]`。
- **#22 Setext 误判**：下划线行只在其上一行是「段落行」时生效（非空、非围栏定界符、
  非 ATX、非列表项 `[-*+]|\d+[.)]`、非引用 `>`）；围栏状态机照旧跳过围栏内行。
- **#23 docstring**：Setext `line` 实际记标题行（下划线行的上一行，1 起计），修正说明。
- **#21（heading_index 侧）**：解析前 `lstrip("\ufeff")`，BOM 文件不再丢首个标题。

### md_render.py

- **#1 raw HTML / JS（P1）**：pandoc 改 `-f gfm-raw_html`（禁 raw HTML）+ `--standalone
  --embed-resources --resource-path=<md 目录>`（顺带修 #24 相对图片内嵌）；暂存 HTML 的
  `<head>` 注入严格 CSP `<meta>`（`default-src 'none'; style-src 'unsafe-inline';
  img-src data:`）+ 打印 CSS。单测断言 pandoc 参数与暂存 HTML 含 CSP。
- **#2 测试依赖宿主 PATH（P1）**：`test_chromium_missing_raises_tool_missing` 补
  `monkeypatch.setenv("PATH", str(tmp_path))`（与 sibling 同法）。
- **#3 归一化一致**：`_norm_page_text` 委托 `heading_index.normalize_title`（部件形 +
  强调符 + 去空白），键与页文本两侧同一函数。
- **#4 跨页截断标题错映**：两段式合并为单页循环：逐页先查全页精确，再查该页拼接窗口
  （且 `key` 不在 `p+1` 全页时才记起始页 `p`）；修 docstring。
- **#5 书签父节点错挂**：父节点取栈中最近的更浅层级（level-1 向下扫描）；映射 miss 的
  标题清除栈中该层级及更深的条目；处理后清除更深条目。
- **#11 sidecar venv 依赖**：`setup_venv.sh` 增装 `pypdf` / `pypdfium2`；`render()` 开头
  preflight `import pypdf`（缺失 → `RenderToolMissing`）；`_default_page_texts` 的
  pypdfium2 导入失败同样转 `RenderToolMissing`。
- **#21（md_render 侧）**：`read_text(encoding="utf-8-sig")`。
- **#25 file URL**：`html_path.resolve().as_uri()`。
- **#26 错误映射重复**：`_run_step` 改为返回 `proc`，pandoc 步骤复用（pandoc 的
  FileNotFoundError / TimeoutExpired / 非零退出三类映射不再内联复制）。
- **#13④**：`pandoc_bin=None` 默认改为读 `PAGEINDEX_PANDOC` env（对齐 env.example / 3.3）。

### tree_loop.py

- **#8 并行预算超支**：`ask()` 在锁内「检查 + 预留 estimate」，`complete()` 返回后在锁内
  冲正（reserved −= estimate，tokens += actual）；异常路径 `finally` 释放预留。残余限制
  （actual > estimate 时仍可能小幅超支，为预估口径固有）记入 10.9 #8 行。
- **#9 最后一轮下钻丢证据**：下钻决策加「还有剩余轮次」门（`_loop + 1 < max_loops`）；
  无剩余轮次时不移出 `new_reads`，落入读取路径由 #10 的截断逻辑处理。锁定旧行为的
  `test_long_node_drills_down_instead_of_reading` 断言随修复重写（10.9 #9 行记录）。
- **#10 max_evidence_chars 生效**：下钻由原「node_span 400 行预筛（仅 MD）」改为
  **读取后按 `len(text) > max_evidence_chars` 判定**：有子节点且有余轮 → 撤销该证据
  （selected/rank 回退）、子节点入 frontier（telemetry `oversize_drilled`）；否则截断到
  `max_evidence_chars` 并标 `truncated`（telemetry `truncated_evidence` 既有键）。
  `node_span` 参数与 400 行阈值删除（API 变更，Deviations 记录）。
- **#14 预算截停 ≠ invalid_output**：`ask()` 仅在预算截停时返回 `None`；`ask_json` 见
  `None` 直接返回，不再记 `invalid_output`。
- **#15 schema 校验**：`ask_json(prompt, required_keys)` 要求 JSON dict 至少含一个预期
  键（doc_ids / keep / read+expand / sufficient），否则视同非法输出（重试一次 → 计
  `invalid_output`）。
- **#16 判足够浪费调用**：补选逻辑**不实现**（维持 PI0.6 实测形态，3.2 回填为已知取舍，
  见 #13②）；「无法改变结果的调用」修复——sufficiency 仅在「有证据 ∧ frontier 非空 ∧
  非最后一轮」时调用（锁定旧行为的 `test_single_doc_select_read_sufficient` 断言随修复
  更新，10.9 #16 行记录）。
- **#17 选中 id 解析域**：`select_for_doc` 的 `by_id` 只由本轮展示的 `nodes`（预筛后）
  构建，模型引用未展示节点一律忽略；预筛步骤本就如此，两步一致。
- **#18 doc_ids 去重**：`ids = list(dict.fromkeys(ids))` 保序去重后再映射。
- **#19 Callable 导入**：`from collections.abc import Callable`。

### tree_store.py

- **#12 损坏文件静默清空**：加载失败时先把原文件 `os.replace` 为 `<name>.corrupt`
  留存（保留证据、防下次 upsert 直接覆盖），再按空库继续。锁定旧行为的
  `test_corrupt_file_treated_as_empty` 重写为断言 `.corrupt` 留存（10.9 #12 行记录）。
- **#20 浅拷贝**：读出（get/remove/list_all/find_by_hash/upsert 返回值）与写入
  （upsert 入库）均 `copy.deepcopy`。

### #13 契约偏离处置（逐项二选一）

| 项 | 处置 |
|----|------|
| ① `complete(prompt) -> {"text","tokens"}` | **回填规格**：D7 与 3.1 架构图改为该签名（token 计数是 telemetry / 预算的必要输入，`-> str` 无法承载） |
| ② 选节点 JSON `read`/`expand`（3.2 写 `node_ids`） | **回填规格**：3.2 第 2 步改为实测形态（read=读原文、expand=下钻，PI0.6 口径）；同处记 #16 的补选取舍 |
| ③ `render` 无 `outline_mode`、命名由调用方 | **回填规格**：3.2 删 `outline_mode`（D11 终裁唯一路径 = v3 pypdf 注入）；`<storage>/derived/<md_sha256>.pdf` 命名与原子落位归 PI2 `server.py`（它持有 PAGEINDEX_STORAGE） |
| ④ `PAGEINDEX_PANDOC` 未读 | **改代码**：pandoc_bin 默认读 env |
| ⑤ heading_index API 与 3.2 不符 | **回填规格**：3.2 写实际 API `build_heading_index(md_text) -> list[dict]`（level/title/line/parents）+ `HeadingIndex(md_text).locate()` |
| ⑥ §4 PI1 用例缺口 | **改代码/补测**：浏览器探测优先级补 3 例；`<md_sha256>.pdf` 命名随 ③ 归 PI2（§4 对应句同步改）；`get_page_content` 漂移回退 / `page_fallback` 属注入的 `read_node`（PI2 `pi_adapter` 职责），§4 该句标注 PI2 覆盖 |

## Files

修改：

1. `scripts/pageindex/heading_index.py`（#6 #7 #21 #22 #23）
2. `scripts/pageindex/md_render.py`（#1 #3 #4 #5 #11 #13④ #21 #24 #25 #26）
3. `scripts/pageindex/tree_loop.py`（#8 #9 #10 #14 #15 #16(后半) #17 #18 #19）
4. `scripts/pageindex/tree_store.py`（#12 #20）
5. `scripts/pageindex/setup_venv.sh`（#11）
6. `tests/unit/test_pageindex_heading_index.py`（新增 #6/#7/#21/#22 回归）
7. `tests/unit/test_pageindex_md_render.py`（#2 PATH、pandoc 参数/CSP、探测优先级、
   页映射归一化与跨页截断、书签父节点、BOM 渲染）
8. `tests/unit/test_pageindex_tree_loop.py`（预算预留并行、去重、解析域、schema、
   下钻/截断重写、sufficiency 门）
9. `tests/unit/test_pageindex_tree_store.py`（损坏文件留存、深拷贝）
10. `docs/specs/PAGEINDEX_TREE_RETRIEVAL_SPEC.md`（10.9 修复记录、#13 回填 3.2/D7/§4、
    PI1.1 行回填、版本 1.3）
11. `docs/MOC.md` / `README.md`（v1.3 状态同步）

新建：`.agents/plans/PI1.1.md`（本文件）。

## Order

heading_index → tree_store → md_render → tree_loop → 四个测试文件 → 真实 pandoc+Chrome
复跑 #1 样例（验收④，本机工具、零 LLM token）→ 规格回填 → sync → 全量回归 + redlines。

## Risks

- **既有测试断言更新（规格许可）**：`test_long_node_drills_down_instead_of_reading`、
  `test_corrupt_file_treated_as_empty`、`test_single_doc_select_read_sufficient`（call 数）、
  `test_command_construction`（pandoc 参数）、`test_chromium_missing_raises_tool_missing`
  （PATH）——均为修复所要求的语义修正，10.9 对应行记录。
- **`gfm-raw_html` 兼容性**：本机 pandoc 3.11 实测验证；fake runner 测试只断言参数。
- **test_guard hook**：fix_mode 标记在 Test 步建立，标记存在期间不改既有测试；预计划的
  测试修改全部在 Implement 阶段完成。
- **行为漂移**：树检索循环的选文档 / 选节点 / 判足够 prompt 与 telemetry 键名保持 POC
  口径，只改预算记账与解析域，不改 prompt 文案。

## Verification

- `pytest tests/unit/test_pageindex_*.py -q` 全绿（新增回归每项修复前失败）。
- 真实 pandoc + Chrome 复跑：iframe(file://) + `<script>` 样例派生 PDF 页文本不含
  secret 内容与脚本输出（验收④）。
- `pytest tests -q`（CI 禁网等价命令）全绿；`python scripts/check_redlines.py` 0 违规。
- `ruff check scripts/pageindex tests/unit/test_pageindex_*.py` 无 F821（#19）。

## Deviations

1. **`tree_loop.search` 删除 `node_span` 参数**：下钻 / 截断判定统一改为「读取后按
   `max_evidence_chars`」（#10），原型的 400 行预筛删除；`MAX_EVIDENCE_CHARS` 语义不变。
   规格本就按 `max_evidence_chars` 表述（3.2 第 3 步），非偏离，仅 API 收敛。
2. **sufficiency 调用门**（#16）：仅在「有证据 ∧ frontier 非空 ∧ 非最后一轮」时调用；
   「判不足后补选」维持原型口径（仅沿展开子节点 frontier），在 3.2 回填为已知取舍。
3. **预算预留的残余限制**（#8）：actual tokens 超出 estimate 的部分在并行窗口内仍可能
   超支，为「按预估 prompt token 检查」口径的固有局限，10.9 #8 行记录。
4. **#19 落点变更**：`node_span` 删除后 `Callable` 注解不复存在，原方向「补
   `from collections.abc import Callable`」改为「删除未用导入」（ruff F821 随之消失）。
