# Sample Document（说明文件，非 ingest 输入）

这是一个说明文件——**ingest 只支持 `.pdf`，本文件不会被摄取**，仅用于占位说明。

目录下的 `*.pdf` 是真实摄取验收（I5/G1 收口）用的样例文档；端到端测试
（`tests/e2e/test_data_ingestion.py`）则使用 `tests/fixtures/pdf_minimal.py`
的生成器在临时目录即时构造 PDF，不依赖仓库内的文件。

## 示例章节一

RAG（Retrieval-Augmented Generation）通过检索外部知识库增强生成质量。

## 示例章节二

MCP（Model Context Protocol）定义了 AI 助手与外部工具之间的标准通信协议。
