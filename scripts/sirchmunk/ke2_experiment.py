#!/usr/bin/env python3
"""KE2 专用 runner：知识演化成对实验（SIRCHMUNK_KNOWLEDGE_EVOLUTION_SPEC 第 4 章第 3 问）。

仅在 ``RAG_AGENTIC_INTEGRATION=1`` 下执行真实 sidecar / LLM（门控，花 token）；
主 venv 运行、只经 HTTP 调 sidecar，不 import sirchmunk（红线适用）。
不复用 baseline_round.sh：独立临时 work_path、预热快照、逐组快照恢复、
成对双臂（A=复用开/演化关，B=复用开/演化开）、逐请求完整答案与裁决、
成对统计与成本上限。产出 ``scripts/sirchmunk/ke2_results.json`` / ``.md``。

预设（2026-11-15 经用户确认，写入 PRESET）：
- 2 个独立语义组 × 每组 3 条正式改写 × 2 次重复；
- 成本硬上限 4,000,000 token（超出即中止并如实归档）；
- 提升判定门：B−A 的 reword 题复用命中差 ≥ 2 且 A 臂 reword 命中 = 0。

判分口径（不得统称 hit）：``source_hit``（期望来源文件名出现在证据）、
``reuse_candidate``（日志 Found similar cluster）、``fast_reused``（日志
[FAST] Reused）、``soft_reused``（候选命中但未观察到硬复用 = 实际消费不可判定）、
``answer_correct``（rubric 判别片段出现在答案文本）。非 200、日志块不对齐、
答案不可裁决的轮次记 ``invalid`` 并写明原因；目标机制（connect/merge）未生效的
合并类结论记 ``not_assessable``。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "sirchmunk"))

from reuse_log_markers import split_requests  # noqa: E402  纯 stdlib 日志解析（SX4.2）

PROBE_FIXTURE = REPO_ROOT / "tests/fixtures/agentic/ke2_probe_questions.json"
GOLDEN_QUESTIONS = REPO_ROOT / "tests/fixtures/agentic/golden_questions.json"
GOLDEN_QUESTIONS_DOCS = REPO_ROOT / "tests/fixtures/agentic/golden_questions_docs.json"
# 语义独立的预热补足题（与 Web UI 启动命令 / BM25 IDF 两个探针主题均不重叠）。
WARMUP_FILL_IDS = {"Q2", "Q4", "Q6", "D1", "D3", "D4"}

EXCLUDE_PATTERNS = ["tests/fixtures/**", "scripts/sirchmunk/*results*"]
SIDECAR_PORT = 8587
BASE_URL = f"http://127.0.0.1:{SIDECAR_PORT}"

#: 2026-11-15 用户确认的预设（改动须先经用户批准并回写规格）。
PRESET: dict = {
    "preset_date": "2026-11-15",
    "semantic_groups": ["P1", "P2"],
    "rewords_per_group": 3,
    "repeats": 2,
    "warmup_min_new_clusters": 5,
    "min_reuse_gain": 2,
    "require_armA_reword_zero": True,
    "max_total_tokens": 4_000_000,
    "max_cost_usd": 8.0,
    "max_requests": 90,
    "drain_timeout_s": 120,
    "drain_quiet_s": 20,
    "request_timeout_s": 600.0,
}

_EVOLVE_LINE = re.compile(r"\[Evolve\]")
_SAVED_CLUSTER = re.compile(r"Saved knowledge cluster (\S+)")
_MERGED_LOG = re.compile(r"\[Evolve\] Connected and merged clusters for (\d+)")


# --------------------------------------------------------------------------- #
# 纯函数（离线单测覆盖）                                                       #
# --------------------------------------------------------------------------- #
def load_probe_groups() -> list[dict]:
    """KE2 探针 fixture → 按组分层：formal（seed/repeat/reword/distractor）与 train。"""
    data = json.loads(PROBE_FIXTURE.read_text(encoding="utf-8"))
    groups: dict[str, dict] = {}
    for q in data["questions"]:
        g = groups.setdefault(q["group"], {"group": q["group"], "formal": [], "train": []})
        (g["train"] if q["role"] == "train" else g["formal"]).append(q)
    for g in groups.values():
        role_order = {"seed": 0, "repeat": 1, "reword": 2, "distractor": 3}
        g["formal"].sort(key=lambda q: (role_order.get(q["role"], 9), q["id"]))
    return [groups[name] for name in sorted(groups)]


def load_warmup_questions() -> list[dict]:
    """预热补足题：从 golden fixtures 按 id 取（语义独立，不带 rubric 也无妨）。"""
    wanted = list(WARMUP_FILL_IDS)
    out: list[dict] = []
    for path in (GOLDEN_QUESTIONS, GOLDEN_QUESTIONS_DOCS):
        data = json.loads(path.read_text(encoding="utf-8"))
        for q in data["questions"]:
            if q["id"] in wanted:
                out.append({**q, "role": "warmup_fill", "group": "warmup"})
                wanted.remove(q["id"])
    if wanted:
        raise ValueError(f"warmup fill ids not found: {wanted}")
    return out


def build_arm_env(base_text: str, work_path: Path, evolution: bool) -> str:
    """在用户 env 文件文本上做实验覆盖（纯字符串函数）。

    复用恒开（两臂同构）；演化按臂；work_path 指向该臂独立临时目录；
    EMBEDDING_CACHE_DIR 缺省指向日常模型缓存（避免 470MB 重复下载）。
    """
    default_model_cache = REPO_ROOT / "data/db/sirchmunk/.cache/models"
    lines_out: list[str] = []
    seen: set[str] = set()
    for line in base_text.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key == "SIRCHMUNK_WORK_PATH":
            lines_out.append(f"SIRCHMUNK_WORK_PATH={work_path}")
        elif key == "SIRCHMUNK_ENABLE_CLUSTER_REUSE":
            lines_out.append("SIRCHMUNK_ENABLE_CLUSTER_REUSE=true")
        elif key == "SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION":
            lines_out.append(f"SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION={'true' if evolution else 'false'}")
        elif key == "SIRCHMUNK_BASE_URL":
            lines_out.append(f"SIRCHMUNK_BASE_URL={BASE_URL}")
        else:
            lines_out.append(line)
        if key:
            seen.add(key)
    if "SIRCHMUNK_WORK_PATH" not in seen:
        lines_out.append(f"SIRCHMUNK_WORK_PATH={work_path}")
    if "SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION" not in seen:
        lines_out.append(f"SIRCHMUNK_ENABLE_KNOWLEDGE_EVOLUTION={'true' if evolution else 'false'}")
    if "EMBEDDING_CACHE_DIR" not in seen and default_model_cache.exists():
        lines_out.append(f"EMBEDDING_CACHE_DIR={default_model_cache}")
    return "\n".join(lines_out) + "\n"


def extract_row_metrics(data: dict, evidence_repo_root: Path, expected_sources: list[str]) -> dict:
    """逐请求判定字段（不统称 hit）。"""
    cluster = data.get("cluster") or {}
    evidence_files = [
        str(e.get("file_or_url")) for e in (cluster.get("evidences") or []) if isinstance(e, dict)
    ]
    answer = str(data.get("answer") or "")
    return {
        "evidence_files": evidence_files,
        "answer": answer,
        "total_llm_tokens": data.get("total_llm_tokens") or 0,
        "llm_call_count": data.get("llm_call_count"),
        "source_hit": _source_hit(expected_sources, evidence_files, evidence_repo_root),
    }


def _source_hit(expected: list[str], evidence_files: list[str], repo_root: Path) -> bool:
    """source 级命中：期望来源文件名出现在证据且同树（S3.1 口径）。"""
    for exp in expected:
        exp_name = Path(exp).name
        for ev in evidence_files:
            if Path(ev).name == exp_name:
                return True
    return False


def judge_row(row: dict, log_block: dict | None) -> dict:
    """合并 HTTP 指标与日志块 → 判分字段 + 轮次分类与原因。"""
    reasons: list[str] = []
    if row.get("status") != 200:
        reasons.append(f"http_{row.get('status')}")
    if log_block is None:
        reasons.append("log_block_not_aligned")
    answer = row.get("answer") or ""
    fragment = row.get("answer_should_contain")
    answer_correct = bool(fragment) and fragment in answer
    if fragment and not answer.strip():
        reasons.append("empty_answer")
    judged = {
        # status / answer_should_contain 必须透传：日志对齐环节会对同一行二次
        # judge（KE2 首跑实测：丢失会令正式轮全被误判 invalid）。
        **{k: row.get(k) for k in ("arm", "group", "role", "repeat", "id", "query",
                                   "elapsed_s", "total_llm_tokens", "status",
                                   "answer_should_contain")},
        "source_hit": row.get("source_hit", False),
        "reuse_candidate": bool(log_block and log_block.get("hit")),
        "fast_reused": bool(log_block and log_block.get("fast_reused")),
        "reuse_similarity": log_block.get("similarity") if log_block else None,
        "soft_reused": bool(log_block and log_block.get("hit") and not log_block.get("fast_reused")),
        "answer_correct": answer_correct,
        "answer": answer,
    }
    if reasons:
        judged["category"] = "invalid"
        judged["reasons"] = reasons
    else:
        judged["category"] = "valid"
    return judged


def parse_evolve_events(log_text: str) -> dict:
    """预热/演化机制证据：新簇保存 id、Phase1 合并日志、演化步数。"""
    return {
        "saved_cluster_ids": list(dict.fromkeys(_SAVED_CLUSTER.findall(log_text))),
        "phase1_merge_events": [int(m) for m in _MERGED_LOG.findall(log_text)],
        "evolve_steps": len(_EVOLVE_LINE.findall(log_text)),
    }


def check_budget(state: dict, preset: dict) -> str | None:
    """返回中止原因（None = 未触上限）。"""
    if state["total_tokens"] > preset["max_total_tokens"]:
        return f"token cap exceeded: {state['total_tokens']} > {preset['max_total_tokens']}"
    if state["requests"] > preset["max_requests"]:
        return f"request cap exceeded: {state['requests']} > {preset['max_requests']}"
    return None


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def paired_analysis(rows_a: list[dict], rows_b: list[dict], preset: dict) -> dict:
    """预设判定门：B−A 的 reword fast_reused 题次差 + 成对 bootstrap 区间。"""
    def reword_hits(rows: list[dict]) -> list[int]:
        return [1 if r["fast_reused"] else 0 for r in rows
                if r.get("category") == "valid" and r.get("role") == "reword"]

    hits_a, hits_b = reword_hits(rows_a), reword_hits(rows_b)
    if not hits_a and not hits_b:
        # 无有效 reword 轮（全 invalid 或缺失）→ 不得作确定性正负裁决（规格 6.2）。
        return {
            "reword_hits_armA": 0, "reword_total_armA": 0,
            "reword_hits_armB": 0, "reword_total_armB": 0,
            "diff": 0, "bootstrap_diff_ci95": [0, 0],
            "wilson_armA": [0.0, 0.0], "wilson_armB": [0.0, 0.0],
            "preset_gate": {"min_diff": preset["min_reuse_gain"],
                            "require_armA_reword_zero": preset["require_armA_reword_zero"]},
            "verdict": "not_assessable",
            "verdict_reason": "no valid reword rounds in either arm",
        }
    diff = sum(hits_b) - sum(hits_a)
    # 成对 bootstrap（固定种子可复现）；n 小，区间仅作参考量级。
    rng = random.Random(42)
    diffs: list[int] = []
    n_pairs = min(len(hits_a), len(hits_b))
    for _ in range(10_000):
        sa = sum(hits_a[rng.randrange(len(hits_a))] for _ in range(len(hits_a))) if hits_a else 0
        sb = sum(hits_b[rng.randrange(len(hits_b))] for _ in range(len(hits_b))) if hits_b else 0
        diffs.append(sb - sa)
    diffs.sort()
    lo, hi = diffs[int(0.025 * len(diffs))], diffs[int(0.975 * len(diffs)) - 1]
    gate_met = (
        diff >= preset["min_reuse_gain"]
        and (sum(hits_a) == 0 or not preset["require_armA_reword_zero"])
    )
    return {
        "reword_hits_armA": sum(hits_a), "reword_total_armA": len(hits_a),
        "reword_hits_armB": sum(hits_b), "reword_total_armB": len(hits_b),
        "diff": diff,
        "bootstrap_diff_ci95": [lo, hi],
        "wilson_armA": list(wilson_interval(sum(hits_a), len(hits_a))),
        "wilson_armB": list(wilson_interval(sum(hits_b), len(hits_b))),
        "preset_gate": {"min_diff": preset["min_reuse_gain"],
                        "require_armA_reword_zero": preset["require_armA_reword_zero"]},
        "verdict": ("positive" if gate_met else
                    "negative" if diff < 0 or (sum(hits_a) == 0 and sum(hits_b) == 0) else
                    "insufficient_evidence"),
    }


def summarize_arm(rows: list[dict]) -> dict:
    valid = [r for r in rows if r.get("category") == "valid"]
    by_role: dict[str, dict] = {}
    for role in ("seed", "repeat", "reword", "distractor"):
        subset = [r for r in valid if r.get("role") == role]
        by_role[role] = {
            "n": len(subset),
            "source_hit": sum(1 for r in subset if r["source_hit"]),
            "reuse_candidate": sum(1 for r in subset if r["reuse_candidate"]),
            "fast_reused": sum(1 for r in subset if r["fast_reused"]),
            "answer_correct": sum(1 for r in subset if r["answer_correct"]),
            "tokens": sum(r.get("total_llm_tokens") or 0 for r in subset),
        }
    invalid = [r["id"] for r in rows if r.get("category") == "invalid"]
    return {
        "by_role": by_role,
        "invalid_rounds": invalid,
        "total_tokens": sum(r.get("total_llm_tokens") or 0 for r in rows),
        "total_requests": len(rows),
    }


def sidecar_thread_count(pid: int) -> int | None:
    """macOS ``ps -M`` 线程数（非 macOS / 进程已退返回 None）。"""
    try:
        out = subprocess.run(["ps", "-M", str(pid)], capture_output=True, text=True, timeout=10)
        if out.returncode != 0:
            return None
        return len([ln for ln in out.stdout.splitlines() if ln.strip()]) - 1
    except (OSError, subprocess.TimeoutExpired):
        return None


# --------------------------------------------------------------------------- #
# 门控编排（以下全部真实调用，不进离线套件）                                   #
# --------------------------------------------------------------------------- #
def _wait_healthy(token: str, timeout_s: float = 90.0) -> bool:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(
                f"{BASE_URL}/api/v1/search/status",
                headers={"Authorization": f"Bearer {token}"},
            )
            urllib.request.urlopen(req, timeout=2)
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            time.sleep(1)
    return False


def _stop_sidecar(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _wait_drain(log_path: Path, manifest_path: Path, preset: dict) -> dict:
    """等演化后台任务安静（日志无新 [Evolve] 且 manifest 稳定）或超时。"""
    quiet, last_size, last_mtime = 0.0, 0, 0.0
    deadline = time.monotonic() + preset["drain_timeout_s"]
    while time.monotonic() < deadline:
        size = log_path.stat().st_size if log_path.exists() else 0
        mtime = manifest_path.stat().st_mtime if manifest_path.exists() else 0.0
        if size == last_size and mtime == last_mtime:
            quiet += 1.0
            if quiet >= preset["drain_quiet_s"]:
                return {"drained": True, "waited_s": quiet}
        else:
            quiet = 0.0
        last_size, last_mtime = size, mtime
        time.sleep(1.0)
    return {"drained": False, "waited_s": preset["drain_timeout_s"]}


def _post_search(client, token: str, payload: dict, stream: bool = False):
    url = f"{BASE_URL}/api/v1/search" + ("/stream" if stream else "")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    started = time.perf_counter()
    if stream:
        with client.stream("POST", url, json=payload, headers=headers,
                           timeout=PRESET["request_timeout_s"]) as resp:
            answer_parts: list[str] = []
            for line in resp.iter_lines():
                pass  # SSE 逐帧消费；result 帧由服务器保证完整（主实验不用流式）
            status = resp.status_code
        # 主实验限定串行同步端点；stream 仅预热 smoke 用，不进正式数据。
        return {"status": status, "data": {}, "elapsed_s": round(time.perf_counter() - started, 2)}
    resp = client.post(url, json=payload, headers=headers, timeout=PRESET["request_timeout_s"])
    elapsed = round(time.perf_counter() - started, 2)
    data = {}
    if resp.status_code == 200:
        body = resp.json()
        data = body.get("data") or {}
    return {"status": resp.status_code, "data": data, "elapsed_s": elapsed}


def run_arm(
    arm: str,
    env_path: Path,
    venv_dir: Path,
    token: str,
    work_path: Path,
    probe_paths: list[str],
    repo_root_snapshot: Path,
    state: dict,
    preset: dict,
) -> dict:
    """单臂完整流程：预热 → 快照 → 逐组（恢复快照 → 正式探针）→ drain。"""
    import httpx

    log_path = work_path.parent / f"sidecar_arm{arm}.log"
    manifest_path = work_path / ".cache" / "evolve" / "manifest.json"
    rows: list[dict] = []
    arm_record: dict = {"arm": arm, "evolution": arm == "B", "rows": rows}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def start_sidecar() -> subprocess.Popen:
        log_fh = log_path.open("ab")
        proc = subprocess.Popen(
            ["bash", str(REPO_ROOT / "scripts/sirchmunk/start_sidecar.sh"),
             str(venv_dir), str(env_path)],
            cwd=str(REPO_ROOT), stdout=log_fh, stderr=subprocess.STDOUT,
            env={**os.environ, "SIRCHMUNK_PORT": str(SIDECAR_PORT)},
        )
        if not _wait_healthy(token):
            raise RuntimeError(f"arm {arm}: sidecar did not become healthy")
        return proc

    def send(question: dict, role: str, group: str, repeat: int) -> dict:
        payload = {
            "query": question["query"], "mode": "FAST", "response_format": "context",
            "paths": probe_paths, "exclude_patterns": EXCLUDE_PATTERNS,
            "enable_dir_scan": True, "llm_fallback": False,
        }
        with httpx.Client(timeout=PRESET["request_timeout_s"]) as client:
            resp = _post_search(client, token, payload)
        metrics = extract_row_metrics(resp["data"], repo_root_snapshot, question.get("expected_sources", []))
        state["requests"] += 1
        state["total_tokens"] += metrics["total_llm_tokens"] or 0
        return judge_row({
            "arm": arm, "group": group, "role": role, "repeat": repeat,
            "id": question["id"], "query": question["query"],
            "answer_should_contain": question.get("answer_should_contain"),
            "expected_sources": question.get("expected_sources", []),
            "elapsed_s": resp["elapsed_s"], "status": resp["status"], **metrics,
        }, None)  # 日志块在臂结束后统一对齐（请求串行，块序=请求序）

    # ---- 预热：seed + 训练改写 + 语义独立补足 ----
    proc = start_sidecar()
    try:
        probe_groups = load_probe_groups()
        warmup_rows: list[dict] = []
        for g in probe_groups:
            for q in [next(x for x in g["formal"] if x["role"] == "seed"), *g["train"]]:
                warmup_rows.append(send(q, q["role"], g["group"], 0))
        for q in load_warmup_questions():
            warmup_rows.append(send(q, "warmup_fill", "warmup", 0))
        drain = _wait_drain(log_path, manifest_path, preset)

        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        events = parse_evolve_events(log_text)
        new_clusters = [cid for cid in dict.fromkeys(events["saved_cluster_ids"])]
        arm_record["warmup"] = {
            "rows": warmup_rows,
            "saved_cluster_ids": new_clusters,
            "phase1_merge_events": events["phase1_merge_events"],
            "evolve_steps": events["evolve_steps"],
            "drain": drain,
            "target_mechanism_effective": (
                len(new_clusters) >= preset["warmup_min_new_clusters"]
                and bool(events["phase1_merge_events"])
            ) if arm == "B" else None,
            "threads_after_warmup": sidecar_thread_count(proc.pid),
        }
        if arm == "B" and not arm_record["warmup"]["target_mechanism_effective"]:
            arm_record["mechanism_note"] = (
                "target mechanism (>=5 new clusters + phase-1 merge) NOT confirmed; "
                "merge-related conclusions are not_assessable"
            )
        # 预热快照（knowledge / spec / evolve 全量）
        cache_dir = work_path / ".cache"
        snapshot_dir = work_path.parent / f"warmup_snapshot_arm{arm}"
        if cache_dir.exists():
            shutil.copytree(cache_dir, snapshot_dir, dirs_exist_ok=True)
        arm_record["warmup_snapshot"] = {
            "path_name": snapshot_dir.name,
            "manifest": json.loads(manifest_path.read_text()) if manifest_path.exists() else None,
        }
    finally:
        _stop_sidecar(proc)

    # ---- 正式语义组：每组从同一快照的独立副本开始 ----
    snapshot_cache = work_path.parent / f"warmup_snapshot_arm{arm}"
    for g in probe_groups:
        for sub in ("knowledge", "spec", "evolve"):
            shutil.rmtree(work_path / ".cache" / sub, ignore_errors=True)
            src = snapshot_cache / sub
            if src.exists():
                shutil.copytree(src, work_path / ".cache" / sub)
        proc = start_sidecar()
        try:
            for repeat in range(1, preset["repeats"] + 1):
                for q in g["formal"]:
                    row = send(q, q["role"], g["group"], repeat)
                    rows.append(row)
                    abort = check_budget(state, preset)
                    if abort:
                        arm_record["abort_reason"] = abort
                        raise RuntimeError(abort)
            drain = _wait_drain(log_path, manifest_path, preset)
            arm_record.setdefault("group_drains", {})[g["group"]] = drain
            arm_record.setdefault("threads_after_probes", {})[g["group"]] = sidecar_thread_count(proc.pid)
        finally:
            _stop_sidecar(proc)
        # 组后 manifest / 簇存储差异
        arm_record.setdefault("post_group_manifest", {})[g["group"]] = (
            json.loads(manifest_path.read_text()) if manifest_path.exists() else None
        )

    # ---- 日志对齐：按访问行切块，串行块序 = 发送序 ----
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    blocks = split_requests(log_text)
    send_order = [r for g_rows in [arm_record["warmup"]["rows"], rows] for r in g_rows]
    aligned = 0
    for i, r in enumerate(send_order):
        block = blocks[i] if i < len(blocks) else None
        merged = judge_row(r, block)
        r.clear()
        r.update(merged)
        aligned += 1 if block else 0
    arm_record["log_alignment"] = {
        "requests_sent": len(send_order), "log_blocks_found": len(blocks), "aligned": aligned,
    }
    arm_record["sidecar_log_tail"] = log_text[-8000:]
    arm_record["summary"] = summarize_arm(rows)
    return arm_record


def write_report(results: dict, out_dir: Path) -> None:
    json_path = out_dir / "ke2_results.json"
    md_path = out_dir / "ke2_results.md"
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    a, b = results["arms"]["A"]["summary"], results["arms"]["B"]["summary"]
    pa = results["paired"]
    lines = [
        "# KE2 成对实验结果（知识演化 vs 只开复用）",
        "",
        f"- 日期：{results['meta']['date']}｜语料 commit：`{results['meta']['corpus_commit']}`｜"
        f"上游：`{results['meta']['upstream_commit']}`",
        f"- 预设：{results['meta']['preset']['semantic_groups']} 组 × "
        f"{results['meta']['preset']['rewords_per_group']} 改写 × {results['meta']['preset']['repeats']} 重复；"
        f"提升门 = B−A ≥ {results['meta']['preset']['min_reuse_gain']} 且 A 臂 reword = 0",
        f"- 成本：A {a['total_tokens']} tok / B {b['total_tokens']} tok（上限 "
        f"{results['meta']['preset']['max_total_tokens']}）",
        "",
        "| 臂 | 角色 | n | source_hit | reuse_candidate | fast_reused | answer_correct | tokens |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, s in (("A(演化关)", a), ("B(演化开)", b)):
        for role, st in s["by_role"].items():
            lines.append(
                f"| {name} | {role} | {st['n']} | {st['source_hit']} | {st['reuse_candidate']} "
                f"| {st['fast_reused']} | {st['answer_correct']} | {st['tokens']} |"
            )
    lines += [
        "",
        f"## 判定",
        "",
        f"- reword fast_reused：A {pa['reword_hits_armA']}/{pa['reword_total_armA']} vs "
        f"B {pa['reword_hits_armB']}/{pa['reword_total_armB']}，差值 {pa['diff']}，"
        f"bootstrap 95% CI {pa['bootstrap_diff_ci95']}",
        f"- **verdict: {pa['verdict']}**（门：B−A ≥ {pa['preset_gate']['min_diff']} 且 A reword = 0）",
        "",
        "无效轮与原因、逐请求完整答案与日志对齐见 ke2_results.json。",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KE2 paired knowledge-evolution experiment (gated).")
    parser.add_argument("--env-file", default=str(REPO_ROOT / ".env-sirchmunk"))
    parser.add_argument("--venv-dir", default=str(REPO_ROOT / ".venv-sirchmunk"))
    parser.add_argument("--corpus-commit", default="HEAD", help="语料快照基线 commit（默认当前 HEAD）")
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "scripts/sirchmunk"))
    args = parser.parse_args(argv)

    if os.getenv("RAG_AGENTIC_INTEGRATION") != "1":
        print("KE2 experiment is gated: set RAG_AGENTIC_INTEGRATION=1 to run (costs real tokens).",
              file=sys.stderr)
        return 2

    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f"env file missing: {env_path}", file=sys.stderr)
        return 2
    token = ""
    for line in env_path.read_text().splitlines():
        if line.startswith("SIRCHMUNK_API_TOKEN="):
            token = line.split("=", 1)[1].strip()
    if not token:
        print("SIRCHMUNK_API_TOKEN missing in env file", file=sys.stderr)
        return 2

    tmp = Path(os.path.expanduser(os.environ.get("KE2_TMPDIR", "~/tmp/ke2_experiment")))
    if tmp.exists():
        print(f"refusing to overwrite existing experiment dir: {tmp}", file=sys.stderr)
        return 2
    tmp.mkdir(parents=True)

    def git(repo: Path, *a: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True,
                              check=True).stdout.strip()

    corpus_commit = git(REPO_ROOT, "rev-parse", args.corpus_commit)
    upstream_commit = git(Path(os.path.expanduser("~/Desktop/Project/Job-Search/sirchmunk")),
                          "rev-parse", "HEAD")
    snap = tmp / "corpus_snapshot"
    subprocess.run(["bash", str(REPO_ROOT / "scripts/sirchmunk/corpus_snapshot.sh"),
                    corpus_commit, str(snap)], check=True, capture_output=True)
    corpus_root = snap / "corpus"
    probe_paths = [str(corpus_root / p) for p in ("README.md", "src", "docs")]

    state = {"requests": 0, "total_tokens": 0}
    base_env_text = env_path.read_text()
    results: dict = {
        "meta": {
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "corpus_commit": corpus_commit,
            "upstream_commit": upstream_commit,
            "preset": PRESET,
            "probe_paths": probe_paths,
            "exclude_patterns": EXCLUDE_PATTERNS,
            "snapshot_sums": (snap / "SHA256SUMS").read_text(encoding="utf-8")[:2000],
        },
        "state": state,
        "arms": {},
    }
    try:
        for arm, evolution in (("A", False), ("B", True)):
            work = tmp / f"arm_{arm}" / "work"
            work.mkdir(parents=True, exist_ok=True)
            (work / ".cache").mkdir(exist_ok=True)
            arm_env = tmp / f"arm_{arm}" / "env"
            arm_env.write_text(build_arm_env(base_env_text, work, evolution), encoding="utf-8")
            os.chmod(arm_env, 0o600)
            results["arms"][arm] = run_arm(
                arm, arm_env, Path(args.venv_dir), token, work, probe_paths,
                corpus_root, state, PRESET,
            )
        results["paired"] = paired_analysis(
            results["arms"]["A"]["rows"], results["arms"]["B"]["rows"], PRESET
        )
        verdict_reasons = []
        for arm in ("A", "B"):
            rec = results["arms"][arm]
            if rec.get("mechanism_note"):
                verdict_reasons.append(f"arm {arm}: {rec['mechanism_note']}")
            if rec["log_alignment"]["aligned"] != rec["log_alignment"]["requests_sent"]:
                verdict_reasons.append(f"arm {arm}: log misalignment")
        results["verdict_caveats"] = verdict_reasons
        write_report(results, Path(args.out_dir))
        print(f"KE2 done: verdict={results['paired']['verdict']} "
              f"tokens A={results['arms']['A']['summary']['total_tokens']} "
              f"B={results['arms']['B']['summary']['total_tokens']}")
        return 0
    finally:
        if os.getenv("KE2_KEEP_TMP") != "1":
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
