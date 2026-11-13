## S0.2 旁路对比结果（mode=DEEP）

| id | 类别 | source 级命中 | 耗时 ms | 墙钟 ms | 睡眠疑似 | LLM 调用 | Token | 首个证据文件 |
|----|------|--------------|---------|---------|----------|----------|-------|--------------|
| Q5 | multihop | ✅ | 12824.3 | 12824.2 | - | 6 | 66421 | /Users/wanghaonan/Desktop/Project/Job-Search/reference/MODULAR-RAG-MCP-SERVER/src/ingestion/embedding/sparse_encoder.py |
| Q6 | multihop | ❌ | 12077.3 | 12077.2 | - | 0 | 0 | /Users/wanghaonan/Desktop/Project/Job-Search/reference/MODULAR-RAG-MCP-SERVER |

分类命中汇总：
- multihop: 1/2
