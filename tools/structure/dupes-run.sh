#!/bin/bash
# Duplicate statements/definitions: dupes-run.sh DATASET_DIR OUT_DIR SOLUTION_NDJSON_ZST   (run from tools/structure/)
set -euo pipefail
DS=$1; O=$2; EXPORT=$3; mkdir -p $O
if [ ! -s $O/project-names.txt ]; then
  python -c "
import duckdb
c = duckdb.connect(); c.execute('set threads=4')
c.execute(\"copy (select name from read_parquet('$DS/constants.parquet') where origin = 'project') to '$O/project-names.txt' (header false, quote '', escape '')\")
"
fi
zstd -dc --long=30 "$EXPORT" | ${RHS_BIN:-rs/target/release}/dupes $O $O/project-names.txt
