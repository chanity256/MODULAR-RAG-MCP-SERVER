# Streamlit 退役规格（SR 系列）

> 本规格承接 Streamlit Dashboard（legacy）的**物理删除**：范围是页面层、启动脚本、`streamlit` 运行依赖、仅服务 Streamlit 的配置与页面级测试，以及文档/技能镜像同步。`observability/dashboard/services/` 等 API 层共享服务与对应测试**一律保留**。
> 由 [backlog/webui-parity-followups_backlog.md](../backlog/) 单文件晋升（原文见 git 历史，纯移动提交 fcaf0cf）。退役门槛三条中前两条已满足（WC10.3 门控冒烟 2026-10-15 通过，见 [06_WEBUI_CHAT_SPEC_frozen](../frozen/06_WEBUI_CHAT_SPEC_frozen.md) 冻结记录；#2/#5 与 #4/#1 已由 [08_WEBUI_PARITY_SPEC_frozen](../frozen/08_WEBUI_PARITY_SPEC_frozen.md)、[09_WEBUI_SIDECAR_SPEC_frozen](../frozen/09_WEBUI_SIDECAR_SPEC_frozen.md) 交付冻结），第三条经使用者 2026-11-13 确认：**不再依赖 Streamlit**。

- **版本**：v1.0（2026-11-13）
- **起源**：[analysis/WEBUI_PARITY_AUDIT.md](../analysis/WEBUI_PARITY_AUDIT.md)（WC10.1 双轨核验交付物）→ webui-parity-followups 台账（#1–#5 已由 WP/WS 两轮规格交付；#3 展示细节接受项的决议随本规格存档，见 §2 Out of Scope）
- **决策依据**：三问答案并入 §1；不经 backlog 草案改写，直接按退役范围立项
- **任务前缀**：SR（与既有 A–I / S / IR / WC / LG / SX / WP / WS / V / G 系列均不冲突）

## 1. 决策依据

台账「决策三问」按退役主题重新作答，作为本规格的立项依据：

1. **它解决了哪个已经观察到的问题？**——Web UI 已成为主界面（WC0–WC10 冻结），Streamlit 双轨自 WC10 起不再新增功能；保留两套 UI 意味着每次口径变化都要双处维护（如 `src/api/deps.py` 探活注释、sidecar 托管双入口），且 `streamlit>=1.36` 是纯为 legacy 保留的运行时依赖。台账原列差异 #1–#5 已全部由 WP/WS 交付或裁决接受，双轨核验使命结束。
2. **是否有更简单、改动更小的方案？**——更简单的方案是「继续保留不删」，但该方案正是本规格要终结的现状；删除范围内的每一项都对应退役门槛清单（原台账 §3）的收口动作，服务层及其测试不动，改动面已收敛到最小。
3. **如何确认它确实有效？**——退役后 `pytest tests -q` 离线全量全绿（含 `test_docs_links.py` 零坏链与 drift guard）、`python scripts/start_api.py --serve-ui` 正常启动 Web UI、全仓 `import streamlit` 页面代码零残留；门控真实集成测试不受影响。

**退役门槛核销表**（原台账 §3 → 本规格基线状态）：

| 门槛 | 状态 |
|------|------|
| WC10.3 门控冒烟通过、规格冻结 | 已满足（2026-10-15，06 规格冻结记录） |
| 台账 #1/#4 由 WS 规格补齐；#2/#5 由 WP 规格补齐 | 已满足（08/09 规格均已收口冻结） |
| 使用者确认不再依赖 Streamlit | 已确认（2026-11-13，本规格立项会话） |

## 2. 核心目标与范围

### 2.1 In Scope

| 编号 | 模块 | 名称 | 优先级 | 一句话目标 |
|------|------|------|--------|-----------|
| SR0 | `src/observability/dashboard/` + `scripts/` | 页面层与入口删除 | P0 | 删 `app.py`、`pages/`（7 页 + `__init__.py`）、`scripts/start_dashboard.py` |
| SR1 | `tests/` | 页面级测试删除 | P0 | 删 `test_dashboard_smoke.py`、`test_query_traces_source.py`、`test_start_dashboard.py` |
| SR2 | `requirements.txt` | streamlit 依赖移除 | P0 | 删 `streamlit>=1.36`，全仓页面代码 `import streamlit` 零残留 |
| SR3 | `src/core/settings.py` + `config/settings.yaml` + 测试 fixture | 仅服务 Streamlit 的配置清理 | P1 | 删 `DashboardConfig` 与 `dashboard:` 配置节，同步更新 `test_dashboard_services.py` / `test_config_loading.py` 的 fixture（既有测试语义变更，记录于 §4） |
| SR4 | docs/ + AGENTS.md + README | 文档同步 | P1 | README 运行手册与功能表、AGENTS.md 常用命令与 WS 行、USAGE_GUIDE（目录树、2.7、5、10 章；10.9 改为退役记录）、MOC 谱系表与分区索引、backlog/README |
| SR5 | `.claude/` `.github/` `.factory/` skills | 技能镜像同步 | P2 | 失效化 qa-tester 的 AppTest 模式、auto-coder 架构引用、setup / project-learner 中的 Streamlit 启动指引 |
| SR6 | 全量回归 | 离线全量测试 | P0 | `pytest tests -q` 全绿，记录计数变化（基线 2464 passed / 11 skipped） |

### 2.2 Out of Scope

- **`observability/dashboard/services/` 不删**：`DataService` / `TraceService` / `ConfigService` / `SearchService` / `IngestionService` / `EvaluationService` / `QueryService` / `chat_retrievers.py` 是 `src/api/` 的直接依赖，`services/__init__.py` 与 `dashboard/__init__.py` 保留并继续导出 services；其测试（`test_dashboard_services.py` 等）除 SR3 的 fixture 调整外语义不变。
- **#3 展示细节接受项不排期**：评估页路径预填、模式按钮配色、Traces 空命中标题、golden set 路径报错时机等 Streamlit 侧展示行为随 Streamlit 一并消失；Web 侧行为即为终态，不再另立任务。
- **frozen/ 规格不改写**：06 / 09 规格中涉及 `start_dashboard.py` 的历史描述保持原样（冻结只读）；引用口径的更新落在 AGENTS.md 与 USAGE_GUIDE。
- **frontend/、src/api/ 行为零变化**：Web UI 与 API 层不因退役改动任何行为；仅 `src/api/deps.py`、`scripts/sidecar_manager.py` 中提及 legacy 的注释随 SR3 顺带更新。
- **门控真实集成测试不动**：`RAG_LLM_INTEGRATION` / `RAG_E2E_INTEGRATION` / `RAG_AGENTIC_INTEGRATION` 门控语义不变。

## 3. 删除与保留清单

### 3.1 删除

| 路径 | 说明 |
|------|------|
| `src/observability/dashboard/app.py` | Streamlit 多页应用入口 |
| `src/observability/dashboard/pages/` | 7 个页面（overview / ingestion_manager / ingestion_traces / query_playground / query_traces / evaluation_panel / data_browser）+ `pages/__init__.py` |
| `scripts/start_dashboard.py` | Streamlit 启动器（含 sidecar 托管，编排已由 `scripts/sidecar_manager.py` 共享） |
| `tests/e2e/test_dashboard_smoke.py` | 七页 AppTest 冒烟 |
| `tests/e2e/test_query_traces_source.py` | Query Traces 页 AppTest（WC7.4） |
| `tests/unit/test_start_dashboard.py` | 启动器参数解析（G1） |
| `requirements.txt` 中 `streamlit>=1.36` | 运行时依赖 |
| `src/core/settings.py` 中 `DashboardConfig` + `settings.dashboard` 字段与解析 | 仅 `start_dashboard.py` 读 `dashboard.port` |
| `config/settings.yaml` 中 `dashboard:` 节 | 同上 |

### 3.2 保留

| 路径 | 保留原因 |
|------|----------|
| `src/observability/dashboard/services/`（8 个模块 + `__init__.py`） | `src/api/deps.py` 与全部 API 路由的共享服务层 |
| `src/observability/dashboard/__init__.py` | 继续导出 services（改为仅 services 导出，不含 pages） |
| `tests/unit/test_dashboard_services.py` 等 services 测试 | API 层契约测试，仅 SR3 的 fixture 行调整 |
| `tests/e2e_web/`（fake_api_server 等） | Web UI 冒烟，消费 services 而非 pages |
| `scripts/sidecar_manager.py` | 共享托管编排，`start_api.py --manage-sidecar`（WS0）依赖 |

### 3.3 依赖方向（退役后）

```
src/api/ ──▶ observability/dashboard/services/ ──▶ core/libs
frontend/ (Next 静态导出) ──▶ start_api.py --serve-ui ──▶ src/api/
（Streamlit 页面层与 start_dashboard.py 不复存在；sidecar 托管唯一入口为 --manage-sidecar）
```

## 4. 测试方案

- **离线确定性**：SR 完成后 `pytest tests -q` 必须全绿；删除页面级测试导致的用例计数下降属预期，SR6 记录前后计数。零网络纪律不变，外部依赖一律注入 fake。
- **既有测试语义变更清单**（本规格唯一一处，随 SR3 落地）：
  - `tests/unit/test_dashboard_services.py`：移除 `dashboard:` YAML fixture 与 `settings.dashboard.port == 8502` 断言（该节随 `DashboardConfig` 删除）；ConfigService / DataService / TraceService 契约断言全部保留。
  - `tests/unit/test_config_loading.py`：移除 `dashboard:` 节相关用例行；其余加载/校验语义不变。
- **删除的测试语义归属**：`test_dashboard_smoke.py` / `test_query_traces_source.py`（AppTest 页面冒烟）与 `test_start_dashboard.py`（参数解析）的语义随页面层退役一并废止，不迁移。
- **文档守卫**：`pytest tests/unit/test_docs_links.py` 必须全绿（MOC 无漏登记、全仓相对链接零坏链）。
- **门控真实冒烟不受影响**：`tests/e2e/test_chat_real_integration.py` 等门控测试仅消费 services，默认 skip 行为不变。

## 5. 任务排期与进度跟踪

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|----------|------|
| SR0 | 删除页面层与启动入口（§3.1 前三行） | 文件不存在；`python scripts/start_api.py --serve-ui` 正常启动 Web UI | [ ] |
| SR1 | 删除页面级测试（§3.1 三个测试文件） | 文件不存在；剩余测试可收集 | [ ] |
| SR2 | requirements.txt 删 `streamlit>=1.36` | 全仓 `src/` `tests/` `scripts/` 无 `import streamlit` 残留 | [ ] |
| SR3 | 删 `DashboardConfig` / `settings.yaml` `dashboard:` 节；同步 SR4 清单中的两个测试 fixture；更新 `sidecar_manager.py` / `api/deps.py` legacy 注释 | `load_settings` 正常；两个测试文件改动后通过 | [ ] |
| SR4 | 文档同步（§2.1 SR4 行所列全部落点） | `pytest tests/unit/test_docs_links.py` 全绿；README / AGENTS / USAGE_GUIDE 无残留的 `start_dashboard` 启动指引 | [ ] |
| SR5 | 技能镜像同步（§2.1 SR5 行所列镜像） | 镜像内无会误导的 Streamlit 启动 / AppTest 指引 | [ ] |
| SR6 | 全量回归并记录计数变化 | `pytest tests -q` 全绿；SR 记录测试数（基线 2464 passed / 11 skipped） | [ ] |

依赖顺序：SR0 → SR1 → SR2 → SR3 →（SR4、SR5 可并行）→ SR6。

## 6. 验收清单

- [ ] 全仓不再存在 Streamlit 页面层、`start_dashboard.py`、页面级 AppTest 测试。
- [ ] `requirements.txt` 不再含 `streamlit`；主 venv 无 streamlit 安装需求。
- [ ] `observability/dashboard/services/` 及其测试完整保留，`src/api/` 行为零变化。
- [ ] `pytest tests -q` 离线全量全绿，计数变化已在 SR6 记录。
- [ ] `pytest tests/unit/test_docs_links.py` 全绿；MOC 谱系表含本规格的「单文件晋升」记录。
- [ ] README / AGENTS.md / USAGE_GUIDE / 技能镜像中不再有 Streamlit 启动指引；USAGE_GUIDE 10.9 改为退役记录。
- [ ] 本规格收口后按治理流程冻结入 `frozen/`（编号 = 冻结时 frozen/ 最大编号 +1），如无遗留事项则不建派生台账。

## 7. 回写清单（收口时同步）

- [ ] README：运行手册删除 Streamlit 命令段；功能对比表删除「Dashboard（legacy）」行；「开发文档」导航表移除台账、登记新规格。
- [ ] AGENTS.md：常用命令删除 `start_dashboard.py` 行；WS 规格引用行中 `scripts/start_dashboard.py` 的描述改为唯一入口 `--manage-sidecar`。
- [ ] USAGE_GUIDE：目录树删 `start_dashboard.py` 行；第 2.7 / 5 章删除 Streamlit 段；第 10 章引言与 10.9 改写为退役记录（删除条件表核销）。
- [ ] MOC：specs/ 分区索引登记本规格；backlog/ 分区与 README「当前候选」移除台账；谱系表新增「Streamlit 退役（SR）」行（单文件晋升）。
- [ ] `docs/analysis/WEBUI_PARITY_AUDIT.md` 文件头加一行「退役执行见规格链接」（不改写正文）。
