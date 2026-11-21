#!/usr/bin/env python3
"""Per-theorem closures by origin, folded-decl counts per theorem, Mathlib per-area usage. Args: constants-mapped.tsv deps-constants.tsv OUTJSON"""
import collections, json, re, sys
CM, DEPS, OUT = sys.argv[1:4]
THMS = ['riemannHypothesis', 'riemannZeta_eq_zero_re', 'LFunction_ne_zero_of_half_lt_re', 'LFunction_eq_zero_re', 'LFunction_eq_zero_re_of_isPrimitive']
NOT_IMPORTED = re.compile(r'^(Cache(\.|$)|LakeMain$|LeanChecker$|LeanIR$|Leanc$)')
per = [collections.Counter() for _ in THMS]
per_decl = [set() for _ in THMS]
all5_decl = set(); any_decl = set()
ml_used = collections.Counter(); ml_mods_used = collections.defaultdict(set)
kind_origin = collections.Counter()
depth_hist = collections.Counter()
with open(CM) as f:
    h = f.readline().rstrip('\n').split('\t'); H = {x: i for i, x in enumerate(h)}
    for line in f:
        p = line.rstrip('\n').split('\t')
        o = p[H['origin']]; mask = int(p[H['thm_mask']]); d = p[H['decl']]; fkey = (d, p[H['file']])
        own = p[H['shared_aux']] != '1'
        kind_origin[(o, p[H['kind']])] += 1
        depth_hist[(o, int(p[H['depth']]))] += 1
        for k in range(5):
            if mask >> k & 1:
                per[k][o] += 1
                if o == 'project' and p[H['file']] and own:
                    per_decl[k].add(fkey)
        if o == 'project' and p[H['file']] and own:
            any_decl.add(fkey)
            if mask == 31:
                all5_decl.add(fkey)
        if o == 'mathlib':
            mod = p[H['module']]; area = '.'.join(mod.split('.')[:2])
            ml_used[area] += 1; ml_mods_used[area].add(mod)
ml_total = collections.Counter(); ml_mods_total = collections.defaultdict(set)
with open(DEPS) as f:
    f.readline()
    for line in f:
        name, mod, pkg, kind, unsafe = line.rstrip('\n').split('\t')
        if pkg == 'mathlib' and not NOT_IMPORTED.match(mod):
            area = '.'.join(mod.split('.')[:2]); ml_total[area] += 1; ml_mods_total[area].add(mod)
areas = sorted(ml_total, key=lambda a: -ml_used[a])
res = {
    'per_theorem': {t: {'constants_by_origin': dict(per[k]), 'constants_total': sum(per[k].values()), 'project_decls': len(per_decl[k])} for k, t in enumerate(THMS)},
    'project_decls_any_theorem': len(any_decl), 'project_decls_all_five': len(all5_decl),
    'kind_by_origin': {f'{a}/{b}': c for (a, b), c in sorted(kind_origin.items())},
    'mathlib_areas': [(a, ml_used[a], ml_total[a], round(100 * ml_used[a] / ml_total[a], 2), len(ml_mods_used[a]), len(ml_mods_total[a])) for a in areas],
    'max_depth': max(d for (o, d) in depth_hist),
    'depth_hist_project': sorted((d, c) for (o, d), c in depth_hist.items() if o == 'project'),
}
json.dump(res, open(OUT, 'w'), indent=1)
print(json.dumps({k: v for k, v in res.items() if k not in ('mathlib_areas', 'depth_hist_project')}, indent=1))
print('mathlib areas (used constants, total constants, %, modules used, modules total):')
for r in res['mathlib_areas'][:40]: print(' ', r)
print('depth hist project:', res['depth_hist_project'])
