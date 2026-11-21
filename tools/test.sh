#!/bin/bash
# Build the tools image and run the tests in it (no network): tools/test.sh
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
IMG=${IMG:-rhpd-tools:0.1}
docker build -q -t "$IMG" "$here" > /dev/null
docker run --rm --init --name "${NAME:-rhpd}-test" --network none "$IMG" bash -c 'set -e; W=$(mktemp -d)
  python structure/tests/test_dom.py $RHS_BIN/dom
  python structure/tests/test_dominators.py $W/dom
  python structure/tests/test_ownership.py $W/dom
  if [ -n "${LEAN_TOOLCHAIN:-}" ] && bash structure/tests/relevance/export.sh $W/rel > $W/export.log 2>&1; then
    python structure/tests/test_relevance.py $RHS_BIN/relevance $W/rel | head -1
  else echo "relevance test skipped: needs Lean 4.33.1 and lean4export v4.33.0 (LEAN_TOOLCHAIN, LEAN4EXPORT)"; fi'
