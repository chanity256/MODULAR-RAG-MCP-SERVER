#!/usr/bin/env bash
# Sirchmunk sidecar preflight — 阶段 S0.1（SIRCHMUNK_INTEGRATION_SPEC 4.1）。
# 检查 rg / rga 二进制与 sidecar 健康状态；知识簇复用开启时预下载 embedding
# 模型（SX4.1，SIRCHMUNK_SKIP_MODEL_PRELOAD=1 可跳过）；任一失败即非零退出。
# 用法: bash scripts/sirchmunk/preflight.sh [sidecar_base_url]
# 环境变量: SIRCHMUNK_ENV_FILE（默认 .env-sirchmunk）、SIRCHMUNK_VENV（默认 .venv-sirchmunk）
set -uo pipefail

BASE_URL="${1:-${SIRCHMUNK_BASE_URL:-http://127.0.0.1:8584}}"
fail=0

echo "== preflight: sidecar dependencies =="

# 1. rg
if command -v rg >/dev/null 2>&1; then
  echo "[ok] rg: $(command -v rg) ($(rg --version | head -1))"
else
  echo "[FAIL] rg not found. Install: brew install ripgrep"
  fail=1
fi

# 2. rga（缺失时上游构造器会自动联网下载 —— 禁止，见 spec 4.1）
if command -v rga >/dev/null 2>&1; then
  echo "[ok] rga: $(command -v rga) ($(rga --version | head -1))"
else
  echo "[FAIL] rga not found. Install: brew install ripgrep-all"
  echo "       (sirchmunk would otherwise download binaries from GitHub at runtime;"
  echo "        offline construction raises RuntimeError — never rely on auto-install)"
  fail=1
fi

# 3. sidecar 健康检查（未启动时提示而非失败，便于分步搭建）
# sidecar 对 /api/ 路由强制 Bearer 鉴权（spec 3.2），须携带 token；
# token 从 env 文件读取（与 start_sidecar.sh 同源），缺失时按未鉴权探测。
ENV_FILE="${SIRCHMUNK_ENV_FILE:-.env-sirchmunk}"
AUTH_HEADER=()
if [ -z "${SIRCHMUNK_API_TOKEN:-}" ] && [ -f "$ENV_FILE" ]; then
  SIRCHMUNK_API_TOKEN="$(grep -E '^SIRCHMUNK_API_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
fi
if [ -n "${SIRCHMUNK_API_TOKEN:-}" ]; then
  AUTH_HEADER=(-H "Authorization: Bearer ${SIRCHMUNK_API_TOKEN}")
fi
echo "-- health: GET ${BASE_URL}/api/v1/search/status"
# ${arr[@]+...} 形式：bash 3.2（macOS 自带）在 set -u 下展开空数组会报 unbound variable。
if curl -sf --max-time 5 ${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"} "${BASE_URL}/api/v1/search/status" >/dev/null 2>&1; then
  echo "[ok] sidecar is up at ${BASE_URL}"
else
  echo "[warn] sidecar not reachable at ${BASE_URL} (start with scripts/sirchmunk/start_sidecar.sh)"
  [ "${SIRCHMUNK_REQUIRE_SIDECAR:-0}" = "1" ] && fail=1
fi

# 4. 知识簇复用的 embedding 模型预下载（SX4.1，可选）
# 读取方式与 start_sidecar.sh 一致（env 文件 source 后覆盖进程环境），
# 未设置时按上游语义视为开启（api/search.py 缺省 "true"）。
echo "-- cluster reuse: embedding model"
VENV_DIR="${SIRCHMUNK_VENV:-.venv-sirchmunk}"
reuse="${SIRCHMUNK_ENABLE_CLUSTER_REUSE-__unset__}"
work_path="${SIRCHMUNK_WORK_PATH:-}"
cache_dir="${EMBEDDING_CACHE_DIR:-}"
model_id="${EMBEDDING_MODEL_ID:-}"
if [ -f "$ENV_FILE" ]; then
  env_vals="$(
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE" >/dev/null 2>&1
    printf '%s\n%s\n%s\n%s\n' "${SIRCHMUNK_ENABLE_CLUSTER_REUSE-__unset__}" \
      "${SIRCHMUNK_WORK_PATH:-}" "${EMBEDDING_CACHE_DIR:-}" "${EMBEDDING_MODEL_ID:-}"
  )"
  { read -r reuse; read -r work_path; read -r cache_dir; read -r model_id; } <<< "$env_vals"
fi
reuse_lc="$(printf '%s' "$reuse" | tr '[:upper:]' '[:lower:]')"
if [ "$reuse" = "__unset__" ]; then
  echo "[warn] SIRCHMUNK_ENABLE_CLUSTER_REUSE is unset: upstream treats this as ENABLED"
  reuse_lc="true"
fi
if [ "$reuse_lc" != "true" ]; then
  echo "[ok] cluster reuse disabled (SIRCHMUNK_ENABLE_CLUSTER_REUSE=$reuse); model preload skipped"
elif [ "${SIRCHMUNK_SKIP_MODEL_PRELOAD:-0}" = "1" ]; then
  echo "[warn] cluster reuse enabled but SIRCHMUNK_SKIP_MODEL_PRELOAD=1; first search may block on model load"
elif [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "[FAIL] cluster reuse enabled but $VENV_DIR/bin/python not found (run setup_venv.sh first)"
  fail=1
else
  # 与 AgenticSearch 的解析一致：EMBEDDING_CACHE_DIR 优先，否则 work_path/.cache/models。
  if [ -z "$cache_dir" ]; then
    cache_dir="${work_path:-$HOME/.sirchmunk}/.cache/models"
  fi
  echo "   model=${model_id:-<upstream default>} cache_dir=$cache_dir (first download may take minutes)"
  if model_dir="$(
    PRELOAD_CACHE_DIR="$cache_dir" PRELOAD_MODEL_ID="$model_id" "$VENV_DIR/bin/python" -c '
import os, sys
from sirchmunk.utils.embedding_util import EmbeddingUtil
path = EmbeddingUtil.preload_model(
    cache_dir=os.path.expanduser(os.environ["PRELOAD_CACHE_DIR"]),
    model_id=os.environ.get("PRELOAD_MODEL_ID") or None,
)
sys.stdout.write(str(path))
' 2>/dev/null
  )" && [ -n "$model_dir" ]; then
    echo "[ok] embedding model ready: $model_dir"
  else
    echo "[FAIL] embedding model preload failed (check network / EMBEDDING_MODEL_ID / EMBEDDING_CACHE_DIR)"
    fail=1
  fi
fi

exit "$fail"
