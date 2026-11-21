"""Candidate-cut tables from the unused-hypothesis pass, folded to the nodes of dominators.py.

usage: relevance_tables.py OUT_DIR DATA_DIR
Reads OUT_DIR/dominators.tsv.gz, OUT_DIR/work/const-node.npy and OUT_DIR/relevance/{tag}-{cone,positions}.tsv; writes
DATA_DIR/relevance-dropped-{tag}.tsv (nodes that leave the relevance-pruned cone) and
DATA_DIR/removable-hypotheses-{tag}.tsv (constants with removable hypotheses, ranked by what they drag in), for
tag = binders, deadlet.
"""
import os, sys
import numpy as np
from common import *


def main():
    out, data = sys.argv[1], sys.argv[2]
    os.makedirs(data, exist_ok=True)
    con = connect()
    D = load_dom(con, out)
    cnode = np.load(os.path.join(out, 'work', 'const-node.npy'))
    idom = D['idom_node'].astype(np.int64)
    for tag in ('binders', 'deadlet'):
        cpath = os.path.join(out, 'relevance', f'{tag}-cone.tsv')
        if not os.path.exists(cpath):
            continue
        rc = con.execute(f"""select id, in_kernel_cone k, in_pruned_cone p, project from read_csv('{cpath}',
            delim='\\t', header=true, quote='', escape='')""").fetchnumpy()
        k, p, pr = rc['k'] == 1, rc['p'] == 1, rc['project'] == 1
        nk = cnode[rc['id'][k & pr]]; nk = np.unique(nk[nk > 0])
        npr = cnode[rc['id'][k & pr & p]]; npr = np.unique(npr[npr > 0])
        dropped = np.setdiff1d(nk, npr)
        write_tsv(os.path.join(data, f'relevance-dropped-{tag}.tsv'),
                  ['decl', 'kind', 'file', 'start_line', 'end_line', 'lines', 'node_type', 'idom'],
                  ((D['decl'][v], D['kind'][v], D['file'][v], D['start_line'][v], D['end_line'][v], D['lines'][v],
                    D['node_type'][v] if 'node_type' in D else '', D['decl'][idom[v]]) for v in dropped))
        pp = os.path.join(out, 'relevance', f'{tag}-positions.tsv')
        rows = con.execute(f"""select name, kind, binders, removable, in_kernel_cone, in_pruned_cone, irrelevant_sites,
            seeds_dropped, drags_dropped from read_csv('{pp}', delim='\\t', header=true, quote='', escape='',
            types={{'drags_dropped': 'BIGINT', 'removable': 'VARCHAR'}}) where removable is not null and removable <> ''
            and in_kernel_cone = 1 order by drags_dropped desc nulls last, seeds_dropped desc, name""").fetchall()
        write_tsv(os.path.join(data, f'removable-hypotheses-{tag}.tsv'),
                  ['name', 'kind', 'binders', 'removable', 'in_kernel_cone', 'in_pruned_cone', 'call_sites',
                   'seeds_dropped', 'drags_dropped'], rows)
        log(tag, 'dropped nodes', len(dropped), 'constants with removable hypotheses', len(rows))


if __name__ == '__main__':
    main()
