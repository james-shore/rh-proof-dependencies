#!/usr/bin/env python3
"""Map the literature bundle: Published statement <- *_of_literature theorem -> the theorem(s) its value uses.
Usage: literature_map.py DATASET_DIR PUBLISHED_NAMES OUT"""
import os, sys
import duckdb
D, PUB, OUT = sys.argv[1:4]
os.makedirs(OUT, exist_ok=True)
con = duckdb.connect()
con.execute("SET threads TO 8; SET memory_limit = '100GB'")
con.execute(f"CREATE TABLE c AS SELECT id, name, kind, origin, decl, file, start_line, end_line, in_cone, shared_aux, type_dag FROM read_parquet('{D}/constants.parquet')")
LIT = 'src/Development/AutomorphicArithmetic/ThetaCorrespondence/ThetaPairLifts/ThirdPlaceSeries/EveryCharacterOfLiteratureOfVWin.lean'
con.execute(f"CREATE TABLE lit AS SELECT * FROM c WHERE file = '{LIT}' AND NOT shared_aux")
print('constants folded into the literature file:', con.execute("SELECT count(*), count(*) FILTER (WHERE in_cone) FROM lit").fetchall())
con.execute(f"CREATE TABLE e AS SELECT src_id, dst_id, via FROM read_parquet('{D}/edges.parquet') WHERE src_id IN (SELECT id FROM lit)")
pubs = [l.strip() for l in open(PUB) if l.strip()]
con.execute("CREATE TABLE pub (short VARCHAR)")
con.executemany("INSERT INTO pub VALUES (?)", [(p,) for p in pubs])
con.execute("""CREATE TABLE pubc AS SELECT pub.short, c.id, c.name, c.kind, c.in_cone, c.file, c.start_line
  FROM pub LEFT JOIN c ON c.name = 'GlobalTheory.Published.' || pub.short""")
print('published statements found / in cone:', con.execute("SELECT count(id), count(*) FILTER (WHERE in_cone) FROM pubc").fetchall())
# each literature theorem: which Published statement its type concludes, and what its value uses
con.execute("""CREATE TABLE lmap AS
  SELECT l.name AS lit_thm, l.in_cone AS lit_in_cone, l.start_line AS lit_line,
         string_agg(DISTINCT CASE WHEN e.via = 'type' AND t.name LIKE 'GlobalTheory.Published.%' THEN t.name END, ',' ORDER BY t.name) AS published,
         string_agg(DISTINCT CASE WHEN e.via = 'value' AND t.origin = 'project' AND t.name NOT LIKE 'GlobalTheory.Published.%'
                                       AND t.file <> l.file AND NOT t.shared_aux THEN t.name END, ',' ORDER BY t.name) AS proof_refs,
         string_agg(DISTINCT CASE WHEN e.via = 'value' AND t.file = l.file AND t.name <> l.name AND NOT t.shared_aux THEN t.name END, ',' ORDER BY t.name) AS same_file_refs
  FROM lit l LEFT JOIN e ON e.src_id = l.id LEFT JOIN c t ON t.id = e.dst_id
  WHERE l.kind = 'theorem' GROUP BY 1, 2, 3""")
con.execute(f"COPY (SELECT * FROM lmap ORDER BY lit_line) TO '{OUT}/literature-theorems.tsv' (FORMAT csv, DELIMITER '\t', HEADER)")
con.execute(f"COPY (SELECT * FROM pubc ORDER BY short) TO '{OUT}/published-statements.tsv' (FORMAT csv, DELIMITER '\t', HEADER)")
print(con.execute("SELECT count(*), count(*) FILTER (WHERE lit_in_cone), count(published), count(proof_refs) FROM lmap").fetchall())
print(con.execute("SELECT lit_thm, lit_in_cone, published, proof_refs, same_file_refs FROM lmap ORDER BY lit_line LIMIT 12").fetchall())
# direct proofs: in-cone theorems whose type is exactly the statement constant (type DAG = 1 node)
con.execute("""CREATE TABLE direct AS
  SELECT p.short, s.name AS proof, s.file, s.start_line, s.end_line, s.decl
  FROM pubc p JOIN read_parquet('""" + D + """/edges.parquet') e ON e.dst_id = p.id AND e.via = 'type'
  JOIN c s ON s.id = e.src_id
  WHERE s.kind = 'theorem' AND s.type_dag = 1 AND s.in_cone AND NOT s.shared_aux""")
con.execute(f"COPY (SELECT * FROM direct ORDER BY short, proof) TO '{OUT}/published-direct-proofs.tsv' (FORMAT csv, DELIMITER '\t', HEADER)")
print('statements with a direct proof:', con.execute("SELECT count(DISTINCT short), count(*) FROM direct").fetchall())
print(con.execute("SELECT short, count(*) n FROM direct GROUP BY 1 ORDER BY n DESC LIMIT 5").fetchall())
