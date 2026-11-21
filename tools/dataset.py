#!/usr/bin/env python3
"""Build dataset v0.1 (constants, edges, decls, directories) as Parquet + compressed CSV from route A outputs.
Args: WORK (has full/{constants,edges}.tsv and fold-v0/*) OUT. Needs duckdb."""
import os, sys
import duckdb

WORK, OUT = sys.argv[1:3]
os.makedirs(OUT, exist_ok=True)
con = duckdb.connect()
con.execute("SET threads TO 8; SET memory_limit = '200GB'; SET preserve_insertion_order = false")
TSV = "delim='\\t', header=true, quote='', escape='', all_varchar=true"

con.execute(f"""CREATE TABLE cm AS SELECT * FROM read_csv('{WORK}/fold-v0/constants-mapped.tsv.gz', {TSV})""")
con.execute("""
CREATE TABLE constants AS SELECT
  CAST(id AS INTEGER) AS id, name, kind, origin, module,
  CAST(nlevels AS INTEGER) AS nlevels,
  CAST(type_dag AS BIGINT) AS type_dag, CAST(NULLIF(value_dag, '') AS BIGINT) AS value_dag,
  CAST(type_tree AS UBIGINT) AS type_tree, CAST(NULLIF(value_tree, '') AS UBIGINT) AS value_tree,
  CAST(refs_type AS INTEGER) AS refs_type, CAST(refs_value AS INTEGER) AS refs_value,
  CAST(depth AS INTEGER) >= 0 AS in_cone, TRUE AS in_export,
  CAST(depth AS INTEGER) AS depth, CAST(thm_mask AS TINYINT) AS thm_mask,
  CAST(block AS INTEGER) AS block, CAST(record AS INTEGER) AS record,
  NULLIF(decl, '') AS decl, NULLIF(aux_rule, '') AS aux_rule, NULLIF(map_note, '') AS map_note, shared_aux = '1' AS shared_aux,
  NULLIF(file, '') AS file, CAST(NULLIF(start_line, '') AS INTEGER) AS start_line, CAST(NULLIF(end_line, '') AS INTEGER) AS end_line,
  'routeA-text' AS source, info
FROM cm ORDER BY id""")
con.execute("DROP TABLE cm")

con.execute(f"""CREATE TABLE e AS SELECT CAST(src AS INTEGER) AS src_id, CAST(dst AS INTEGER) AS dst_id, via
               FROM read_csv('{WORK}/full/edges.tsv', {TSV})""")
con.execute("""CREATE TABLE edges AS SELECT a.name AS src, b.name AS dst, e.via, e.src_id, e.dst_id
               FROM e JOIN constants a ON a.id = e.src_id JOIN constants b ON b.id = e.dst_id""")
con.execute("DROP TABLE e")

con.execute(f"""CREATE TABLE d AS SELECT * FROM read_csv('{WORK}/fold-v0/decls.tsv.gz', {TSV})""")
RHT = os.path.exists(f'{WORK}/fold-v0/rhtrim-status-decls.tsv.gz') and os.environ.get('NO_RHTRIM') != '1'
if RHT:
    con.execute(f"""CREATE TABLE rs AS SELECT * FROM read_csv('{WORK}/fold-v0/rhtrim-status-decls.tsv.gz', {TSV})""")
con.execute("""
CREATE TABLE decls AS SELECT
  d.decl, d.kind, 'project' AS origin, d.module, d.file,
  CAST(d.start_line AS INTEGER) AS start_line, CAST(d.end_line AS INTEGER) AS end_line, CAST(d.lines AS INTEGER) AS lines,
  CAST(d.n_constants AS INTEGER) AS n_constants, CAST(d.n_constants_in_cone AS INTEGER) AS n_constants_in_cone,
  c.type_dag, c.value_dag,
  d.in_cone = '1' AS in_cone, CAST(d.depth AS INTEGER) AS depth, CAST(d.n_shared_aux_in_cone AS INTEGER) AS n_shared_aux_in_cone,
  """ + ("COALESCE(rs.rhtrim_status, 'n/a') AS rhtrim_status, " if RHT else "") + """'routeA-text' AS source
FROM d
LEFT JOIN (SELECT name, any_value(type_dag) AS type_dag, any_value(value_dag) AS value_dag FROM constants GROUP BY name) c ON c.name = d.decl
""" + ("LEFT JOIN rs ON rs.decl = d.decl AND rs.file = d.file" if RHT else ""))
con.execute(f"""CREATE TABLE directories AS SELECT dir, CAST(level AS INTEGER) AS level,
  CAST(files AS INTEGER) AS files, CAST(files_in_import_closure AS INTEGER) AS files_in_import_closure,
  CAST(files_in_cone AS INTEGER) AS files_in_cone, CAST(lines AS INTEGER) AS lines, CAST(lines_in_cone AS INTEGER) AS lines_in_cone,
  CAST(decls AS INTEGER) AS decls, CAST(decls_in_cone AS INTEGER) AS decls_in_cone,
  CAST(pct_decls_used AS DOUBLE) AS pct_decls_used, CAST(pct_lines_used AS DOUBLE) AS pct_lines_used
  FROM read_csv('{WORK}/fold-v0/directories.tsv', {TSV})""")

# byte-deterministic files: sort on a unique key (in parallel), then write with one thread in insertion order
KEYS = {'constants': 'id', 'edges': 'src_id, dst_id, via', 'decls': 'file, start_line, decl, kind', 'directories': 'level, dir'}
for t, k in KEYS.items():
    con.execute(f"CREATE TABLE {t}_sorted AS SELECT * FROM {t} ORDER BY {k}")
con.execute("SET threads TO 1; SET preserve_insertion_order = true")
for t in KEYS:
    n = con.execute(f'SELECT count(*) FROM {t}_sorted').fetchone()[0]
    con.execute(f"COPY {t}_sorted TO '{OUT}/{t}.parquet' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 1000000)")
    con.execute(f"COPY {t}_sorted TO '{OUT}/{t}.csv.gz' (FORMAT csv, HEADER, COMPRESSION gzip)")
    print(t, n, flush=True)
con.execute("SET threads TO 8")
chk = con.execute("""SELECT
  (SELECT count(*) FROM constants WHERE in_cone),
  (SELECT count(*) FROM constants WHERE in_cone AND origin = 'project'),
  (SELECT count(*) FROM decls WHERE in_cone),
  (SELECT count(*) FROM edges),
  """ + ("(SELECT count(*) FROM decls WHERE in_cone AND rhtrim_status IN ('cut', 'dropped_file'))" if RHT else "NULL") + """""").fetchall()
print('checks (in_cone constants, project in_cone, in_cone decls, edges, in_cone decls cut by withdrawn rh-trim):', chk)
