#!/bin/bash
# Build the tools image and run the tests in it (no network): tools/test.sh
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
IMG=${IMG:-rhpd-tools:0.1}
docker build -q -t "$IMG" "$here" > /dev/null
docker run --rm --init --name "${NAME:-rhpd}-test" --network none --cpus "${CPUS:-4}" --memory "${MEM:-32g}" "$IMG" bash -c 'set -e; W=$(mktemp -d)
  python structure/tests/test_dom.py $RHS_BIN/dom
  python structure/tests/test_dominators.py $W/dom
  python structure/tests/test_ownership.py $W/dom
  python structure/tests/test_query.py $W/dom
  if [ -n "${LEAN_TOOLCHAIN:-}" ] && bash structure/tests/relevance/export.sh $W/rel > $W/export.log 2>&1; then
    python structure/tests/test_relevance.py $RHS_BIN/relevance $W/rel | head -1
    python structure/tests/test_dupes.py $RHS_BIN/dupes $W/rel | head -1
    python structure/tests/test_xtwins.py $RHS_BIN/xtwins $W/rel | head -1
  else echo "relevance, dupes and xtwins tests skipped: needs Lean 4.33.1 and lean4export v4.33.0 (LEAN_TOOLCHAIN, LEAN4EXPORT)"; fi'
