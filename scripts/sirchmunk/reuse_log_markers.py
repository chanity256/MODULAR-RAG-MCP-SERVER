#!/usr/bin/env python3
"""从 sidecar 日志逐请求提取知识簇复用标记（SX4.2）。

复用命中不写 telemetry（spec 3.5），只能看日志。按 uvicorn 访问日志行
``"POST /api/v1/search..."`` 把日志切成逐请求块（请求串行时块序 = 请求序），
每块统计：

- ``hit``：``Found similar cluster: <name> (similarity: x)``（硬复用候选命中，
  FAST 另有 ``[FAST] Reused cached knowledge cluster``）；
- ``miss``：``No similar clusters found``；
- ``model_not_ready``：embedding 模型未就绪而跳过复用；
- ``docqa``：DEEP 的 DocQA 短路（发生在复用判定之前）。

用法: python scripts/sirchmunk/reuse_log_markers.py SIDECAR_LOG [ROWS_JSON]
给出 compare.py ``--json-out`` 的 JSON 时按顺序对齐题目 id。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ACCESS = re.compile(r'"POST /api/v1/search(?:/stream)? HTTP/1\.[01]" (\d{3})')
_FOUND = re.compile(r"Found similar cluster: (.*?) \(similarity: ([0-9.]+)\)")


def split_requests(text: str) -> list[dict]:
    blocks: list[dict] = []
    current: list[str] = []
    for line in text.splitlines():
        current.append(line)
        m = _ACCESS.search(line)
        if m:
            blocks.append(_summarize(current, int(m.group(1))))
            current = []
    return blocks


def _summarize(lines: list[str], status: int) -> dict:
    body = "\n".join(lines)
    found = _FOUND.findall(body)
    return {
        "status": status,
        "hit": bool(found),
        "similarity": float(found[-1][1]) if found else None,
        "cluster_name": found[-1][0] if found else None,
        "fast_reused": "[FAST] Reused cached knowledge cluster" in body,
        "miss": "No similar clusters found" in body,
        "model_not_ready": "skipping cluster reuse" in body,
        "docqa": "[DocQA] Intent" in body,
    }


def main(argv: list[str]) -> int:
    blocks = split_requests(Path(argv[1]).read_text(encoding="utf-8", errors="replace"))
    ids = [r["id"] for r in json.loads(Path(argv[2]).read_text())] if len(argv) > 2 else []
    for i, b in enumerate(blocks):
        b["id"] = ids[i] if i < len(ids) else f"#{i + 1}"
    print(json.dumps(blocks, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
