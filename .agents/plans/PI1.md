# Plan: [PI1] sidecar 四模块 heading_index / md_render / tree_store / tree_loop

Status: implemented

## Files

新建（均 `scripts/pageindex/`，纯模块除 md_render 的子进程/pypdf 外零三方依赖）：

1. `scripts/pageindex/heading_index.py` — 纯标准库：`build_heading_index(md_text)`（ATX + Setext，跳过围栏代码块与 front matter、空标题），`normalize_title()`（NFKC + 强调符剔除 + CJK 部件形映射，承接 POC `RADICAL_FORM_MAP`），`HeadingIndex.locate(title, parent_path)`（精确归一化匹配 → 父路径消歧 → 包含关系模糊兜底 → `None`；`Preface` / `<parent> (intro)` 合成节点规则化映射）。
2. `scripts/pageindex/tree_store.py` — 纯标准库：`TreeStore`（JSON 文件、原子写 tmp+replace、`threading.Lock` 单进程锁）；记录 `{source_path, file_hash, format, doc_id, derived_pdf, indexed_at, node_count, headings?}`；`get / upsert / remove（返回被删记录供调用方删派生 PDF）/ list_all / find_by_hash`。
3. `scripts/pageindex/md_render.py` — `render(md_path, out_pdf, *, runner, page_texts_fn, pandoc="pandoc", timeout_s) -> RenderResult(pages, elapsed_ms, bookmarks, map_misses)`：打印 CSS HTML 模板、pandoc + Chromium 命令按列表构造（不经 shell；Chromium 强制 `--headless=new` / `--disable-gpu` / 独立临时 `--user-data-dir`，探测 `PAGEINDEX_CHROMIUM` > 系统 Chrome/Chromium）、页映射（pypdfium2 懒加载、可注入）、Bookmarks 注入（pypdf，主 venv 已声明依赖）、产物 tmp + 原子 rename；错误分型 `RenderToolMissing` / `RenderTimeout` / `RenderFailed`；不 import pageindex / litellm。
4. `scripts/pageindex/tree_loop.py` — 纯标准库：`search(query, docs, *, complete, read_node, max_nodes, max_loops, max_token_budget) -> LoopResult`，从 POC `TreeSearcher`（poc_compare.py，PI0.6 实测 hit 0.64 的形态）忠实移植：选文档 → 选节点（单层预筛 >25→≤15、多篇并行）→ 读原文 → 判足够闭环；`max_nodes` 硬截断、每次 LLM 调用前预估预算检查、同节点去重、非法 JSON 重试一次后降级（telemetry `invalid_output`）、`score = 1/rank`。

测试（主 venv 离线，零网络零真实工具）：

5. `tests/unit/test_pageindex_heading_index.py`
6. `tests/unit/test_pageindex_md_render.py`（fake runner + 注入 page_texts_fn + pypdf 生成最小 PDF 断言 Outline 注入与原子改名；断言 `sys.modules` 无 pageindex / litellm）
7. `tests/unit/test_pageindex_tree_store.py`
8. `tests/unit/test_pageindex_tree_loop.py`（fake complete / fake read_node）

## Order

heading_index（被其余模块依赖）→ tree_store → md_render → tree_loop → 测试随各模块同步写、每模块落地即跑该模块测试。

## Risks

- **契约锁定**：证据 `source_path` 永不为派生 PDF 路径（10.3 ⑤）由 tree_loop 回映射签名保证；`md_render` 不 import pageindex / litellm 用单测断言 `sys.modules` 锁定。
- **行为漂移**：tree_loop 移植须保持 POC 判足够 prompt 语义与 telemetry 键名（PI0.6 复测口径依赖），移植后用 POC 同构 fake 场景断言。
- **主 venv 纯净性**：pypdf 是声明依赖可直接用；pypdfium2 未声明 → 仅懒加载 + 注入点，测试不触碰。
- **既有测试语义**：不改动任何现有测试；`scripts/check_redlines.py` 不受影响（本任务不改红线规则，PI2 才加 no-pageindex-import）。

## Verification

- `pytest tests/unit/test_pageindex_*.py -q` 全绿（四文件预计 40+ 用例，覆盖 §4 测试方案列出的全部 PI1 用例：ATX/Setext/围栏/front matter/合成节点/消歧/模糊兜底；命令行列表构造与三类错误分型/原子改名/Outline 注入；原子写/幂等/删除返回；选文档/选节点/去重/截断/闭环/预算前置/预筛/非法 JSON 降级/回映射）。
- `pytest tests -o addopts="" -q` 全量离线回归不回退（≥3012 passed 基线 + 新增）。
- `python scripts/check_redlines.py` 0 违规。

## Deviations

1. **`map_headings_to_pages` 语义修正**（偏离原型）：原型的逐页 `key in pages[p] or key in joined[p]` 混合匹配会把「落在第 N 页的标题」系统性映射到 N−1（joined 窗口包含下一页全文，键必然在窗口命中）。改为两段式：先全页精确匹配，未命中（标题文本被分页截断）再用相邻页拼接窗口兜底，窗口命中记起始页。单测以全页唯一键断言 `[0,1,2]`（原型算法在该用例下给 `[0,0,1]`）。
2. **同名标题消歧失败从严**：规格明确「仍歧义或未匹配返回 None」，故父路径消歧失败时返回 `None`（POC 原型回退取第一个候选，可能给出错误行区间；从严符合 D12④「错误定位不如不定位」）。
3. `tree_loop` 的非法 JSON 重试语义按规格补齐（POC 原型无重试）：非法时重试一次，仍非法记 `invalid_output` 降级。
4. 全量回归中 `test_loader_pdf_contract` 2 例失败为本地环境既有差异（本地 venv 装有 markitdown，测试前提是「本地无 markitdown」；CI 干净环境通过），与本次新增文件无关，未改动。
