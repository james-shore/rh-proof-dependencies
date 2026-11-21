#!/bin/bash
# Export all of Mathlib (with core and its dependencies) with lean4export v4.33.0 on Lean 4.33.1.
# usage: mathlib-export.sh MATHLIB_DIR OUT.ndjson.zst   env: LEAN_TOOLCHAIN, LEAN4EXPORT (as in tests/relevance/export.sh)
# MATHLIB_DIR is a Mathlib checkout with its prebuilt cache (lake exe cache get). No network needed.
set -euo pipefail
M=$1; OUT=$2
TC=${LEAN_TOOLCHAIN:?set LEAN_TOOLCHAIN to a Lean 4.33.1 toolchain directory}
L4E=${LEAN4EXPORT:?set LEAN4EXPORT to a lean4export v4.33.0 binary}
LP=$TC/lib/lean:$M/.lake/build/lib/lean
for d in $M/.lake/packages/*; do [ -d $d/.lake/build/lib/lean ] && LP=$LP:$d/.lake/build/lib/lean; done
echo "LEAN_PATH=$LP" >&2
git -C $M rev-parse HEAD >&2 || true
set -o pipefail
PATH=$TC/bin:$PATH LEAN_PATH=$LP $L4E Mathlib | tee >(sha256sum > $OUT.unpacked.sha256) | zstd -q -T2 -10 --long=30 -o $OUT
sha256sum $OUT > $OUT.sha256
ls -la $OUT; cat $OUT.unpacked.sha256
