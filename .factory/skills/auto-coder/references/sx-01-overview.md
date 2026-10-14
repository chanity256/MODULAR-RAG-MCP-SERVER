## 1. 概述

### 1.1 背景与目标

G0 三方对照的结论只在「无编译、无复用、冷启动」配置下成立。本规格补齐 sirchmunk 其余可经 sidecar 使用的能力，并修复规格与实现的三处偏差：

1. **telemetry 消费**：`SearchContext.to_dict()` 将 `context.telemetry` 合并到 `data` 顶层（`schema/search_context.py:163-189`），`converter.py` 未读取，trace/Dashboard 无展示；
2. **知识编译运维入口**：HTTP API 无编译端点，需脚本化 sidecar venv CLI 并完成「编译能否翻盘」实验；
3. **知识簇复用**：上游 core 依赖已含 torch/sentence-transformers，关闭理由（依赖安装）不再成立，需受控开启并评估。

外加两枚小增量：请求参数透传（`max_depth`/`enable_dir_scan`）与 SSE 流式（仅 Dashboard DEEP）。

### 1.2 修正后的能力可达性（源码复核）

| 能力 | 上一版判断 | 复核结论（`b314e11`） |
|------|-----------|----------------------|
| telemetry | 「`data.telemetry` 未消费」 | **无 `data.telemetry` 键**——`to_dict()` 把动态 telemetry `setdefault` 合并进顶层；应读 `loop_count`/`budget_remaining`/`llm_call_count`/`retrieval_logs`/`read_file_ids`/`search_history` 及机制标记（`self_correct_entered`/`fallback_best_candidate`/`answer_withheld_by_policy` 等，见 `search.py` 20+ 处 `_record_context_telemetry` 调用） |
| 知识簇复用 | 「需安装 torch + embedding 模型」 | `.venv-sirchmunk` 实测已含 torch 2.14.1 / sentence-transformers 6.1.0（上游 requirements/core.txt）；真实代价仅 MiniLM 下载（`EMBEDDING_MODEL_ID`，默认 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`）+ 常驻内存 |
| 知识演化 | 「随复用失效」 | **HTTP 不可达**：`AgenticSearch(enable_knowledge_evolution=False)` 为默认，`api/search.py`/CLI 均不传 `True`；开启须改上游 → out of scope |
| 编译覆盖面 | 「树索引只对 ≥50KB 大文档生效」 | **门槛是 3,000 字符**（`learnings/tree_indexer.py:26` `_TREE_MIN_CHARS`；50,000 是启发式窗口上限）。但编译**完全不处理 `.py`**：`compiler._ELIGIBLE_EXTENSIONS` 只含 pdf/docx/doc/md/markdown/html/htm/rst/tex/txt/pptx/xlsx，`_TREE_EXTENSIONS` 再去掉 pptx/xlsx。现有 golden 集 6 题中 Q1/Q2/Q5/Q6 的 `expected_sources` 全是 `.py`，编译最多影响 Q3/Q4 |

---
