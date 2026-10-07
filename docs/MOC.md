# 开发文档 MOC（docs/ 索引）

> 本文件是 docs/ 目录的**索引文档（MOC, Map of Content）**：docs/ 下增删文档后必须同步更新本文件。项目入口见 [根目录 README](../README.md)。

## specs/ — 开发规格（落点）

**立项后新规格的存放处**（从 [backlog/](backlog/) 晋升）。

| 文档 | 说明 |
|------|------|
| [INGESTION_ROBUSTNESS_SPEC.md](specs/INGESTION_ROBUSTNESS_SPEC.md) | 摄取管线健壮性规格（IR 系列，IR0–IR6 已交付收口，实现偏差见 7.1；现状审计 A1–A7、历史表 v2 / 版本替换 / 同步模式 / 重试诊断 / 通用格式 Loader） |

新规格格式对齐 frozen/ 中 SIRCHMUNK / OBSIDIAN 先例：含模块设计、测试方案、任务排期与进度跟踪表，任务编号用新前缀避免冲突。

## frozen/ — 已冻结文档

任务全部收口、不再新增或改动任务的规格与台账，作为架构与设计参考保留。

| 文档 | 说明 |
|------|------|
| [DEV_SPEC.md](frozen/DEV_SPEC.md) | 主开发规格：架构设计、模块设计、技术选型、测试方案、阶段 A–I 排期（已冻结为架构参考） |
| [SIRCHMUNK_INTEGRATION_SPEC.md](frozen/SIRCHMUNK_INTEGRATION_SPEC.md) | Sirchmunk Agentic 检索整合规格（S 系列，S0–S4 已交付收口，G0 终裁记录） |
| [ACCEPTANCE_GAPS.md](frozen/ACCEPTANCE_GAPS.md) | 验收缺口台账（G1–G3，已关闭） |

## analysis/ — 分析与规划

架构判断文档，非可执行排期。

| 文档 | 说明 |
|------|------|
| [MICROSERVICES_ANALYSIS.md](analysis/MICROSERVICES_ANALYSIS.md) | 微服务架构分析：当前模块化单体判断与演进方向 |
| [SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md](analysis/SIRCHMUNK_CAPABILITY_GAP_ANALYSIS.md) | Sirchmunk 能力覆盖差距分析：已接入与未接入能力对照、对 G0 结论的影响（非排期） |

## backlog/ — 候选功能与待开发规格

已有初步想法、但尚未开发排期的功能提案与待开发规格。约定见 [backlog/README.md](backlog/README.md)。

| 文档 | 说明 |
|------|------|
| [OBSIDIAN_RAG_SPEC.md](backlog/OBSIDIAN_RAG_SPEC.md) | Obsidian Vault RAG 扩展规格（V 系列，任务 V1–V7 全部未开始；含完整模块设计与验收标准，待排期） |

## reference/ — 使用参考

面向使用者的速查手册与路线图。

| 文档 | 说明 |
|------|------|
| [USAGE_GUIDE.md](reference/USAGE_GUIDE.md) | 项目使用指南：主项目用法、Sirchmunk sidecar 搭建与日常启动、常见问题排查、安全边界 |
| [FUTURE_OPTIMIZATION_ROADMAP.md](reference/FUTURE_OPTIMIZATION_ROADMAP.md) | 后续优化路线图：Dashboard 前后端分离迁移及其他候选方向（含第 10 章决策原则） |
