# QA Test Progress

> Generated: 2026-11-23（SD4.3 每周 QA 离线回归 automation）

> Total: 2（本文件当前仅登记 SD4.3 周度离线回归发现的待修复项；QA 计划全量用例执行另见 qa-tester 会话）

> ✅ Pass: 0 | ❌ Fail: 0 | ⏭️ Skip: 0 | 🔧 Fix: 2 | ⬜ Pending: 0

## SD4.3 离线回归登记 — 2026-11-23

- HEAD: `c4807b7`（main，Merge PR #29 feat/pi-pageindex）
- pytest（与 `.github/workflows/ci.yml` 逐字同源，禁网）：
  **3011 passed / 2 failed / 23 skipped / 1 xfailed**（total 3037，exit 1）
- `python scripts/check_redlines.py`：**OK (0 violations)**
- 上游 CI 同 commit：**success**（本地 2 个失败为本地 venv 环境漂移，非 main 代码回归，见下）

| 状态 | QA 用例 ID | pytest 节点 | 一句话原因 |
|---|---|---|---|
| 🔧 | （QA_PYTEST_MAP 无对应用例，仅记 pytest 节点） | `tests/unit/test_loader_pdf_contract.py::test_engine_auto_falls_back_to_pypdf_without_markitdown` | 本地 venv 装有 markitdown 0.1.8 且其 [pdf] 依赖（pdfminer.six/pdfplumber）于 2026-11-16 后被补装，`engine="auto"` 下真实转换成功不再回退 pypdf；用例仅 `monkeypatch.delitem(sys.modules, "markitdown")`，无法阻止已安装包被重新导入 |
| 🔧 | （QA_PYTEST_MAP 无对应用例，仅记 pytest 节点） | `tests/unit/test_loader_pdf_contract.py::test_engine_markitdown_required_but_missing_raises` | 同一环境漂移：markitdown 可导入（未抛 ImportError）故不抛 `PdfLoaderError`；CI 只装 `markitdown[docx,pptx,xlsx]`（无 pdfminer.six），同 commit 上通过 |

### Triage 备注（供人处理；automation 不修）

- 两个用例对环境敏感：任何装齐 markitdown[pdf] 依赖的 venv 都会红（本轮已实测：阻断 markitdown 导入后同文件 17/17 通过）。若判定为测试设计问题（模拟"未安装"的手段不足），请另起任务改用更强的隔离手段（如 import hook / 隔离 venv），不得弱化或删除断言。
- 本轮与上周（2026-11-16，`c28b50f`，2811/21/1 全绿）计数差异逐项归因见 automation 报告；main 侧自上周新增 204 个测试（MR/RA/AC/AR/PI 系列），skip +2 来自新增 `tests/e2e/test_mode_router_real_integration.py`（MR5）。
