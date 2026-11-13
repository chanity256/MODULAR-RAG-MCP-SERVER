# Streamlit 退役规格（SR 系列）

> 本规格承接 Streamlit Dashboard（legacy）的**物理删除**：范围是页面层、启动脚本、`streamlit` 运行依赖、仅服务 Streamlit 的配置与页面级测试，以及文档/技能镜像同步。`observability/dashboard/services/` 等 API 层共享服务与对应测试**一律保留**；`scripts/sidecar_manager.py` 的单测随启动器删除而**迁移**，不废止。
> 由 [backlog/webui-parity-followups_backlog.md](../backlog/) 单文件晋升（原文见 git 历史，纯移动提交 fcaf0cf）。退役门槛三条中前两条已满足（WC10.3 门控冒烟 2026-10-15 通过，见 [06_WEBUI_CHAT_SPEC_frozen](../frozen/06_WEBUI_CHAT_SPEC_frozen.md) 冻结记录；#2/#5 与 #4/#1 已由 [08_WEBUI_PARITY_SPEC_frozen](../frozen/08_WEBUI_PARITY_SPEC_frozen.md)、[09_WEBUI_SIDECAR_SPEC_frozen](../frozen/09_WEBUI_SIDECAR_SPEC_frozen.md) 交付冻结），第三条经使用者 2026-11-13 确认：**不再依赖 Streamlit**。

- **版本**：v1.1（2026-11-13）
- **修订记录**：v1.0（2026-11-13）立项；v1.1（2026-11-13）按仓库实证审阅修订——sidecar_manager 单测迁移、`pyproject.toml` 依赖、frozen/03 坏链、配置兼容行为、SR2 验收口径、文档/技能落点补全与锚点约束、预期测试计数
- **起源**：[analysis/WEBUI_PARITY_AUDIT.md](../analysis/WEBUI_PARITY_AUDIT.md)（WC10.1 双轨核验交付物）→ webui-parity-followups 台账（#1–#5 已由 WP/WS 两轮规格交付；#3 展示细节接受项的决议随本规格存档，见 §2 Out of Scope）
- **决策依据**：三问答案并入 §1；不经 backlog 草案改写，直接按退役范围立项
- **任务前缀**：SR（与既有 A–I / S / IR / WC / LG / SX / WP / WS / V / G 系列均不冲突）

## 1. 决策依据

台账「决策三问」按退役主题重新作答，作为本规格的立项依据：

1. **它解决了哪个已经观察到的问题？**——Web UI 已成为主界面（WC0–WC10 冻结），Streamlit 双轨自 WC10 起不再新增功能；保留两套 UI 意味着每次口径变化都要双处维护（如 `src/api/deps.py` 探活注释、sidecar 托管双入口），且 `streamlit>=1.36` 是纯为 legacy 保留的运行时依赖。台账原列差异 #1–#5 已全部由 WP/WS 交付或裁决接受，双轨核验使命结束。
2. **是否有更简单、改动更小的方案？**——更简单的方案是「继续保留不删」，但该方案正是本规格要终结的现状；删除范围内的每一项都对应退役门槛清单（原台账 §3）的收口动作，服务层及其测试不动，改动面已收敛到最小。
3. **如何确认它确实有效？**——退役后 `pytest tests -q` 离线全量全绿（含 `test_docs_links.py` 零坏链与 drift guard）、`python scripts/start_api.py --serve-ui` 正常启动 Web UI、全仓 `streamlit` 实际 import 语句零残留；门控真实集成测试不受影响。

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
| SR1 | `tests/` | 页面级测试删除 + sidecar 单测迁移 | P0 | 删 `test_dashboard_smoke.py`、`test_query_traces_source.py`；`test_start_dashboard.py` 中 6 个 `sidecar_manager` 用例迁至新文件 `tests/unit/test_sidecar_manager.py`，其余 4 个随启动器废止后删除原文件 |
| SR2 | `requirements.txt` + `pyproject.toml` | streamlit 依赖移除 | P0 | 两处删 `streamlit>=1.36` 并清理两处提及 streamlit 的注释；全仓无 `streamlit` 实际 import 语句 |
| SR3 | `src/core/settings.py` + `config/settings.yaml` + 测试 fixture + 注释 | 仅服务 Streamlit 的配置清理 | P1 | 删 `DashboardConfig` 与 `dashboard:` 配置节，同步更新 `test_dashboard_services.py` / `test_config_loading.py` 的 fixture 并补一条旧配置兼容用例（既有测试语义变更，记录于 §4）；按 §3.4 清理提及 Streamlit 的代码注释 |
| SR4 | docs/ + AGENTS.md + README | 文档同步 | P1 | 按 §7 回写清单逐项落地（README、AGENTS.md、USAGE_GUIDE、frozen/03 链接、WEBUI_PARITY_AUDIT 文件头、OBSIDIAN backlog 注记、MOC、backlog/README） |
| SR5 | `.claude/` `.github/` `.factory/` skills | 技能镜像同步 | P2 | 按 §3.5 清单失效化 / 改写 Streamlit 启动、AppTest 指引与简历素材中的 Streamlit 表述 |
| SR6 | 全量回归 | 离线全量测试 | P0 | `pytest tests -q` 全绿，记录计数变化（基线 2464 passed / 11 skipped，预期见 §4） |

### 2.2 Out of Scope

- **`observability/dashboard/services/` 不删**：`DataService` / `TraceService` / `ConfigService` / `SearchService` / `IngestionService` / `EvaluationService` / `QueryService` / `chat_retrievers.py` 是 `src/api/` 的直接依赖，`services/__init__.py` 与 `dashboard/__init__.py` 保留并继续导出 services；其测试（`test_dashboard_services.py` 等）除 SR3 的 fixture 调整外语义不变。是否把 services 搬出 `dashboard/` 目录不在本规格范围。
- **#3 展示细节接受项不排期**：评估页路径预填、模式按钮配色、Traces 空命中标题、golden set 路径报错时机等 Streamlit 侧展示行为随 Streamlit 一并消失；Web 侧行为即为终态，不再另立任务。
- **frozen/ 规格内容不改写**：06 / 09 等规格中涉及 `start_dashboard.py` 的历史描述保持原样（冻结只读）；引用口径的更新落在 AGENTS.md 与 USAGE_GUIDE。**唯一例外**：`frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md` 第 161 行指向 `../../scripts/start_dashboard.py` 的 Markdown 链接在文件删除后成为坏链（`test_docs_links.py` 会失败），按 995f53b「仅链接路径改写、不改内容」先例，改为行内代码 `` `scripts/start_dashboard.py` ``（已删除）而不改动该行其余文字。
- **frontend/、src/api/ 行为零变化**：Web UI 与 API 层不因退役改动任何行为；仅 §3.4 所列注释随 SR3 顺带更新。
- **常驻分析文档的历史描述不改写**：`FUTURE_OPTIMIZATION_ROADMAP.md`、`MICROSERVICES_ANALYSIS.md`、`SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md`、`SIRCHMUNK_WEBUI_CHAT_REUSE_ANALYSIS.md` 中的 Streamlit 描述记录的是写作当时的状态，原样保留（`WEBUI_PARITY_AUDIT.md` 仅加文件头一行，见 §7）。
- **QA 测试计划的替代方案不在范围**：qa-tester 的 Dashboard UI（AppTest）用例组随 Streamlit 失效，SR5 只做失效标注与指引改写；改用 Playwright / Web UI 的 QA 方案如需要另走 backlog 提案。
- **门控真实集成测试不动**：`RAG_LLM_INTEGRATION` / `RAG_E2E_INTEGRATION` / `RAG_AGENTIC_INTEGRATION` 门控语义不变。

## 3. 删除与保留清单

### 3.1 删除

| 路径 | 说明 |
|------|------|
| `src/observability/dashboard/app.py` | Streamlit 多页应用入口 |
| `src/observability/dashboard/pages/` | 7 个页面（overview / ingestion_manager / ingestion_traces / query_playground / query_traces / evaluation_panel / data_browser）+ `pages/__init__.py`（连同 `__pycache__/` 整个目录删除） |
| `scripts/start_dashboard.py` | Streamlit 启动器（含 sidecar 托管，编排已由 `scripts/sidecar_manager.py` 共享） |
| `tests/e2e/test_dashboard_smoke.py` | 七页 AppTest 冒烟（19 个用例） |
| `tests/e2e/test_query_traces_source.py` | Query Traces 页 AppTest（WC7.4，8 个用例） |
| `tests/unit/test_start_dashboard.py` | 启动器测试（G1，10 个用例 / 收集 11 项）；其中 6 个 `sidecar_manager` 用例先按 §3.2 迁出再删文件 |
| `requirements.txt` / `pyproject.toml` 中 `streamlit>=1.36` | 运行时依赖（两处登记，均删）；两处注释 `requirements.txt:2`、`pyproject.toml:15` 中的 streamlit 字样一并去掉 |
| `src/core/settings.py` 中 `DashboardConfig` + `Settings.dashboard` 字段 + `_build_dashboard` + `load_settings` 中 `dashboard` 节的读取与类型校验 + `__all__` 中的 `"DashboardConfig"` + `_REQUIRED_FIELDS` 注释中的 dashboard 字样 | 仅 `start_dashboard.py` 读 `dashboard.port` |
| `config/settings.yaml` 中 `dashboard:` 节（含其上方注释行） | 同上 |

**pandas 已核实无需动作**：`pandas` 仅被 3 个页面文件（`query_traces` / `query_playground` / `ingestion_traces`）导入，未在 `requirements.txt` / `pyproject.toml` 显式声明，仅作为 streamlit 的传递依赖安装（`pip show pandas` → `Required-by: streamlit`）；非页面代码不依赖它。USAGE_GUIDE 10.9 原「删前确认 pandas」一条据此核销。

### 3.2 保留与迁移

| 路径 | 处理 |
|------|------|
| `src/observability/dashboard/services/`（8 个模块 + `__init__.py`） | 保留：`src/api/deps.py` 与全部 API 路由的共享服务层 |
| `src/observability/dashboard/__init__.py` | 保留：现状已只导出 services（`ConfigService` / `DataService` / `TraceService`），不 import pages，**无需改导出**；仅 docstring「Web Dashboard（阶段 G）」改为「API 层共享服务包」之类的现状描述 |
| `tests/unit/test_dashboard_services.py` 等 services 测试 | 保留：API 层契约测试，仅 SR3 的 fixture 行调整 |
| `tests/unit/test_evaluation_service.py:228`、`tests/unit/test_ingestion_service.py:176` 的 `test_service_has_no_streamlit_import`；`tests/unit/test_api_layering.py` 禁止导入表中的 `"streamlit"` | 保留：退役后仍是有效的回归守卫（防止 streamlit 被重新引入），不视为残留 |
| `tests/e2e_web/`（fake_api_server 等） | 保留：Web UI 冒烟，消费 services 而非 pages |
| `scripts/sidecar_manager.py` | 保留：共享托管编排，`start_api.py --manage-sidecar`（WS0）依赖 |
| `test_start_dashboard.py` 中的 `test_sidecar_healthy_http_error_means_alive`、`test_sidecar_healthy_connection_error_means_down`、`test_ensure_sidecar_skips_when_disabled`、`test_ensure_sidecar_reuses_healthy_instance`、`test_ensure_sidecar_spawns_and_waits_healthy`、`test_ensure_sidecar_script_exit_is_not_fatal` | **迁移**至新文件 `tests/unit/test_sidecar_manager.py`：改为直接导入 `scripts/sidecar_manager.py`（monkeypatch 目标从启动器再导出改为 `sidecar_manager` 模块本身），断言语义不变。原因：`tests/unit/test_start_api_sidecar.py` 把 `ensure_sidecar` 整体替换为 fake，不覆盖探活 / 复用 / 拉起 / 脚本退出这四条真实逻辑，直接删除会让保留模块失去覆盖 |

### 3.3 依赖方向（退役后）

```
src/api/ ──▶ observability/dashboard/services/ ──▶ core/libs
frontend/ (Next 静态导出) ──▶ start_api.py --serve-ui ──▶ src/api/
（Streamlit 页面层与 start_dashboard.py 不复存在；sidecar 托管唯一入口为 --manage-sidecar）
```

### 3.4 注释清理（SR3，纯注释、不改行为）

| 位置 | 处理 |
|------|------|
| `scripts/sidecar_manager.py:3-4` 模块 docstring | 删去「与 legacy `start_dashboard.py` 共用」，改为 Web UI 唯一入口 |
| `src/api/deps.py:46` | 「与 start_dashboard 口径一致」改为自述口径（任何 HTTP 响应都算存活） |
| `config/settings.yaml:155` `api.enabled` 行尾注释 | 去掉「Streamlit 不受影响」 |
| `src/api/routes/overview.py:40`、`src/api/routes/search.py:10`、`src/api/routes/traces.py:17` | 「与 Streamlit 总览/页/追踪页一致」改为直接陈述当前口径 |
| `src/observability/dashboard/services/evaluation_service.py:3,11`、`ingestion_service.py:3,11` docstring | 「Streamlit 页面与未来 FastAPI 路由共用」改为「供 `src/api/` 路由使用」；「不依赖 Streamlit」一句保留（与守卫测试呼应） |
| `frontend/components/ingestion/ManagedDocuments.tsx:14`、`tests/integration/test_api_search.py:220`、`tests/unit/test_dashboard_services.py:3` | 去掉「parity with the Streamlit page」「与 Streamlit 页同一条」「不启动 Streamlit」等对比措辞 |

`tests/fixtures/generate_qa_test_pdfs.py` 中的 Streamlit 字样是 QA 测试 PDF 的正文素材（模拟文档内容），不是代码口径，保留不改。

### 3.5 技能镜像清单（SR5）

| 镜像 | 处理 |
|------|------|
| `qa-tester/SKILL.md`（`.claude` / `.factory` / `.github` 三处） | description 与正文中的「Dashboard UI via Streamlit AppTest」模式、A–F 行与 AppTest 章节标注为「Streamlit 已退役，该模式失效」，不再指导执行 |
| `qa-tester/references/test_patterns.md`、`qa-tester/scripts/qa_validate_notes.py`（三处） | AppTest 模板段标注失效；脚本中如有按 AppTest 标记校验的逻辑，改为不再要求该标记（不改其余校验） |
| `.github/skills/qa-tester/QA_TEST_PLAN.md` | 第 52 行启动方式与 Dashboard UI 用例组（A–F）标注「Streamlit 退役，用例失效」 |
| `.github/skills/setup/SKILL.md` | 删除 `pip install streamlit`、`import streamlit` 自检、`start_dashboard.py` 启动与相关排障条目，改为 Web UI 启动（`start_api.py --serve-ui`） |
| `.github/skills/project-learner/SKILL.md` | D8.2「Streamlit App 分页」改为「services 层 + `src/api/` + `frontend/`」 |
| `resume-writer/references/project_highlights.md`（三处）、`.github/skills/project-review/references/question_bank.md`、`.github/skills/interview-prep/references/report_template.md` | Streamlit Dashboard 表述改为 Web UI（FastAPI + Next），与 README 简历示例保持一致 |
| `auto-coder/references/*.md`（`.claude` / `.factory` / `.github`） | **不手改**：这些文件是 `sync_spec.py` 从 frozen / specs 规格按章节切分生成的产物，手改会在下次同步时被覆盖，且其源头（01/06/08/09 等冻结规格）本身不改写 |

注意：上述多个技能文件在立项时工作区已有未提交改动，SR5 在其基础上叠加修改，不得覆盖或回退这些改动。

## 4. 测试方案

- **离线确定性**：SR 完成后 `pytest tests -q` 必须全绿；零网络纪律不变，外部依赖一律注入 fake。
- **预期计数**（SR6 记录实测值并与之核对）：待删三个文件共收集 38 项（`--collect-only`，均非 skip）；迁移 6 项、新增 1 项兼容用例，预期 **2464 − 38 + 6 + 1 = 2433 passed / 11 skipped**。如基线在执行前已变化，按同一公式重算。
- **既有测试语义变更清单**（本规格唯一一处，随 SR3 落地）：
  - `tests/unit/test_dashboard_services.py`：移除 `_VALID_YAML` 中的 `dashboard:` 节，以及 `test_config_service_loads_from_explicit_path` 中的 `settings.dashboard.port == 8502` 断言；该用例改为断言另一个仅在该 fixture 中出现的自定义值（如 `observability.log_file == "./logs/custom-traces.jsonl"`），保持「从显式路径加载」的验证语义。ConfigService / DataService / TraceService 其余契约断言全部保留。
  - `tests/unit/test_config_loading.py`：从主 fixture 中移除 `dashboard:` YAML 节（该文件原本没有针对 dashboard 的断言）；**新增** `test_legacy_dashboard_section_is_ignored`：含旧 `dashboard:` 节的配置仍可加载，且 `Settings` 不再有 `dashboard` 属性。
- **配置兼容行为（显式约定）**：删除后 `load_settings` 对未登记的顶层节不做校验，旧 `settings.yaml` 中残留的 `dashboard:` 节被静默忽略（仍保留在 `Settings.raw` 中），不会 fail-fast。这与「非法值 fail-fast」纪律不冲突：该纪律针对已登记字段的非法取值，已退役的节不属于登记字段。上述新增用例锁定这一行为。
- **迁移用例**：`tests/unit/test_sidecar_manager.py` 中的 6 个用例必须在不依赖 `start_dashboard.py` 的前提下通过（SR1 验收）。
- **删除的测试语义归属**：`test_dashboard_smoke.py` / `test_query_traces_source.py`（AppTest 页面冒烟）与 `test_start_dashboard.py` 剩余 4 个用例（`test_parse_headless_and_explicit_port`、`test_resolve_port_from_settings`、`test_deprecation_notice_points_to_web_ui`、`test_main_prints_deprecation_notice_to_stderr_only`）的语义随启动器与页面层退役一并废止，不迁移。
- **文档守卫**：`pytest tests/unit/test_docs_links.py` 必须全绿（MOC 无漏登记、全仓相对链接零坏链）。该测试会剥离 `#锚点` 只校验文件路径，锚点有效性需按 §7 USAGE_GUIDE 一行的约束人工保证。
- **门控真实冒烟不受影响**：`tests/e2e/test_chat_real_integration.py` 等门控测试仅消费 services，默认 skip 行为不变。

## 5. 任务排期与进度跟踪

| 编号 | 任务 | 验收标准 | 状态 |
|------|------|----------|------|
| SR0 | 删除页面层与启动入口（§3.1 前三行） | 文件不存在；在 `frontend/out/` 已构建、`api.enabled: true`（或加 `--force`）的前提下，`python scripts/start_api.py --serve-ui` 正常启动 Web UI | [ ] |
| SR1 | 迁移 6 个 sidecar_manager 用例至 `tests/unit/test_sidecar_manager.py`；删除 §3.1 三个测试文件 | 三个文件不存在；`pytest tests/unit/test_sidecar_manager.py` 6 passed；剩余测试可收集 | [ ] |
| SR2 | `requirements.txt` 与 `pyproject.toml` 删 `streamlit>=1.36` 并清理两处注释 | 两处均无 streamlit；`rg -n "^\s*(import\|from) streamlit" src tests scripts` 零命中（§3.2 所列守卫断言中的字符串不计） | [ ] |
| SR3 | 删 `DashboardConfig` 及 §3.1 所列 settings 关联代码、`settings.yaml` 的 `dashboard:` 节；按 §4 改两个测试文件并新增兼容用例；按 §3.4 清理注释 | `load_settings` 正常加载仓库配置与含旧 `dashboard:` 节的配置；两个测试文件改动后通过 | [ ] |
| SR4 | 文档同步（§7 回写清单全部条目） | `pytest tests/unit/test_docs_links.py` 全绿；README / AGENTS / USAGE_GUIDE 无残留的 `start_dashboard` 启动指引；`#109-streamlit-dashboardlegacy` 锚点仍可达 | [ ] |
| SR5 | 技能镜像同步（§3.5 清单） | 镜像内无会误导的 Streamlit 启动 / AppTest 指引；auto-coder 生成产物未被手改；未覆盖既有未提交改动 | [ ] |
| SR6 | 全量回归并记录计数变化 | `pytest tests -q` 全绿；记录实测计数并与 §4 预期（2433 passed / 11 skipped）核对，差异需说明 | [ ] |

依赖顺序：SR0 → SR1 → SR2 → SR3 →（SR4、SR5 可并行）→ SR6。SR0 删除启动器后、SR1 完成前，`test_start_dashboard.py` 与两个 AppTest 文件会失败，建议 SR0 与 SR1 在同一提交内完成，避免出现红色中间提交。

## 6. 验收清单

- [ ] 全仓不再存在 Streamlit 页面层、`start_dashboard.py`、页面级 AppTest 测试。
- [ ] `requirements.txt` 与 `pyproject.toml` 均不再含 `streamlit`；主 venv 无 streamlit 安装需求（已安装的旧环境可自行 `pip uninstall streamlit`，非验收项）。
- [ ] `sidecar_manager.py` 的探活 / 复用 / 拉起 / 脚本退出逻辑仍有单测覆盖（`tests/unit/test_sidecar_manager.py`）。
- [ ] `observability/dashboard/services/` 及其测试完整保留，`src/api/` 行为零变化。
- [ ] 旧配置中残留的 `dashboard:` 节被静默忽略，有用例锁定。
- [ ] `pytest tests -q` 离线全量全绿，计数变化已在 SR6 记录。
- [ ] `pytest tests/unit/test_docs_links.py` 全绿；MOC 谱系表含本规格的「单文件晋升」记录。
- [ ] README / AGENTS.md / USAGE_GUIDE / 技能镜像中不再有 Streamlit 启动指引；USAGE_GUIDE 10.9 改为退役记录且标题不变。
- [ ] 本规格收口后按治理流程冻结入 `frozen/`（编号 = 冻结时 frozen/ 最大编号 +1），如无遗留事项则不建派生台账。

## 7. 回写清单（收口时同步）

- [x] README「开发文档」导航表：移除台账、登记本规格（立项会话已在工作区同步）。
- [ ] README 其余：「运行手册」第 6 节删除「Streamlit Dashboard（已弃用 legacy）」小节（第 304 行起，含 `streamlit run` 命令、sidecar 托管说明与七页面 legacy 列表）；「核心能力一览」删除「Dashboard（legacy）」行；「技术亮点」第 72 行删去「Streamlit Dashboard 已降为 legacy」；「简历参考」三个示例（第 549 / 565 / 571 / 593 行）中的 Streamlit 改为 Web UI（FastAPI + Next）表述，与 resume-writer 技能素材一致。
- [ ] AGENTS.md：常用命令删除 `start_dashboard.py` 行；第 12 行 WC 规格引用中「Streamlit 已弃用 legacy、保留可用」与「Streamlit 删除条件见 USAGE_GUIDE 第 10 章」改为「Streamlit 已退役（见本规格）」；第 14 行 WS 规格引用中台账描述保持，`scripts/start_dashboard.py` 相关描述改为唯一入口 `--manage-sidecar`；第 63 行架构节 `src/observability/` 条目中「Streamlit Dashboard（`pages/` 七页面 + `services/` 业务封装；页面层禁止直接 import…）」改为「`dashboard/services/`（`src/api/` 共享服务层）」，「Dashboard 动态渲染」改为「Web UI 动态渲染」；文首「开发文档参考」登记本规格（如尚未登记）。
- [ ] USAGE_GUIDE：
  - 文首第 3 行「Streamlit 已降为 legacy」改为「Streamlit 已退役」；
  - 2.1 目录树：删 `start_dashboard.py` 行（第 83 行），第 74 行 observability 注释删去「+ Streamlit 页面（legacy）」；
  - 2.6 节：删除第 170–173 行 Streamlit 启动段；
  - 2.7 节：删除 Streamlit 页专属描述，标题与正文改为 Web UI Query Playground（保留 Deep 同步检索、无实时进度日志的说明及 10.6 链接）；
  - 第 5 章：删除「Streamlit legacy 路径」段，日常启动以 `start_api.py --serve-ui --manage-sidecar` 与手动启动 sidecar 两条路径为准；
  - 10 章引言：「与旧 Streamlit 七页逐项对照」保留为历史链接，补一句已退役；10.3 节第 477 行 `api.enabled` 注释去掉「Streamlit」；10.6 节第 560 行删去「Streamlit legacy 的 Deep 走流式…」一句；
  - 10.9 节：**标题保持 `### 10.9 Streamlit Dashboard（legacy）` 不变**（`README.md:311`、`USAGE_GUIDE.md:170`、`WEBUI_PARITY_AUDIT.md:155` 三处以锚点 `#109-streamlit-dashboardlegacy` 指向此处；前两处随所在段落删除或改写时可一并去掉，AUDIT 正文不可改写，标题一改该锚点即失效且 `test_docs_links.py` 查不出），正文改写为退役记录：删除条件表逐条核销（含 pandas 核销）、退役日期与本规格链接。
- [ ] `docs/frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md:161`：仅把指向 `start_dashboard.py` 的 Markdown 链接改为行内代码（§2.2 例外条款），不改其他内容。
- [ ] `docs/analysis/WEBUI_PARITY_AUDIT.md` 文件头加一行「退役执行见规格链接」（不改写正文）。
- [ ] `docs/backlog/OBSIDIAN_RAG_SPEC_backlog.md`：文件头加一行注记——第 48 / 74 / 133 / 141 行按 Streamlit 页面形态与 AppTest 冒烟设计的 M3/M4 交互方案在 Streamlit 退役后失效，立项时须改为 Web UI（`frontend/` + `src/api/`）形态重写；不改正文。
- [x] MOC：specs/ 分区索引登记本规格；backlog/ 分区移除台账；谱系表新增「Streamlit 退役（SR）」行（单文件晋升）（立项会话已在工作区同步）。
- [x] backlog/README「当前候选」移除台账（立项会话已在工作区同步）。
- [ ] 收口冻结时：MOC 谱系表本规格行的「规格」列更新为 frozen 路径；MOC 文档描述同步改为「已收口冻结」。
