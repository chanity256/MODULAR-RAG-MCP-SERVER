#!/usr/bin/env bash
# Sirchmunk sidecar 独立 venv 搭建 — 阶段 S0.1（SIRCHMUNK_INTEGRATION_SPEC 4.8）。
# 独立 venv（Python 3.11/3.12）+ 锁定上游 commit；主项目 venv 不受影响。
# 用法: bash scripts/sirchmunk/setup_venv.sh [sirchmunk_repo_dir] [venv_dir]
set -euo pipefail

SIRCHMUNK_REPO="${1:-${SIRCHMUNK_REPO:-$HOME/Desktop/Project/Job-Search/sirchmunk}}"
VENV_DIR="${2:-${SIRCHMUNK_VENV:-.venv-sirchmunk}}"
PINNED_COMMIT="b314e11"

if [ ! -d "$SIRCHMUNK_REPO/.git" ]; then
  echo "sirchmunk repo not found at: $SIRCHMUNK_REPO"
  echo "git clone https://github.com/modelscope/sirchmunk.git \"$SIRCHMUNK_REPO\""
  exit 1
fi

echo "== checkout pinned commit $PINNED_COMMIT in $SIRCHMUNK_REPO =="
git -C "$SIRCHMUNK_REPO" fetch origin main
git -C "$SIRCHMUNK_REPO" checkout "$PINNED_COMMIT"

echo "== create venv at $VENV_DIR (python3.12 -> python3.11 -> python3) =="
PY=python3.12; command -v "$PY" >/dev/null 2>&1 || { PY=python3.11; }
command -v "$PY" >/dev/null 2>&1 || PY=python3
"$PY" -m venv "$VENV_DIR"

echo "== install sirchmunk (pinned) into sidecar venv =="
"$VENV_DIR/bin/pip" install --upgrade pip
# kreuzberg 依赖预发布版本（spec 4.8），如解析失败可加 --pre 重试。
"$VENV_DIR/bin/pip" install -e "$SIRCHMUNK_REPO" || "$VENV_DIR/bin/pip" install --pre -e "$SIRCHMUNK_REPO"
# 上游未声明 python-multipart，但 FastAPI 文件上传路由在 import 期即要求它，缺失时 sirchmunk serve 启动崩溃。
"$VENV_DIR/bin/pip" install python-multipart
# 上游只声明 kreuzberg>=4.0.0rc1；5.x 把 OutputFormat 成员改成小写（plain），上游
# document_extractor 仍用 OutputFormat.PLAIN，compile 会逐文件失败（SX3.2 实测）。
"$VENV_DIR/bin/pip" install "kreuzberg>=4.0.0,<5"

echo "== verify import =="
"$VENV_DIR/bin/python" -c "import sirchmunk; print('sirchmunk', sirchmunk.__version__)"

echo
echo "Next steps:"
echo "  1. cp scripts/sirchmunk/env.example <sidecar>/.env  # 填 LLM_API_KEY 与 SIRCHMUNK_API_TOKEN"
echo "  2. bash scripts/sirchmunk/preflight.sh              # rg/rga 检查"
echo "  3. bash scripts/sirchmunk/start_sidecar.sh \"$VENV_DIR\""
