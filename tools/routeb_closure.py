#!/usr/bin/env python3
"""Closure over a route B dump with lean4export's semantics, and comparison with route A.
Usage: routeb_closure.py ROUTEB_PREFIX ROUTEA_DATASET_DIR OUT ROOT[,ROOT...]
  ROUTEB_PREFIX: <prefix>.constants.tsv / .edges.tsv / .modules.tsv from routeb
  ROUTEA_DATASET_DIR: dataset v0.1 dir (constants.parquet, edges.parquet)
Semantics (lean4export v4.33.0): .const refs in type and value (recursor value = rules' rhs); nat literal -> Nat;
string literal -> Char.ofNat, String.ofList; quot -> Eq; an inductive block / the quot package comes whole;
unsafe constants are skipped (with their references)."""
import os, sys
import duckdb

RB, RA, OUT, ROOTS = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4].split(',')
os.makedirs(OUT, exist_ok=True)
con = duckdb.connect()
con.execute("SET threads TO 8; SET memory_limit = '200GB'; SET preserve_insertion_order = false")
TSV = "delim='\\t', header=true, quote='', escape='', all_varchar=true"
con.execute(f"""CREATE TABLE bc AS SELECT CAST(id AS INTEGER) id, name, CAST(NULLIF(module_idx,'') AS INTEGER) module_idx, kind,
  unsafe = 'true' AS unsafe, block, lit_nat = 'true' AS lit_nat, lit_str = 'true' AS lit_str FROM read_csv('{RB}.constants.tsv', {TSV})""")
con.execute(f"""CREATE TABLE be AS SELECT CAST(src_id AS INTEGER) s, CAST(dst_id AS BIGINT) d FROM read_csv('{RB}.edges.tsv', {TSV})""")
bad = con.execute("SELECT count(*) FROM be WHERE d = 4294967295").fetchone()[0]
print('route B dangling refs:', bad)
# exporter edges
con.execute("""CREATE TABLE g AS
  SELECT s, CAST(d AS INTEGER) d FROM be WHERE d <> 4294967295
  UNION ALL SELECT bc.id, n.id FROM bc JOIN bc n ON n.name = 'Nat' WHERE bc.lit_nat
  UNION ALL SELECT bc.id, n.id FROM bc JOIN bc n ON n.name IN ('Char.ofNat', 'String.ofList') WHERE bc.lit_str
  UNION ALL SELECT bc.id, n.id FROM bc JOIN bc n ON n.name = 'Eq' WHERE bc.kind = 'quot'
  UNION ALL SELECT a.id, b.id FROM bc a JOIN bc b ON a.block = b.block AND a.id <> b.id""")
con.execute("CREATE TABLE g2 AS SELECT g.s, g.d FROM g JOIN bc t ON t.id = g.d WHERE NOT t.unsafe")
roots = ', '.join(f"'{r}'" for r in ROOTS)
con.execute(f"CREATE TABLE vis AS SELECT id FROM bc WHERE name IN ({roots}) AND NOT unsafe")
con.execute("CREATE TABLE fr AS SELECT id FROM vis")
while True:
    con.execute("CREATE OR REPLACE TABLE nw AS SELECT DISTINCT g2.d AS id FROM g2 JOIN fr ON g2.s = fr.id ANTI JOIN vis ON vis.id = g2.d")
    k = con.execute("SELECT count(*) FROM nw").fetchone()[0]
    if k == 0:
        break
    con.execute("INSERT INTO vis SELECT id FROM nw")
    con.execute("CREATE OR REPLACE TABLE fr AS SELECT id FROM nw")
con.execute("CREATE TABLE bset AS SELECT bc.name FROM vis JOIN bc USING (id)")
nb = con.execute("SELECT count(*) FROM bset").fetchone()[0]
# route A closure from the same roots over export edges + blocks
con.execute(f"CREATE TABLE ac AS SELECT id, name, block FROM read_parquet('{RA}/constants.parquet')")
con.execute(f"""CREATE TABLE ag AS SELECT src_id s, dst_id d FROM read_parquet('{RA}/edges.parquet')
  UNION ALL SELECT a.id, b.id FROM ac a JOIN ac b ON a.block = b.block AND a.id <> b.id""")
con.execute(f"CREATE TABLE avis AS SELECT id FROM ac WHERE name IN ({roots})")
con.execute("CREATE TABLE afr AS SELECT id FROM avis")
while True:
    con.execute("CREATE OR REPLACE TABLE anw AS SELECT DISTINCT ag.d AS id FROM ag JOIN afr ON ag.s = afr.id ANTI JOIN avis ON avis.id = ag.d")
    k = con.execute("SELECT count(*) FROM anw").fetchone()[0]
    if k == 0:
        break
    con.execute("INSERT INTO avis SELECT id FROM anw")
    con.execute("CREATE OR REPLACE TABLE afr AS SELECT id FROM anw")
con.execute("CREATE TABLE aset AS SELECT ac.name FROM avis JOIN ac USING (id)")
na = con.execute("SELECT count(*) FROM aset").fetchone()[0]
onlyb = con.execute("SELECT name FROM bset EXCEPT SELECT name FROM aset").fetchall()
onlya = con.execute("SELECT name FROM aset EXCEPT SELECT name FROM bset").fetchall()
print(f'closure of {ROOTS}: route B {nb}, route A {na}; only in B {len(onlyb)}, only in A {len(onlya)}')
for x in onlyb[:20]:
    print('  only B:', x[0])
for x in onlya[:20]:
    print('  only A:', x[0])
con.execute(f"COPY bset TO '{OUT}/routeb-closure.tsv' (FORMAT csv, DELIMITER '\t', HEADER)")
with open(os.path.join(OUT, 'reconcile.txt'), 'w') as f:
    f.write(f'roots {ROOTS}\nroute B closure {nb}\nroute A closure {na}\nonly B {len(onlyb)}\nonly A {len(onlya)}\n')
    for x in onlyb:
        f.write(f'onlyB\t{x[0]}\n')
    for x in onlya:
        f.write(f'onlyA\t{x[0]}\n')
