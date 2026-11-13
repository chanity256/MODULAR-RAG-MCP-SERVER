# Agent Note: PdfLoader 对 markitdown 原始字节透传的守卫

Status: implemented

## Problem

`test_cli_broken_pdf_maps_to_failed_file_not_crash`（IR 期间在干净 HEAD 复现的既有失败）期望畸形 PDF 在 load 阶段失败、CLI 记 `1 failed`，实际却 `3 ingested`、退出码 0。诊断链：

- 测试的 `broken.pdf`（`%PDF-1.4` 头 + 垃圾正文）走 engine=auto 的 markitdown 路；markitdown 0.1.8 的 PdfConverter 因缺 `[pdf]` 依赖抛 `MissingDependencyException`，但该异常被 markitdown 内部吞掉，落进纯文本兜底转换器——**把原始 PDF 字节当文本"成功"返回**（`'%PDF-1.4\nthis is not a real pdf body\n%%EOF'`），不抛异常。
- `PdfLoader._extract_text` 对 markitdown 结果无校验；空文本是合法的（扫描件 PDF 语义），垃圾透传文本因此被当成功转换 → Document 正常入库。
- 旁证：`_extract_images` / `_safe_page_count` 的 `PdfReader` 全部抛 `startxref not found`，但各自被 try/except 降级成 warning——文本提取若真走 pypdf 早该失败，恰恰证明 markitdown 路没有抛。
- IR5/IR6 收口时该测试是绿的：当时的 markitdown 对此输入会抛异常走 pypdf 降级。是 markitdown 升级（依赖漂移）暴露的 loader 契约缺口，不是代码回归。

## Decision

`_extract_text` 的 markitdown 成功路径加一道守卫：转换结果 `lstrip()` 后以 `%PDF-` 开头（原始 PDF 字节透传的标志）即视为转换失败——在既有 try 块内 raise `PdfLoaderError`，自然复用既有的两个 except 分支：

- `engine="auto"`：warning（含 `markitdown[pdf]` 安装指引）后降级 pypdf——畸形 PDF 经 pypdf 真正抛 `PdfReadError`，load 失败，CLI 恢复 `1 failed` 语义；
- `engine="markitdown"`（强制）：转 `PdfLoaderError` 上抛，与其它转换失败同语义。

## Alternatives considered

- **pypdf 先行校验（先严格解析再决定引擎）**：最强论据是从根上识别畸形 PDF。被否：每个文件多做一次完整 `PdfReader`（图片/页数已是独立 PdfReader 调用，再加一次重复解析），且 pypdf 的容错版本也可能变——守卫应钉在「透传」这个直接症状上。
- **空文本也视为失败**：被否，扫描件 PDF 抽取为空是既定合法语义（IR 规格 2.2 Out of Scope 明确保留），不能一刀切。
- **要求 `markitdown[pdf]` 依赖**：最强论据是 PdfConverter 真正可用、不再有透传路。被否为即时方案：主依赖零新增是 IR5 的既定纪律，且透传守卫对装了 `[pdf]` 的环境也无害（真转换不会以 `%PDF-` 开头）。

## Consequences

- **收益**：畸形 PDF 恢复「load 失败 → CLI 记 failed」的既定语义；守卫同时覆盖「markitdown 未来再把字节透传」的同类漂移。
- **代价**：理论上存在以 `%PDF-` 开头的「合法 markitdown 产出」（例如 PDF 正文里逐字讨论 `%PDF-` 标记且恰在文首）会被误判降级——降级后 pypdf 仍能正确解析出文本，仅引擎记录不同，无误入库风险。
- **范围外**：`MarkitdownLoader`（docx/pptx/xlsx/html）可能有同类透传面（如损坏 docx 被当文本返回），本次未扩——它已有空文本报错兜底，且无失败用例驱动，留作观察。

## Verification

- `tests/unit/test_loader_pdf_contract.py` 新增 2 例：auto 引擎透传 → 降级 pypdf 真解析（`last_engine == "pypdf"`）；强制 markitdown 引擎透传 → `PdfLoaderError` 含 `raw PDF content`。
- `tests/e2e/test_data_ingestion.py::test_cli_broken_pdf_maps_to_failed_file_not_crash` 转正；全量 `pytest tests` 离线全绿（0 failed）。
