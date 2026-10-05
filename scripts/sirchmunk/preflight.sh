#!/usr/bin/env bash
# Sirchmunk sidecar preflight — 阶段 S0.1（SIRCHMUNK_INTEGRATION_SPEC 4.1）。
# 检查 rg / rga 二进制与 sidecar 健康状态；任一失败即非零退出。
# 用法: bash scripts/sirchmunk/preflight.sh [sidecar_base_url]
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
echo "-- health: GET ${BASE_URL}/api/v1/search/status"
if curl -sf --max-time 5 "${BASE_URL}/api/v1/search/status" >/dev/null 2>&1; then
  echo "[ok] sidecar is up at ${BASE_URL}"
else
  echo "[warn] sidecar not reachable at ${BASE_URL} (start with scripts/sirchmunk/start_sidecar.sh)"
  [ "${SIRCHMUNK_REQUIRE_SIDECAR:-0}" = "1" ] && fail=1
fi

exit "$fail"
