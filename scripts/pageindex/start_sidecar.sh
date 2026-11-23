#!/usr/bin/env bash
# 启动 PageIndex sidecar（独立 venv）— PI2（PAGEINDEX_TREE_RETRIEVAL_SPEC §3.4）。
# 绑定 loopback + Bearer 鉴权；主 venv 永不 import pageindex（红线）。
# 用法: bash scripts/pageindex/start_sidecar.sh [venv_dir] [env_file]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="${1:-${PAGEINDEX_VENV:-$REPO_ROOT/.venv-pageindex}}"
ENV_FILE="${2:-${PAGEINDEX_ENV_FILE:-$REPO_ROOT/.env-pageindex}}"

# 相对参数按调用方 CWD 绝对化：下方存在性检查与 cd 到仓库根后的
# source / exec 必须指向同一路径（否则非仓库根启动时检查通过、执行失败）。
[ "$VENV_DIR" = "${VENV_DIR#/}" ] && VENV_DIR="$PWD/$VENV_DIR"
[ "$ENV_FILE" = "${ENV_FILE#/}" ] && ENV_FILE="$PWD/$ENV_FILE"

[ -x "$VENV_DIR/bin/python" ] || { echo "pageindex venv missing at $VENV_DIR (run scripts/pageindex/setup_venv.sh first)"; exit 1; }
[ -f "$ENV_FILE" ] || { echo "env file $ENV_FILE missing (cp scripts/pageindex/env.example $ENV_FILE)"; exit 1; }

# 相对路径 env（PAGEINDEX_STORAGE 默认 ./data/db/pageindex）按仓库根解析（RA 口径）。
cd "$REPO_ROOT"

# 导出 env 文件（set -a 使 export 自动应用于后续命令），覆盖 host/token/storage/LLM。
set -a; source "$ENV_FILE"; set +a

HOST="${PAGEINDEX_HOST:-127.0.0.1}"
PORT="${PAGEINDEX_PORT:-8585}"

echo "== start pageindex sidecar: host=$HOST port=$PORT (env: $ENV_FILE) =="
# 启动日志落实际安装的上游版本（对齐 KE0：实际版本随 venv 漂移，须打印实际值供审计）。
echo "== pageindex version: $("$VENV_DIR/bin/python" -c 'from importlib.metadata import version; print(version("pageindex"))' 2>/dev/null || echo unknown) =="

exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/server.py"
