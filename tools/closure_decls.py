#!/usr/bin/env python3
"""Kernel closure of given root constants over dataset edges + block membership; writes the project declarations in it.
Usage: closure_decls.py DATASET_DIR ROOT[,ROOT...] OUT.tsv"""
import sys
import duckdb
D, ROOTS, OUT = sys.argv[1], sys.argv[2].split(','), sys.argv[3]
con = duckdb.connect()
con.execute("SET threads TO 8; SET memory_limit = '100GB'")
con.execute(f"CREATE TABLE c AS SELECT id, name, block, origin, decl, file, shared_aux FROM read_parquet('{D}/constants.parquet')")
con.execute(f"""CREATE TABLE g AS SELECT src_id s, dst_id d FROM read_parquet('{D}/edges.parquet')
  UNION ALL SELECT a.id, b.id FROM c a JOIN c b ON a.block = b.block AND a.id <> b.id""")
roots = ', '.join(f"'{r}'" for r in ROOTS)
con.execute(f"CREATE TABLE vis AS SELECT id FROM c WHERE name IN ({roots})")
con.execute("CREATE TABLE fr AS SELECT id FROM vis")
while True:
    con.execute("CREATE OR REPLACE TABLE nw AS SELECT DISTINCT g.d AS id FROM g JOIN fr ON g.s = fr.id ANTI JOIN vis ON vis.id = g.d")
    if con.execute("SELECT count(*) FROM nw").fetchone()[0] == 0:
        break
    con.execute("INSERT INTO vis SELECT id FROM nw")
    con.execute("CREATE OR REPLACE TABLE fr AS SELECT id FROM nw")
print('constants in closure', con.execute("SELECT count(*) FROM vis").fetchone()[0])
con.execute(f"""COPY (SELECT DISTINCT decl, file FROM vis JOIN c USING (id) WHERE origin = 'project' AND decl IS NOT NULL AND file IS NOT NULL AND NOT shared_aux)
  TO '{OUT}' (FORMAT csv, DELIMITER '\t', HEADER)""")
print('project decls', con.execute(f"SELECT count(*) FROM read_csv('{OUT}', delim='\\t', header=true)").fetchone()[0])
