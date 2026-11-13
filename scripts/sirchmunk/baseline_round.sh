#!/usr/bin/env bash
# SX0.4 基线协议的单轮执行（SX3.2 编译后复跑同样使用）：
#   清空知识簇与 spec 缓存 → 启动 sidecar → 两个 golden fixture 跑一遍 → 停 sidecar。
# 每轮冷启动的原因：复用关闭时上游仍会保存知识簇，DEEP Phase 1 的
# _probe_knowledge_cache 会以 SQL LIKE 读取它们，同题重复跑会被上一轮污染。
# 编译产物（work_path/.cache/compile）不在清理范围内；编译写入的知识簇
# 经 BASELINE_KNOWLEDGE_SNAPSHOT=<编译后 knowledge 目录副本> 每轮恢复。
#
# 用法: bash scripts/sirchmunk/baseline_round.sh MODE REP OUTDIR
#   MODE=FAST|DEEP，REP=轮次编号，OUTDIR 建议放在仓库外（避免被检索到）。
#   BASELINE_EXPECT_REUSE=true：env 文件须开启复用（SX4.2 复用开对照），默认要求关闭。
#   BASELINE_FIXTURES="a b"：tests/fixtures/agentic/ 下的 fixture 名（不含 .json），
#   默认 "golden_questions golden_questions_docs"。
# 结果汇总: python scripts/sirchmunk/aggregate_rounds.py OUTDIR merged.json summary.md
# 真实调用，消耗 LLM token；不在离线测试套件内。
set -euo pipefail

MODE="${1:?MODE (FAST|DEEP)}"; REP="${2:?REP}"; OUT="${3:?OUTDIR}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"
ENV_FILE="${SIRCHMUNK_ENV_FILE:-.env-sirchmunk}"
BASE_URL="http://${SIRCHMUNK_HOST:-127.0.0.1}:${SIRCHMUNK_PORT:-8584}"
mkdir -p "$OUT"

set -a; source "$ENV_FILE"; set +a
EXPECT_REUSE="${BASELINE_EXPECT_REUSE:-false}"
[ "${SIRCHMUNK_ENABLE_CLUSTER_REUSE:-}" = "$EXPECT_REUSE" ] || {
  echo "SIRCHMUNK_ENABLE_CLUSTER_REUSE must be '$EXPECT_REUSE' (set BASELINE_EXPECT_REUSE to change)" >&2; exit 2; }
WORK="${SIRCHMUNK_WORK_PATH:?SIRCHMUNK_WORK_PATH not set}"
[ -d "$WORK/.cache" ] || { echo "work path $WORK/.cache missing" >&2; exit 2; }

if curl -s -o /dev/null --max-time 2 "$BASE_URL/"; then
  echo "sidecar already running at $BASE_URL; stop it first" >&2; exit 3
fi

rm -rf "$WORK/.cache/knowledge" "$WORK/.cache/spec"
# 编译会把知识簇写进 knowledge 存储（上游 compiler.py 的 _storage.insert），
# 编译后复跑须从编译完成时的快照恢复，而不是清空。
if [ -n "${BASELINE_KNOWLEDGE_SNAPSHOT:-}" ]; then
  [ -d "$BASELINE_KNOWLEDGE_SNAPSHOT" ] || { echo "snapshot $BASELINE_KNOWLEDGE_SNAPSHOT missing" >&2; exit 2; }
  cp -R "$BASELINE_KNOWLEDGE_SNAPSHOT" "$WORK/.cache/knowledge"
fi

bash scripts/sirchmunk/start_sidecar.sh > "$OUT/sidecar_${MODE}_r${REP}.log" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null; wait $PID 2>/dev/null || true' EXIT

AUTH=()
[ -n "${SIRCHMUNK_API_TOKEN:-}" ] && AUTH=(-H "Authorization: Bearer $SIRCHMUNK_API_TOKEN")
for i in $(seq 1 60); do
  curl -sf --max-time 2 ${AUTH[@]+"${AUTH[@]}"} "$BASE_URL/api/v1/search/status" >/dev/null && break
  [ "$i" = 60 ] && { echo "sidecar did not become healthy" >&2; exit 4; }
  sleep 1
done

PY="${PYTHON:-.venv/bin/python}"
rc=0
for FX in ${BASELINE_FIXTURES:-golden_questions golden_questions_docs}; do
  "$PY" scripts/sirchmunk/compare.py --base-url "$BASE_URL" ${SIRCHMUNK_API_TOKEN:+--token "$SIRCHMUNK_API_TOKEN"} \
    --repo-root "$REPO" --mode "$MODE" --fixture "tests/fixtures/agentic/${FX}.json" \
    --json-out "$OUT/${MODE}_r${REP}_${FX}.json" --out "$OUT/${MODE}_r${REP}_${FX}.md" >/dev/null || rc=1
done
echo "round $MODE r$REP done rc=$rc"
exit $rc
