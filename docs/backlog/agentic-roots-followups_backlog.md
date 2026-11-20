# Agentic 检索覆盖上传目录遗留事项台账

> 起源：[14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md](../frozen/14_AGENTIC_UPLOAD_ROOTS_SPEC_frozen.md) 收口遗留（其 §2.2 Out of Scope 明示「另立提案」的两项）。本台账只登记候选，不承诺排期；某项要推进时按单文件晋升另立提案或从台账拆分。
>
> 移交记录（2026-11-20）：候选 #1「相对 `allowed_roots` 改为按项目根解析」拆分移交 [specs/ROOT_ANCHOR_SPEC.md](../specs/ROOT_ANCHOR_SPEC.md)（RA 系列），细节已移入该规格，原文见 git 历史；本台账只剩候选 #2。

## 明确不做（继承规格 Out of Scope，不再重议）

- 修改 PathGuard 契约（根不存在即报错、fail-closed）——S1.3 已冻结并有测试锁定。
- 把上传目录移进 `data/documents/`——跨 collection 串数据且改变既有 doc_id。
- 修改 sidecar 侧配置（`SIRCHMUNK_*`）——白名单由本项目调用方执行。
- 扩展 agentic 白名单到任意用户目录（如 Obsidian vault）——归 [OBSIDIAN_RAG_SPEC_backlog.md](OBSIDIAN_RAG_SPEC_backlog.md)。

## 候选池

### 1. 相对 `allowed_roots` 改为按项目根解析（CWD 根治）——已移交

已于 2026-11-20 拆分移交 [specs/ROOT_ANCHOR_SPEC.md](../specs/ROOT_ANCHOR_SPEC.md)，现状复现、三问与决策见该规格第 1 章。

### 2. agentic 检索按 collection 隔离

- **登记**：2026-11-18，随 AR 规格冻结登记。
- **现状**：sidecar 按文件系统路径检索，不认识 collection；上传目录扁平，不同 collection 的上传文件在 agentic 模式下互相可见。Hybrid 主路（dense + BM25）按 collection 过滤，但 Hybrid 的 agentic 第三路（S2.3，`hybrid_search.py`）触发时同样只按 PathGuard 路径白名单过滤，也可能召回其他 collection 的上传文件。
- **关联问题**：上传同名文件跨 collection 互相覆盖（`save_uploads` 按文件名落盘，AR 规格 §2.2 列为既有行为）。同名文件先后上传到 A、B 两个 collection 后，磁盘文件已是 B 的版本；从 A 删除时，文件因仍被 B 引用而保留（AR2 删除联动），A 的索引与磁盘文件早已不一致。下方根治方案（按 collection 分子目录）可一并解决。
- **三问草案**：① 已观察问题 = 可见范围口径不同（Hybrid 主路按 collection，agentic 及 Hybrid 第三路按路径白名单），来自 AR 规格 §2.2 的设计分析，AR4 冒烟只覆盖单 collection，**尚无跨 collection 实测**；② 更小方案 = 在 USAGE_GUIDE / README 明示口径差异（**未做**：两处现均无此说明）；根治需改上传目录布局（如按 collection 分子目录），会改变 `source_path` / doc_id 并影响删除联动与白名单结构；③ 验证 = 同一上传文件在两个 collection 的可见性离线测试（含 Hybrid 第三路触发场景）。
- **优先级**：低（现单 collection 用法下无实际影响）。
