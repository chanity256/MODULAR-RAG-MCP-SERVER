#!/usr/bin/env bash
# PageIndex sidecar 独立 venv 搭建 — PI0.1（PAGEINDEX_TREE_RETRIEVAL_SPEC，D6 锁定 PyPI 精确版本）。
# 主 venv 永不 import pageindex（红线）；sidecar venv 独立于主项目 venv 不受影响。
# 用法: bash scripts/pageindex/setup_venv.sh [venv_dir]
set -euo pipefail

VENV_DIR="${1:-${PAGEINDEX_VENV:-.venv-pageindex}}"
PINNED_VERSION="0.7.4"

echo "== create venv at $VENV_DIR (python3.12 -> python3.11 -> python3) =="
PY=python3.12; command -v "$PY" >/dev/null 2>&1 || { PY=python3.11; }
command -v "$PY" >/dev/null 2>&1 || PY=python3
"$PY" -m venv "$VENV_DIR"

echo "== install pageindex==$PINNED_VERSION into sidecar venv =="
"$VENV_DIR/bin/pip" install --upgrade pip
# 上游 PyPI 最新稳定版（2026-11-19 发布）；仓库 pyproject 写 0.2.10 与发布口径不一致，按 PyPI 锁定。
"$VENV_DIR/bin/pip" install "pageindex==$PINNED_VERSION"
# sidecar 服务自身依赖（PI2 server.py 用；独立于 pageindex 的依赖链单独声明）
"$VENV_DIR/bin/pip" install fastapi "uvicorn[standard]"

echo "== verify install =="
"$VENV_DIR/bin/python" -c "from importlib.metadata import version; print('pageindex', version('pageindex'))"

echo
echo "Next steps:"
echo "  1. cp scripts/pageindex/env.example .env-pageindex  # 填 LLM_API_KEY 与 PAGEINDEX_API_TOKEN"
echo "  2. bash scripts/pageindex/start_sidecar.sh          # PI2 交付"
