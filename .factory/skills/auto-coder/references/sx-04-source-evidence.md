## 4. 源码实证注意事项

> 全部经 2026-10-13 对 `b314e11` 源码复核；file:line 相对 `sirchmunk/src/sirchmunk/`。

1. **telemetry 是顶层合并非嵌套**：`schema/search_context.py:181-189` 把 `context.telemetry` 逐键 `setdefault` 进 `to_dict()` 顶层；冻结规格 3.2 的 `data.telemetry` 表述错误（已在其 3.2 勘误）。
2. **evolver HTTP 不可达**：`search.py:569-583` 要求 `enable_knowledge_evolution=True` 且 embedding 客户端非空；`api/search.py` 两处实例构造均不传该参。
3. **compile 与 serve 不能并发**：work_path 无文件锁（`search.py:262-263` 按 mtime 判 staleness，Parquet last-writer-wins，冻结规格 4.7）。
4. **singleton 缓存**：`_tree_paths_cache`、`_search_instance`（同步端点）按实例缓存，编译产物对运行中的 sidecar 不可见，须重启。
5. **torch 已在 sidecar venv**：`requirements/core.txt` 含 `sentence-transformers`/`modelscope`/`sentencepiece`；复用的真实代价是模型下载与内存。
6. **SSE 每请求新实例**：`api/search.py` `_create_search_instance`（流式）vs `_get_search_instance`（同步 singleton）——构造副作用与缓存行为不同。
7. **`include_patterns`/`exclude_patterns` 字段名陷阱**（沿冻结规格 3.2，SX2/SX5 透传新参数时同样适用）：FastAPI 对未知字段静默丢弃。
8. **编译覆盖面**：`learnings/compiler.py:381-384` `_ELIGIBLE_EXTENSIONS` 不含 `.py`/`.yaml`；`learnings/tree_indexer.py:26` 树门槛 3,000 字符，`:56-59` `_TREE_EXTENSIONS` 只有文档类。源码问题不受编译影响。
9. **编译文件发现不排除目录**：`compiler.py` `_discover_files` 为 `rglob("*")` + 扩展名过滤，不读 `.gitignore`；CLI 无 exclude 参数（`cli/cli.py:1736-1761`）。范围只能靠显式路径控制。
10. **复用命中无 telemetry 标记，且有软复用**：`search.py:2590-2592`/`3792-3795` 命中即返回，不写标记；`_try_soft_reuse`（`2596`/`3798`）在未命中时仍会改变搜索行为。
11. **`KnowledgeStorage` 非单例**：每实例一个 parquet 同步守护线程 + atexit 钩子（`storage/knowledge_storage.py:364-388`）；外部写入经 mtime 检查重载（`_check_and_reload`），所以单例 sidecar 能看到其他进程写入的 parquet。

---
