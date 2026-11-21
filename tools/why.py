#!/usr/bin/env python3
"""Shortest-path parent per declaration ("why is X needed"), BFS from the five Main theorems over a declaration-level graph.
Nodes: project constants collapse to their folded declaration (decl@file); a shared aux lemma (_proof_N, _simp_N, match_N,
...) is always its own node, since Lean reuses them across declarations; dependency constants stay as themselves.
Edges: constant edges (type/value/lit/quot) + inductive-block membership, collapsed to nodes.
Parent = the lowest-named node at depth - 1 with an edge to the node; witness = the lowest (src, dst) constant edge
between the two. Args: DATASET_DIR (constants.parquet, edges.parquet) OUT. Needs duckdb."""
import os, sys
import duckdb

D, OUT = sys.argv[1:3]
os.makedirs(OUT, exist_ok=True)
THMS = ['riemannHypothesis', 'riemannZeta_eq_zero_re', 'LFunction_ne_zero_of_half_lt_re', 'LFunction_eq_zero_re',
        'LFunction_eq_zero_re_of_isPrimitive']
con = duckdb.connect()
con.execute(f"SET threads TO {int(os.environ.get('THREADS', '8'))}; SET memory_limit = '200GB'; SET preserve_insertion_order = false")
con.execute(f"""CREATE TABLE n0 AS SELECT id, name, origin,
  CASE WHEN origin = 'project' AND decl IS NOT NULL AND file IS NOT NULL AND NOT shared_aux
       THEN decl || '@' || file ELSE origin || ':' || name END AS node
  FROM read_parquet('{D}/constants.parquet')""")
# integer node ids in node-name order, so min(nid) = lowest name
con.execute("CREATE TABLE nodes AS SELECT row_number() OVER (ORDER BY node) - 1 AS nid, node FROM (SELECT DISTINCT node FROM n0)")
con.execute("CREATE TABLE n AS SELECT n0.id, n0.name, nodes.nid FROM n0 JOIN nodes USING (node)")
con.execute(f"""CREATE TABLE ce AS SELECT src_id, dst_id, via FROM read_parquet('{D}/edges.parquet')
  UNION ALL SELECT a.id, b.id, 'block' FROM (SELECT id, block FROM read_parquet('{D}/constants.parquet')) a
    JOIN (SELECT id, block FROM read_parquet('{D}/constants.parquet')) b ON a.block = b.block AND a.id <> b.id""")
# join first, filter after: with the <> in the same statement DuckDB can plan an inequality join between the lookups
con.execute("CREATE TABLE j AS SELECT s.nid AS src, t.nid AS dst FROM ce JOIN n s ON s.id = ce.src_id JOIN n t ON t.id = ce.dst_id")
con.execute("CREATE TABLE de AS SELECT DISTINCT src, dst FROM j WHERE src <> dst")
con.execute("DROP TABLE j")
print('decl-level edges', con.execute('SELECT count(*) FROM de').fetchone()[0], flush=True)
roots = ', '.join(f"'{t}'" for t in THMS)
con.execute(f"CREATE TABLE vis AS SELECT DISTINCT nid, NULL::BIGINT AS parent, 0 AS d FROM n WHERE name IN ({roots})")
con.execute("CREATE TABLE fr AS SELECT nid FROM vis")
d = 0
while True:
    d += 1
    con.execute(f"""CREATE OR REPLACE TABLE nw AS SELECT de.dst AS nid, min(de.src) AS parent, {d} AS d
      FROM de JOIN fr ON de.src = fr.nid ANTI JOIN vis ON vis.nid = de.dst GROUP BY de.dst""")
    k = con.execute('SELECT count(*) FROM nw').fetchone()[0]
    if k == 0:
        break
    con.execute('INSERT INTO vis SELECT * FROM nw')
    con.execute('CREATE OR REPLACE TABLE fr AS SELECT nid FROM nw')
    print('depth', d, 'new nodes', k, flush=True)
print('reached', con.execute('SELECT count(*) FROM vis').fetchone()[0], 'of', con.execute('SELECT count(*) FROM nodes').fetchone()[0], 'nodes', flush=True)
# witness: the lowest (src_id, dst_id) constant edge from a constant of the parent to a constant of the node
con.execute("""CREATE TABLE wit AS SELECT v.nid, min(ce.src_id * 4294967296 + ce.dst_id) AS key
  FROM vis v JOIN n p ON p.nid = v.parent JOIN ce ON ce.src_id = p.id JOIN n c ON c.id = ce.dst_id AND c.nid = v.nid
  WHERE v.parent IS NOT NULL GROUP BY v.nid""")
con.execute("""CREATE TABLE why AS SELECT nn.node, split_part(nn.node, '@', 1) AS decl_or_const,
   CASE WHEN nn.node LIKE '%@src/%' OR nn.node LIKE 'project:%' THEN 'project' ELSE split_part(nn.node, ':', 1) END AS origin,
   v.d AS decl_depth, pn.node AS parent,
   CASE WHEN w.key IS NULL THEN NULL ELSE a.name || ' -> ' || b.name || ' [' ||
     (SELECT min(via) FROM ce WHERE ce.src_id = a.id AND ce.dst_id = b.id) || ']' END AS witness
  FROM vis v JOIN nodes nn ON nn.nid = v.nid LEFT JOIN nodes pn ON pn.nid = v.parent
  LEFT JOIN wit w ON w.nid = v.nid LEFT JOIN n0 a ON a.id = w.key // 4294967296 LEFT JOIN n0 b ON b.id = w.key % 4294967296""")
con.execute("CREATE TABLE why_sorted AS SELECT * FROM why ORDER BY node")
con.execute("SET threads TO 1; SET preserve_insertion_order = true")
con.execute(f"COPY why_sorted TO '{OUT}/why-parents.parquet' (FORMAT parquet, COMPRESSION zstd)")
con.execute(f"COPY why_sorted TO '{OUT}/why-parents.csv.gz' (FORMAT csv, HEADER, COMPRESSION gzip)")
con.execute(f"COPY (SELECT * FROM why_sorted WHERE node LIKE '%@src/%') TO '{OUT}/why-parents-project.tsv.gz' (FORMAT csv, DELIMITER '\t', HEADER, COMPRESSION gzip)")
print(con.execute("SELECT origin, count(*), max(decl_depth) FROM why GROUP BY 1 ORDER BY 2 DESC").fetchall())
