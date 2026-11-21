#!/bin/bash
# Project restatements of Mathlib: xtwins-run.sh DATASET_DIR OUT_DIR SOLUTION_NDJSON_ZST MATHLIB_NDJSON_ZST DEPS_CONSTANTS_TSV
# (run from tools/structure/; MATHLIB_NDJSON_ZST from mathlib-export.sh; DEPS_CONSTANTS_TSV = deps-constants.tsv from reproduce.sh, the dependency-module table)
set -euo pipefail
DS=$1; O=$2; SOL=$3; ML=$4; DEPS=$5; mkdir -p $O/data
if [ ! -s $O/mathlib-keys.tsv ]; then
  zstd -dc --long=30 "$ML" | ${RHS_BIN:-rs/target/release}/xtwins keys $O/mathlib-keys.tsv
fi
if [ ! -s $O/project-names.txt ]; then
  python -c "
import duckdb
c = duckdb.connect(); c.execute('set threads=4')
c.execute(\"copy (select name from read_parquet('$DS/constants.parquet') where origin = 'project') to '$O/project-names.txt' (header false, quote '', escape '')\")
"
fi
zstd -dc --long=30 "$SOL" | ${RHS_BIN:-rs/target/release}/xtwins twins $O/mathlib-keys.tsv $O $O/project-names.txt
python mathlib_restatements.py $DS $O "$DEPS" $O/data
