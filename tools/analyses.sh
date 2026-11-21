#!/bin/bash
# Dominator tree, statement cone, unused hypotheses, duplicates and labelled-statement shares for one dataset.
#   tools/analyses.sh WORKDIR DATASET      (DATASET = v0.1.3 after reproduce.sh, or v0.2 after reproduce-compiled.sh)
# Writes WORKDIR/out/DATASET/analyses/. Every step runs in a container with no network.
# Env: CPUS (default 8), MEM (default 250g), NAME, IMG, SOLUTION (default WORKDIR/dl/solution.ndjson.zst),
# SKIP_RELEVANCE=1, SKIP_DUPES=1.
set -euo pipefail
trap 'echo "analyses.sh failed at line $LINENO"' ERR
here=$(cd "$(dirname "$0")" && pwd)
W=$(realpath -m "${1:?usage: analyses.sh WORKDIR DATASET}")
D=${2:?usage: analyses.sh WORKDIR DATASET}
CPUS=${CPUS:-8}; MEM=${MEM:-250g}; N=${NAME:-rhpd}-$D; IMG=${IMG:-rhpd-tools:0.1}
SOL=$(realpath "${SOLUTION:-$W/dl/solution.ndjson.zst}")
[ -f "$W/out/$D/constants.parquet" ] || { echo "no dataset at $W/out/$D"; exit 1; }
O=/out/$D/analyses
mkdir -p "$W/out/$D/analyses" "$W/work/$D"
log() { echo "[$(date -u +%H:%M:%S)] $*"; }
run() { docker run --rm --init --network none --ulimit nofile=1048576:1048576 --cpus "$CPUS" --memory "$MEM" \
  --user "$(id -u):$(id -g)" -e HOME=/tmp -e RAYON_NUM_THREADS="$CPUS" -e WORKERS="$CPUS" -e RHS_THREADS="$CPUS" \
  -v "$SOL":/dl/solution.ndjson.zst:ro -v "$W/out":/out -v "$here/../data/inputs":/inputs:ro "$@"; }

log "$D: dominator tree and statement cone"
run --name "$N-structure" "$IMG" bash -c "set -e; mkdir -p $O/concepts
  python structure/dominators.py /out/$D $O; python structure/concepts.py /out/$D $O/concepts" > "$W/work/$D/structure.log" 2>&1
if [ -z "${SKIP_RELEVANCE:-}" ]; then
  log "$D: unused hypotheses (parses the export again)"
  run --name "$N-relevance" "$IMG" bash -c "set -e; cd structure; bash relevance-run.sh /out/$D $O/relevance /dl/solution.ndjson.zst
    mkdir -p $O/data; python relevance_tables.py $O $O/data" > "$W/work/$D/relevance.log" 2>&1
fi
if [ -z "${SKIP_DUPES:-}" ]; then
  log "$D: duplicate statements and definitions (parses the export again)"
  run --name "$N-dupes" "$IMG" bash -c "set -e; cd structure; bash dupes-run.sh /out/$D $O/dupes /dl/solution.ndjson.zst
    mkdir -p $O/data; python duplicates.py /out/$D $O/dupes $O/data $O" > "$W/work/$D/dupes.log" 2>&1
fi
log "$D: statements labelled as published results"
run --name "$N-literature" "$IMG" bash -c "set -e; L=$O/literature; mkdir -p \$L
  python literature_map.py /out/$D /inputs/rhtrim-LITERATURE-4339aac-names.txt \$L
  python literature_dom.py $O/dominators.tsv.gz \$L /inputs/jae63-BLUEPRINT-0434143d-labels.tsv \$L/literature.tsv \$L/literature-dom.json
  python literature_groups.py \$L/literature.tsv \$L
  for g in published jae-known jae-crux; do python structure/ownership.py /out/$D $O \$L/L-\$g.txt \$L/own-\$g; done" \
  > "$W/work/$D/literature.log" 2>&1
log "$D: done, $W/out/$D/analyses"
