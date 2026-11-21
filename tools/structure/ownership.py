"""Collective ownership of a set of nodes L on the dominator graph of dominators.py.

owned(L)   = nodes reachable from the five theorems that become unreachable once every node of L is removed (L included):
             everything used only through L, taken together.
closure(L) = nodes reachable from L (L included). closure - owned is what L uses that something outside L also needs.

usage: ownership.py DATASET_DIR OUT_DIR NAMES.txt RESULT_PREFIX
  NAMES.txt: kernel constant names, one per line; each is mapped to its node with work/const-node.npy.
Writes RESULT_PREFIX.tsv (one row per node in closure(L): node, decl, file, start_line, lines, in_L, owned) and
RESULT_PREFIX.json (counts).
"""
import json, os, sys
import numpy as np
from common import *
from report import load_dom


def bfs(off, adj, n, starts, blocked):
    seen = np.zeros(n, dtype=bool)
    F = np.unique(np.asarray([s for s in starts if not blocked[s]], dtype=np.int64))
    seen[F] = True
    while len(F):
        st, en = off[F], off[F + 1]
        ln = en - st
        if ln.sum() == 0:
            break
        base = np.repeat(st - np.concatenate([[0], np.cumsum(ln)[:-1]]), ln)
        nb = np.unique(adj[base + np.arange(int(ln.sum()))])
        nb = nb[~seen[nb] & ~blocked[nb]]
        seen[nb] = True
        F = nb
    return seen


def main():
    ds, out, names_file, prefix = sys.argv[1:5]
    con = connect()
    D = load_dom(con, out)
    n = len(D['node'])
    cnode = np.load(os.path.join(out, 'work', 'const-node.npy'))
    names = [l.strip() for l in open(names_file) if l.strip()]
    q = con.execute(f"select name, id from {rel(find_table(ds, 'constants'))}").fetchnumpy()
    idof = dict(zip(q['name'], q['id']))
    missing = [x for x in names if x not in idof]
    L = sorted({int(cnode[idof[x]]) for x in names if x in idof and cnode[idof[x]] > 0})
    nonproj = [x for x in names if x in idof and cnode[idof[x]] <= 0]
    E = np.fromfile(os.path.join(out, 'work', 'decl-edges.bin'), dtype=np.uint32).reshape(-1, 2).astype(np.int64)
    o = np.argsort(E[:, 0], kind='stable')
    off = np.zeros(n + 1, dtype=np.int64); np.add.at(off, E[:, 0] + 1, 1); off = np.cumsum(off); adj = E[o, 1]
    none = np.zeros(n, dtype=bool)
    inL = np.zeros(n, dtype=bool); inL[L] = True
    full = bfs(off, adj, n, [0], none)
    avoid = bfs(off, adj, n, [0], inL)
    owned = full & ~avoid
    clo = bfs(off, adj, n, L, none)
    lines = D['lines'].astype(np.int64)
    real = D['node_type'] == 'decl' if 'node_type' in D else lines > 0
    res = {'dataset': os.environ.get('RHS_DATASET_VERSION', os.path.basename(os.path.normpath(ds))),
           'names': len(names), 'names_missing': missing, 'names_not_project': nonproj, 'L_nodes': len(L),
           'cone_nodes': int(full[1:].sum()), 'cone_lines': int(lines[full].sum()),
           'owned_nodes': int(owned.sum()), 'owned_decls': int((owned & real).sum()), 'owned_lines': int(lines[owned].sum()),
           'closure_nodes': int(clo.sum()), 'closure_decls': int((clo & real).sum()), 'closure_lines': int(lines[clo].sum()),
           'shared_nodes': int((clo & ~owned).sum()), 'shared_lines': int(lines[clo & ~owned].sum()),
           'owned_not_in_closure': int((owned & ~clo).sum())}
    assert res['owned_not_in_closure'] == 0
    write_json(prefix + '.json', res)
    idx = np.nonzero(clo)[0]
    write_tsv(prefix + '.tsv', ['node', 'decl', 'file', 'start_line', 'lines', 'in_L', 'owned'],
              ((int(v), D['decl'][v], D['file'][v], int(D['start_line'][v]), int(lines[v]), int(inL[v]), int(owned[v])) for v in idx))
    log(res)


if __name__ == '__main__':
    main()
