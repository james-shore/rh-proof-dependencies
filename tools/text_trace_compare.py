#!/usr/bin/env python3
"""Compare a text-based dependency cone with the kernel cone, declaration by declaration.
Usage: text_trace_compare.py CONSTANTS_MAPPED DECLS THEIR_CONE THEIR_INDEX THM_BITS LABEL OUTDIR
  THEIR_CONE: one declaration name per line (optionally 'depth<TAB>name'); THEIR_INDEX: their declaration names
  (first column of a TSV). THM_BITS: a file of kernel (decl, file) pairs, or a kernel theorem mask to compare against (1 = riemannHypothesis only, 31 = any of five).
Names are compared without the `_private.<Module>.0.` prefix (text tools don't produce it)."""
import collections, json, os, re, sys
CM, DECLS, CONE, INDEX, BITS, LABEL, OUT = sys.argv[1:8]
KSET = None
if BITS.isdigit():
    BITS = int(BITS)
else:
    KSET = {tuple(l.rstrip('\n').split('\t')[:2]) for l in open(BITS) if not l.startswith('decl\t')}
    BITS = 0
os.makedirs(OUT, exist_ok=True)
PRIV = re.compile(r'^_private\..+?\.0\.')
NOCONST = {'example', 'notation', 'notation3', 'macro', 'infixl', 'infixr', 'infix', 'prefix', 'postfix', 'run_cmd', 'elab',
           'unif_hint', 'custom_cmd', 'syntax', 'macro_rules', 'elab_rules'}
mask = collections.Counter()
with open(CM) as f:
    h = f.readline().rstrip('\n').split('\t'); H = {x: i for i, x in enumerate(h)}
    for line in f:
        p = line.rstrip('\n').split('\t')
        if p[H['origin']] == 'project' and p[H['decl']] and p[H['shared_aux']] != '1':
            mask[(p[H['decl']], p[H['file']])] |= int(p[H['thm_mask']])
ours = {}  # plain name -> list of (decl, file, kind, in_kernel)
for line in open(DECLS):
    if line.startswith('decl\t'):
        continue
    p = line.rstrip('\n').split('\t')
    if p[1] in NOCONST:
        continue
    k = (p[0], p[3])
    ours.setdefault(PRIV.sub('', p[0]), []).append((p[0], p[3], p[1], (k in KSET) if KSET is not None else bool(mask.get(k, 0) & BITS)))
kernel = {n for n, rs in ours.items() if any(r[3] for r in rs)}
def names(path):
    out = set()
    for line in open(path, encoding='utf-8'):
        line = line.rstrip('\n')
        if not line:
            continue
        parts = line.split('\t')
        n = parts[1] if len(parts) > 1 and parts[0].isdigit() else parts[0]
        out.add(n.replace('«', '').replace('»', ''))
    return out
theirs = names(CONE)
tindex = names(INDEX)
both = theirs & kernel
miss = kernel - theirs
add = theirs - kernel
miss_not_indexed = {n for n in miss if n not in tindex}
miss_not_reached = miss - miss_not_indexed
add_unknown = {n for n in add if n not in ours}
add_known = add - add_unknown
kind_of = lambda n: ours[n][0][2] if n in ours else '?'
res = {
    'label': LABEL, 'kernel_theorem_bits': BITS,
    'kernel_cone_decls': len(kernel), 'their_cone_names': len(theirs), 'their_index_names': len(tindex),
    'both': len(both),
    'kernel_used_but_missing_from_theirs': len(miss),
    '  of which not in their index at all': len(miss_not_indexed),
    '  of which indexed but not reached': len(miss_not_reached),
    'in_theirs_but_not_kernel_used': len(add),
    '  of which a name we have no declaration for': len(add_unknown),
    '  of which a real declaration the kernel does not use': len(add_known),
    'missing_not_indexed_by_kind': dict(collections.Counter(kind_of(n) for n in miss_not_indexed).most_common()),
    'missing_not_reached_by_kind': dict(collections.Counter(kind_of(n) for n in miss_not_reached).most_common()),
    'added_known_by_kind': dict(collections.Counter(kind_of(n) for n in add_known).most_common()),
    'samples': {
        'missing_not_indexed': sorted(miss_not_indexed)[:15], 'missing_not_reached': sorted(miss_not_reached)[:15],
        'added_unknown': sorted(add_unknown)[:15], 'added_known': sorted(add_known)[:15]},
}
json.dump(res, open(os.path.join(OUT, f'{LABEL}-vs-kernel.json'), 'w'), indent=1, ensure_ascii=False)
with open(os.path.join(OUT, f'{LABEL}-vs-kernel.tsv'), 'w') as f:
    f.write('name\tstatus\tkind\tfile\n')
    for tag, s in (('missing_not_indexed', miss_not_indexed), ('missing_not_reached', miss_not_reached),
                   ('added_unknown', add_unknown), ('added_known', add_known)):
        for n in sorted(s):
            r = ours.get(n, [('', '', '?', False)])[0]
            f.write(f'{n}\t{tag}\t{r[2]}\t{r[1]}\n')
print(json.dumps({k: v for k, v in res.items() if k != 'samples'}, indent=1, ensure_ascii=False))
