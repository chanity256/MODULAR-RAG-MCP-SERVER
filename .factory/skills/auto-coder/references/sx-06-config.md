## 6. 配置变更汇总

### 6.1 主项目 `config/settings.yaml`（SX2，可选键）

```yaml
agentic_search:
  # 既有键不变；新增：
  max_depth: null            # 目录扫描深度上限（int 或 null=上游默认）
  enable_dir_scan: true      # DEEP 目录扫描开关
```

### 6.2 Sidecar `.env-sirchmunk`（SX3/SX4，env.example 模板增补）

```bash
# SX3：编译默认路径（compile.sh 未显式传 -- <paths> 时使用；相对仓库根，空格分隔）
# 不要放 "." 或 data/documents（前者会扫进 .venv/build，后者是无关的用户文档）
SIRCHMUNK_COMPILE_PATHS="README.md docs"

# SX4：知识簇复用（默认注释关闭；开启前先跑 preflight 预下载模型）
# SIRCHMUNK_ENABLE_CLUSTER_REUSE=true
# CLUSTER_SIM_THRESHOLD=0.85
# CLUSTER_SIM_TOP_K=3
# EMBEDDING_MODEL_ID=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
# EMBEDDING_CACHE_DIR=${SIRCHMUNK_WORK_PATH}/.cache/models
```

---
