#!/usr/bin/env python3
"""Order-independent content hash of dataset tables (Parquet writes are not byte-deterministic).
Usage: content_hash.py DATASET_DIR [table ...]  -> '<sha256>  <table> <rows>' per table (rows sorted, CSV-rendered)."""
import hashlib, sys
import duckdb
D = sys.argv[1]
tables = sys.argv[2:] or ['constants', 'edges', 'decls', 'directories']
con = duckdb.connect()
con.execute("SET threads TO 8; SET memory_limit = '100GB'")
for t in tables:
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{D}/{t}.parquet')").fetchall()]
    h = hashlib.sha256(); n = 0
    q = f"SELECT * FROM read_parquet('{D}/{t}.parquet') ORDER BY {', '.join(f'\"{c}\"' for c in cols)}"
    cur = con.execute(q)
    while True:
        rows = cur.fetchmany(100000)
        if not rows:
            break
        for r in rows:
            h.update(('\t'.join('' if v is None else str(v) for v in r) + '\n').encode())
        n += len(rows)
    print(f'{h.hexdigest()}  {t} {n}', flush=True)
