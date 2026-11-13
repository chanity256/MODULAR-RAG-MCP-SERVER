#!/usr/bin/env bash
# 启动 Sirchmunk sidecar（独立 venv）— 阶段 S0.1。
# 绑定 127.0.0.1 + Bearer 鉴权；启动后用 preflight.sh 验证健康。
# 用法: bash scripts/sirchmunk/start_sidecar.sh [venv_dir] [env_file]
set -euo pipefail

VENV_DIR="${1:-${SIRCHMUNK_VENV:-.venv-sirchmunk}}"
ENV_FILE="${2:-${SIRCHMUNK_ENV_FILE:-.env-sirchmunk}}"
HOST="${SIRCHMUNK_HOST:-127.0.0.1}"
PORT="${SIRCHMUNK_PORT:-8584}"

[ -x "$VENV_DIR/bin/sirchmunk" ] || { echo "sirchmunk not installed in $VENV_DIR (run setup_venv.sh first)"; exit 1; }
[ -f "$ENV_FILE" ] || { echo "env file $ENV_FILE missing (cp scripts/sirchmunk/env.example $ENV_FILE)"; exit 1; }

echo "== start sidecar: host=$HOST port=$PORT (env: $ENV_FILE) =="
# sirchmunk serve 自行加载 SIRCHMUNK_WORK_PATH/.env；此处导出 env 文件
# 以覆盖 host/token 等（set -a 使 export 自动应用于后续命令）。
set -a; source "$ENV_FILE"; set +a
exec "$VENV_DIR/bin/sirchmunk" serve --host "$HOST" --port "$PORT"
