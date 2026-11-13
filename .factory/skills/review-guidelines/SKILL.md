---
name: review-guidelines
description: "Repository-specific code review guidelines for Modular RAG MCP Server, injected into every automated Droid PR review. Three passes (bugs, security, compliance with docs/frozen specs), Important/Nit severity, at most 5 Nits, excluded paths. Human-readable mirror: REVIEW.md at the repo root."
---

# Review guidelines（Modular RAG MCP Server）

人读版本见仓库根 `REVIEW.md`；`review-checklist` 标记之间的内容与其逐字一致（`tests/unit/test_review_guidelines.py` 锁定）。

<!-- review-checklist:start -->
## 三遍扫

按顺序做三遍，每条发现标注严重度（Important / Nit），按严重度排序输出。

### 第 1 遍：bugs

- 逻辑错误、边界条件（空输入、off-by-one、超时、并发）、异常吞掉或错误降级。
- 回归：改动是否改变了既有调用方依赖的行为、返回结构或默认值。
- 资源泄漏：文件句柄、SQLite 连接、httpx client、线程 / 子进程（sidecar、SSE 流）未关闭。

### 第 2 遍：security

- 注入：SQL 拼接、shell 命令拼接、路径穿越（agentic_search 的 PathGuard 白名单必须在调用前、调用后两层都生效）。
- 认证与暴露面：新增或放宽 HTTP 路由、绑定非 127.0.0.1、绕过 sidecar Bearer Token、`api.enabled` 默认值被改为 true。
- 敏感数据：API key、`.env*` 内容、查询文本或文档片段写进日志、trace、异常信息或 stdout。

### 第 3 遍：合规（对照 docs/frozen/ 规格）

- 改动是否符合对应 frozen 规格的契约、验收标准与既有测试语义（主规格 `docs/frozen/01_DEV_SPEC_frozen.md`，其余见 `docs/MOC.md`）。
- 是否修改了 sirchmunk 上游源码（CI 无法静态检查，只能在 review 中拦）。
- 测试是否离线确定：新测试不得触网、不得依赖真实模型或本机状态（`data/`、`logs/`、`frontend/out/`）；真实依赖必须走 `RAG_*_INTEGRATION` 环境变量门控。
- 新增 pipeline 支持的扩展名是否在 `src/ingestion/pipeline.py` 与 `scripts/ingest.py` 两处同步登记。
- **以下两类一律记 Important**：
  1. 新增或放宽红线白名单 / 豁免条目（`scripts/check_redlines.py` 的 `STDOUT_ALLOWLIST`、`LLM_FALLBACK_EXEMPTIONS`、扫描范围；CI 的 `--allow-hosts`；`.github/CODEOWNERS`）。
  2. 删除或弱化已有测试断言（删用例、放宽断言、加 skip / xfail、改期望值去迎合新行为）——除非 PR 描述引用了规格依据。

## 严重度

- **Important**：会导致错误结果、数据丢失、安全问题、违反 frozen 规格契约或红线，或属于上面「一律记 Important」的两类。必须在合并前修复，或在 PR 评论中写明不修的理由。
- **Nit**：可读性、命名、小的简化建议，不影响正确性。每次 review **最多 5 条 Nit**，只挑最有价值的。
- 不提风格偏好、格式问题与架构观点；拿不准的不要报。

## 排除范围

- 路径：`frontend/out/`、`data/`、`logs/`、`frontend/node_modules/`、`*.lock` 与 `package-lock.json` 的机械变更。
- CI 已强制的检查不重复评论：离线 pytest 全绿、`scripts/check_redlines.py`（sirchmunk import、`llm_fallback` 字面量 False、MCP stdout）、`tests/unit/test_docs_links.py`（坏链与 MOC 登记）、双登记 drift guard、前端 typecheck / lint / vitest。CI 红灯本身就会阻止合并。
<!-- review-checklist:end -->
