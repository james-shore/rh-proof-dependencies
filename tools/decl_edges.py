#!/usr/bin/env python3
"""Project-only declaration graph over the whole environment (not only the cone), from the compiled environment.
Usage: decl_edges.py ROUTEB_DIR OUT
  ROUTEB_DIR: main.constants.tsv, main.edges.tsv (routeb), map/routeb-constants-mapped.tsv (routeb_map.py)
Nodes: a project declaration (its own and folded constants), or, for a shared aux lemma (_proof_N, _simp_N, match_N, ...)
or a project constant without a declaration, the constant itself (node_kind 'constant'), so that no false edge runs
through the declaration that first generated a shared lemma. Edges: src -> dst when a constant of src mentions a constant
of dst in its type or value; self-loops dropped. Writes OUT/decl_edges.{parquet,csv.gz}, sorted, byte-deterministic."""
import os, sys
import duckdb

RB, OUT = sys.argv[1:3]
os.makedirs(OUT, exist_ok=True)
con = duckdb.connect()
con.execute(f"SET threads TO {int(os.environ.get('THREADS', '8'))}; SET memory_limit = '200GB'; SET preserve_insertion_order = false")
TSV = "delim='\\t', header=true, quote='', escape='', all_varchar=true"
con.execute(f"CREATE TABLE c AS SELECT CAST(id AS INTEGER) id, name FROM read_csv('{RB}/main.constants.tsv', {TSV})")
con.execute(f"""CREATE TABLE m AS SELECT name, NULLIF(decl, '') decl, shared_aux = '1' shared FROM read_csv('{RB}/map/routeb-constants-mapped.tsv', {TSV})""")
con.execute("""CREATE TABLE n AS SELECT c.id,
  CASE WHEN m.decl IS NOT NULL AND NOT m.shared THEN m.decl ELSE c.name END AS node,
  CASE WHEN m.decl IS NOT NULL AND NOT m.shared THEN 'decl' ELSE 'constant' END AS node_kind
  FROM c JOIN m USING (name)""")
con.execute(f"""CREATE TABLE e AS SELECT CAST(src_id AS INTEGER) s, CAST(dst_id AS INTEGER) d, via FROM read_csv('{RB}/main.edges.tsv', {TSV})""")
con.execute("CREATE TABLE j AS SELECT a.node src, a.node_kind src_kind, b.node dst, b.node_kind dst_kind, CASE e.via WHEN 't' THEN 'type' ELSE 'value' END via FROM e JOIN n a ON a.id = e.s JOIN n b ON b.id = e.d")
con.execute("CREATE TABLE de AS SELECT DISTINCT src, src_kind, dst, dst_kind, via FROM j WHERE src <> dst")
con.execute("DROP TABLE j")
con.execute("CREATE TABLE de_sorted AS SELECT * FROM de ORDER BY src, dst, via")
print('edges', con.execute("SELECT count(*) FROM de").fetchone()[0], 'node pairs', con.execute("SELECT count(*) FROM (SELECT DISTINCT src, dst FROM de)").fetchone()[0],
      'decl->decl', con.execute("SELECT count(*) FROM de WHERE src_kind = 'decl' AND dst_kind = 'decl'").fetchone()[0], flush=True)
con.execute("SET threads TO 1; SET preserve_insertion_order = true")
con.execute(f"COPY de_sorted TO '{OUT}/decl_edges.parquet' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 1000000)")
con.execute(f"COPY de_sorted TO '{OUT}/decl_edges.csv.gz' (FORMAT csv, HEADER, COMPRESSION gzip)")
