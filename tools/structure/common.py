"""Shared helpers: dataset loading (Parquet, TSV or TSV.gz, v0.1 schema), directory keys, small writers."""
import gzip, json, os, subprocess, sys, time

import duckdb
import numpy as np

T0 = time.time()
THEOREMS = ['riemannHypothesis', 'riemannZeta_eq_zero_re', 'LFunction_ne_zero_of_half_lt_re',
            'LFunction_eq_zero_re', 'LFunction_eq_zero_re_of_isPrimitive']
HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.environ.get('RHS_BIN', os.path.join(HERE, 'rs', 'target', 'release'))


def log(*a):
    print(f'[{time.time() - T0:7.1f}s]', *a, file=sys.stderr, flush=True)


def find_table(ds, name):
    """Path of table `name` in dataset dir `ds`: prefer Parquet, then .tsv.gz/.csv.gz, then .tsv."""
    for ext in ('.parquet', '.tsv.gz', '.csv.gz', '.tsv', '.csv'):
        p = os.path.join(ds, name + ext)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f'{name}.* not in {ds}')


def rel(path):
    """duckdb relation expression for a table file."""
    if path.endswith('.parquet'):
        return f"read_parquet('{path}')"
    delim = ',' if '.csv' in path else '\\t'
    return f"read_csv('{path}', delim='{delim}', header=true, quote='', escape='', sample_size=-1)"


def connect(threads=None):
    con = duckdb.connect()
    con.execute(f"set threads={threads or int(os.environ.get('RHS_THREADS', '16'))}")
    con.execute("set preserve_insertion_order=false")
    con.execute(f"set memory_limit='{os.environ.get('RHS_DUCKDB_MEM', '48GB')}'")
    return con


def cols(con, path):
    return [r[0] for r in con.execute(f"describe select * from {rel(path)}").fetchall()]


def not_generated(con, decls_path, alias=''):
    """SQL condition that drops declarations made by notation/macro/elab commands (decls.generated, from v0.2 on)."""
    pre = f'{alias}.' if alias else ''
    return f' and not coalesce(cast({pre}generated as boolean), false)' if 'generated' in cols(con, decls_path) else ''


def dir_keys(files):
    """level-1 and level-2 key of each `src/...` file path. A file directly in src/ is its own level-1 key, and a file
    directly in src/<lib>/ is its own level-2 key, so the two levels never share a key."""
    d1, d2 = [], []
    for f in files:
        p = f.split('/')
        d1.append('/'.join(p[:2]) if len(p) >= 2 else f)
        d2.append('/'.join(p[:3]) if len(p) >= 3 else f)
    return d1, d2


def run_bin(name, *args):
    cmd = [os.path.join(BIN, name), *map(str, args)]
    log('run', ' '.join(cmd))
    subprocess.run(cmd, check=True)


def write_tsv(path, header, rows):
    """TSV (or deterministic .gz: no file name or timestamp in the header, so equal content gives equal bytes)."""
    import io
    if path.endswith('.gz'):
        raw = open(path, 'wb')
        f = io.TextIOWrapper(gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0), encoding='utf-8')
    else:
        raw, f = None, open(path, 'w', encoding='utf-8')
    with f:
        f.write('\t'.join(header) + '\n')
        for r in rows:
            f.write('\t'.join('' if x is None else str(x) for x in r) + '\n')
    if raw:
        raw.close()


def write_json(path, obj):
    with open(path, 'w') as f:
        json.dump(obj, f, indent=1, default=lambda x: x.item() if hasattr(x, 'item') else str(x))


def subtree_sums(parent, order, w):
    """Sum weights w over each dominator subtree. `order` lists nodes so that children come after parents."""
    s = w.astype(np.float64 if w.dtype.kind == 'f' else np.int64).copy()
    for v in order[::-1]:
        p = parent[v]
        if p >= 0 and p != v:
            s[p] += s[v]
    return s
