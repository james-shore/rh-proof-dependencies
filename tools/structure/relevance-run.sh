#!/bin/bash
# Unused-hypothesis pass: relevance-run.sh DATASET_DIR OUT_DIR SOLUTION_NDJSON_ZST   (run from tools/structure/)
set -euo pipefail
DS=$1; O=$2; EXPORT=$3; mkdir -p $O
python -c "
import duckdb
c = duckdb.connect(); c.execute('set threads=4')
c.execute(\"copy (select name from read_parquet('$DS/constants.parquet') where origin = 'project') to '$O/project-names.txt' (header false, quote '', escape '')\")
"
wc -l $O/project-names.txt
R=riemannHypothesis,riemannZeta_eq_zero_re,LFunction_ne_zero_of_half_lt_re,LFunction_eq_zero_re,LFunction_eq_zero_re_of_isPrimitive
zstd -dc --long=30 "$EXPORT" | DEADLET=1 WATCH_BINDERS=${WATCH_BINDERS:-O} ${RHS_BIN:-rs/target/release}/relevance $O $R $O/project-names.txt
