#!/bin/bash
# Compile RelTest.lean with Lean 4.33.1 and export it with lean4export v4.33.0 (env LEAN_TOOLCHAIN, LEAN4EXPORT).
# usage: export.sh OUTDIR   -> OUTDIR/RelTest.ndjson
set -euo pipefail
O=$1; mkdir -p $O; cp "$(dirname "$0")/RelTest.lean" $O/
TC=${LEAN_TOOLCHAIN:-/opt/rh/elan/toolchains/leanprover--lean4---v4.33.1}   # Lean 4.33.1 toolchain dir
L4E=${LEAN4EXPORT:-/opt/rh/bin/lean4export}                                  # lean4export v4.33.0 binary
cd $O
$TC/bin/lean -o RelTest.olean RelTest.lean
T="RelTest.dupA RelTest.dupB RelTest.tw1 RelTest.tw2 RelTest.natTwin RelTest.root1 RelTest.root2 RelTest.root3 RelTest.root4 RelTest.root5 RelTest.root6 RelTest.inConcl RelTest.inLater RelTest.viaDef RelTest.univ RelTest.dataUnused RelTest.optP"
PATH=$TC/bin:$PATH LEAN_PATH=$TC/lib/lean:$O $L4E RelTest -- $T > RelTest.ndjson
wc -l RelTest.ndjson
