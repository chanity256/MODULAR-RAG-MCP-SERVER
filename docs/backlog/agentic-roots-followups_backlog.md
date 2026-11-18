# Agentic 检索覆盖上传目录遗留事项台账

> 起源：[14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md](../frozen/14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md) 收口遗留（其 §2.2 Out of Scope 明示「另立提案」的两项）。本台账只登记候选，不承诺排期；某项要推进时按单文件晋升另立提案或从台账拆分。

## 明确不做（继承规格 Out of Scope，不再重议）

- 修改 PathGuard 契约（根不存在即报错、fail-closed）——S1.3 已冻结并有测试锁定。
- 把上传目录移进 `data/documents/`——跨 collection 串数据且改变既有 doc_id。
- 修改 sidecar 侧配置（`SIRCHMUNK_*`）——白名单由本项目调用方执行。

## 候选池

### 1. 相对 `allowed_roots` 改为按项目根解析（CWD 根治）

- **现状**：`./data/documents` 与 `./data/uploads` 均按启动进程 CWD 解析；从仓库根以外启动时根不存在，PathGuard fail-closed 拒绝，整个 agentic 工具不可用。USAGE_GUIDE 现以「须从仓库根启动」文档化规避。
- **三问草案**：① 已观察问题 = 文档化约定依赖人的记忆，干净环境误启动即整体不可用（AR0 核查时实证）；② 更小方案 = 仅文档提示（已做）；根治需引入项目根锚点（如配置文件位置或环境变量），影响所有现有根与 sidecar 相对路径约定（spec 4.6 证据路径按 sidecar CWD 回显）；③ 验证 = 任意 CWD 启动的入口离线测试。
- **优先级**：低（文档化规避已覆盖主场景）。

### 2. agentic 检索按 collection 隔离

- **现状**：sidecar 按文件系统路径检索，不认识 collection；上传目录扁平，不同 collection 的上传文件在 agentic 模式下互相可见（hybrid 主路按 collection 过滤，行为不一致）。
- **三问草案**：① 已观察问题 = AR4 实测 hybrid 与 agentic 的可见范围口径不同（hybrid 按 collection、agentic 按路径白名单）；② 更小方案 = 文档明示口径差异（已做）；根治需改上传目录布局（如按 collection 分子目录），会改变 `source_path` / doc_id 并影响删除联动与白名单结构；③ 验证 = 同一上传文件在两个 collection 的可见性离线测试。
- **优先级**：低（现单 collection 用法下无实际影响）。
