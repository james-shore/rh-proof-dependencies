#!/bin/bash
# Compile RelTest.lean with Lean 4.33.1 and export it with lean4export v4.33.0 (env LEAN_TOOLCHAIN, LEAN4EXPORT).
# usage: export.sh OUTDIR   -> OUTDIR/RelTest.ndjson, OUTDIR/KeysTest.ndjson
set -euo pipefail
O=$1; D=$(cd "$(dirname "$0")" && pwd); mkdir -p $O; cp $D/RelTest.lean $D/KeysTest.lean $O/
TC=${LEAN_TOOLCHAIN:?set LEAN_TOOLCHAIN to a Lean 4.33.1 toolchain directory}   # Lean 4.33.1 toolchain dir
L4E=${LEAN4EXPORT:?set LEAN4EXPORT to a lean4export v4.33.0 binary}                                  # lean4export v4.33.0 binary
cd $O
$TC/bin/lean -o RelTest.olean RelTest.lean
T="RelTest.bigLit RelTest.bigLitOff RelTest.dupA RelTest.dupB RelTest.tw1 RelTest.tw2 RelTest.natTwin RelTest.root1 RelTest.root2 RelTest.root3 RelTest.root4 RelTest.root5 RelTest.root6 RelTest.inConcl RelTest.inLater RelTest.viaDef RelTest.univ RelTest.dataUnused RelTest.optP"
PATH=$TC/bin:$PATH LEAN_PATH=$TC/lib/lean:$O $L4E RelTest -- $T > RelTest.ndjson
$TC/bin/lean -o KeysTest.olean KeysTest.lean
PATH=$TC/bin:$PATH LEAN_PATH=$TC/lib/lean:$O $L4E KeysTest > KeysTest.ndjson
wc -l RelTest.ndjson KeysTest.ndjson
