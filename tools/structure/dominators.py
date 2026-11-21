"""Deliverables 1-2: dominator tree over the project cone's folded source-declaration graph, subtree sizes,
top subtrees, chapter cut, per-directory breakdown, and checks.

usage: dominators.py DATASET_DIR OUT_DIR
DATASET_DIR holds cone's tables (constants, edges, decls, directories; Parquet or TSV, v0.1 schema).
"""
import os, sys
import numpy as np
from common import *

TOP = int(os.environ.get('RHS_TOP', '200'))
CHAPTER_FRAC = float(os.environ.get('RHS_CHAPTER_FRAC', '0.01'))


def load(con, ds):
    """Small tables in duckdb (constants -> decl node), the edge table streamed into numpy.

    A project constant becomes a node of its own, instead of folding into its source declaration, when (a) it has no
    in-cone decl row (no text-index match, or a shared auxiliary lemma whose generating declaration is not in the cone),
    or (b) it is a shared auxiliary lemma (`constants.shared_aux`) that a different declaration uses: folding it into
    its generator would add a false edge from that declaration to the generator."""
    import pyarrow as pa
    pc, pe, pd = find_table(ds, 'constants'), find_table(ds, 'edges'), find_table(ds, 'decls')
    ccols, dcols = cols(con, pc), cols(con, pd)
    log('constants', pc, '| edges', pe, '| decls', pd)
    in_cone_c = 'cast(in_cone as int) = 1' if 'in_cone' in ccols else 'depth >= 0'
    shared = 'coalesce(shared_aux, false)' if 'shared_aux' in ccols else 'false'
    con.execute(f"""create table c as select id, name, kind, origin, decl, file, start_line,
        coalesce(type_dag, 0) + coalesce(value_dag, 0) as ksize, block, thm_mask, {shared} as shared from {rel(pc)}
        where {in_cone_c}""")
    dorig = "and origin = 'project'" if 'origin' in dcols else ''
    con.execute(f"""create table d0 as select decl, kind, file, start_line, end_line, lines from {rel(pd)}
        where cast(in_cone as int) = 1 and decl is not null and decl <> '' {dorig}""")
    con.execute("""create table cm as select c.*, d0.decl as dk from c left join d0
        on c.decl = d0.decl and c.file = d0.file and c.start_line = d0.start_line""")
    m = int(con.execute(f'select max(id) from {rel(pc)}').fetchone()[0]) + 1
    stats = {}

    def make(standalone_ids):
        con.register('sa', pa.table({'id': pa.array(np.asarray(standalone_ids, dtype=np.int64))}))
        con.execute("create or replace table cm2 as select cm.*, (cm.origin = 'project' and (cm.dk is null or cm.id in (select id from sa))) as alone from cm")
        # node 0 = virtual root; then source declarations and stand-alone constants, ordered by file and line
        con.execute("""create or replace table nodes as select row_number() over (order by file, start_line, decl) as did, * from (
            select decl, kind, file, start_line, end_line, lines, 0 as unmapped from d0
            union all select name, case when shared then 'shared_aux' else kind end, coalesce(file, ''), coalesce(start_line, 0),
                coalesce(start_line, 0), 0, case when shared then 2 else 1 end from cm2 where alone)""")
        con.execute("""create or replace table cmap as
            select cm2.id, cm2.origin, cm2.ksize, cm2.block, cm2.thm_mask, n.did from cm2 join nodes n
              on n.unmapped = 0 and n.decl = cm2.dk and n.file = cm2.file and n.start_line = cm2.start_line where not cm2.alone
            union all select cm2.id, cm2.origin, cm2.ksize, cm2.block, cm2.thm_mask, n.did from cm2 join nodes n
              on n.unmapped > 0 and n.decl = cm2.name where cm2.alone
            union all select id, origin, ksize, block, thm_mask, null from cm2 where origin <> 'project'""")
        q = con.execute("select id, coalesce(did, -1) as did, origin = 'project' as proj, origin = 'mathlib' as ml, ksize, block, thm_mask from cmap").fetchnumpy()
        assert len(np.unique(q['id'])) == len(q['id']) == con.execute("select count(*) from cm").fetchone()[0]
        C = {'did': np.full(m, -1, dtype=np.int64), 'ml': np.zeros(m, dtype=bool), 'incone': np.zeros(m, dtype=bool)}
        C['did'][q['id']] = q['did']; C['ml'][q['id']] = q['ml']; C['incone'][q['id']] = True
        C['q'] = q
        return C

    C = make([])
    stats['no_decl_row'] = con.execute("select count(*) from cm where origin = 'project' and dk is null").fetchone()[0]
    stats['no_decl_row_shared_aux'] = con.execute("select count(*) from cm where origin = 'project' and dk is null and shared").fetchone()[0]
    sh = con.execute("select id from cm where shared and dk is not null").fetchnumpy()['id']
    is_sh = np.zeros(m, dtype=bool); is_sh[sh] = True
    cross = np.zeros(m, dtype=bool)
    for it in range(20):
        did = C['did']
        before = int(cross.sum())
        for s_, t_ in edge_batches(pe):
            k = is_sh[t_] & (did[s_] >= 0) & (did[s_] != did[t_])
            cross[t_[k]] = True
        if int(cross.sum()) == before:
            break
        C = make(np.nonzero(cross)[0])
    stats['shared_aux_used_by_other_decls'] = int(cross.sum())
    stats['passes'] = it + 1
    log('stand-alone constants:', stats)
    C['standalone_stats'] = stats
    log('cmap:', len(C['q']['id']), 'in-cone constants')
    return C, pe


def edge_batches(pe):
    ecols = None
    if pe.endswith('.parquet'):
        import pyarrow.parquet as pq
        f = pq.ParquetFile(pe)
        names = f.schema_arrow.names
        sc, tc = ('src_id', 'dst_id') if 'src_id' in names else ('src', 'dst')
        for b in f.iter_batches(columns=[sc, tc], batch_size=8_000_000):
            yield b.column(0).to_numpy().astype(np.int64), b.column(1).to_numpy().astype(np.int64)
    else:
        con = connect(4)
        names = cols(con, pe)
        sc, tc = ('src_id', 'dst_id') if 'src_id' in names else ('src', 'dst')
        r = con.execute(f"select {sc}, {tc} from {rel(pe)}").fetchnumpy()
        yield r[sc].astype(np.int64), r[tc].astype(np.int64)


def build(con, C, pe, out):
    D = con.execute("select max(did) from nodes").fetchone()[0]
    n = D + 1
    did, ml = C['did'], C['ml']
    pp, ext, nk = [], [], 0
    for s, t in edge_batches(pe):
        ds, dt = did[s], did[t]
        m = (ds >= 0) & (dt >= 0) & (ds != dt)
        nk += int(m.sum())
        pp.append(np.unique(ds[m] * n + dt[m]))
        x = (ds >= 0) & (dt < 0)
        ext.append(np.unique(ds[x] * len(did) + t[x]))
    pp = np.unique(np.concatenate(pp)); ext = np.unique(np.concatenate(ext))
    es, et = pp // n, pp % n
    log(f'project->project kernel edges between different decls: {nk}; distinct decl edges: {len(pp)}')
    xd, xc = ext // len(did), ext % len(did)
    C['ext_x'] = np.bincount(xd, minlength=n).astype(np.int64)
    C['ext_xm'] = np.bincount(xd[ml[xc]], minlength=n).astype(np.int64)
    # block membership: constants exported together pull each other in; connect their decls both ways
    q = C['q']
    k = q['did'] >= 0
    bd = np.unique(np.stack([q['block'][k].astype(np.int64), q['did'][k]], 1), axis=0)
    ub, cnt = np.unique(bd[:, 0], return_counts=True)
    multi = set(ub[cnt > 1].tolist())
    groups = {}
    for b_, d_ in bd:
        if b_ in multi:
            groups.setdefault(int(b_), []).append(int(d_))
    bedges = sorted({(a, b) for ds_ in groups.values() for a in ds_ for b in ds_ if a != b})
    log(f'blocks spanning several decls: {len(groups)} -> {len(bedges)} block edges')
    roots = {}
    for t in THEOREMS:
        r = con.execute("""select n.did, n.file from c join cmap m on c.id = m.id join nodes n on n.did = m.did
            where c.name = ?""", [t]).fetchall()
        assert len(r) == 1 and r[0][1] == 'src/Main.lean', (t, r)
        roots[t] = r[0][0]
    log('roots', roots)
    extra = np.array(bedges + [(0, roots[t]) for t in THEOREMS], dtype=np.int64).reshape(-1, 2)
    allE = np.concatenate([np.stack([es, et], 1), extra]).astype(np.uint32)
    ef = os.path.join(out, 'work', 'decl-edges.bin')
    allE.tofile(ef)
    return n, roots, ef, len(bedges), es, et


def node_table(con, C, n):
    q = con.execute("""select did, decl, kind, file, start_line, end_line, lines, unmapped from nodes order by did""").fetchnumpy()
    N = {k: np.empty(n, dtype=object if q[k].dtype == object else q[k].dtype) for k in q}
    for k in q:
        N[k][1:] = q[k]
    N['decl'][0] = '(root: the five theorems)'; N['kind'][0] = ''; N['file'][0] = ''
    for k in ('start_line', 'end_line', 'lines', 'unmapped'):
        N[k][0] = 0
    N['lines'] = N['lines'].astype(np.int64)
    qq = C['q']; k = qq['did'] >= 0
    N['nconst'] = np.bincount(qq['did'][k], minlength=n).astype(np.int64)
    N['ksize'] = np.bincount(qq['did'][k], weights=qq['ksize'][k], minlength=n).astype(np.int64)
    mask = np.zeros(n, dtype=np.int64)
    np.bitwise_or.at(mask, qq['did'][k], qq['thm_mask'][k].astype(np.int64))
    N['mask'] = mask
    N['ext_x'], N['ext_xm'] = C['ext_x'], C['ext_xm']
    return N


def read_idom(path, n):
    a = np.loadtxt(path, dtype=np.int64, skiprows=1, delimiter='\t', ndmin=2)
    idom = np.full(n, -1, dtype=np.int64)
    idom[a[:, 0]] = a[:, 1]
    return idom


def tree_order(idom, root):
    """Nodes in BFS order of the dominator tree, their tree depth, and Euler intervals (tin, tout)."""
    n = len(idom)
    kids = [[] for _ in range(n)]
    for v in np.nonzero(idom >= 0)[0]:
        if v != root:
            kids[idom[v]].append(v)
    depth = np.full(n, -1, dtype=np.int64); depth[root] = 0
    order = [root]; i = 0
    while i < len(order):
        v = order[i]; i += 1
        for w in kids[v]:
            depth[w] = depth[v] + 1; order.append(w)
    tin = np.full(n, -1, dtype=np.int64); tout = np.full(n, -1, dtype=np.int64)
    t = 0; st = [(root, 0)]
    tin[root] = t; t += 1
    while st:
        v, k = st[-1]
        if k < len(kids[v]):
            st[-1] = (v, k + 1); w = kids[v][k]
            tin[w] = t; t += 1; st.append((w, 0))
        else:
            tout[v] = t; st.pop()
    return np.array(order, dtype=np.int64), depth, tin, tout, kids


def sums(idom, depth, w):
    s = w.astype(np.int64).copy()
    for d in range(int(depth.max()), 0, -1):
        vs = np.nonzero(depth == d)[0]
        np.add.at(s, idom[vs], s[vs])
    return s


def main():
    ds, out = sys.argv[1], sys.argv[2]
    os.makedirs(os.path.join(out, 'work'), exist_ok=True)
    con = connect()
    C, pe = load(con, ds)
    np.save(os.path.join(out, 'work', 'const-node.npy'), C['did'])
    n, roots, ef, nblock, es, et = build(con, C, pe, out)
    N = node_table(con, C, n)
    log(f'nodes: {n - 1} project decls in the cone (+ virtual root)')

    # combined tree from the virtual root, plus one tree per theorem
    run_bin('dom', ef, n, 0, os.path.join(out, 'work', 'idom-root.tsv'), os.environ.get('RHS_BRUTE', '24'), 1)
    idom = read_idom(os.path.join(out, 'work', 'idom-root.tsv'), n)
    assert (idom[1:] >= 0).all(), f'{(idom[1:] < 0).sum()} cone decls unreachable from the five theorems'
    order, depth, tin, tout, kids = tree_order(idom, 0)
    W = {'decls': np.ones(n, dtype=np.int64), 'lines': N['lines'], 'ksize': N['ksize'], 'nconst': N['nconst'],
         'ext': N['ext_x']}
    W['decls'][0] = 0
    S = {k: sums(idom, depth, w) for k, w in W.items()}
    nkids = np.array([len(k) for k in kids], dtype=np.int64)
    tot = {k: int(S[k][0]) for k in S}
    log('totals', tot)

    # independent check of the reported subtrees on the edge list (in Python, not the Rust tool), top 20 by lines
    ts, tt = tin[es], tin[et]
    cand = [v for v in np.argsort(-S['lines']) if v != 0][:20]
    check = []
    for r in cand:
        a, b = tin[r], tout[r]
        inside_t = (tt > a) & (tt < b)
        bad = int((inside_t & ~((ts >= a) & (ts < b))).sum())
        check.append((N['decl'][r], int(S['decls'][r]), int(inside_t.sum()), bad))
    log('raw-edge check on top-20 subtrees: failures', sum(c[3] for c in check))

    # per-theorem trees
    per_thm = {}
    for t, r in roots.items():
        p = os.path.join(out, 'work', f'idom-{t}.tsv')
        run_bin('dom', ef, n, r, p, 0, 1)
        it = read_idom(p, n)
        o2, d2, _, _, k2 = tree_order(it, r)
        s2 = sums(it, d2, W['lines'] * (it >= 0))
        c2 = sums(it, d2, W['decls'] * (it >= 0))
        top = sorted((v for v in k2[r]), key=lambda v: -s2[v])[:15]
        per_thm[t] = {'reach_decls': int((it >= 0).sum()), 'reach_lines': int(s2[r]),
                      'only_this_theorem_decls': int(S['decls'][r]) - 1, 'only_this_theorem_lines': int(S['lines'][r]) - int(N['lines'][r]),
                      'top_children': [(N['decl'][v], N['file'][v], int(c2[v]), int(s2[v])) for v in top]}

    # outputs
    d1, d2 = dir_keys(N['file'])
    rows = []
    for v in order:
        rows.append((v, N['decl'][v], N['kind'][v], N['file'][v], N['start_line'][v], N['end_line'][v], N['lines'][v],
                     N['nconst'][v], N['ksize'][v], N['ext_x'][v], N['ext_xm'][v], int(N['mask'][v]),
                     idom[v] if v else -1, N['decl'][idom[v]] if v else '', depth[v], nkids[v],
                     S['decls'][v], S['lines'][v], S['ksize'][v], S['nconst'][v], S['ext'][v], tin[v], tout[v],
                     ('root', 'decl', 'no_decl_row', 'shared_aux')[0 if v == 0 else 1 + int(N['unmapped'][v])]))
    hdr = ['node', 'decl', 'kind', 'file', 'start_line', 'end_line', 'lines', 'n_constants', 'kernel_dag', 'ext_refs',
           'ext_refs_mathlib', 'thm_mask', 'idom_node', 'idom', 'dom_depth', 'children', 'sub_decls', 'sub_lines',
           'sub_kernel_dag', 'sub_constants', 'sub_ext_refs', 'tin', 'tout', 'node_type']
    write_tsv(os.path.join(out, 'dominators.tsv.gz'), hdr, rows)
    np.savez(os.path.join(out, 'work', 'tree.npz'), idom=idom, depth=depth, tin=tin, tout=tout,
             **{'S_' + k: S[k] for k in S}, **{'N_' + k: N[k] for k in ('lines', 'ksize', 'nconst', 'ext_x', 'mask')})

    # top subtrees with directory composition and interfaces
    by_tin = np.argsort(tin)
    d1u, d1c = np.unique(np.array(d1, dtype=object), return_inverse=True)
    d2u, d2c = np.unique(np.array(d2, dtype=object), return_inverse=True)
    srt = np.argsort(ts, kind='stable'); ts_s, tt_s = ts[srt], tt[srt]
    srt2 = np.argsort(tt, kind='stable'); tt_t, ts_t = tt[srt2], ts[srt2]
    top_rows = []
    cands = [v for v in np.argsort(-S['lines']) if v != 0 and S['decls'][v] > 1][:TOP]
    for v in cands:
        a, b = tin[v], tout[v]
        members = by_tin[a:b]
        dl = np.bincount(d2c[members], weights=N['lines'][members], minlength=len(d2u))
        comp = [(d2u[i], dl[i]) for i in np.argsort(-dl)[:4] if dl[i] > 0]
        lo, hi = np.searchsorted(ts_s, a), np.searchsorted(ts_s, b)
        dst = tt_s[lo:hi]
        prereq = int(np.unique(dst[(dst < a) | (dst >= b)]).size)
        lo, hi = np.searchsorted(tt_t, a), np.searchsorted(tt_t, a + 1)
        src = ts_t[lo:hi]
        users = int(np.unique(src[(src < a) | (src >= b)]).size)
        top_rows.append((N['decl'][v], N['kind'][v], N['file'][v], N['start_line'][v], depth[v], N['decl'][idom[v]],
                         S['decls'][v], S['lines'][v], S['ksize'][v], S['ext'][v], prereq, users, nkids[v],
                         '; '.join(f'{k} {x / max(1, S["lines"][v]) * 100:.0f}%' for k, x in comp)))
    write_tsv(os.path.join(out, 'top-subtrees.tsv'),
              ['decl', 'kind', 'file', 'start_line', 'dom_depth', 'idom', 'sub_decls', 'sub_lines', 'sub_kernel_dag',
               'sub_ext_refs', 'prereq_project_decls', 'direct_users', 'children', 'lines_by_dir2'], top_rows)

    # chapter cut: maximal subtrees with at most CHAPTER_FRAC of the cone's lines; nodes above them are the spine
    T = CHAPTER_FRAC * tot['lines']
    big = S['lines'] > T
    chapter_of = np.full(n, -1, dtype=np.int64)
    chapters = [v for v in order if v != 0 and not big[v] and big[idom[v]]]
    for v in chapters:
        chapter_of[by_tin[tin[v]:tout[v]]] = v
    spine = [v for v in order if big[v]]
    ch_rows = sorted(((N['decl'][v], N['file'][v], N['start_line'][v], N['decl'][idom[v]], S['decls'][v], S['lines'][v],
                       S['ksize'][v]) for v in chapters), key=lambda r: -r[5])
    write_tsv(os.path.join(out, 'chapters.tsv'), ['decl', 'file', 'start_line', 'idom', 'sub_decls', 'sub_lines',
                                                  'sub_kernel_dag'], ch_rows)
    spine_rows = [(N['decl'][v], N['file'][v], N['start_line'][v], N['decl'][idom[v]] if v else '', N['lines'][v],
                   S['decls'][v], S['lines'][v], nkids[v]) for v in spine]
    write_tsv(os.path.join(out, 'spine.tsv'), ['decl', 'file', 'start_line', 'idom', 'own_lines', 'sub_decls',
                                               'sub_lines', 'children'], spine_rows)

    # per-directory: lines in cone by which chapter (or the spine) they fall in
    ch_ids, ch_code = np.unique(chapter_of, return_inverse=True)  # -1 = spine (and root)
    dir_rows = []
    for lvl, du, dc in ((1, d1u, d1c), (2, d2u, d2c)):
        key = dc.astype(np.int64) * len(ch_ids) + ch_code
        key[0] = -1
        ok = key >= 0
        cnt = np.bincount(key[ok], weights=N['lines'][ok], minlength=len(du) * len(ch_ids)).reshape(len(du), len(ch_ids))
        nd = np.bincount(dc[1:], minlength=len(du)); nl = np.bincount(dc[1:], weights=N['lines'][1:], minlength=len(du))
        nk = np.bincount(dc[1:], weights=N['ksize'][1:], minlength=len(du))
        sp = np.nonzero(ch_ids == -1)[0]
        for i, name in enumerate(du):
            if nd[i] == 0:
                continue
            row = cnt[i]
            order_c = [j for j in np.argsort(-row)[:3] if row[j] > 0]
            spine_ln = int(row[sp[0]]) if len(sp) else 0
            touched = int(((row > 0) & (ch_ids >= 0)).sum())
            dir_rows.append((name, lvl, int(nd[i]), int(nl[i]), int(nk[i]), touched, spine_ln,
                             '; '.join(f'{N["decl"][ch_ids[j]] if ch_ids[j] >= 0 else "(spine)"} {int(row[j])}' for j in order_c)))
    write_tsv(os.path.join(out, 'dir-dominators.tsv'),
              ['dir', 'level', 'decls_in_cone', 'lines_in_cone', 'kernel_dag_in_cone', 'chapters_touched',
               'lines_on_spine', 'top_chapters_by_lines'], dir_rows)

    nkind = con.execute("select unmapped, count(*) from nodes group by all order by all").fetchall()
    summary = {'dataset': os.environ.get('RHS_DATASET_VERSION', os.path.basename(os.path.normpath(ds))),
               'node_kinds': {['source declarations', 'constants without an in-cone decl row', 'shared aux lemmas standing alone (generator not in the cone, or used by another declaration)'][k]: v for k, v in nkind},
               'standalone_stats': C['standalone_stats'],
               'nodes': n - 1, 'decl_edges': int(len(es)),
               'block_edges': nblock, 'totals': tot, 'root_children': len(kids[0]),
               'max_dom_depth': int(depth.max()), 'chapter_threshold_lines': T, 'chapters': len(chapters),
               'spine_nodes': len(spine), 'raw_edge_check_top20': check, 'per_theorem': per_thm,
               'dom_check': open(os.path.join(out, 'work', 'idom-root.tsv.check')).read()}
    write_json(os.path.join(out, 'dominators-summary.json'), summary)
    log('done:', {k: summary[k] for k in ('nodes', 'decl_edges', 'root_children', 'max_dom_depth', 'chapters', 'spine_nodes')})


if __name__ == '__main__':
    main()
