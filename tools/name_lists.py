#!/usr/bin/env python3
"""Plain name lists for grep: cone-constants.txt.gz (the kernel constants in the five theorems' closure) and
cone-decls.txt.gz (the names of the used project declarations), one name per line, sorted by code point.
Usage: name_lists.py DATASET_DIR [OUT_DIR]"""
import gzip, os, sys
import duckdb

D = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else D
con = duckdb.connect()
for out, q in [('cone-constants', f"SELECT name FROM read_parquet('{D}/constants.parquet') WHERE in_cone"),
               ('cone-decls', f"SELECT DISTINCT decl FROM read_parquet('{D}/decls.parquet') WHERE in_cone")]:
    names = sorted(r[0] for r in con.execute(q).fetchall())
    with open(os.path.join(OUT, out + '.txt.gz'), 'wb') as f, \
            gzip.GzipFile(filename='', mode='wb', fileobj=f, compresslevel=9, mtime=0) as g:
        g.write(''.join(n + '\n' for n in names).encode('utf-8'))
    print(out, len(names), flush=True)
