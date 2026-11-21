"""Ask path questions of the proof cone, at the level of source declarations.

    query.py --data DATASET_DIR [--cache FILE] deps NAME [--ext]     what NAME uses directly
    query.py ... users NAME                                         what uses NAME directly (inside the cone)
    query.py ... path A B                                           one shortest chain "A uses ... uses B"
    query.py ... paths A B [--max N]                                up to N distinct shortest chains
    query.py ... meet A B                                           nearest declarations that use both A and B
    query.py ... dominator NAME --dom dominators.tsv.gz             NAME's dominator chain to the root, subtree size
    query.py ... batch < queries.txt                                one query per line, cache loaded once

DATASET_DIR holds the dataset's `constants`, `edges` and `decls` tables (Parquet or TSV; route A or route B). The first run
folds the kernel edges to declarations with the same rule as dominators.py and caches the result (FILE, default
DATASET_DIR/../query-cache-<version>.npz, or $RHS_QUERY_CACHE); later runs load only the cache. No network.

NAME may be a full or short name (`omTied_retie_of_cube`, `AuxBlock8.Conclusion1`), a kernel constant folded into a
declaration (`foo._proof_1`), or `NAME@path-substring` to pick among same-named declarations. An ambiguous name lists
its matches and exits with status 2.
Edges are marked by where the use occurs: `stmt` (in the statement), `proof` (in the proof or definition body),
`both`.
"""
import argparse, os, sys, tempfile
from collections import deque
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
VIA = {1: 'stmt', 2: 'proof', 3: 'both'}


# ---------------------------------------------------------------------------- cache
def pack(strs):
    b = [x.encode('utf-8') for x in strs]
    off = np.zeros(len(b) + 1, dtype=np.int64); off[1:] = np.cumsum([len(x) for x in b])
    return np.frombuffer(b''.join(b), dtype=np.uint8), off


def unpack(blob, off):
    raw = blob.tobytes()
    return [raw[off[i]:off[i + 1]].decode('utf-8') for i in range(len(off) - 1)]


def build_cache(ds, path):
    from common import connect, find_table, rel, log
    import dominators as D
    con = connect()
    C, pe = D.load(con, ds)
    did = C['did']
    n = con.execute("select max(did) from nodes").fetchone()[0] + 1
    q = con.execute("""select did, decl, kind, file, start_line, end_line, lines from nodes order by did""").fetchnumpy()
    names = [''] * n; kinds = [''] * n; files = [''] * n
    start = np.zeros(n, dtype=np.int32); end = np.zeros(n, dtype=np.int32); lines = np.zeros(n, dtype=np.int32)
    for i, v in enumerate(q['did']):
        names[v] = str(q['decl'][i]); kinds[v] = str(q['kind'][i]); files[v] = str(q['file'][i])
        start[v] = q['start_line'][i]; end[v] = q['end_line'][i]; lines[v] = q['lines'][i]
    names[0] = '(root: the five theorems)'
    # every kernel constant name -> node (for names of folded helpers) and the names of non-project constants
    cq = con.execute(f"select id, name, origin from {rel(find_table(ds, 'constants'))}").fetchnumpy()
    cname = np.empty(int(cq['id'].max()) + 1, dtype=object); cname[cq['id']] = cq['name']
    # fold edges to nodes, keeping where the use occurs (bit 1 = statement, 2 = proof/body)
    import pyarrow.parquet as pq
    keys, ext = [], []
    def batches():
        if pe.endswith('.parquet'):
            f = pq.ParquetFile(pe)
            nm = f.schema_arrow.names
            sc, tc = ('src_id', 'dst_id') if 'src_id' in nm else ('src', 'dst')
            for b in f.iter_batches(columns=[sc, tc, 'via'], batch_size=8_000_000):
                via = b.column(2).to_numpy(zero_copy_only=False)
                yield b.column(0).to_numpy().astype(np.int64), b.column(1).to_numpy().astype(np.int64), via
        else:
            r = con.execute(f"select src_id, dst_id, via from {rel(pe)}").fetchnumpy()
            yield r['src_id'].astype(np.int64), r['dst_id'].astype(np.int64), r['via']
    for s, t, via in batches():
        bit = np.where(via == 'type', 1, 2).astype(np.int64)
        ds_, dt = did[s], did[t]
        m = (ds_ >= 0) & (dt >= 0) & (ds_ != dt)
        keys.append(np.unique((ds_[m] * n + dt[m]) * 4 + bit[m]))
        x = (ds_ >= 0) & (dt < 0)
        ext.append(np.unique(ds_[x] * len(did) + t[x]))
    k = np.unique(np.concatenate(keys))
    pair, bit = k // 4, k % 4
    up, inv = np.unique(pair, return_inverse=True)
    vb = np.zeros(len(up), dtype=np.int8); np.bitwise_or.at(vb, inv, bit.astype(np.int8))
    src, dst = up // n, up % n
    ext = np.unique(np.concatenate(ext)); es, ec = ext // len(did), ext % len(did)
    uc, ei = np.unique(ec, return_inverse=True)
    roots = [r[0] for r in con.execute("""select n.did from c join cmap m on c.id = m.id join nodes n on n.did = m.did
        where c.name in ('riemannHypothesis', 'riemannZeta_eq_zero_re', 'LFunction_ne_zero_of_half_lt_re',
        'LFunction_eq_zero_re', 'LFunction_eq_zero_re_of_isPrimitive')""").fetchall()]
    alias_ids = np.nonzero(did > 0)[0]
    nb, no = pack(names); kb, ko = pack(kinds); fb, fo = pack(files); ab, ao = pack([str(cname[c]) for c in alias_ids])
    np.savez_compressed(path, n=n, names_b=nb, names_o=no, kinds_b=kb, kinds_o=ko, files_b=fb, files_o=fo,
                        start=start, end=end, lines=lines, src=src.astype(np.int32), dst=dst.astype(np.int32), via=vb,
                        alias_b=ab, alias_o=ao, alias_node=did[alias_ids].astype(np.int32),
                        roots=np.array(roots, dtype=np.int32), dataset=np.frombuffer(os.path.basename(os.path.normpath(ds)).encode(), dtype=np.uint8))
    xb, xo = pack([str(cname[c]) for c in uc])
    np.savez_compressed(path.replace('.npz', '') + '.ext.npz', ext_src=es.astype(np.int32), ext_idx=ei.astype(np.int32),
                        ext_b=xb, ext_o=xo)
    log(f'cache: {n - 1} nodes, {len(src)} edges, {len(es)} external uses -> {path}')


class G:
    def __init__(self, path):
        z = np.load(path, allow_pickle=False)
        self.path = path
        self.n = int(z['n']); self.dataset = z['dataset'].tobytes().decode()
        self.names = unpack(z['names_b'], z['names_o']); self.kinds = unpack(z['kinds_b'], z['kinds_o'])
        self.files = unpack(z['files_b'], z['files_o']); self.start = z['start']; self.lines = z['lines']
        src, dst, self.via_ = z['src'], z['dst'], z['via']
        self.fo, self.fa, self.fv = self._csr(src, dst, self.via_)
        self.ro, self.ra, self.rv = self._csr(dst, src, self.via_)
        self.alias = dict(zip(unpack(z['alias_b'], z['alias_o']), z['alias_node'].tolist()))
        self.roots = z['roots'].tolist()

    def ext(self, v):
        z = np.load(self.path.replace('.npz', '') + '.ext.npz', allow_pickle=False)
        es, ei = z['ext_src'], z['ext_idx']
        lo, hi = np.searchsorted(es, v), np.searchsorted(es, v + 1)
        idx = ei[lo:hi]
        off, raw = z['ext_o'], z['ext_b'].tobytes()
        return [raw[off[i]:off[i + 1]].decode('utf-8') for i in idx.tolist()]

    def _csr(self, a, b, v):
        o = np.argsort(a, kind='stable')
        off = np.searchsorted(a[o], np.arange(self.n + 1))
        return off, b[o], v[o]

    def label(self, v):
        if v == 0:
            return self.names[0]
        loc = f'{self.files[v]}:{self.start[v]}' if self.files[v] else '(no source span)'
        return f'{self.names[v]}  [{self.kinds[v]}, {loc}, {self.lines[v]} lines]'

    def resolve(self, q):
        """Node ids matching a full or short name, optionally `name@path-substring`."""
        q, _, where = q.partition('@')
        def short(nm):
            if nm.startswith('_private.') and '.0.' in nm:
                nm = nm.split('.0.', 1)[1]
            return nm
        hits = set()
        if q in self.alias:
            hits.add(self.alias[q])
        hits.update(v for v in range(1, self.n) if self.names[v] == q)
        if not hits:
            suf = '.' + q
            hits.update(v for v in range(1, self.n) if (lambda s: s == q or s.endswith(suf))(short(self.names[v])))
            if not hits:
                hits.update(self.alias[a] for a in self.alias if (lambda s: s == q or s.endswith(suf))(short(a)))
        if where:
            hits = {v for v in hits if where in self.files[v]}
        return sorted(hits)

    def one(self, q):
        h = self.resolve(q)
        if len(h) == 1:
            return h[0]
        if not h:
            print(f'no declaration in the cone matches {q!r}')
        else:
            print(f'{q!r} matches {len(h)} declarations; give a fuller name or NAME@path:')
            for v in h[:40]:
                print('  ' + self.label(v))
            if len(h) > 40:
                print(f'  ... {len(h) - 40} more')
        sys.exit(2)

    def out(self, v):
        return self.fa[self.fo[v]:self.fo[v + 1]], self.fv[self.fo[v]:self.fo[v + 1]]

    def inn(self, v):
        return self.ra[self.ro[v]:self.ro[v + 1]], self.rv[self.ro[v]:self.ro[v + 1]]

    def bfs(self, a, b):
        """Distances from a along uses, stopping once b is reached; returns dist dict."""
        dist = {a: 0}; q = deque([a])
        while q:
            u = q.popleft()
            if u == b:
                break
            for w in self.out(u)[0].tolist():
                if w not in dist:
                    dist[w] = dist[u] + 1; q.append(w)
        return dist

    def shortest_paths(self, a, b, limit):
        dist = self.bfs(a, b)
        if b not in dist:
            return []
        # distances to b backwards, restricted to nodes on shortest paths
        db = {b: 0}; q = deque([b])
        while q:
            u = q.popleft()
            for w in self.inn(u)[0].tolist():
                if w not in db and w in dist and dist[w] + db[u] + 1 == dist[b]:
                    db[w] = db[u] + 1; q.append(w)
        paths, stack = [], [(a, [a])]
        while stack and len(paths) < limit:
            u, p = stack.pop()
            if u == b:
                paths.append(p); continue
            nxt = [w for w in self.out(u)[0].tolist() if w in db and dist.get(w) == dist[u] + 1 and db[w] == db[u] - 1]
            for w in sorted(nxt, key=lambda w: self.names[w], reverse=True):
                stack.append((w, p + [w]))
        return paths

    def rbfs(self, a):
        dist = {a: 0}; q = deque([a])
        while q:
            u = q.popleft()
            for w in self.inn(u)[0].tolist():
                if w not in dist:
                    dist[w] = dist[u] + 1; q.append(w)
        return dist

    def via(self, u, w):
        ws, vs = self.out(u)
        i = np.nonzero(ws == w)[0]
        return VIA.get(int(vs[i[0]]), '?') if len(i) else '?'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', required=True)
    ap.add_argument('--cache')
    ap.add_argument('--dom', help='dominators.tsv.gz (for the dominator subcommand)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('deps'); p.add_argument('name'); p.add_argument('--ext', action='store_true', help='also list Mathlib/core constants')
    p = sub.add_parser('users'); p.add_argument('name')
    p = sub.add_parser('path'); p.add_argument('a'); p.add_argument('b')
    p = sub.add_parser('paths'); p.add_argument('a'); p.add_argument('b'); p.add_argument('--max', type=int, default=10)
    p = sub.add_parser('dominator'); p.add_argument('name')
    p = sub.add_parser('meet'); p.add_argument('a'); p.add_argument('b'); p.add_argument('--max', type=int, default=3)
    p = sub.add_parser('build')
    sub.add_parser('batch', help='read one query per line from stdin (e.g. "paths A B --max 3"); load the cache once')
    a = ap.parse_args()
    ver = os.path.basename(os.path.normpath(a.data))
    cache = a.cache or os.environ.get('RHS_QUERY_CACHE') or os.path.join(os.path.dirname(os.path.normpath(a.data)), f'query-cache-{ver}.npz')
    if a.cmd == 'build' or not os.path.exists(cache):
        build_cache(a.data, cache)
        if a.cmd == 'build':
            return
    g = G(cache)
    print(f'# dataset {g.dataset}; {g.n - 1:,} declaration nodes in the cone')
    if a.cmd == 'batch':
        import shlex
        for line in sys.stdin:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            print(f'\n$ {line}')
            try:
                run(g, ap.parse_args(['--data', a.data, '--cache', cache] + (['--dom', a.dom] if a.dom else []) + shlex.split(line)), cache)
            except SystemExit:
                pass
        return
    run(g, a, cache)


def run(g, a, cache):
    if a.cmd == 'deps':
        v = g.one(a.name)
        ws, vs = g.out(v)
        print(f'{g.label(v)}\n  uses {len(ws)} project declaration{"" if len(ws) == 1 else "s"}:')
        for w, b in sorted(zip(ws.tolist(), vs.tolist()), key=lambda x: g.names[x[0]]):
            print(f'    [{VIA.get(b, "?")}] {g.label(w)}')
        en = g.ext(v)
        print(f'  and {len(en)} Mathlib/core constants' + (':' if a.ext else ' (--ext to list)'))
        if a.ext:
            for x in sorted(en):
                print(f'    {x}')
    elif a.cmd == 'users':
        v = g.one(a.name)
        ws, vs = g.inn(v)
        print(f'{g.label(v)}\n  is used directly by {len(ws)} declaration{"" if len(ws) == 1 else "s"} in the cone:')
        for w, b in sorted(zip(ws.tolist(), vs.tolist()), key=lambda x: g.names[x[0]]):
            print(f'    [{VIA.get(b, "?")}] {g.label(w)}')
    elif a.cmd in ('path', 'paths'):
        x, y = g.one(a.a), g.one(a.b)
        ps = g.shortest_paths(x, y, 1 if a.cmd == 'path' else a.max)
        if not ps:
            print(f'{g.names[x]} does not use {g.names[y]}, directly or indirectly.')
            return
        print(f'{len(ps)} shortest chain(s) of length {len(ps[0]) - 1}' + (f' (showing up to {a.max})' if a.cmd == 'paths' else '') + ':')
        for k, pth in enumerate(ps, 1):
            print(f'  chain {k}:')
            for i, v in enumerate(pth):
                arrow = '' if i == 0 else f'    uses ({g.via(pth[i - 1], v)}) '
                print(f'    {arrow}{g.label(v)}')
    elif a.cmd == 'meet':
        x, y = g.one(a.a), g.one(a.b)
        da, db = g.rbfs(x), g.rbfs(y)
        both = [v for v in da if v in db]
        if not both:
            print(f'nothing in the cone uses both {g.names[x]} and {g.names[y]}'); return
        both.sort(key=lambda v: (da[v] + db[v], max(da[v], db[v]), g.names[v]))
        best = da[both[0]] + db[both[0]]
        top = [v for v in both if da[v] + db[v] == best][:a.max]
        print(f'{len(both):,} declarations use both (transitively); nearest at combined distance {best}:')
        for v in top:
            print(f'  {g.label(v)}')
            for tgt in (x, y):
                p = g.shortest_paths(v, tgt, 1)[0]
                print('      ' + ' -> '.join(g.names[u] for u in p))
    elif a.cmd == 'dominator':
        from common import connect
        con = connect(2)
        dom = a.dom or os.path.join(os.path.dirname(cache), 'dominators.tsv.gz')
        d = con.execute(f"""select node, decl, file, start_line, idom_node, sub_decls, sub_lines from read_csv('{dom}',
            delim='\\t', header=true, quote='', escape='', sample_size=-1)""").fetchnumpy()
        d = {k: (x.filled('' if x.dtype == object else 0) if isinstance(x, np.ma.MaskedArray) else x) for k, x in d.items()}
        v = g.one(a.name)
        key = {(str(dn), str(f), int(s)): i for i, (dn, f, s) in enumerate(zip(d['decl'], d['file'], d['start_line']))}
        i = key.get((g.names[v], g.files[v], int(g.start[v])))
        if i is None:
            print('not found in the dominator table (different dataset version?)'); sys.exit(1)
        node_row = {int(nd): j for j, nd in enumerate(d['node'])}
        print(f'{g.label(v)}\n  dominates {int(d["sub_decls"][i]):,} nodes, {int(d["sub_lines"][i]):,} lines (itself included).'
              '\n  Dominator chain (each is used only through the next):')
        j = i
        while True:
            print(f'    {d["decl"][j]}  [{d["file"][j]}:{d["start_line"][j]}; dominates {int(d["sub_decls"][j]):,} nodes, {int(d["sub_lines"][j]):,} lines]')
            p = int(d['idom_node'][j])
            if p < 0 or int(d['node'][j]) == 0:
                break
            j = node_row[p]


if __name__ == '__main__':
    main()
