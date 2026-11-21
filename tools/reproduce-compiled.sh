#!/bin/bash
# Optional stage after tools/reproduce.sh: declarations, files and lines from the compiled environment (dataset v0.2).
#   OLEANS=/path/to/build/lib/lean tools/reproduce-compiled.sh WORKDIR
# This loads the proof's compiled .olean files, which were produced by running the proof's code. Treat them as
# untrusted. Every container runs with no network, as a non-root user, with no capabilities, a read-only root
# filesystem and read-only mounts of the oleans, the toolchain and Mathlib.
# Needs: OLEANS, a full build of the 175,187 project modules with Lean 4.33.1 against Mathlib 0df444a3 (Main.olean at
# its top); WORKDIR, the directory tools/reproduce.sh filled. Env: CPUS (default 8), MEM (default 250g), NAME, IMG,
# SOLUTION (as for reproduce.sh), SKIP_STRUCTURE=1 (skip tools/analyses.sh on v0.2).
set -euo pipefail
trap 'echo "reproduce-compiled.sh failed at line $LINENO"' ERR
here=$(cd "$(dirname "$0")" && pwd)
W=$(realpath -m "${1:?usage: OLEANS=... reproduce-compiled.sh WORKDIR}")
OLEANS=$(realpath "${OLEANS:?set OLEANS to the lib/lean directory of a full build}")
CPUS=${CPUS:-8}; MEM=${MEM:-250g}; N=${NAME:-rhpd}-compiled; IMG=${IMG:-rhpd-tools:0.1}
TC=$W/lean/elan/toolchains/leanprover--lean4---v4.33.1
WS=$W/lean/mathlib4
R=$W/compiled
[ -f "$OLEANS/Main.olean" ] || { echo "no Main.olean in $OLEANS"; exit 1; }
[ -x "$TC/bin/lean" ] && [ -d "$WS/.lake/build/lib/lean/Mathlib" ] || { echo "run tools/reproduce.sh first"; exit 1; }
[ -f "$W/out/v0.1.3/constants.parquet" ] || { echo "run tools/reproduce.sh first (dataset v0.1.3)"; exit 1; }
mkdir -p "$R/build" "$R/out"
log() { echo "[$(date -u +%H:%M:%S)] $*"; }
sbx() { docker run --rm --init --network none --user "$(id -u):$(id -g)" --cap-drop ALL \
  --security-opt no-new-privileges --read-only --tmpfs /tmp:rw,exec,size=8g --cpus "$CPUS" --memory "$MEM" \
  --ulimit nofile=1048576:1048576 -e HOME=/tmp "$@"; }

log "build the dump program (Lean core only, no proof code)"
rm -rf "$R/build/routeb" && cp -r "$here/routeb" "$R/build/"
sbx --name "$N-build" -v "$TC":/tc:ro -v "$R/build/routeb":/rb -w /rb "$IMG" bash -c 'PATH=/tc/bin:$PATH lake build -q'

log "search path: project oleans, Mathlib and its packages, core"
{ echo /oleans; echo /ws/.lake/build/lib/lean
  for d in "$WS"/.lake/packages/*; do [ -d "$d/.lake/build/lib/lean" ] && echo "/ws/.lake/packages/$(basename "$d")/.lake/build/lib/lean"; done
  echo /tc/lib/lean; } > "$R/out/sp.txt"

log "dump Main's environment (loads the untrusted oleans, sandboxed)"
sbx --name "$N-dump" -v "$OLEANS":/oleans:ro -v "$WS":/ws:ro -v "$TC":/tc:ro -v "$R/build/routeb/.lake/build/bin":/bin2:ro \
  -v "$R/out":/work -w /work "$IMG" /bin2/routeb /work/sp.txt /work/main Main > "$R/dump.log" 2>&1

ROOTS5=riemannHypothesis,riemannZeta_eq_zero_re,LFunction_ne_zero_of_half_lt_re,LFunction_eq_zero_re,LFunction_eq_zero_re_of_isPrimitive
TARGETS=$(tr -s ' \n' ',' < "$W/mirror/export-targets.txt" | sed 's/,$//')
PROJ=BuildAid,CompanionTheory,Development,Formalism,GroundTheory,Lemmata,PositivityTheory,ReciprocityTheory,Supplements,Statements,Main
py() { local n=$1; shift; sbx --name "$N-$n" -v "$W/out":/out -v "$R/out":/work -v "$W/mirror":/mirror:ro -v "$W/work":/w:ro "$IMG" "$@"; }

log "closure of the five theorems, compared with the kernel export"
py closure-five python routeb_closure.py /work/main /out/v0.1.3 /work/five "$ROOTS5" | tee "$R/closure-five.log"
py closure-targets python routeb_closure.py /work/main /out/v0.1.3 /work/targets "$TARGETS" | tee "$R/closure-targets.log"
grep -q "only in B 0, only in A 0" "$R/closure-five.log" || { echo "compiled cone differs from the export"; exit 1; }

log "map constants to declarations and compiled ranges"
py map python routeb_map.py /work/main "$PROJ" /work/five/routeb-closure.tsv /work/map

log "dataset v0.2, why-parents, name lists, declaration graph"
py dataset bash -c "set -e; python dataset_v2.py /out/v0.1.3 /work/map/routeb-constants-mapped.tsv /w/decl-index.tsv.gz /mirror /out/v0.2
  cp /out/v0.1.3/edges.parquet /out/v0.1.3/edges.csv.gz /out/v0.2/; python why.py /out/v0.2 /out/v0.2/why
  python name_lists.py /out/v0.2; python decl_edges.py /work /out/v0.2"
( cd "$W/out/v0.2" && sha256sum *.parquet *.csv.gz *.txt.gz keep-v2.tsv why/why-parents.parquet why/why-parents.csv.gz )
if [ -z "${SKIP_STRUCTURE:-}" ]; then
  NAME="${NAME:-rhpd}" IMG="$IMG" CPUS="$CPUS" MEM="$MEM" bash "$here/analyses.sh" "$W" v0.2
fi
log "done: $W/out/v0.2 (expected: 804,912 used declarations of 1,061,400; compiled cone 1,484,729 = export)"
