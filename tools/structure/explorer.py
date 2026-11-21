"""Deliverable 5: docs/explorer.html, a self-contained page (no network) with treemaps by directory and by dominator
subtree, and the statement-cone list. Data only: names, paths, line numbers, counts.

usage: explorer.py DATASET_DIR OUT_DIR HTML_PATH
"""
import html, json, os, sys
import numpy as np
from common import *

HERE = os.path.dirname(os.path.abspath(__file__))
DIR_MIN_FRAC = float(os.environ.get('RHS_EXPLORER_DIR_MIN_FRAC', '0.00005'))   # smallest directory/file tile, share of all lines
MAX_KIDS = int(os.environ.get('RHS_EXPLORER_MAX_KIDS', '120'))
DOM_MIN_LINES = int(os.environ.get('RHS_EXPLORER_DOM_MIN_LINES', '1500'))
STMT_CAP = int(os.environ.get('RHS_EXPLORER_STMT_CAP', '20000'))
ARCHIVE_LINES = 17374215  # newline count over the archive's 175,187 .lean files


def lib_of(path):
    p = str(path).split('/')
    return p[1].replace('.lean', '') if len(p) > 1 else str(path)


def prune(kids, minsize):
    """Keep the largest children (by all lines) at or above `minsize` lines, at most MAX_KIDS; lump the rest."""
    kids.sort(key=lambda c: (-c['L'], c['n']))
    keep = [c for c in kids[:MAX_KIDS] if c['L'] >= minsize]
    rest = kids[len(keep):]
    if rest:
        lump = {'n': f'{len(rest)} smaller entries', 'lump': 1, 'lib': -1}
        for k in ('l', 'd', 'L', 'D', 'k'):
            lump[k] = sum(c.get(k, 0) for c in rest)
        keep.append(lump)
    return keep


def dir_tree(con, ds, libs):
    pd = find_table(ds, 'decls')
    if 'type_dag' in cols(con, pd):
        ksize = 'coalesce(type_dag, 0) + coalesce(value_dag, 0)'
        src = rel(pd)
    else:   # from v0.2 the kernel term size lives on constants only: the declaration's own constants (aux_rule self)
        src = f"""(select d.*, k.k from {rel(pd)} d left join (select decl, file, start_line,
            sum(coalesce(type_dag, 0) + coalesce(value_dag, 0)) as k from {rel(find_table(ds, 'constants'))}
            where origin = 'project' and aux_rule = 'self' group by all) k using (decl, file, start_line))"""
        ksize = 'coalesce(k, 0)'
    q = con.execute(f"""select file, coalesce(lines, 0) as lines, cast(in_cone as int) as ic, {ksize} as k
        from {src} where origin = 'project' and decl <> '' and file is not null
        {not_generated(con, pd)} order by file, decl""").fetchnumpy()
    root = {'n': 'src', 'p': 'src', 'c': {}, 'l': 0, 'd': 0, 'L': 0, 'D': 0, 'k': 0}
    for f, ln, ic, k in zip(q['file'], q['lines'], q['ic'], q['k']):
        node, path = root, 'src'
        chain = [root]
        for part in str(f).split('/')[1:]:
            path += '/' + part
            node = node['c'].setdefault(part, {'n': part, 'p': path, 'c': {}, 'l': 0, 'd': 0, 'L': 0, 'D': 0, 'k': 0})
            chain.append(node)
        for nd in chain:
            nd['L'] += int(ln); nd['D'] += 1
            if ic:
                nd['l'] += int(ln); nd['d'] += 1; nd['k'] += int(k)
    minsize = DIR_MIN_FRAC * root['L']

    def finish(nd):
        lb = lib_of(nd['p'])
        nd['lib'] = libs.index(lb) if lb in libs else -1
        kids = list(nd['c'].values())
        if not kids:
            del nd['c']
            return nd
        nd['c'] = prune(kids, minsize)
        for c in nd['c']:
            if not c.get('lump'):
                finish(c)
        return nd
    return finish(root)


def dom_tree(D, libs, prereq):
    n = len(D['node'])
    idom = D['idom_node'].astype(np.int64)
    kids = {}
    for v in range(1, n):
        kids.setdefault(int(idom[v]), []).append(v)
    for v in kids:
        kids[v].sort(key=lambda c: -D['sub_lines'][c])
    sub_l, own = D['sub_lines'], D['lines']
    # main library of each subtree: bottom-up sums of lines by library
    lib_code = np.array([libs.index(lib_of(f)) if lib_of(f) in libs else len(libs) for f in D['file']], dtype=np.int64)
    order = np.argsort(D['dom_depth'], kind='stable')
    acc = np.zeros((n, len(libs) + 1), dtype=np.int64)
    acc[np.arange(n), lib_code] = own
    for v in order[::-1]:
        if v:
            acc[idom[v]] += acc[v]
    d2 = ['/'.join(str(f).split('/')[:3]) for f in D['file']]
    t2 = {}
    for k, ln in zip(d2[1:], own[1:]):
        if k:
            t2[k] = t2.get(k, 0) + int(ln)
    dirs2 = [k for k, _ in sorted(t2.items(), key=lambda x: (-x[1], x[0]))][:12]
    code2 = np.array([dirs2.index(k) if k in dirs2 else len(dirs2) for k in d2], dtype=np.int64)
    acc2 = np.zeros((n, len(dirs2) + 1), dtype=np.int64)
    acc2[np.arange(n), code2] = own
    for v in order[::-1]:
        if v:
            acc2[idom[v]] += acc2[v]
    dom_tree.dirs2 = dirs2

    def build(v):
        chain = []
        side = []
        cur = v
        while True:
            ks = kids.get(cur, [])
            if ks and sub_l[ks[0]] >= 0.9 * sub_l[cur]:
                side.extend(ks[1:])
                chain.append(cur)
                cur = ks[0]
            else:
                break
        # cur: bottom of the chain
        own_chain = sum(int(own[x]) for x in chain) + int(own[cur])
        allk = side + kids.get(cur, [])
        node = {'n': str(D['decl'][v]), 'f': str(D['file'][v]), 's': int(D['start_line'][v]), 'l': int(sub_l[v]),
                'd': int(D['sub_decls'][v]), 'k': int(D['sub_kernel_dag'][v]),
                'lib': int(np.argmax(acc[v][:len(libs)])) if acc[v][:len(libs)].sum() else -1,
                'x2': (lambda j: int(j) if j < len(dirs2) and acc2[v][j] > 0 else -1)(int(np.argmax(acc2[v]))),
                'x': (dirs2[int(np.argmax(acc2[v]))] if int(np.argmax(acc2[v])) < len(dirs2) else ''),
                'q': prereq.get((str(D['decl'][v]), str(D['file'][v]), int(D['start_line'][v])))}
        if node['q'] is None:
            del node['q']
        if chain:
            node['chain'] = [f'{D["decl"][x]}  ({D["file"][x]}:{D["start_line"][x]})' for x in chain[1:] + [cur]]
        if v == 0:
            node['n'] = 'the five theorems'
            del node['f'], node['s']
            if chain:
                node['n'] = 'the five theorems › ' + str(D['decl'][cur])
        big = [c for c in allk if sub_l[c] >= DOM_MIN_LINES]
        small = [c for c in allk if sub_l[c] < DOM_MIN_LINES]
        ch = [build(c) for c in big[:MAX_KIDS]]
        small += big[MAX_KIDS:]
        lump_l = sum(int(sub_l[c]) for c in small) + own_chain
        lump_d = sum(int(D['sub_decls'][c]) for c in small) + len(chain) + (1 if v != 0 else 0)
        lump_k = sum(int(D['sub_kernel_dag'][c]) for c in small) + sum(int(D['kernel_dag'][x]) for x in chain + [cur])
        if lump_l or lump_d:
            ch.append({'n': f'{len(small)} smaller subtrees + {len(chain) + (1 if v else 0)} merged declarations',
                       'lump': 1, 'lib': -1, 'x2': -1, 'l': lump_l, 'd': lump_d, 'k': lump_k})
        if ch:
            node['c'] = ch
        return node
    import sys as _s
    _s.setrecursionlimit(100000)
    return build(0)


def main():
    ds, out, html_path = sys.argv[1], sys.argv[2], sys.argv[3]
    version = os.environ.get('RHS_DATASET_VERSION', os.path.basename(os.path.normpath(ds)))
    con = connect()
    D = load_dom(con, out)
    S = json.load(open(os.path.join(out, 'dominators-summary.json')))
    # libraries ordered by lines in cone
    tl = {}
    for f, ln in zip(D['file'][1:], D['lines'][1:]):
        tl[lib_of(f)] = tl.get(lib_of(f), 0) + int(ln)
    libs = [k for k, _ in sorted(tl.items(), key=lambda x: (-x[1], x[0])) if k][:12]
    prereq = {}
    p = os.path.join(out, 'top-subtrees.tsv')
    with open(p) as f:
        h = next(f).rstrip('\n').split('\t')
        for line in f:
            r = dict(zip(h, line.rstrip('\n').split('\t')))
            prereq[(r['decl'], r['file'], int(r['start_line']))] = int(r['prereq_project_decls'])
    dirt = dir_tree(con, ds, libs)
    domt = dom_tree(D, libs, prereq)
    # statement cone
    stmt = {'strict': [], 'defs': [], 'counts': {}}
    for key, fn in (('strict', 'statement-cone-strict.tsv'), ('defs', 'statement-cone-with_definitions.tsv')):
        pth = os.path.join(out, 'concepts', fn)
        rows = []
        if os.path.exists(pth):
            with open(pth) as f:
                next(f)
                for line in f:
                    a = line.rstrip('\n').split('\t')
                    rows.append([a[0], a[1], a[2], int(a[3]), int(a[4])])
        stmt['counts'][key] = len(rows)
        stmt[key] = rows[:STMT_CAP]
    cs = {}
    cp = os.path.join(out, 'concepts', 'concepts-summary.json')
    if os.path.exists(cp):
        cs = json.load(open(cp))
    roots = ', '.join(f'<code>{html.escape(r)}</code>' for r in cs.get('roots', []))
    stmt['intro'] = ('Project declarations a reader meets in the statements of the five theorems and of the key chain '
                     f'({roots}). <em>Statements only</em> follows the types; <em>With definitions</em> also unfolds every '
                     'definition those types use, but still only the statements of theorems. Depth is the number of steps '
                     'from a root.')
    tot = S['totals']
    meta = {
        'facts': [(f'{S.get("node_kinds", {}).get("source declarations", tot["decls"]):,}', 'project declarations in the cone'),
                  (f'{tot["lines"]:,}', 'lines inside them'),
                  (f'{tot["lines"] / ARCHIVE_LINES * 100:.0f}%', f'of the archive\'s {ARCHIVE_LINES:,} lines'),
                  (f'{S["max_dom_depth"]}', 'dominator tree depth'), (version, 'dataset')],
        'notes': [
            f'Dataset {html.escape(version)}. It holds everything the proof terms of the five theorems use, read from '
            'Sutherland\'s kernel export. Each project constant is counted with the source declaration it belongs to. '
            'Lines are the lines inside those declarations, not whole files.',
            'A declaration A dominates B when every path from the five theorems to B goes through A. So B is used only '
            'through A, and each subtree can be read on its own, taking its prerequisites as given.',
            '<em>Kernel DAG</em> is the size of a declaration\'s kernel terms. Mathlib and Lean core are not shown.',
            'Names in this project are often misleading. The page shows paths and counts, not what a directory is about.',
        ],
    }
    data = {'meta': meta, 'libs': libs, 'dirs2': [d[4:] for d in dom_tree.dirs2], 'dir': dirt, 'dom': domt, 'stmt': stmt}
    blob = json.dumps(data, separators=(',', ':'), ensure_ascii=False).replace('</', '<\\/')
    tpl = open(os.path.join(HERE, 'explorer_template.html')).read()
    page = '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n' + \
        tpl.replace('__DATA__', blob).replace('<div class="wrap">', '</head>\n<body>\n<div class="wrap">', 1) + '\n</body>\n</html>\n'
    os.makedirs(os.path.dirname(os.path.abspath(html_path)), exist_ok=True)
    with open(html_path, 'w') as f:
        f.write(page)
    log('wrote', html_path, f'{len(page) / 1e6:.1f} MB')


if __name__ == '__main__':
    main()
