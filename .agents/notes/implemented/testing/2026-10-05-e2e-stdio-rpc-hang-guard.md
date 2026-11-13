# Agent Note: 子进程 stdio E2E 的 RPC 读取必须有超时，通知不等响应

Status: implemented

## Problem

全量 `pytest` 从约 4 秒变成超过 300 秒仍不结束，最终被外层超时杀掉，没有任何失败输出。根因在 `tests/e2e/test_agentic_mcp.py`：测试发送 `notifications/initialized` 后调用了阻塞的 `proc.stdout.readline()`。按 JSON-RPC 语义，通知（没有 `id`）不会有响应，所以这次读取永远不返回。

这类错误难查有两个原因：

- 子进程 stdio 测试的阻塞读没有超时，协议写错时表现为「整个套件挂住」，而不是「某个测试失败」。日志看不出卡在哪里，只能逐个文件单跑定位。
- 同一个 fixture 还有一个潜在的同类风险：`stderr=subprocess.PIPE` 没有人读。server 日志写满管道缓冲区（macOS 约 64KB）后，子进程会阻塞在写 stderr 上，stdout 读取同样永久挂起。

## Decision

`tests/e2e/test_agentic_mcp.py` 的 `server` fixture 和 `_rpc` 帮助函数采用以下写法：

- stderr 重定向到 `tmp_path / "server.stderr.log"` 文件，不用 PIPE，从根上消除管道写满导致的阻塞，日志事后仍可查看。
- 后台守护线程（`_pump`）逐行读 stdout 放进 `queue.Queue`；`_rpc` 用 `queue.get(timeout=15)` 取响应，超时就 `pytest.fail`，消息里带出 RPC 方法名。
- 通知必须以 `expect_response=False` 发送；`_rpc` 的 docstring 写明这条约束。
- shutdown 之后用 `_drain_stdout` 等进程退出，再收集剩余行，供 stdout 纯净断言使用，不再调用 `communicate()`（stdout 已经被 pump 线程占用）。
- fixture 清理时，进程还活着就直接 `kill()`。

## Alternatives considered

- **引入 `pytest-timeout` 给整个套件加全局超时**：最强论据是一行配置就能兜住所有测试的挂起，包括以后新写的。被否作为本次修复：它只把「挂住」变成「超时失败」，报错不指出是哪次 RPC 没有响应；而且要新增一个 dev 依赖和全局超时阈值，单测和 E2E 的合理时长差异很大，阈值不好定。以后再出现非 RPC 类挂起时可以重新考虑。
- **用 `select` / `selectors` 在 stdout 文件描述符上做带超时的读取**：最强论据是不需要额外线程，读取逻辑留在调用点。被否：`text=True` 时 `TextIOWrapper` 有自己的读缓冲，`select` 看的是底层 fd，可能出现「数据已在 Python 缓冲里但 fd 不可读」的误判；Windows 上管道也不支持 `select`。
- **只修那一行通知调用，保持 helper 不变**：最强论据是改动最小，根因确实只有这一行。被否：helper 仍然在下一次协议写错时无提示地挂住整个套件，stderr PIPE 的潜在阻塞也还在；这类问题定位成本远高于加超时的成本。

## Consequences

- **收益**：协议写错时只会让单个测试在 15 秒内失败，并指出具体方法名；全量套件恢复到约 7 秒（1128 passed / 3 skipped）。
- **代价**：每个 server 实例多一个守护线程，helper 比直接 `readline()` 复杂；15 秒是固定上限，未来如果有合法的慢 RPC（例如真实 sidecar 下的 DEEP 调用），必须单独调大 `_RPC_TIMEOUT_S`。
- **已知上限**：I1 阶段的 `tests/e2e/test_mcp_client.py` 仍是 `stderr=subprocess.PIPE` 加无超时的 `readline()`。它目前对通知的处理是正确的，但没有超时兜底。下次修改该文件，或再出现 E2E 挂起时，应该把本 helper 抽到共享的 `tests/e2e/conftest.py` 并统一替换。

## Verification

- `tests/e2e/test_agentic_mcp.py`：4 个测试，其中 1 个由 `RAG_AGENTIC_INTEGRATION` 门控跳过。
- 复现方法：把 `notifications/initialized` 那一行恢复成默认的 `expect_response=True`，测试应在 15 秒内以 "no JSON-RPC response" 失败，而不是挂住。
- `PYTHONPATH=src .venv/bin/python -m pytest -q -o addopts=""` 在约 7 秒内完成。
