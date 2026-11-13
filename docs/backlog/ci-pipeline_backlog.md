<!-- 候选提案：CI 持续集成管线。将 01_DEV_SPEC_frozen.md §4.5 的可选 CI/CD 设计与 FUTURE_OPTIMIZATION_ROADMAP §6.7 的「CI 分别执行 Python 与前端检查」落为可排期提案。立项前不进 specs/。 -->

# CI 持续集成管线提案

> 版本：0.1 — 候选提案（草案），未立项排期
>
> 起源：[01_DEV_SPEC_frozen.md §4.5「测试工具链与 CI/CD 集成」](../frozen/01_DEV_SPEC_frozen.md)（可选 CI/CD Pipeline 设计，明确注明「本地项目不强制要求 CI/CD」）；[FUTURE_OPTIMIZATION_ROADMAP.md §6.7「测试与工程质量」](../reference/FUTURE_OPTIMIZATION_ROADMAP.md)「CI 分别执行 Python 与前端检查」

## 0. 现状盘点（提案依据）

项目当前**没有任何 CI 配置**：无 `.github/workflows/`、Jenkinsfile、GitLab CI 等文件。质量保障完全依赖开发者手动执行 AGENTS.md 约定的门禁命令：

| 门禁 | 命令 | 现状 |
|------|------|------|
| Python 全量测试 | `pytest tests -q` | 离线零网络，当前基线 2464 passed / 11 skipped |
| 前端静态检查 | `npm run typecheck && npm run lint && npm test`（`frontend/`） | vitest + typecheck + lint，离线 |
| Web UI 冒烟 | `pytest tests/e2e_web -q` | 假后端离线；`frontend/out` 未构建则自动 skip |
| 门控真实集成 | `RAG_LLM_INTEGRATION` / `RAG_E2E_INTEGRATION` / `RAG_AGENTIC_INTEGRATION` | 默认跳过，仅显式开启才触网 |

检查本身齐全且离线可复现，但**没有自动化触发机制**；同时仓库也没有 pre-commit hook 与 ruff/mypy 等静态检查配置，提交前门禁同样靠自觉。

## 1. 提案三问（对齐决策原则）

1. **解决了哪个已经观察到的问题？**
   多轮迭代（IR/WC/LG/WP/WS 系列）中，「全量测试全绿」这一合并前提完全依赖开发者手动执行 `pytest tests -q` 与前端三连检查；一旦遗漏（或只跑了改动文件附近的子集），坏变更会直接进入分支基线。规格 §4.5 虽设计了三阶段 CI/CD Pipeline，但停留在「可选设计」，从未落地。
2. **是否有更简单、改动更小的方案？**
   有：只加一个 GitHub Actions workflow（两个 job 分别跑 Python 与前端既有命令），不引入任何新工具链、不做 CD、不开门控真实测试。所有 CI 步骤与 AGENTS.md 本地命令**逐字同源**，CI 绿灯与本地全绿语义一致，无额外维护面。
3. **如何确认它确实有效？**
   - 每个 PR 与 push 到默认分支自动触发，绿灯即「离线全量门禁通过」；
   - CI 用的命令与本地完全相同，任何人本地复现结果应与 CI 一致；
   - 运行时长可控（pip/npm 依赖缓存），Python 全量测试在本地分钟级，CI 同量级。

## 2. 范围草案

### In Scope（最小可用路径）

| 任务 | 内容 | 一句话目标 |
|------|------|-----------|
| CI-1 | `.github/workflows/ci.yml` Python job | 拉起 Python 环境 → 安装依赖 → `pytest tests -q` 全量离线测试（零网络，天然适配 CI） |
| CI-2 | 同 workflow 前端 job | Node 22 → `npm ci` → `typecheck && lint && test`，与 Python job 并行 |
| CI-3（可选） | Web UI 冒烟接 CI | 前端 job 内追加 `npm run build`，随后跑 `pytest tests/e2e_web -q`（冒烟 + 假后端，离线；不 build 则维持 skip 语义） |
| CI-4 | 文档同步 | AGENTS.md 常用命令增补 CI 说明；README 视需要加 workflow status badge |

触发条件：PR + push 默认分支。

### Out of Scope（明确不做）

- **不做 CD / 部署**：项目是本地单机应用，无部署目标。
- **不开启门控真实集成测试**：`RAG_LLM_INTEGRATION` / `RAG_E2E_INTEGRATION` 需真实模型与 API Key（触网），`RAG_AGENTIC_INTEGRATION` 需 sidecar 进程；与「CI 默认离线」纪律冲突，保持默认跳过。
- **不做 DEV_SPEC §4.5 第三阶段「RAG 质量评估」**：评估需 API Key 与真实 LLM；[02_ACCEPTANCE_GAPS_frozen.md](../frozen/02_ACCEPTANCE_GAPS_frozen.md) 已明确「带 Key 的手工验收……不要默认开在 CI」。
- **不引入覆盖率门禁**：DEV_SPEC §4.5 的覆盖率目标（单测 ≥80% 等）留待有覆盖率产出后另行评估，先不作为 CI 拦截条件。
- **不引入 ruff / mypy / pre-commit 工具链**：静态检查属独立改进，如需要另起提案（避免本提案夹带工具链选型决策）。
- **不做 OpenAPI schema 版本变更检查**：roadmap §6.7 的另一条独立建议，不并入。

## 3. 立项注意（晋升 specs/ 时执行）

- 立项方式：单文件晋升（`git mv` 本文件 → `specs/CI_PIPELINE_SPEC.md`，先纯移动提交再改写提交）；本提案三问并入规格第 1 章「决策依据」。
- 任务前缀 `CI`（2 字母，与既有 A–I / S / IR / SX / WC / LG / WP / WS / V 系列不冲突）。
- 规格需补齐：workflow 文件结构图（job 拆分与依赖）、缓存策略（pip / npm）、失败重试语义（不重试 flaky，直接红）、测试方案（CI 配置本身的验证方式——本地 `act` 类工具可选，或以首次真实 run 验收）。
- 排期定位：低优先级补强项，不阻塞任何在途功能规格。
