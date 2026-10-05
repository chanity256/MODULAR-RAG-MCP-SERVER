# Agent Note: agentic_search 工具在调用方执行路径白名单（双层防线）

Status: implemented

## Problem

`agentic_search` 让 MCP 客户端（也就是一个 LLM）指定要检索的 `paths`，再由 sirchmunk 的 ReAct Agent 自行决定读哪些文件。源码审计（`b314e11`）确认上游没有可依赖的范围限制：

- ReAct 工具 `file_read` 可以读 LLM 请求的任意绝对路径，没有范围校验（`agentic/tools.py:574-628`）。
- `SIRCHMUNK_ALLOWED_PATHS` 只保护 FastAPI 的文件浏览端点；`POST /api/v1/search` 不做白名单校验（`api/search.py:309-336`）。

语料本身不可信：文档里的 prompt injection 可以诱导 Agent 去读 `~/.ssh` 或项目的 `.env`，再把内容写进答案或证据。不在本项目这一侧拦截，这个工具就是一个任意文件读取通道。

## Decision

白名单由本项目在调用方执行，实现为 `src/libs/agentic_search/path_guard.py` 的 `PathGuard`，配置来自 `agentic_search.allowed_roots` 和 `agentic_search.denied_substrings`。

- **第一层：调用前，fail-closed**。`validate_paths` 对每个请求路径做 `expanduser` + realpath 归一。符号链接逃逸、落在白名单根之外、命中 `denied_substrings`（默认 `.env` / `.git` / `data/db`），任一条件成立就整体拒绝。工具把 `PathGuardError` 转为 `ToolInvalidParams`（-32602），请求不会发出。`paths` 缺省时使用 `allowed_roots`。
- **第二层：调用后，静默过滤**。`filter_evidence_paths` 处理证据和 `source_files`：白名单外或命中排除子串的条目直接丢弃，不报错（调用已经发生，这一层是兜底）。`http(s)://` 证据原样保留。
- **相对证据路径**：sidecar 可能按它自己的 CWD 回显相对路径。`_resolve_evidence_path` 依次尝试当前 CWD、每个允许根、每个允许根的父目录，只接受解析结果真实存在、并且落在白名单内的文件；解析不到就丢弃。`AgenticSearchTool._build_result` 用归一化后的绝对路径改写 `evidence.source_path`，citations 因此可以直接定位文件。
- **配套门控**：`enabled=true` 时 `allowed_roots` 必须非空（配置解析期 fail-fast）；DEEP 模式需要 `allow_deep: true`，否则拒绝（单次可达数十秒，且 Agent 探索面更大）；工具只在 `agentic_search.enabled` 时由 `build_default_protocol_handler` 注册。
- **信任模型**：agentic 答案按不可信输入处理，只作为指向白名单内文件的引用。需要重度使用 DEEP 时，sidecar 应该跑在受限用户或容器里。

## Alternatives considered

- **依赖上游 `SIRCHMUNK_ALLOWED_PATHS`**：最强论据是逻辑在服务端，所有客户端（包括直接 curl）都受保护，本项目不用写守卫代码。被否：经核实它不作用于 `/api/v1/search`，也拦不住 ReAct `file_read`；如果把它当作安全边界，等于依赖一个并不存在的保护。
- **只做调用前校验**：最强论据是实现简单，请求路径合法时结果自然合法。被否：调用前校验只约束请求的 `paths`，管不到 Agent 在探索中自己决定读的文件。prompt injection 恰好发生在调用之后，需要第二层兜底。
- **只做调用后过滤**：最强论据是一层就能覆盖所有泄漏出口。被否：sidecar 已经读过越权文件，内容可能进了 LLM 上下文和答案文本，事后过滤证据列表拦不住答案里的内容；越权请求也应该给调用方明确的 -32602，而不是返回一个被删空的结果。
- **用 OS 级沙箱（容器、受限用户）代替应用层守卫**：最强论据是唯一真正可靠的隔离，能管住所有读路径。被否作为默认方案：对个人项目的本机部署门槛太高（要 Docker 或额外用户）。它作为 DEEP 重度使用的建议保留，与 PathGuard 叠加，不互相替代。

## Consequences

- **收益**：越权请求在发出前就失败，并返回结构化的 -32602；越权证据不会进入 citations；符号链接逃逸和 `~` 展开都在归一化之后检查。
- **代价**：fail-closed 会误拒，例如白名单内但路径中含 `.env` 子串的合法文件也会被拒；`denied_substrings` 是子串匹配，不是 glob。
- **代价**：相对证据的解析是猜测。解析不到真实文件的证据会被丢弃，所以 sidecar CWD 与本项目差异很大时，可能出现「答案有内容、引用为空」。
- **已知上限**：第二层只过滤路径，不清洗答案文本，越权内容理论上仍可能出现在 `answer` 里。这是 sidecar 方案的固有限制。出现以下任一情况时必须重访本决定：上游给 `file_read` 加上范围校验；sidecar 改为容器化默认部署；需要多租户共享 sidecar。

## Verification

- `tests/unit/test_agentic_path_guard.py`（17 个）覆盖空根、缺失根、白名单外、符号链接逃逸、排除子串、`.git` 默认排除、相对路径、URL 保留和去重。
- `tests/unit/test_agentic_tool.py`（20 个）覆盖 -32602 校验矩阵、DEEP 门控、`isError` 降级和证据过滤。
- 相对证据路径解析目前只由 `tests/integration/test_agentic_http.py` 覆盖，没有专门的单测。
- `tests/e2e/test_agentic_mcp.py::test_out_of_allowlist_paths_structured_32602` 在子进程 server 上断言 `paths=["/etc"]` 返回 -32602。
