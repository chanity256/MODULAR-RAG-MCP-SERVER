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
# 记录实际运行的上游 commit（KE0：editable 安装指向检出目录，实际版本随检出漂移，
# 启动日志必须落实际值供实验审计；见 SIRCHMUNK_KNOWLEDGE_EVOLUTION_SPEC KE0）。
SIRCHMUNK_SRC="$("$VENV_DIR/bin/python" -c 'import sirchmunk, os; print(os.path.dirname(os.path.dirname(os.path.abspath(sirchmunk.__file__))))' 2>/dev/null || true)"
if [ -n "$SIRCHMUNK_SRC" ] && git -C "$SIRCHMUNK_SRC" rev-parse HEAD >/dev/null 2>&1; then
  echo "== sidecar sirchmunk: $SIRCHMUNK_SRC @ $(git -C "$SIRCHMUNK_SRC" rev-parse HEAD) =="
else
  echo "== sidecar sirchmunk version: $("$VENV_DIR/bin/python" -c 'import sirchmunk; print(getattr(sirchmunk, "__version__", "unknown"))' 2>/dev/null || echo unknown) (no git metadata) =="
fi
# sirchmunk serve 自行加载 SIRCHMUNK_WORK_PATH/.env；此处导出 env 文件
# 以覆盖 host/token 等（set -a 使 export 自动应用于后续命令）。
set -a; source "$ENV_FILE"; set +a

# KE1：知识演化开关（SIRCHMUNK_KNOWLEDGE_EVOLUTION_SPEC 方案 B）。
# 开启分支：先跑 preflight 前置（演化依赖 embedding 模型），再改走 sidecar venv
# 内的 evolve_launcher.py（同一 ASGI 应用，显式传 host/port/log_level）。
# 关闭分支：保持原 `sirchmunk serve` 命令不变（回滚安全：改回 false 即恢复原入口）。
if [ "${SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION:-false}" = "true" ]; then
  echo "== knowledge evolution ENABLED (KE1): preflight 前置 → evolve_launcher.py =="
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  # preflight 前置必须读取与本分支相同的 env 文件（KE1 门控实测：不导出时
  # preflight 读默认 .env-sirchmunk，自定义 env 文件的复用/模型口径会被漏检）。
  SIRCHMUNK_ENV_FILE="$ENV_FILE" bash "${SIRCHMUNK_PREFLIGHT_SCRIPT:-$SCRIPT_DIR/preflight.sh}"
  exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/evolve_launcher.py" \
    --host "$HOST" --port "$PORT" --log-level "${SIRCHMUNK_LOG_LEVEL:-info}"
fi

exec "$VENV_DIR/bin/sirchmunk" serve --host "$HOST" --port "$PORT"
