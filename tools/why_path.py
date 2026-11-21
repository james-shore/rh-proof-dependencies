#!/usr/bin/env python3
"""Print why a declaration is in the cone: the BFS chain from a Main theorem to it.
Usage: uv run --with duckdb tools/cone/why_path.py why-parents.parquet <decl name or substring> [...]"""
import sys
import duckdb
P = sys.argv[1]
con = duckdb.connect()
con.execute(f"CREATE TABLE w AS SELECT * FROM read_parquet('{P}')")
for q in sys.argv[2:]:
    # project declarations are 'decl@file'; other nodes (dependencies, shared aux lemmas) are 'origin:name'
    hits = con.execute("""SELECT node FROM w WHERE decl_or_const = ? OR node = ? OR decl_or_const LIKE '%.' || ?
                          OR node LIKE '%:' || ? OR node LIKE '%:%.' || ? LIMIT 5""", [q, q, q, q, q]).fetchall()
    if not hits:
        print(f'{q}: not in the cone'); continue
    for (node,) in hits:
        chain = []
        cur = node
        while cur is not None and len(chain) < 80:
            r = con.execute('SELECT node, parent, witness, decl_depth FROM w WHERE node = ?', [cur]).fetchone()
            chain.append(r); cur = r[1]
        print(f'== {node}  (decl depth {chain[0][3]})')
        for n, p, wit, d in reversed(chain):
            print(f'  [{d:>2}] {n}' + (f'\n        via {wit}' if wit else ''))
