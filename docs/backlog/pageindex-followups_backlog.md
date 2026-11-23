# PageIndex PI 规格遗留事项（followups）

> 起源：[PAGEINDEX_TREE_RETRIEVAL_SPEC](../specs/PAGEINDEX_TREE_RETRIEVAL_SPEC.md)（PI 系列，开发中）。PI0 立项门收口（2026-11-23 D9 终裁达标，PI1 开工）时，按 PI0.7 任务把过程发现「派生台账条目、上游 issue 草稿、单测锁定项各自归位」；PI1–PI7 执行期间的新遗留继续记入本台账。
>
> 性质：第 1 类是决议与已知限制记录（非候选）；第 2 类是上游 issue 草稿（提交与否由用户决定，本项目侧已有规避）；第 3 类是可行动的候选池，推进任何一项按 [README 约定](README.md)另立提案；第 4 类是裁决保留的可选优化；第 5 类是 PI2 代码审查遗留的低危修复池（PI2.1 批次，待排期）。
>
> 关联：[03_SIRCHMUNK_INTEGRATION_SPEC_frozen](../frozen/03_SIRCHMUNK_INTEGRATION_SPEC_frozen.md)（进程隔离红线）、[18_AGENTIC_COLLECTION_ISOLATION_SPEC_frozen](../frozen/18_AGENTIC_COLLECTION_ISOLATION_SPEC_frozen.md)（collection 定界）。

---

## 1. 决议与已知限制

| # | 事项 | 决议 / 现状 |
|---|------|------------|
| 1 | **PDF 侧问题集仅 5 条且三路全 1.0、无区分度** | 用户终裁（2026-11-22，PI0.6 出口）：不补跨节归纳题，维持 5 题记为已知限制；后续若需要 PDF 侧门槛数据须扩题（补题须逐条确认，PI0.2 先例） |
| 2 | **agentic_deep 对 PDF 证据恒空** | PI0 判无效路维持（规格 10.3 ③ / 10.8 #5）；上游 issue 草稿见第 2 类 #2 |
| 3 | **主摄取链路无 PDF 页码 metadata**（规格 10.3 ④） | longdoc 的 PDF 页码取树节点（D8），主摄取链路不补（Out of Scope 不变）；hybrid 的 PDF 页码引用若要做，另立提案 |
| 4 | **语料目录不得置于 `data/db/` 下**（规格 10.3 ⑤） | 使用约束 + 设计约束（派生 PDF 必须在 `data/db/pageindex/derived/` 下且证据路径回映射原 MD）；规格内 PI1 单测 + PI2 运维文档锁定，此处留提醒 |
| 5 | **上游 `submit_document` 同 PDF 重复提交不去重**（规格 10.7 #5） | 上游存 `<hash>_1.pdf` 并生成第二个 doc；PI1 `tree_store` 按 `source_path → file_hash` 幂等规避；上游缺口随第 2 类 #3 一并观察 |

## 2. 上游 issue 草稿（sirchmunk / pageindex，提交与否由用户决定）

### 2.1 sirchmunk：auth middleware 把 401 包装成 500（规格 10.3 ②）

- **现象**：`sirchmunk/api/main.py:107` 的 auth middleware 捕获 `HTTPException(401)` 后统一返回 500 `INTERNAL_ERROR`，客户端无法区分「鉴权失败」与「服务端错误」。
- **影响**：本项目侧已用 env 注入 token 规避（不触发 401）；但对其他调用方是排障误导。
- **建议**：middleware 直接放行 `HTTPException`（re-raise），或按状态码分类响应。

### 2.2 sirchmunk：DEEP 模式对 PDF 文档证据片段恒空（规格 10.3 ③）

- **现象**：`mode=DEEP` 检索 PDF（`examples/documents/2023-annual-report.pdf`，222 页）返回 summary 但 source snippet 恒为空（"No source snippet was recorded"）；另一分支报 `ConfidenceDecision` 参数缺失。同一问题集对 Markdown 文档正常。
- **影响**：依赖 snippet 的下游（本项目 agentic 引用证据）对 PDF 拿不到任何证据，该路被迫判无效。
- **建议**：核查 DEEP 路径的 snippet 记录环节对 PDF 文档类型的处理；`ConfidenceDecision` 缺参分支补充默认值或显式报错。

### 2.3 pageindex：DocStore 不按内容去重（规格 10.7 #5）

- **现象**：`PageIndexClient.submit_document` 对同一 PDF 重复提交时存为 `<hash>_1.pdf` 并生成第二个 doc_id，不按内容哈希幂等。
- **影响**：调用方需自行幂等（本项目 PI1 `tree_store` 以 `file_hash` 为键规避）；重复提交浪费全量摘要 token。

## 3. 已知缺陷候选池（产品侧，推进须另立提案）

| # | 事项 | 出处 | 说明 |
|---|------|------|------|
| 1 | **摄取链路空文本防护**：PDF 空/扫描页文本 chunk 一路流到 embed 阶段才崩（`texts must be non-empty string`） | 规格 10.3 ① | IR 冻结契约对 pypdf 空文本「行为保持不变」、空文本按 load 失败记 `failed_stage` 只覆盖 markitdown loader；需在摄取链路补跳过或按 load 失败处理 + 单测 |
| 2 | **Chroma upsert 对 `images` 空 list 元数据的非空校验**：1.7 MB MD 摄取在 store 阶段失败并整体回滚（PI0.6 复测期间实测两轮复现） | 规格 10.8 #6 | 摄取历史不记 success，每次重跑都重试失败；与 #1 同属「异常内容流到 store 才崩」族，修复时可一并处理 |

## 4. 裁决保留的可选优化

| # | 事项 | 决议 |
|---|------|------|
| 1 | **「flash 结构 + 按需摘要」混合建树**（flash 零 LLM 树 1,375 节点 / 回定位 95.4%，无摘要；submit 树带摘要、检索更省） | 用户终裁（2026-11-23，PI0.7）：维持 submit 全量摘要为 PI1 基线；混合方案留本台账作可选优化，重开需新证据（如 token 成本再次成为瓶颈） |

## 5. PI2 代码审查遗留（PI2.1 批次，待排期）

> 来源：PI2 代码审查（commit `6c77fff`，2026-11-23）。审查 P2 中安全 / 契约类三项已随审查修复并带回归单测：`/index` `file_hash` 非 64 位 hex 拒绝（防派生 PDF 路径穿越写，规格 §3.4 已回填）、`/search` 非法 body 与非整数参数信封化（不裸抛 500）、`start_sidecar.sh` 相对参数按调用方 CWD 绝对化；既有测试 fixture 同步换真实 hex hash。以下三项低危暂缓：

| # | 事项 | 出处 | 说明 |
|---|------|------|------|
| 1 | **重索引孤儿派生 PDF 无 GC** | `scripts/pageindex/pi_adapter.py` `index()` | 派生 PDF 按内容哈希命名（D12①）：MD 内容变更后重索引落新 `<new_hash>.pdf` 并覆盖记录，旧 `<old_hash>.pdf` 无任何回收路径（`delete()` 只删当前记录的 `derived_pdf`），每次内容修订泄漏一份。修复：重渲染前对同 `source_path` 旧记录中不同的 `derived_pdf` unlink；或并入 PI4 对账同步做清理 |
| 2 | **`API_VERSION = "1.0"` 同批双份** | `scripts/pageindex/server.py` / `pi_adapter.py` | 同一提交引入两处同名常量：`pi_adapter` 供 `/status` 的 `version` 字段（契约面），`server` 仅喂 FastAPI `version=` 元数据，须人工保持一致；收敛为单一所有者（以 `status()` 为准） |
| 3 | **plan 中 `PAGEINDEX_STORAGE` 读取归属笔误** | `.agents/plans/PI2.md` | Files 第 2 条写 `main()` 读 `PAGEINDEX_STORAGE`，实际由 `pi_adapter.PageIndexAdapter.from_env()` 读取（运行行为无差异）；下次改动该 plan 时顺手更正 |
