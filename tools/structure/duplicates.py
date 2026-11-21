"""Duplicate statements and definitions (from rs/src/bin/dupes.rs), folded to source declarations.

usage: duplicates.py DATASET_DIR DUPES_DIR DATA_DIR [DOM_OUT_DIR (default: DUPES_DIR/..)]
Exact (1, 2): equal hash-consed expression ids, i.e. alpha-equivalent terms (binder names and binder info ignored;
level-parameter names count). Theorems compare on their statement, definitions on statement and value.
Up to renaming (3): equal after replacing every constant by its class representative, iterated to a fixpoint.
Source level = the declaration's own kernel constant (`aux_rule = self`); compiler-generated helpers are counted
separately. Members often restate one another (`theorem b : S := a`), so keeping a member also keeps every member its
proof reaches. "Redundant lines" of a cluster = its lines minus min over members k of the lines of the members reachable
from k (edges of dominators.py's node graph between members of the cluster, closed inside the cluster; a path that
leaves the cluster and comes back is not seen, so this can overstate).
Writes DATA_DIR/duplicates-{statements,definitions,mathlib,renaming}.tsv and DUPES_DIR/duplicates-summary.json.
"""
import json, os, sys
import numpy as np
from common import *


def main():
    ds, dd, data = sys.argv[1:4]
    dom_out = sys.argv[4] if len(sys.argv) > 4 else os.path.dirname(os.path.normpath(dd))
    os.makedirs(data, exist_ok=True)
    cnode = np.load(os.path.join(dom_out, 'work', 'const-node.npy'))
    E = np.fromfile(os.path.join(dom_out, 'work', 'decl-edges.bin'), dtype=np.uint32).reshape(-1, 2).astype(np.int64)
    con = connect()
    con.execute(f"""create table d as select x.id, x.name, x.kind, x.exact_class, x.twin_class, x.twin_round,
        c.origin, coalesce(c.module, '') as module, coalesce(c.aux_rule, '') as aux, coalesce(c.decl, '') as decl,
        coalesce(c.file, '') as file, coalesce(c.start_line, 0) as start_line
        from read_csv('{dd}/dupes-constants.tsv', delim='\\t', header=true, quote='', escape='', sample_size=-1) x
        join {rel(find_table(ds, 'constants'))} c on c.id = x.id""")
    con.execute(f"""create table dl as select decl, file, start_line, lines from {rel(find_table(ds, 'decls'))}
        where origin = 'project' and decl <> ''""")
    gen_rows = not_generated(con, find_table(ds, 'decls'))
    con.execute(f"""create table dgen as select decl, file, start_line from {rel(find_table(ds, 'decls'))}
        where origin = 'project' and decl <> '' {'and coalesce(cast(generated as boolean), false)' if gen_rows else 'and false'}""")
    con.execute("""create table s as select d.*, coalesce(dl.lines, 0) as lines from d left join dl
        on d.decl = dl.decl and d.file = dl.file and d.start_line = dl.start_line
        where d.origin = 'project' and d.aux = 'self'""")
    con.execute("delete from s where (decl, file, start_line) in (select decl, file, start_line from dgen)")
    S = {'dataset': os.environ.get('RHS_DATASET_VERSION', os.path.basename(os.path.normpath(ds)))}
    S['constants'] = dict(con.execute("""select kind || '/' || case when origin = 'project' then 'project' else 'other' end, count(*)
        from d group by all""").fetchall())
    S['source_level'] = dict(con.execute("select kind, count(*) from s group by kind").fetchall())

    def keep_cost(cls, kind):
        """Per cluster: min over members k of the lines of members reachable from k inside the cluster; and the number
        of members that directly reference another member (restatements)."""
        m = con.execute(f"""select s.id, s.{cls}, s.lines from s join (select {cls} c from s where kind = '{kind}'
            group by {cls} having count(*) > 1) x on x.c = s.{cls} where s.kind = '{kind}' order by s.id""").fetchnumpy()
        node = cnode[m['id']]
        clus_of = {}
        for nd, c in zip(node, m[cls]):
            clus_of[int(nd)] = int(c)
        lines_of = {int(nd): int(l) for nd, l in zip(node, m['lines'])}
        nodes = np.array(sorted(clus_of), dtype=np.int64)
        inset = np.zeros(int(max(E.max(), nodes.max() if len(nodes) else 0)) + 1, dtype=bool); inset[nodes] = True
        k = inset[E[:, 0]] & inset[E[:, 1]]
        adj = {}
        for a, b in E[k]:
            a, b = int(a), int(b)
            if clus_of[a] == clus_of[b]:
                adj.setdefault(a, []).append(b)
        members = {}
        for nd, c in clus_of.items():
            members.setdefault(c, []).append(nd)
        cost, wrappers = {}, {}
        for c, ms in members.items():
            best = None
            for k0 in ms:
                seen, st = {k0}, [k0]
                while st:
                    u = st.pop()
                    for v in adj.get(u, ()):
                        if v not in seen:
                            seen.add(v); st.append(v)
                cst = sum(lines_of[x] for x in seen)
                best = cst if best is None else min(best, cst)
            cost[c] = best
            wrappers[c] = sum(1 for x in ms if adj.get(x))
        return cost, wrappers

    def clusters(cls, kind, name):
        """Clusters (size >= 2) of source-level project constants of `kind` by class column `cls`."""
        cost, wrappers = keep_cost(cls, kind)
        import pyarrow as pa
        con.register('kc', pa.table({'cls': pa.array(list(cost), pa.int64()), 'keep': pa.array(list(cost.values()), pa.int64()),
                                     'wrap': pa.array([wrappers[c] for c in cost], pa.int64())}))
        con.execute(f"""create or replace table cl as select {cls} as cls, count(*) as n, sum(lines) as lines,
            min(lines) as min_lines from s where kind = '{kind}' group by {cls} having count(*) > 1""")
        con.execute("create or replace table cl as select cl.*, kc.keep, kc.wrap from cl join kc on kc.cls = cl.cls")
        con.execute(f"""create or replace table clm as select {cls} as cls, count(*) filter (where origin <> 'project') as nm,
            string_agg(name, ' ' order by id) filter (where origin <> 'project') as mnames from d group by {cls}""")
        r = con.execute("""select count(*), coalesce(sum(n), 0), coalesce(sum(lines - keep), 0), coalesce(sum(lines), 0),
            count(*) filter (where (select nm from clm where clm.cls = cl.cls) > 0), coalesce(sum(wrap), 0),
            coalesce(sum(lines - min_lines), 0) from cl""").fetchone()
        S[name] = {'clusters': r[0], 'declarations': int(r[1]), 'redundant_lines': int(r[2]), 'lines': int(r[3]),
                   'clusters_with_a_mathlib_or_core_twin': r[4], 'members_restating_another_member': int(r[5]),
                   'lines_minus_shortest_member_ignoring_reuse': int(r[6])}
        rows = con.execute(f"""select cl.cls, cl.n, cl.lines - cl.keep as redundant, coalesce(clm.nm, 0), coalesce(clm.mnames, ''),
            s.name, s.file, s.start_line, s.lines from cl join s on s.{cls} = cl.cls and s.kind = '{kind}'
            left join clm on clm.cls = cl.cls order by redundant desc, cl.cls, s.lines desc, s.name""").fetchall()
        return rows

    hdr = ['cluster', 'cluster_size', 'cluster_redundant_lines', 'mathlib_or_core_twins', 'twin_names', 'decl', 'file',
           'start_line', 'lines']
    st = clusters('exact_class', 'theorem', 'statements_exact')
    write_tsv(os.path.join(data, 'duplicates-statements.tsv'), hdr, st)
    df = clusters('exact_class', 'def', 'definitions_exact')
    write_tsv(os.path.join(data, 'duplicates-definitions.tsv'), hdr, df)
    # direct reuse candidates: source-level project constants with an identical Mathlib/core constant
    mm = con.execute("""select s.name, s.kind, s.file, s.start_line, s.lines, d.name, d.origin, d.module
        from s join d on d.exact_class = s.exact_class and d.origin <> 'project' and d.kind = s.kind
        order by s.kind, s.lines desc, s.name, d.name""").fetchall()
    write_tsv(os.path.join(data, 'duplicates-mathlib.tsv'),
              ['decl', 'kind', 'file', 'start_line', 'lines', 'twin', 'twin_origin', 'twin_module'], mm)
    S['mathlib_twins_exact'] = {k: v for k, v in con.execute("""select s.kind, count(distinct s.id) from s join d
        on d.exact_class = s.exact_class and d.origin <> 'project' and d.kind = s.kind group by s.kind""").fetchall()}
    S['mathlib_twins_exact_lines'] = con.execute("""select coalesce(sum(lines), 0) from s where exists (select 1 from d
        where d.exact_class = s.exact_class and d.origin <> 'project' and d.kind = s.kind)""").fetchone()[0]
    # up to renaming: clusters that are larger than the exact ones
    st3 = clusters('twin_class', 'theorem', 'statements_up_to_renaming')
    df3 = clusters('twin_class', 'def', 'definitions_up_to_renaming')
    S['mathlib_twins_up_to_renaming'] = {k: v for k, v in con.execute("""select s.kind, count(distinct s.id) from s join d
        on d.twin_class = s.twin_class and d.origin <> 'project' and d.kind = s.kind group by s.kind""").fetchall()}
    write_tsv(os.path.join(data, 'duplicates-renaming.tsv'), hdr + ['kind'],
              [r + ('theorem',) for r in st3] + [r + ('def',) for r in df3])
    # all project theorem constants (helpers included)
    S['all_project_theorem_constants'] = dict(zip(['constants', 'in_clusters', 'clusters'], con.execute("""
        select (select count(*) from d where origin = 'project' and kind = 'theorem'),
               coalesce(sum(n), 0), count(*) from (select exact_class, count(*) n from d where origin = 'project'
               and kind = 'theorem' group by exact_class having count(*) > 1)""").fetchone()))
    S['alt_named_in_statement_clusters'] = sum(1 for r in st if '_alt' in r[5])
    S['top_statement_clusters'] = []
    seen = []
    for r in st:
        if r[0] not in seen:
            seen.append(r[0])
        if len(seen) > 15:
            break
    for c in seen[:15]:
        mem = [r for r in st if r[0] == c]
        S['top_statement_clusters'].append({'size': mem[0][1], 'redundant_lines': int(mem[0][2]), 'mathlib_twins': mem[0][4],
                                            'members': [(r[5], f'{r[6]}:{r[7]}', int(r[8])) for r in mem[:6]]})
    stats = open(os.path.join(dd, 'dupes-stats.txt')).read().splitlines()
    S['rounds'] = [l for l in stats if l.startswith('round') or 'exact classes' in l or 'WARNING' in l or 'unstable' in l]
    write_json(os.path.join(dd, 'duplicates-summary.json'), S)
    log({k: S[k] for k in ('statements_exact', 'definitions_exact', 'mathlib_twins_exact', 'statements_up_to_renaming',
                           'definitions_up_to_renaming')})


if __name__ == '__main__':
    main()
