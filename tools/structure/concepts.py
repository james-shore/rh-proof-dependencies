"""Deliverable 3: statement cone, definitions vs lemmas, and the Mathlib interface.

usage: concepts.py DATASET_DIR OUT_DIR
- statement cone (strict): closure over `type` edges only (plus inductive-block membership) from the five theorems and
  the key chain keithadler/rh-trim names. These are the constants a reader meets in the statements' types.
- statement cone (with definitions): as above, but a def/opaque also contributes its value edges (its definition);
  theorems still contribute only their type. These are the definitions a reader needs to unfold the statements.
- kinds: source declarations in the project cone by keyword.
- Mathlib interface: Mathlib constants referenced directly from project cone constants, by area and most used.
"""
import os, sys
import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq
from common import *

CHAIN = ['UpperTheory.FinalTheorems.everyCharacter', 'UpperTheory.FinalTheorems.everyCharacter_of_row',
         'UpperTheory.LiteratureTheorems.every_character_of_literature_of_vWin',
         'UpperTheory.Lit.every_character_of_theta_integrals_local_matching',
         'UpperTheory.Top.every_character_of_green_v_win']


def csr(n, s, t):
    o = np.argsort(s, kind='stable')
    off = np.zeros(n + 1, dtype=np.int64)
    np.add.at(off, s + 1, 1)
    return np.cumsum(off), t[o].astype(np.int32)


def gather(off, adj, F):
    st, en = off[F], off[F + 1]
    ln = en - st
    tot = int(ln.sum())
    if tot == 0:
        return np.empty(0, dtype=np.int32)
    base = np.repeat(st - np.concatenate([[0], np.cumsum(ln)[:-1]]), ln)
    return adj[base + np.arange(tot)]


def bfs(n, roots, graphs, block_off, block_adj, block_of):
    """Level-synchronous BFS over the union of (off, adj, src_mask) graphs plus block membership."""
    dist = np.full(n, -1, dtype=np.int32)
    F = np.unique(np.array(roots, dtype=np.int64))
    dist[F] = 0
    lvl = 0
    while len(F):
        lvl += 1
        nb = [gather(off, adj, F[mask[F]]) for off, adj, mask in graphs]
        nb.append(gather(block_off, block_adj, block_of[F]))
        N = np.unique(np.concatenate(nb)).astype(np.int64)
        N = N[dist[N] < 0]
        dist[N] = lvl
        F = N
    return dist


def main():
    ds, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    con = connect()
    pcn, pe, pd = find_table(ds, 'constants'), find_table(ds, 'edges'), find_table(ds, 'decls')
    q = con.execute(f"""select id, name, kind, origin, coalesce(module, '') as module, coalesce(decl, '') as decl,
        coalesce(file, '') as file, coalesce(start_line, 0) as start_line, block, cast(in_cone as int) as in_cone
        from {rel(pcn)} order by id""").fetchnumpy()
    n = int(q['id'].max()) + 1
    assert (q['id'] == np.arange(n)).all()
    kind, origin = q['kind'], q['origin']
    is_def = np.isin(kind, ['def', 'opaque'])
    proj = origin == 'project'
    ml = origin == 'mathlib'
    incone = q['in_cone'] == 1
    name_id = {nm: i for i, nm in enumerate(q['name'])}
    # block membership
    bk = q['block'].astype(np.int64)
    ub, binv = np.unique(bk, return_inverse=True)
    block_off, block_adj = csr(len(ub), binv, np.arange(n))
    block_of = binv
    # edges by kind
    S, T, V = [], [], []
    if pe.endswith('.parquet'):
        f = pq.ParquetFile(pe)
        sc, tc = ('src_id', 'dst_id') if 'src_id' in f.schema_arrow.names else ('src', 'dst')
        for b in f.iter_batches(columns=[sc, tc, 'via'], batch_size=8_000_000):
            via = b.column(2)
            S.append(b.column(0).to_numpy().astype(np.int32)); T.append(b.column(1).to_numpy().astype(np.int32))
            V.append(np.where(pc.equal(via, 'type').to_numpy(zero_copy_only=False), 0,
                              np.where(pc.equal(via, 'value').to_numpy(zero_copy_only=False), 1, 2)).astype(np.int8))
    else:
        r = con.execute(f"select src_id, dst_id, via from {rel(pe)}").fetchnumpy()
        S, T = [r['src_id'].astype(np.int32)], [r['dst_id'].astype(np.int32)]
        V = [np.where(r['via'] == 'type', 0, np.where(r['via'] == 'value', 1, 2)).astype(np.int8)]
    S, T, V = np.concatenate(S), np.concatenate(T), np.concatenate(V)
    log(f'edges: {len(S)} (type {int((V == 0).sum())}, value {int((V == 1).sum())}, other {int((V == 2).sum())})')
    ty = V == 0
    toff, tadj = csr(n, S[ty], T[ty])
    vv = (V == 1) & is_def[S]
    voff, vadj = csr(n, S[vv], T[vv])
    lo = V == 2
    loff, ladj = csr(n, S[lo], T[lo])
    everyone = np.ones(n, dtype=bool)
    roots = [name_id[t] for t in THEOREMS] + [name_id[c] for c in CHAIN if c in name_id]
    missing = [c for c in CHAIN if c not in name_id]
    strict = bfs(n, roots, [(toff, tadj, everyone), (loff, ladj, everyone)], block_off, block_adj, block_of)
    withdef = bfs(n, roots, [(toff, tadj, everyone), (voff, vadj, is_def), (loff, ladj, everyone)],
                  block_off, block_adj, block_of)
    summ = {'roots': [q['name'][r] for r in roots], 'missing_chain_names': missing}
    for nm, dist in (('strict', strict), ('with_definitions', withdef)):
        m = dist >= 0
        summ[nm] = {'constants': int(m.sum()), 'project': int((m & proj).sum()), 'mathlib': int((m & ml).sum()),
                    'other': int((m & ~proj & ~ml).sum()), 'max_depth': int(dist.max())}
        # project rows folded to source declarations
        rows = {}
        for i in np.nonzero(m & proj)[0]:
            key = (q['decl'][i] or q['name'][i], q['file'][i], int(q['start_line'][i]))
            r = rows.setdefault(key, [kind[i], int(dist[i]), 0])
            r[1] = min(r[1], int(dist[i])); r[2] += 1
        out_rows = sorted(((k[0], v[0], k[1], k[2], v[1], v[2]) for k, v in rows.items()), key=lambda r: (r[4], r[2], r[3]))
        write_tsv(os.path.join(out, f'statement-cone-{nm}.tsv'),
                  ['decl', 'kind_of_first_constant', 'file', 'start_line', 'depth', 'constants'], out_rows)
        summ[nm]['project_decls'] = len(out_rows)
        mrows = sorted(((q['name'][i], kind[i], q['module'][i], int(dist[i])) for i in np.nonzero(m & ~proj)[0]),
                       key=lambda r: (r[3], r[0]))
        write_tsv(os.path.join(out, f'statement-cone-{nm}-deps.tsv'), ['name', 'kind', 'module', 'depth'], mrows)
        log(nm, summ[nm])

    # kinds of source declarations in the project cone
    ng = not_generated(con, pd)
    dk = con.execute(f"""select kind, count(*) filter (where cast(in_cone as int) = 1) as in_cone, count(*) as total,
        sum(lines) filter (where cast(in_cone as int) = 1) as lines_in_cone
        from {rel(pd)} where origin = 'project' and decl <> '' {ng} group by kind order by in_cone desc""").fetchall()
    if ng:
        summ['generated_declarations'] = con.execute(f"""select count(*), count(*) filter (where cast(in_cone as int) = 1)
            from {rel(pd)} where origin = 'project' and coalesce(cast(generated as boolean), false)""").fetchone()
    summ['decl_kinds'] = [list(r) for r in dk]
    defs = {'def', 'abbrev', 'structure', 'class', 'inductive', 'instance', 'opaque', 'class inductive'}
    summ['definitions_vs_lemmas'] = {
        'definitions (def/abbrev/structure/class/inductive/instance/opaque)': sum(r[1] for r in dk if r[0] in defs),
        'lemmas (theorem/lemma)': sum(r[1] for r in dk if r[0] in ('theorem', 'lemma')),
        'other': sum(r[1] for r in dk if r[0] not in defs and r[0] not in ('theorem', 'lemma'))}

    # Mathlib interface: direct references from project cone constants to Mathlib constants
    x = proj[S] & incone[S] & ml[T]
    xs, xt = S[x], T[x]
    pairs = np.unique(xs.astype(np.int64) * n + xt)
    xs, xt = pairs // n, pairs % n
    # per Mathlib constant: distinct project source declarations using it
    dkey = np.array([f'{q["decl"][i]}\t{q["file"][i]}\t{q["start_line"][i]}' for i in range(n)], dtype=object)
    _, dcode = np.unique(dkey, return_inverse=True)
    dp = np.unique(dcode[xs].astype(np.int64) * n + xt)
    users = np.bincount(dp % n, minlength=n)
    used = np.nonzero(users)[0]
    area = np.array(['.'.join(str(md).split('.')[:2]) if md else '' for md in q['module']], dtype=object)
    au = {}
    for i in used:
        a = au.setdefault(area[i], [0, 0])
        a[0] += 1; a[1] += int(users[i])
    area_rows = sorted(((k, v[0], v[1]) for k, v in au.items()), key=lambda r: -r[1])
    write_tsv(os.path.join(out, 'mathlib-areas.tsv'), ['area', 'mathlib_constants_used', 'project_decl_uses'], area_rows)
    top = used[np.argsort(-users[used], kind='stable')]
    write_tsv(os.path.join(out, 'mathlib-used.tsv'), ['name', 'kind', 'module', 'project_decls_using'],
              ((q['name'][i], kind[i], q['module'][i], int(users[i])) for i in top))
    summ['mathlib'] = {'mathlib_constants_in_cone': int((ml & incone).sum()),
                       'mathlib_constants_used_directly_by_project': int(len(used)),
                       'mathlib_modules_used_directly': int(len({q['module'][i] for i in used})),
                       'areas': len(area_rows)}
    write_json(os.path.join(out, 'concepts-summary.json'), summ)
    log('done', summ['mathlib'], summ['definitions_vs_lemmas'])


if __name__ == '__main__':
    main()
