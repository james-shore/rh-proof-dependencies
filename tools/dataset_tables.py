#!/usr/bin/env python3
"""Small tables in data/ from a dataset build (v0.2 and later). Usage:
  dataset_tables.py DATASET_DIR ASKED_DIRS_TSV OUT_DIR SUFFIX
Writes OUT_DIR/directories-SUFFIX.tsv (the directories table), dirs-level3-asked-SUFFIX.tsv (the directories listed in
ASKED_DIRS_TSV, first column), aux-only-decls-SUFFIX.tsv (not in the cone, but
shared aux lemmas they generated are), and prints the line
split (unit: source lines of src/, Challenge.lean excluded). Byte-deterministic (sorted, one thread)."""
import json, os, sys
import duckdb

D, ASK, OUT, SUF = sys.argv[1:5]
con = duckdb.connect()
con.execute("SET threads TO 1; SET preserve_insertion_order = true")
con.execute(f"CREATE VIEW d AS SELECT * FROM read_parquet('{D}/decls.parquet')")
def tsv(sql, path):
    con.execute(f"COPY ({sql}) TO '{path}' (FORMAT csv, DELIMITER '\t', HEADER, QUOTE '', ESCAPE '')")
tsv(f"SELECT * FROM read_parquet('{D}/directories.parquet') ORDER BY level, dir", f'{OUT}/directories-{SUF}.tsv')
asked = [l.split('\t')[0] for l in open(ASK, encoding='utf-8').read().split('\n')[1:] if l]
con.execute("CREATE TABLE asked (ord INTEGER, dir VARCHAR)")
con.executemany("INSERT INTO asked VALUES (?, ?)", list(enumerate(asked)))
tsv("""SELECT a.dir, count(d.decl) decls, count(d.decl) FILTER (WHERE d.in_cone) decls_in_cone,
  round(100.0 * count(d.decl) FILTER (WHERE d.in_cone) / count(d.decl), 1) pct_decls,
  sum(d.lines) decl_lines, coalesce(sum(d.lines) FILTER (WHERE d.in_cone), 0) decl_lines_in_cone
  FROM asked a LEFT JOIN d ON starts_with(d.file, a.dir || '/') AND NOT d.generated GROUP BY a.ord, a.dir ORDER BY a.ord""",
    f'{OUT}/dirs-level3-asked-{SUF}.tsv')
tsv("""SELECT decl, kind, file, start_line, end_line, n_shared_aux_in_cone FROM d
  WHERE NOT in_cone AND NOT generated AND n_shared_aux_in_cone > 0 ORDER BY decl""",
    f'{OUT}/aux-only-decls-{SUF}.tsv')
s = json.load(open(f'{D}/dataset-v2-stats.json'))
NOCONST, OUTSIDE = 19710, 3922942  # text-index categories, independent of attribution (v0.2 line split)
split = {'total': s['lines_total'], 'used': s['lines_in_cone_command_spans'],
         'unused': s['lines_in_any_decl_command_span'] - s['lines_in_cone_command_spans'],
         'duplicate_copies': s['lines_total'] - s['lines_in_any_decl_command_span'] - NOCONST - OUTSIDE,
         'no_constant_commands': NOCONST, 'outside': OUTSIDE}
print(json.dumps({'line_split': split, 'aux_only': con.execute(f"SELECT count(*) FROM read_csv('{OUT}/aux-only-decls-{SUF}.tsv', delim='\t', header=true)").fetchone()[0]}))
