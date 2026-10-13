#!/usr/bin/env bash
# Sirchmunk 知识编译运维入口 — SX3.1（SIRCHMUNK_EXTENSION_SPEC 3.4）。
# 包装 sidecar venv 的 `sirchmunk compile` CLI，写入与 sidecar 相同的 work_path。
#
# 用法:
#   bash scripts/sirchmunk/compile.sh [--full] [--shallow] [--max-files N] [--concurrency N] [-- <paths>...]
#   bash scripts/sirchmunk/compile.sh --status [-- <paths>...]
#   bash scripts/sirchmunk/compile.sh --lint [--fix] [-- <paths>...]
# 未传 `-- <paths>` 时取 env 文件的 SIRCHMUNK_COMPILE_PATHS（空格分隔，相对仓库根）。
#
# 护栏:
#   - 停机：写入模式（编译、--lint --fix）前探活 sidecar，任何 HTTP 响应（含 401）都算存活，
#     存活即拒绝——work_path 无文件锁，并发写 Parquet 是 last-writer-wins（spec 4.3）。
#   - 路径：上游 _discover_files 是 rglob("*")，不读 .gitignore、无 exclude 参数（spec 4.9），
#     所以拒绝仓库根及其祖先、work_path 本身，以及位于或包含 .venv*/.git/build/logs/cache 的路径。
#   - 缓存：编译产物对运行中的 sidecar 不可见（_tree_paths_cache 按实例缓存，spec 4.4），
#     编译后必须（重新）启动 sidecar。
#
# 环境变量: SIRCHMUNK_VENV（默认 .venv-sirchmunk）、SIRCHMUNK_ENV_FILE（默认 .env-sirchmunk）。
# 退出码: 0 成功 / 1 环境缺失 / 2 用法或路径被拒 / 3 sidecar 仍在运行 / 其余透传上游。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$REPO_ROOT"

VENV_DIR="${SIRCHMUNK_VENV:-.venv-sirchmunk}"
ENV_FILE="${SIRCHMUNK_ENV_FILE:-.env-sirchmunk}"
FORBIDDEN_NAMES=(".git" "build" "logs" "cache")  # 另加 .venv* 前缀匹配

usage() { sed -n '4,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 2; }
die() { echo "error: $2" >&2; exit "$1"; }

full=0; shallow=0; status=0; lint=0; fix=0
max_files=""; concurrency=""
paths=()
while [ $# -gt 0 ]; do
  case "$1" in
    --full) full=1 ;;
    --shallow) shallow=1 ;;
    --status) status=1 ;;
    --lint) lint=1 ;;
    --fix) fix=1 ;;
    --max-files|--concurrency)
      [ $# -ge 2 ] || die 2 "$1 requires a value"
      [[ "$2" =~ ^[1-9][0-9]*$ ]] || die 2 "$1 must be a positive integer, got '$2'"
      if [ "$1" = "--max-files" ]; then max_files="$2"; else concurrency="$2"; fi
      shift ;;
    --) shift; paths=("$@"); break ;;
    -h|--help) usage ;;
    *) die 2 "unknown argument '$1' (paths go after --)" ;;
  esac
  shift
done

[ $((status + lint)) -le 1 ] || die 2 "--status and --lint are mutually exclusive"
if [ $((status + lint)) -eq 1 ]; then
  [ $((full + shallow)) -eq 0 ] && [ -z "$max_files$concurrency" ] \
    || die 2 "--status/--lint cannot be combined with --full/--shallow/--max-files/--concurrency"
fi
[ "$fix" -eq 0 ] || [ "$lint" -eq 1 ] || die 2 "--fix is only valid with --lint"

[ -x "$VENV_DIR/bin/sirchmunk" ] || die 1 "sirchmunk not installed in $VENV_DIR (run setup_venv.sh first)"
[ -f "$ENV_FILE" ] || die 1 "env file $ENV_FILE missing (cp scripts/sirchmunk/env.example $ENV_FILE)"
set -a; source "$ENV_FILE"; set +a
[ -n "${SIRCHMUNK_WORK_PATH:-}" ] || die 1 "SIRCHMUNK_WORK_PATH is not set in $ENV_FILE"

# 绝对化（不要求存在）：相对路径按仓库根解析，逐段消解 . 与 ..。
abs_path() {
  local p="$1" out="" seg
  [[ "$p" = /* ]] || p="$REPO_ROOT/$p"
  local IFS=/
  for seg in $p; do
    case "$seg" in
      ""|.) ;;
      ..) out="${out%/*}" ;;
      *) out="$out/$seg" ;;
    esac
  done
  echo "${out:-/}"
}

is_forbidden_name() {
  local name="$1" f
  [[ "$name" = .venv* ]] && return 0
  for f in "${FORBIDDEN_NAMES[@]}"; do [ "$name" = "$f" ] && return 0; done
  return 1
}

WORK_PATH="$(abs_path "${SIRCHMUNK_WORK_PATH/#\~/$HOME}")"

if [ ${#paths[@]} -eq 0 ]; then
  [ -n "${SIRCHMUNK_COMPILE_PATHS:-}" ] || die 2 "no paths given and SIRCHMUNK_COMPILE_PATHS is empty"
  read -r -a paths <<< "$SIRCHMUNK_COMPILE_PATHS"
fi

resolved=()
for raw in "${paths[@]}"; do
  [ -n "$raw" ] || die 2 "empty path"
  p="$(abs_path "$raw")"
  [ -e "$p" ] || die 2 "path does not exist: $raw"
  [ "$p" != "/" ] || die 2 "refusing '/'"
  case "$REPO_ROOT/" in
    "$p"/*|"$p/") die 2 "refusing '$raw': it is the repo root or one of its ancestors (would scan .venv/.git/build)" ;;
  esac
  case "$WORK_PATH/" in
    "$p"/*) die 2 "refusing '$raw': it contains the sidecar work_path $WORK_PATH" ;;
  esac
  case "$p/" in
    "$WORK_PATH"/*) die 2 "refusing '$raw': it is inside the sidecar work_path" ;;
  esac
  # 仓库内路径只检查仓库根以下的段，避免仓库本身所在目录名误伤。
  rel="${p#/}"
  case "$p" in "$REPO_ROOT"/*) rel="${p#"$REPO_ROOT"/}" ;; esac
  IFS=/ read -r -a segs <<< "$rel"
  for seg in "${segs[@]}"; do
    is_forbidden_name "$seg" && die 2 "refusing '$raw': path contains forbidden directory '$seg'"
  done
  if [ -d "$p" ]; then
    hit="$(find "$p" -mindepth 1 -type d \( -name '.venv*' -o -name .git -o -name build -o -name logs -o -name cache \) -print -quit 2>/dev/null || true)"
    [ -z "$hit" ] || die 2 "refusing '$raw': it contains forbidden directory ${hit#"$p"/}"
  fi
  resolved+=("$p")
done

writes=1
if [ "$status" -eq 1 ] || { [ "$lint" -eq 1 ] && [ "$fix" -eq 0 ]; }; then writes=0; fi

if [ "$writes" -eq 1 ]; then
  command -v curl >/dev/null 2>&1 || die 1 "curl not found; cannot verify the sidecar is stopped"
  BASE_URL="${SIRCHMUNK_BASE_URL:-http://127.0.0.1:8584}"
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "${BASE_URL}/api/v1/search/status" 2>/dev/null || true)"
  if [ -n "$code" ] && [ "$code" != "000" ]; then
    die 3 "sidecar is running at $BASE_URL (HTTP $code); stop it before compiling (work_path has no file lock)"
  fi
fi

cmd=("$VENV_DIR/bin/sirchmunk" compile --work-path "$WORK_PATH" --paths "${resolved[@]}")
[ "$full" -eq 1 ] && cmd+=(--full)
[ "$shallow" -eq 1 ] && cmd+=(--shallow)
[ -n "$max_files" ] && cmd+=(--max-files "$max_files")
[ -n "$concurrency" ] && cmd+=(--concurrency "$concurrency")
[ "$status" -eq 1 ] && cmd+=(--status)
[ "$lint" -eq 1 ] && cmd+=(--lint)
[ "$fix" -eq 1 ] && cmd+=(--fix)

echo "== sirchmunk compile: work_path=$WORK_PATH paths=${resolved[*]} ==" >&2
rc=0
"${cmd[@]}" || rc=$?

if [ "$writes" -eq 1 ] && [ "$rc" -eq 0 ]; then
  echo "NOTE: compile output is only picked up by a freshly started sidecar; (re)start it with scripts/sirchmunk/start_sidecar.sh" >&2
fi
exit "$rc"
