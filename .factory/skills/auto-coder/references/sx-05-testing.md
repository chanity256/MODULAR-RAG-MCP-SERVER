## 5. 测试方案

### 5.1 离线（默认必须全绿）

| 层 | 覆盖 |
|----|------|
| 单元（SX1） | `parse_search_context`：顶层 telemetry 键白名单收集、`retrieval_logs` 降维计数、键缺失/畸形容忍（恒不抛）、fixture 锁 shape |
| 单元（SX1） | tool trace metadata 含 `agentic_telemetry`；fake backend 注入 |
| 单元（SX2） | `_build_payload` 透传 `max_depth`/`enable_dir_scan`、恒发 `llm_fallback=false`、配置校验 fail-fast |
| 单元（SX3） | compile.sh 停机护栏（探活存活即拒）、路径护栏（`.`/`.venv*`/`.git` 等被拒、未传路径时取 `SIRCHMUNK_COMPILE_PATHS`）、参数拼装（`tmp_path` mock sidecar venv 可执行文件） |
| 单元（SX1） | `zero_llm_path` 派生规则：`llm_call_count == 0` 且 `loop_count == 0` 才为真，键缺失时为 `None` 不猜测 |
| 单元（SX5） | 罐头 SSE 帧解析：log/result/error/heartbeat/断流；`parse_search_context` 复用正确 |
| Dashboard | `search_service` 流式路径 fake 覆盖；页面冒烟沿用 `AppTest.from_file` |

### 5.2 门控真实测试（仅显式开启）

| 门控 | 内容 |
|------|------|
| `RAG_AGENTIC_INTEGRATION=1` | 真实 sidecar：SX1 telemetry 字段实测非空校验；SX3 编译前后 golden 集对照（每题 ×3，复用关闭）；SX4 复用延迟/命中实测 + 复用开/关非命中回归；SX5 SSE 真实流冒烟 + 连续请求线程数观察 |

---
