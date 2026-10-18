# WebUI 双轨核验遗留差异（WebUI Parity Follow-ups）

> **起源**：[WEBUI_CHAT_SPEC](../frozen/06_WEBUI_CHAT_SPEC_frozen.md) WC10.1 双轨功能等价核验（2026-10-14，逐项矩阵与缺口清单见 [analysis/WEBUI_PARITY_AUDIT.md](../analysis/WEBUI_PARITY_AUDIT.md)）。
> **状态**：候选池，**未立项**。下列各项均不阻塞 Web UI 作为主界面；Streamlit 保留为 legacy（不删除代码），这些差异是物理删除 Streamlit 之前应再评估的清单。
> **移交记录一（2026-10-18）**：#5（追踪列表翻页）、#2（评估趋势折线图）及 #3 中的 422 预检已承接至 [frozen/08_WEBUI_PARITY_SPEC_frozen.md](../frozen/08_WEBUI_PARITY_SPEC_frozen.md)（WP 系列，已收口冻结）。
> **移交记录二（2026-10-18）**：#4（sidecar 托管编排）、#1（Deep 实时进度日志）已承接至 [../specs/WEBUI_SIDECAR_SPEC.md](../specs/WEBUI_SIDECAR_SPEC.md)（WS 系列，决策依据见其版本引言）；本台账剩余 #3 的展示细节接受项，实现细节不再在此保留（见 git 历史）。

## 1. 决策三问

1. **它解决了哪个已经观察到的问题？**——WC10.1 逐项对照七个 Streamlit 页面与 Web 页面，发现的能力差异（#1、#2、#4、#5）与展示差异（#3）。其中 #1/#2/#4/#5 及 #3 的预检已全部移交（WP、WS 两轮规格），本台账仅保留 #3 的接受项作为决议记录。
2. **是否有更简单、改动更小的方案？**——已随承接规格逐项裁决（WP 用既有组件复用；WS 复用 Chat 的 `search_log` 帧通道与 `SearchService.run_stream`，不新增第二套流式实现）。
3. **如何确认它确实有效？**——验收方式已并入各承接规格的测试方案章（WP 第 3 章；WS 第 3 章：fake sidecar 日志流 + Playwright 断言进度日志逐条出现）。

## 2. 遗留差异

| # | 差异 | Streamlit 行为 | Web 现状 | 取舍与建议 |
|---|------|---------------|---------|-----------|
| 3 | **展示细节差异**（差异，接受） | 评估页预填默认 golden set 路径；评估/Playground 模式按钮三色；Query Traces 在无 Dense/Sparse 命中时仍显示两个空标题；golden set 路径不存在时页面立即报错 | 路径留空表示用 `evaluation.golden_test_set` 默认值（占位符提示）；模式按钮单色选中态；Dense/Sparse 两区仅在任一有命中时出现；路径不存在时任务失败并在进度区显示错误 | 结果等价，仅交互形态不同，不单独排期（其中「提交前校验路径存在」的 422 预检已由 WP2 交付） |

> 已移交项（保留一行索引，细节见各规格）：#1 Deep 实时进度日志、#4 sidecar 托管编排 → [WEBUI_SIDECAR_SPEC](../specs/WEBUI_SIDECAR_SPEC.md)（WS 系列，进行中）；#5 翻页、#2 趋势图、#3 预检 → [frozen/08_WEBUI_PARITY_SPEC_frozen.md](../frozen/08_WEBUI_PARITY_SPEC_frozen.md)（WP 系列，已收口）。

## 3. 退役门槛关联

物理删除 `src/observability/dashboard/`（pages 与对应测试）与 `scripts/start_dashboard.py` 的前置条件：

- WC10.3 门控冒烟通过、规格冻结；
- 上表 #1 / #4 已补齐或明确放弃（已移交 [WEBUI_SIDECAR_SPEC](../specs/WEBUI_SIDECAR_SPEC.md) 承接，WS 系列收口即视为已补齐；#2 / #5 已由 WP 规格交付，视为已补齐）；
- 使用者确认不再依赖 Streamlit（USAGE_GUIDE 10.9「Streamlit Dashboard（legacy）」一节有删除条件说明）。

`DataService` / `TraceService` / `ConfigService` / `SearchService` 等服务层是 API 层的依赖，**不随 Streamlit 删除**；只删页面层与启动脚本。
