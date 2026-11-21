#!/usr/bin/env python3
"""Route B mapping: constants -> folded top-level declaration -> file/line, from the compiled declaration ranges.
Usage: routeb_map.py ROUTEB_PREFIX PROJECT_ROOTS IN_CONE_TSV OUT
  PROJECT_ROOTS: comma-separated first components of project modules (e.g. Main,Statements,Development,...)
  IN_CONE_TSV: names in the cone (first column, header), e.g. routeb_closure's routeb-closure.tsv
Folding: a constant without a range takes the range of its longest dotted prefix that has one in the same module
(`foo._proof_1` -> `foo`); a ranged constant whose range lies inside another constant's range in the same file
(fields, constructors, `where` helpers) folds into the outermost one."""
import collections, json, os, re, sys

RB, ROOTS, CONE, OUT = sys.argv[1], set(sys.argv[2].split(',')), sys.argv[3], sys.argv[4]
os.makedirs(OUT, exist_ok=True)
mods = {}
with open(RB + '.modules.tsv') as f:
    f.readline()
    for line in f:
        i, m = line.rstrip('\n').split('\t')
        mods[i] = m
in_cone = set()
with open(CONE) as f:
    f.readline()
    for line in f:
        in_cone.add(line.rstrip('\n').split('\t')[0])
C = []  # (name, module, kind, start, end)
rng = {}
allmod = {}  # every constant -> module (for cross-module aux lemmas)
with open(RB + '.constants.tsv') as f:
    h = f.readline().rstrip('\n').split('\t'); H = {x: i for i, x in enumerate(h)}
    for line in f:
        p = line.rstrip('\n').split('\t')
        m = mods.get(p[H['module_idx']], '')
        allmod[p[H['name']]] = m
        if m.split('.')[0] not in ROOTS:
            continue
        s, e = p[H['start_line']], p[H['end_line']]
        C.append((p[H['name']], m, p[H['kind']], int(s) if s else None, int(e) if e else None))
        if s:
            rng[p[H['name']]] = (m, int(s), int(e))
# outermost-range folding per file
by_file = collections.defaultdict(list)
for n, (m, s, e) in rng.items():
    by_file[m].append((s, -e, n))
top = {}
for m, lst in by_file.items():
    lst.sort()
    cur = None
    for s, ne, n in lst:
        e = -ne
        if cur is not None and s >= cur[0] and e <= cur[1]:
            top[n] = cur[2]
        else:
            cur = (s, e, n)
            top[n] = n
PRIV = re.compile(r'^(_private\..+?\.0)\.')
# auxiliary lemmas/matchers Lean shares between declarations: in the cone without making their generator "used"
SHARED = re.compile(r'^(_proof_\d+(_\d+)*|proof_\d+|_simp_\d+(_\d+)*|_abel_\d+(_\d+)*|_aux_\d+(_\d+)*|_?match_\d+(_\d+)*|_auxLemma|\d+)$')
stats = collections.Counter()
decl_consts = collections.Counter()
decl_cone = collections.Counter()
decl_shared = collections.Counter()
out = open(os.path.join(OUT, 'routeb-constants-mapped.tsv'), 'w')
out.write('name\tmodule\tkind\tdecl\trule\tshared_aux\tfile\tstart_line\tend_line\tin_cone\n')
for n, m, k, s, e in C:
    if n in rng and rng[n][0] == m:
        d, rule = top[n], 'self' if top[n] == n else 'nested'
    else:
        comps = n.split('.')
        pm = PRIV.match(n)
        floor = len(pm.group(1).split('.')) + 1 if pm else 1
        d = None
        for j in range(len(comps) - 1, floor - 1, -1):
            pre = '.'.join(comps[:j])
            if pre in rng and rng[pre][0] == m:
                d = top[pre]; break
        rule = 'prefix' if d else 'unranged'
        if d is None and pm:  # aux of a public declaration, named privately
            pc = n[len(pm.group(1)) + 1:].split('.')
            for j in range(len(pc) - 1, 0, -1):
                pre = '.'.join(pc[:j])
                if pre in rng and rng[pre][0] == m:
                    d = top[pre]; rule = 'private-aux'; break
        if d is None:  # aux lemma generated here for a declaration from another module
            pc = (n[len(pm.group(1)) + 1:] if pm else n).split('.')
            for j in range(len(pc) - 1, 0, -1):
                pre = '.'.join(pc[:j])
                if pre in rng:
                    d = top[pre]; rule = 'cross-module-aux'; break
                if pre in allmod:
                    rule = 'dep-aux'; break
            else:
                if pc[-1].startswith('_auto'):
                    rule = 'module-aux'
    shared = False
    if d and rule != 'self' and rule != 'nested':
        dp = d[len(PRIV.match(d).group(1)) + 1:] if PRIV.match(d) else d
        body = n[len(PRIV.match(n).group(1)) + 1:] if PRIV.match(n) else n
        rest = body[len(dp) + 1:].split('.') if body.startswith(dp + '.') else body.split('.')[-1:]
        shared = bool(rest) and bool(SHARED.match(rest[0]))
    stats[rule + ('/shared' if shared else '')] += 1
    if d:
        dm, ds, de = rng[d]
        decl_consts[d] += 1
        decl_cone[d] += (n in in_cone) and not shared
        decl_shared[d] += (n in in_cone) and shared
        f = 'src/' + dm.replace('.', '/') + '.lean'
        out.write(f'{n}\t{m}\t{k}\t{d}\t{rule}\t{int(shared)}\t{f}\t{ds}\t{de}\t{int(n in in_cone)}\n')
    else:
        out.write(f'{n}\t{m}\t{k}\t\t{rule}\t0\t\t\t\t{int(n in in_cone)}\n')
out.close()
kind = {n: k for n, m, k, s, e in C}
with open(os.path.join(OUT, 'keep-v1.tsv'), 'w') as f:
    f.write('decl\tkind\tfile\tstart_line\tend_line\tn_constants_in_cone\n')
    rows = []
    for d, c in decl_cone.items():
        if c:
            m, s, e = rng[d]
            rows.append((d, kind.get(d, '?'), 'src/' + m.replace('.', '/') + '.lean', s, e, c))
    rows.sort(key=lambda r: (r[2], r[3]))
    for r in rows:
        f.write('\t'.join(map(str, r)) + '\n')
res = {'project_constants': len(C), 'with_range': len(rng), 'fold_rules': dict(stats),
       'top_level_decls': sum(1 for n in rng if top[n] == n), 'top_level_decls_in_cone': sum(1 for d, c in decl_cone.items() if c),
       'project_constants_in_cone': sum(1 for n, *_ in C if n in in_cone),
       'decls_in_cone_only_via_shared_aux': sum(1 for d, c in decl_shared.items() if c and not decl_cone[d])}
json.dump(res, open(os.path.join(OUT, 'routeb-map-stats.json'), 'w'), indent=1)
print(json.dumps(res, indent=1))
