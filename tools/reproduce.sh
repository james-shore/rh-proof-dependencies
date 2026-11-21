#!/bin/bash
# Rebuild the dataset and the structure tables from the published kernel export.
#   tools/reproduce.sh WORKDIR
# Needs Linux, Docker, git, curl, ~120 GB of disk, ~250 GB of RAM. Stage 1 downloads (network). Stage 2 runs every
# step in a container with no network. No code from the proof runs: the export and the source are read as data.
# Env: CPUS (default 8), MEM (default 250g), SOLUTION (an existing solution.ndjson.zst), SKIP_STRUCTURE=1,
# SKIP_RELEVANCE=1, SKIP_DUPES=1, SKIP_UNIVERSES=1 (the analyses are tools/analyses.sh), NAME (container name prefix, default rhpd), IMG (image tag, default rhpd-tools:0.1).
set -euo pipefail
trap 'echo "reproduce.sh failed at line $LINENO; see the logs under $W"' ERR
here=$(cd "$(dirname "$0")" && pwd)
W=$(realpath -m "${1:?usage: reproduce.sh WORKDIR}")
CPUS=${CPUS:-8}
MEM=${MEM:-250g}
N=${NAME:-rhpd}
V=v0.1.3
IMG=${IMG:-rhpd-tools:0.1}
SOL_SHA=61a8938ad5f5e57397c1c0e3b954916af546d9004ade01c3555744a681b05381
UNPACKED_SHA=5ea74b912147c5c93cc2b6d70cc727cf908df2f62ac6a20480a3fa02f6be244e
MIRROR=https://github.com/leanprover-community/riemann-hypothesis
MIRROR_REV=8b83d5e7b319ebc7b3be6e59ce3411ba2b09d116
THMS=riemannHypothesis,riemannZeta_eq_zero_re,LFunction_ne_zero_of_half_lt_re,LFunction_eq_zero_re,LFunction_eq_zero_re_of_isPrimitive
mkdir -p "$W"/{dl,lean,work/full,work/fold-v0,out}
log() { echo "[$(date -u +%H:%M:%S)] $*"; }

# ---------- stage 1: downloads ----------
log "kernel export"
if [ -n "${SOLUTION:-}" ]; then SOL=$(realpath "$SOLUTION"); else
  SOL=$W/dl/solution.ndjson.zst
  [ -e "$SOL" ] || curl -sSf -C - --retry 10 -o "$SOL" https://math.mit.edu/~drew/grh/solution.ndjson.zst
fi
echo "$SOL_SHA  $SOL" | sha256sum -c -
log "source $MIRROR_REV"
[ -d "$W/mirror/.git" ] || git clone -q "$MIRROR" "$W/mirror"
git -C "$W/mirror" checkout -q "$MIRROR_REV"
[ "$(git -C "$W/mirror" rev-parse HEAD)" = "$MIRROR_REV" ] || { echo "source is not at $MIRROR_REV"; exit 1; }
log "Lean 4.33.1 and Mathlib 0df444a3 prebuilt cache"
if [ ! -f "$W/lean/staged.ok" ]; then
  docker run --rm --ulimit nofile=1048576:1048576 --cpus "$CPUS" --memory "$MEM" --name "$N-stage" -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" -v "$W/lean":/x \
    -v "$here/origin-stage.sh":/stage.sh:ro python:3.12-slim-bookworm bash /stage.sh > "$W/lean/stage.log" 2>&1
  if grep -q "decompression(s) failed" "$W/lean/cache-get.log"; then echo "Mathlib cache unpack failed: $W/lean/cache-get.log"; exit 1; fi
  touch "$W/lean/staged.ok"
fi
log "tools image $IMG"
docker build -q -t "$IMG" "$here" > /dev/null

# ---------- stage 2: offline ----------
run() { docker run --rm --init --network none --ulimit nofile=1048576:1048576 --cpus "$CPUS" --memory "$MEM" \
  --user "$(id -u):$(id -g)" -e HOME=/tmp -e RAYON_NUM_THREADS="$CPUS" -e WORKERS="$CPUS" -e RHS_THREADS="$CPUS" \
  -v "$SOL":/dl/solution.ndjson.zst:ro -v "$W/mirror":/mirror:ro -v "$W/lean":/x -v "$W/work":/work -v "$W/out":/out \
  "$@"; }
log "parse the export"
run --name "$N-parse" "$IMG" bash -c "set -o pipefail
  zstd -dc --long=30 /dl/solution.ndjson.zst | tee >(sha256sum > /work/full/unpacked.sha256) |
  export-parse /work/full /mirror/export-targets.txt $THMS" 2> "$W/work/full/parse.log"
sleep 2; grep -q "^$UNPACKED_SHA " "$W/work/full/unpacked.sha256" || { echo "unpacked sha256 mismatch"; exit 1; }
log "Mathlib, core and dependency constants"
run --name "$N-origin" "$IMG" bash -c '
  set -euo pipefail; mkdir -p /x/out /x/run; cp origin.lean /x/run/; cd /x/run
  T=/x/elan/toolchains/leanprover--lean4---v4.33.1
  list() { (cd "$2" && find . -name "*.olean" -type f | sed "s|^\./||; s|\.olean$||" | sort | sed "s|$|\t$1\t$2|"); }
  { list core $T/lib/lean; list mathlib /x/mathlib4/.lake/build/lib/lean
    for d in /x/mathlib4/.lake/packages/*; do [ -d $d/.lake/build/lib/lean ] && list "$(basename $d)" $d/.lake/build/lib/lean; done; } > modules.tsv
  $T/bin/lean --run origin.lean modules.tsv origin
  { printf "name\tmodule\tpackage\tkind\tisUnsafe\n"; LC_ALL=C sort -t"	" -k1,1 -k2,2 origin.rows.tsv; } > /x/out/deps-constants.tsv' \
  > "$W/lean/origin.log" 2>&1
log "source declaration index (text only)"
run --name "$N-index" "$IMG" python decl_index.py /mirror /work/decl-index.tsv.gz --jobs "$CPUS" \
  --summary /work/decl-index-summary.md > "$W/work/index.log" 2>&1
log "fold and map"
run --name "$N-fold" "$IMG" bash -c "set -e
  python fold.py /work/full/constants.tsv /x/out/deps-constants.tsv /work/decl-index.tsv.gz /mirror /work/fold-v0
  python summarize.py /work/fold-v0/constants-mapped.tsv /x/out/deps-constants.tsv /work/fold-v0/summary-stats.json
  cd /work/fold-v0 && gzip -6 -kf constants-mapped.tsv decls.tsv" > "$W/work/fold.log" 2>&1
log "dataset tables and why-parents"
run --name "$N-dataset" "$IMG" bash -c "set -e
  python dataset.py /work /out/$V; python why.py /out/$V /out/$V/why; python content_hash.py /out/$V; python name_lists.py /out/$V" > "$W/work/dataset.log" 2>&1

if [ -z "${SKIP_STRUCTURE:-}" ]; then
  SOLUTION="$SOL" NAME="$N" IMG="$IMG" CPUS="$CPUS" MEM="$MEM" bash "$here/analyses.sh" "$W" "$V"
fi
if [ -z "${SKIP_UNIVERSES:-}" ]; then
  log "universe levels (parses the export again)"
  run --name "$N-universes" "$IMG" bash -c "set -o pipefail; mkdir -p /out/universes
    zstd -dc --long=30 /dl/solution.ndjson.zst | KEEP_EXPR_DETAIL=1 universes /out/universes $THMS" > "$W/work/universes.log" 2>&1
fi

log "checks"
grep -E "parse errors|^constants: |closure" "$W/work/full/stats.txt" || true
grep -E "^checks" "$W/work/dataset.log" || true
( cd "$W/work" && sha256sum full/constants.tsv full/edges.tsv fold-v0/keep-v0.tsv fold-v0/directories.tsv )
grep -E "  (constants|edges|decls|directories) " "$W/work/dataset.log" || true
log "done: $W/out/$V (expected: 1484729 constants in the closure, 1270184 project, 804911 declarations used)"
