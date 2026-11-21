#!/usr/bin/env python3
"""Per literature theorem: the subtree it exclusively dominates (the dominator tree from tools/structure), nesting, and the union.
Usage: literature_dom.py DOMINATORS_TSV_GZ LITDIR LABELS_TSV OUT_TSV OUT_JSON
  LITDIR: published-statements.tsv, published-direct-proofs.tsv, literature-theorems.tsv (literature_map.py)
  LABELS_TSV: extra labelled items (label_source, label, statement_or_theorem name, proof names comma-separated or empty)
Source declarations in a subtree = nodes with kind != 'shared_aux' and lines > 0."""
import bisect, collections, csv, gzip, json, sys
DOM, LIT, LABELS, OUT, OUTJ = sys.argv[1:6]
csv.field_size_limit(1 << 30)
rows = list(csv.DictReader(gzip.open(DOM, 'rt', encoding='utf-8'), delimiter='\t'))
N = len(rows)
by_decl = collections.defaultdict(list)
for r in rows:
    by_decl[r['decl']].append(r)
# preorder arrays
order = sorted(rows, key=lambda r: int(r['tin']))
tins = [int(r['tin']) for r in order]
src_pref = [0]; line_pref = [0]
for r in order:
    is_src = r['kind'] != 'shared_aux' and int(r['lines'] or 0) > 0
    src_pref.append(src_pref[-1] + is_src)
    line_pref.append(line_pref[-1] + (int(r['lines'] or 0) if is_src else 0))
def span(r):
    a, b = int(r['tin']), int(r['tout'])
    i, j = bisect.bisect_left(tins, a), bisect.bisect_left(tins, b)
    return i, j
def sub(r):
    i, j = span(r)
    return src_pref[j] - src_pref[i], line_pref[j] - line_pref[i]
def node_of(name):
    rs = by_decl.get(name)
    return rs[0] if rs else None

items = []  # (source, label, statement, proofs[])
direct = collections.defaultdict(set)
for r in csv.DictReader(open(f'{LIT}/published-direct-proofs.tsv'), delimiter='\t'):
    direct['GlobalTheory.Published.' + r['short']].add(r['decl'] or r['proof'])
via = collections.defaultdict(set)
for r in csv.DictReader(open(f'{LIT}/literature-theorems.tsv'), delimiter='\t'):
    for s in (r['published'] or '').split(','):
        if s:
            for p in (r['proof_refs'] or '').split(','):
                if p:
                    via[s].add(p)
for r in csv.DictReader(open(f'{LIT}/published-statements.tsv'), delimiter='\t'):
    if not r['name']:
        continue
    items.append(('rh-trim docs/LITERATURE.md', 'published result', r['name'], sorted(direct[r['name']] | via[r['name']])))
for r in csv.DictReader(open(LABELS), delimiter='\t'):
    items.append((r['label_source'], r['label'], r['name'], [p for p in r['proofs'].split(',') if p]))

all_proof_nodes = {}
out = []
lit_items = []
for src, label, stmt, proofs in items:
    pn = [node_of(p) for p in proofs]
    pn = [x for x in pn if x is not None]
    measured = 'proof' if pn else ''
    if not pn and node_of(stmt) is not None and label != 'published result':
        pn = [node_of(stmt)]  # the labelled item itself (a theorem, or a def whose proof was not identified)
        measured = 'item itself (' + pn[0]['kind'] + ')'
    for x in pn:
        all_proof_nodes[x['node']] = x
    d = sum(sub(x)[0] for x in pn); l = sum(sub(x)[1] for x in pn)
    s = node_of(stmt)
    out.append(dict(label_source=src, label=label, statement=stmt,
                    statement_file=(s['file'] + ':' + s['start_line']) if s else '',
                    proofs=','.join(x['decl'] for x in pn), proof_files=','.join(f"{x['file']}:{x['start_line']}" for x in pn),
                    n_proofs=len(pn), exclusive_decls=d, exclusive_lines=l,
                    dom_depth=min((int(x['dom_depth']) for x in pn), default=-1), measured=measured))
# per label group: nesting (a proof node inside another's subtree) and the union of outermost subtrees
def group_of(o):
    return o['label_source'].split(' ')[0] + ' / ' + ('known theorem' if o['label'].startswith('known') else o['label'].split(' (')[0])
groups = collections.defaultdict(dict)
for o in out:
    for dname in [x for x in o['proofs'].split(',') if x]:
        r = node_of(dname)
        if r is not None:
            groups[group_of(o)][r['node']] = r
nested = {}
gres = {}
for g, nodes in groups.items():
    pn = sorted(nodes.values(), key=lambda r: int(r['tin']))
    stack = []; outer = []
    for r in pn:
        while stack and not (int(stack[-1]['tin']) <= int(r['tin']) < int(stack[-1]['tout'])):
            stack.pop()
        if stack:
            nested[(g, r['node'])] = stack[-1]['decl']
        else:
            outer.append(r)
        stack.append(r)
    gres[g] = {'proof_nodes': len(pn), 'nested': len(pn) - len(outer),
               'union_exclusive_decls': sum(sub(r)[0] for r in outer), 'union_exclusive_lines': sum(sub(r)[1] for r in outer)}
for o in out:
    g = group_of(o)
    o['nested_in'] = ','.join(sorted({nested[(g, node_of(x)['node'])] for x in o['proofs'].split(',') if x and node_of(x) is not None and (g, node_of(x)['node']) in nested}))
tot_d = src_pref[-1]; tot_l = line_pref[-1]
for g in gres.values():
    g['pct_decls'] = round(100 * g['union_exclusive_decls'] / tot_d, 2); g['pct_lines'] = round(100 * g['union_exclusive_lines'] / tot_l, 2)
with open(OUT, 'w') as f:
    cols = ['label_source', 'label', 'statement', 'statement_file', 'measured', 'n_proofs', 'proofs', 'proof_files',
            'exclusive_decls', 'exclusive_lines', 'dom_depth', 'nested_in']
    f.write('\t'.join(cols) + '\n')
    for o in sorted(out, key=lambda o: (o['label_source'], -o['exclusive_decls'])):
        f.write('\t'.join(str(o[c]) for c in cols) + '\n')
res = {'items': len(out), 'items_with_proof_node': sum(1 for o in out if o['n_proofs']),
       'cone_source_decls': tot_d, 'cone_source_lines': tot_l, 'groups': gres}
json.dump(res, open(OUTJ, 'w'), indent=1)
print(json.dumps(res, indent=1))
for o in sorted(out, key=lambda o: -o['exclusive_decls'])[:15]:
    print(o['exclusive_decls'], o['exclusive_lines'], o['label'], o['statement'], o['proofs'][:80], o['nested_in'][:60])
