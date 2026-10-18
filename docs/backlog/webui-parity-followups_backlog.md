# WebUI 双轨核验遗留差异（WebUI Parity Follow-ups）

> **起源**：[WEBUI_CHAT_SPEC](../frozen/06_WEBUI_CHAT_SPEC_frozen.md) WC10.1 双轨功能等价核验（2026-10-14，逐项矩阵与缺口清单见 [analysis/WEBUI_PARITY_AUDIT.md](../analysis/WEBUI_PARITY_AUDIT.md)）。
> **状态**：候选池，**未立项**。下列各项均不阻塞 Web UI 作为主界面；Streamlit 保留为 legacy（不删除代码），这些差异是物理删除 Streamlit 之前应再评估的清单。
> **移交记录（2026-10-18）**：#5（追踪列表翻页）、#2（评估趋势折线图）及 #3 中的 422 预检已承接至 [frozen/08_WEBUI_PARITY_SPEC_frozen.md](../frozen/08_WEBUI_PARITY_SPEC_frozen.md)（WP 系列，已收口冻结），细节见该规格；本台账保留 #1、#4 与 #3 的其余差异。

## 1. 决策三问

1. **它解决了哪个已经观察到的问题？**——WC10.1 逐项对照七个 Streamlit 页面与 Web 页面，发现的能力差异（#1、#2、#4、#5）与展示差异（#3）。其余项已等价或在核验中直接修复（Query Traces 的 Chat 阶段面板与来源附注，审计文档 L2）。其中 #2/#5 与 #3 的预检已移交 WP 规格，此处仅剩 #1、#4 与 #3 的接受项。
2. **是否有更简单、改动更小的方案？**——#1 可以不新增端点：Chat 已有 `search_log` 帧通道，Playground Deep 可复用同一套 sidecar 日志转帧代码。
3. **如何确认它确实有效？**——#1 用 fake sidecar 日志流 + Playwright 断言进度日志逐条出现（#2/#3 预检的验收方式已并入 [WEBUI_PARITY_SPEC](../frozen/08_WEBUI_PARITY_SPEC_frozen.md) 第 4 章）。

## 2. 遗留差异

| # | 差异 | Streamlit 行为 | Web 现状 | 取舍与建议 |
|---|------|---------------|---------|-----------|
| 1 | **Query Playground Deep 模式缺实时进度日志**（缺失） | Deep 经 `SearchService.run_stream` 在 `st.status` 内实时追加 sidecar 进度日志（SX5，最近 30 行、单行截断 300 字） | `POST /api/v1/search` 走同步 `SearchService.run`，Deep 只显示 spinner，完成后一次性出结果 | 页面级便利，功能结果等价。补齐需新增流式端点（WS，对齐 Chat 的 `search_log` 帧）。**注意**：`run_stream` 命中 SX5.3 的 sidecar 流式线程泄漏（见 [sirchmunk-extension-followups](sirchmunk-extension-followups_backlog.md) #1），与 Chat 的 `agentic_progress_stream` 同一风险，应默认关闭或随上游修复再做 |
| 3 | **展示细节差异**（差异，接受） | 评估页预填默认 golden set 路径；评估/Playground 模式按钮三色；Query Traces 在无 Dense/Sparse 命中时仍显示两个空标题；golden set 路径不存在时页面立即报错 | 路径留空表示用 `evaluation.golden_test_set` 默认值（占位符提示）；模式按钮单色选中态；Dense/Sparse 两区仅在任一有命中时出现；路径不存在时任务失败并在进度区显示错误 | 结果等价，仅交互形态不同，不单独排期（其中「提交前校验路径存在」的 422 预检已移交 [WEBUI_PARITY_SPEC](../frozen/08_WEBUI_PARITY_SPEC_frozen.md) WP2，已交付） |
| 4 | **sidecar 托管编排仅在 Streamlit 启动器**（缺失，审计 L1） | `start_dashboard.py` 探活 → 拉起 `start_sidecar.sh` → 等健康 → 随进程回收 | `start_api.py` 无此编排，agentic 模式需手动 `bash scripts/sirchmunk/start_sidecar.sh` | 属「Streamlit 独有逻辑」，删除 Streamlit 前二选一：抽到共享模块并给 `start_api.py` 加 `--manage-sidecar`，或文档声明手动启动并接受 |

## 3. 退役门槛关联

物理删除 `src/observability/dashboard/`（pages 与对应测试）与 `scripts/start_dashboard.py` 的前置条件：

- WC10.3 门控冒烟通过、规格冻结；
- 上表 #1 / #4 已补齐或明确放弃（#2 / #5 已由 [WEBUI_PARITY_SPEC](../frozen/08_WEBUI_PARITY_SPEC_frozen.md) 承接并交付，视为已补齐）；
- 使用者确认不再依赖 Streamlit（USAGE_GUIDE 10.9「Streamlit Dashboard（legacy）」一节有删除条件说明）。

`DataService` / `TraceService` / `ConfigService` / `SearchService` 等服务层是 API 层的依赖，**不随 Streamlit 删除**；只删页面层与启动脚本。
