#!/usr/bin/env bash
# KE0：物化不可变语料快照（SIRCHMUNK_KNOWLEDGE_EVOLUTION_SPEC KE0 / 第 4 章「固定与配对」）。
# 从指定 commit 导出 tracked 树（git archive，天然排除 data/、logs/ 等未跟踪运行时产物），
# 并生成 SHA256 清单：同一 commit 重复物化结果逐字节一致，供 KE2 成对实验固定与审计语料版本。
# 用法: bash scripts/sirchmunk/corpus_snapshot.sh <commit> <outdir> [repo_dir]
#   <outdir> 建议放在仓库外（避免快照自身被检索/摄取；KE2 查询的泄题排除口径不因快照改变）。
#   [repo_dir] 缺省为脚本所在项目仓库（即实验语料本体）；单测注入临时仓库。
# 产出布局：<outdir>/corpus/（tracked 树）、<outdir>/SHA256SUMS（相对 corpus/ 的清单）、
#           <outdir>/snapshot_info.txt（commit 与物化时间）。
# 离线、零网络、不消耗 LLM token。
set -euo pipefail

COMMIT="${1:?usage: corpus_snapshot.sh <commit> <outdir> [repo_dir]}"
OUT="${2:?usage: corpus_snapshot.sh <commit> <outdir> [repo_dir]}"
REPO="${3:-$(cd "$(dirname "$0")/../.." && pwd)}"

git -C "$REPO" cat-file -e "${COMMIT}^{commit}" 2>/dev/null || {
  echo "commit $COMMIT not found in $REPO" >&2
  exit 1
}
if [ -e "$OUT" ]; then
  echo "outdir $OUT already exists (refuse to overwrite an immutable snapshot)" >&2
  exit 2
fi

RESOLVED="$(git -C "$REPO" rev-parse "${COMMIT}^{commit}")"
CORPUS="$OUT/corpus"
mkdir -p "$CORPUS"
git -C "$REPO" archive "$RESOLVED" | tar -x -C "$CORPUS"

# 清单：相对 corpus/ 的路径 + SHA256，路径排序保证跨机器逐字节一致
# （find -print0 / sort -z / read -d '' 兼容 macOS BSD 与 GNU 工具链；
#  ${f#./} 去掉 find 的 ./ 前缀，得到与 corpus/ 相对路径一致的清单行）。
(
  cd "$CORPUS" &&
    while IFS= read -r -d '' f; do
      printf '%s  %s\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "${f#./}"
    done < <(find . -type f -print0 | LC_ALL=C sort -z)
) >"$OUT/SHA256SUMS"
printf 'commit=%s\ncreated_at=%s\n' "$RESOLVED" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  >"$OUT/snapshot_info.txt"

echo "== corpus snapshot: $RESOLVED -> $OUT ($(wc -l <"$OUT/SHA256SUMS" | tr -d ' ') files) =="
