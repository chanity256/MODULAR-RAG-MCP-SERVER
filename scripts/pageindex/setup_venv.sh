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
# md_render 渲染链依赖（10.9 #11：pageindex 只声明 PyPDF2，pypdf / pypdfium2
# 须显式安装，否则干净 venv 上每次 MD 渲染 ModuleNotFoundError）
"$VENV_DIR/bin/pip" install pypdf pypdfium2

echo "== verify install =="
"$VENV_DIR/bin/python" -c "from importlib.metadata import version; print('pageindex', version('pageindex'))"

# 渲染链工具探测（D11，PI2）：pandoc 与系统 Chrome/Chromium 非 pip 依赖。
# 缺失只影响 MD 建树（/index 对 MD 返回 render: 前缀错误，R9），原生 PDF
# 不受影响；playwright 托管浏览器为可选回退，不强制安装（~300 MB）。
echo "== detect render toolchain (D11) =="
PANDOC_BIN="${PAGEINDEX_PANDOC:-pandoc}"
if command -v "$PANDOC_BIN" >/dev/null 2>&1; then
  echo "pandoc: $("$PANDOC_BIN" --version 2>/dev/null | head -1)"
else
  echo "WARN: pandoc not found (set PAGEINDEX_PANDOC or install pandoc; MD 建树不可用)"
fi
CHROME_BIN="${PAGEINDEX_CHROMIUM:-}"
if [ -n "$CHROME_BIN" ] && [ -x "$CHROME_BIN" ]; then
  echo "chromium: $CHROME_BIN (PAGEINDEX_CHROMIUM)"
else
  CHROME_FOUND=""
  for cand in \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "/Applications/Chromium.app/Contents/MacOS/Chromium" \
    "/usr/bin/google-chrome" "/usr/bin/google-chrome-stable" \
    "/usr/bin/chromium" "/usr/bin/chromium-browser"; do
    if [ -x "$cand" ]; then CHROME_FOUND="$cand"; break; fi
  done
  if [ -z "$CHROME_FOUND" ]; then
    for name in chromium chromium-browser google-chrome google-chrome-stable; do
      if command -v "$name" >/dev/null 2>&1; then CHROME_FOUND="$(command -v "$name")"; break; fi
    done
  fi
  if [ -n "$CHROME_FOUND" ]; then
    echo "chromium: $CHROME_FOUND (system)"
  else
    echo "WARN: no system Chrome/Chromium found (playwright 托管浏览器仅作可选回退；MD 建树不可用)"
  fi
fi

echo
echo "Next steps:"
echo "  1. cp scripts/pageindex/env.example .env-pageindex  # 填 LLM_API_KEY 与 PAGEINDEX_API_TOKEN"
echo "  2. bash scripts/pageindex/start_sidecar.sh"
