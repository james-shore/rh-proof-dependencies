#!/usr/bin/env python3
"""Route A mapping: export constants -> origin -> folded source declaration -> file/line; per-directory used fraction.

Inputs (paths as args): constants.tsv (export-parse), deps-constants.tsv (origin.lean), decl-index.tsv.gz (decl_index.py),
mirror root. Writes into OUT: constants-mapped.tsv, decls.tsv, directories.tsv, keep-v0.tsv, fold-stats.json.
"""
import collections, gzip, json, os, re, sys

CONSTS, DEPS, INDEX, MIRROR, OUT = sys.argv[1:6]
os.makedirs(OUT, exist_ok=True)
THMS = ['riemannHypothesis', 'riemannZeta_eq_zero_re', 'LFunction_ne_zero_of_half_lt_re', 'LFunction_eq_zero_re',
        'LFunction_eq_zero_re_of_isPrimitive']
# modules that are executables or tools, never imported by Main
NOT_IMPORTED = re.compile(r'^(Cache(\.|$)|LakeMain$|LeanChecker$|LeanIR$|Leanc$)')
KIND_MAP = {'defn': 'def', 'thm': 'theorem', 'induct': 'inductive', 'ctor': 'constructor', 'rec': 'recursor'}

# ---------- import closure of Main (text: `import X` lines) ----------
src = os.path.join(MIRROR, 'src')
imports = {}
file_lines = {}
for root, _, files in os.walk(src):
    for fn in files:
        if not fn.endswith('.lean'):
            continue
        p = os.path.join(root, fn)
        rel = os.path.relpath(p, MIRROR)
        if rel == 'src/Challenge.lean':  # added by the mirror for Comparator; not part of the archive
            continue
        mod = os.path.relpath(p, src)[:-5].replace(os.sep, '.')
        imps = []
        n = 0
        with open(p, encoding='utf-8') as f:
            for line in f:
                n += 1
                if line.startswith('import '):
                    imps.extend(line.split()[1:])
        imports[mod] = (rel, imps)
        file_lines[rel] = n
reach = set()
stack = ['Main']
while stack:
    m = stack.pop()
    if m in reach or m not in imports:
        continue
    reach.add(m)
    stack.extend(imports[m][1])
files_in_closure = {imports[m][0] for m in reach}

# ---------- deps ----------
deps = {}
with open(DEPS) as f:
    next(f)
    for line in f:
        name, module, package, kind, unsafe = line.rstrip('\n').split('\t')
        if NOT_IMPORTED.match(module):
            continue
        deps.setdefault(name, (module, package.lower(), KIND_MAP.get(kind, kind)))

# ---------- decl index ----------
rows = []
by_k = collections.defaultdict(list)
with gzip.open(INDEX, 'rt', encoding='utf-8') as f:
    hdr = f.readline().rstrip('\n').split('\t')
    H = {h: i for i, h in enumerate(hdr)}
    for line in f:
        r = line.rstrip('\n').split('\t')
        if r[H['file']] == 'src/Challenge.lean':
            continue
        rid = len(rows)
        rows.append(r)
        k = r[H['kernel_name']] or r[H['full_name']]
        if k:
            by_k[k].append(rid)
C = lambda r, c: r[H[c]]
top_of = []  # row -> top-level row (children fold into parent)
key_top = {}
for rid, r in enumerate(rows):
    key_top[(C(r, 'file'), C(r, 'full_name'))] = rid
for rid, r in enumerate(rows):
    par = C(r, 'parent')
    t = key_top.get((C(r, 'file'), par)) if par else None
    top_of.append(t if t is not None else rid)

def pick(cands):
    if len(cands) == 1:
        return cands[0], ''
    inc = [c for c in cands if C(rows[c], 'file') in files_in_closure]
    if len(inc) == 1:
        return inc[0], 'dup-resolved-by-imports'
    tops = {top_of[c] for c in (inc or cands)}
    if len(tops) == 1:
        return (inc or cands)[0], ''
    return (inc or cands)[0], 'ambiguous'

AUX = [
    (r'_proof_\d+(_\d+)*', 'proof'), (r'proof_\d+', 'proof'), (r'_simp_\d+(_\d+)*', 'simp'), (r'_abel_\d+(_\d+)*', 'abel'),
    (r'_aux_\d+(_\d+)*', 'aux'), (r'_?match_\d+(_\d+)*', 'match'), (r'eq_\d+|eq_def|eq_unfold|_unfold', 'eqn'),
    (r'rec|recOn|casesOn|below|brecOn|binductionOn|ibelow|ndrec|ndrecOn|noConfusion|noConfusionType|ctorIdx|toCtorIdx|ctorElim|ctorElimType|rec_\d+|below_\d+|brecOn_\d+|elim', 'inductive-aux'),
    (r'decEq|ofNat|ofNat_ctorIdx|toCtorIdx_ofNat|ofNat_toCtorIdx', 'derived'), (r'_auto_\d+|_autoParam', 'autoparam'),
    (r'to[A-Z].*', 'parent-proj'), (r'_arg_pusher', 'recursion'),
    (r'mk', 'ctor'), (r'injEq|inj|sizeOf_spec|_sizeOf_\d+|_sizeOf_inst|mk\.injEq', 'ctor-aux'),
    (r'congr_simp', 'congr'), (r'ext|ext_iff', 'ext'),
    (r'_unary|_binary|_mutual|_f|induct|mutual_induct|fun_cases|_sunfold|_unsafe_rec|_sparseCasesOn_\d+|fixedPoint|partial_correctness', 'recursion'),
    (r'_cstage\d|_closed_\d+|_lambda_\d+|_spec_\d+|_redArg|_boxed|_elambda_\d+', 'compiler'),
    (r'_auxLemma|\d+', 'numbered'),
]
AUXRE = [(re.compile('^(' + p + ')$'), k) for p, k in AUX]

def classify(suffix):
    s = suffix[0]
    for rx, k in AUXRE:
        if rx.match(s):
            return k
    return 'prefix-other'

PRIV = re.compile(r'^(_private\.(.+?)\.0)\.(.*)$')
# Lean reuses identically-typed auxiliary lemmas and matchers across declarations, so one of these can be in the cone
# while the declaration that first generated it is not; they don't make that declaration "used".
SHARED_AUX = {'proof', 'simp', 'abel', 'aux', 'match', 'numbered'}
TOMUL = [('addSubgroup', 'subgroup'), ('AddSubgroup', 'Subgroup'), ('addOrbit', 'orbit'), ('AddOrbit', 'Orbit'),
         ('AddFundamentalDomain', 'FundamentalDomain'), ('vadd', 'smul'), ('VAdd', 'SMul'), ('nsmul', 'pow'), ('sum', 'prod'),
         ('Sum', 'Prod'), ('zero', 'one'), ('Zero', 'One'), ('neg', 'inv'), ('Neg', 'Inv'), ('add', 'mul'), ('Add', 'Mul'),
         ('sub', 'div'), ('Sub', 'Div')]

def to_mul_variants(s):
    out = []
    t = s
    for a, b in TOMUL:
        t = t.replace(a, b)
        if a in s:
            out.append(s.replace(a, b))
    out.append(t)
    return [v for v in dict.fromkeys(out) if v != s]

TOK = re.compile(r'[A-Z][a-z0-9]*|[a-z0-9]+|[^\x00-\x7f]+')
def toks(s):
    return {t.lower() for t in TOK.findall(s)}
def sig_toks(sig):
    out = set()
    for ident in re.findall(r"[A-Za-z_\u0080-\uffff][A-Za-z0-9_'.\u0080-\uffff]*", sig):
        out |= toks(ident.split('.')[-1])
    return out


def resolve(name):
    """-> (row id or None, rule, detail)"""
    if name in by_k:
        rid, note = pick(by_k[name])
        return rid, 'self', note
    comps = name.split('.')
    m = PRIV.match(name)
    floor = len(m.group(1).split('.')) + 1 if m else 1
    for k in range(len(comps) - 1, floor - 1, -1):
        pre = '.'.join(comps[:k])
        if pre in by_k:
            rid, note = pick(by_k[pre])
            return rid, classify(comps[k:]), note
    if m:  # private aux whose parent is indexed without the private prefix, or vice versa
        r2 = resolve(m.group(3))
        if r2[0] is not None:
            return r2[0], r2[1], 'private-prefix-dropped'
    if name.endswith('Cpt'):
        return None, 'cpt_copy', ''
    last = comps[-1]
    for suf in ('_assoc', '_apply', '_symm_apply', '_coe', '_def', '_iff'):
        if last.endswith(suf):
            pre = '.'.join(comps[:-1] + [last[:-len(suf)]])
            if pre in by_k:
                rid, note = pick(by_k[pre])
                return rid, 'attr-generated', note
    for variant in to_mul_variants(last):
        pre = '.'.join(comps[:-1] + [variant])
        if pre in by_k:
            rid, note = pick(by_k[pre])
            return rid, 'to_additive', note
    for k in range(len(comps) - 1, 0, -1):
        pre = '.'.join(comps[:k])
        if pre in deps:
            return None, 'dep-aux', pre
    return None, 'unmapped', ''

# ---------- constants ----------
stats = collections.Counter()
rule_counts = collections.Counter()
unmapped_samples = collections.defaultdict(list)
row_hits = collections.Counter()
row_hits_cone = collections.Counter()
row_shared_cone = collections.Counter()
row_depth = {}
row_consts = collections.defaultdict(list)
origin_counts = collections.Counter()
origin_in5 = collections.Counter()
mathlib_mods = collections.Counter()
kindmis = []
# pass 1: resolve every non-dep name; collect instance-shaped leftovers
first = {}
with open(CONSTS) as f:
    f.readline()
    for line in f:
        p = line.split('\t', 3)
        name, kind = p[1], p[2]
        d = deps.get(name)
        if d and d[2] == kind:
            continue
        first[name] = resolve(name)
used_rows = {r[0] for r in first.values() if r[0] is not None and r[1] == 'self'}
anon = collections.defaultdict(list)
for rid, r in enumerate(rows):
    if C(r, 'kind') in ('instance', 'derived_instance') and C(r, 'name_guessed') == '1' and rid not in used_rows:
        anon[C(r, 'namespace')].append(rid)
need = {}
for name, (rid, rule, note) in first.items():
    if rule not in ('prefix-other', 'unmapped'):
        continue
    m = PRIV.match(name)
    body = m.group(3) if m else name
    comps = body.split('.')
    j = next((i for i, x in enumerate(comps) if x.startswith('inst')), None)
    if j is None:
        continue
    need[name] = ('.'.join(comps[:j + 1]), comps[j + 1:], '.'.join(comps[:j]), m.group(2) if m else None)
pairs = []
for base, ns, mod in {(v[0], v[2], v[3]) for v in need.values()}:
    nsc = ns.split('.') if ns else []
    cands = []
    for k in range(len(nsc), -1, -1):
        cands = [c for c in anon.get('.'.join(nsc[:k]), []) if mod is None or C(rows[c], 'module') == mod]
        if cands:
            break
    bt = toks(base.split('.')[-1][4:])
    for c in cands:
        st = sig_toks(C(rows[c], 'sig'))
        g = toks((C(rows[c], 'full_name').split('.')[-1])[4:])
        score = len(bt & st) / max(1, len(bt)) + 0.5 * len(bt & g) / max(1, len(bt | g))
        pairs.append((score, base, c))
pairs.sort(key=lambda x: -x[0])
inst_assign = {}
taken = set()
for score, base, c in pairs:
    if base in inst_assign or c in taken:
        continue
    inst_assign[base] = (c, score)
    taken.add(c)
for name, (base, rest, ns, mod) in need.items():
    if base in inst_assign:
        c, score = inst_assign[base]
        first[name] = (c, 'instance-heuristic' if not rest else classify(rest), f'instance-heuristic:{score:.2f}')
stats['instance_bases_needing_match'] = len({v[0] for v in need.values()})
stats['instance_bases_matched'] = len(inst_assign)

out = open(os.path.join(OUT, 'constants-mapped.tsv'), 'w')
with open(CONSTS) as f:
    hdr_c = f.readline().rstrip('\n').split('\t')
    out.write('\t'.join(hdr_c[:3] + ['origin', 'module', 'decl', 'aux_rule', 'map_note', 'shared_aux', 'file', 'start_line', 'end_line'] + hdr_c[3:]) + '\n')
    for line in f:
        p = line.rstrip('\n').split('\t')
        name, kind, depth = p[1], p[2], int(p[12])
        d = deps.get(name)
        if d and d[2] != kind:
            kindmis.append((name, kind, d))
            d = None
        decl = rule = note = file = sl = el = ''
        if d:
            origin, module = d[1], d[0]
            if origin == 'mathlib':
                mathlib_mods[module] += depth >= 0
        else:
            origin = 'project'
            rid, rule, note = first[name]
            rule_counts[rule] += 1
            if rid is not None:
                t = top_of[rid]
                r = rows[t]
                decl, file, sl, el, module = C(r, 'kernel_name') or C(r, 'full_name'), C(r, 'file'), C(r, 'start_line'), C(r, 'end_line'), C(r, 'module')
                row_hits[t] += 1
                if depth >= 0 and rule in SHARED_AUX:
                    row_shared_cone[t] += 1
                elif depth >= 0:
                    row_hits_cone[t] += 1
                    row_depth[t] = min(row_depth.get(t, 1 << 30), depth)
                if note:
                    stats['note:' + note.split(':')[0]] += 1
                if rule == 'prefix-other' and len(unmapped_samples['prefix-other']) < 40:
                    unmapped_samples['prefix-other'].append(f'{name} -> {decl}')
            else:
                module = ''
                if rule == 'dep-aux':
                    decl = note
                    note = ''
                m = PRIV.match(name)
                if m:
                    module = m.group(2)
                if len(unmapped_samples[rule]) < 40:
                    unmapped_samples[rule].append(name)
        origin_counts[origin] += 1
        if depth >= 0:
            origin_in5[origin] += 1
        out.write('\t'.join(p[:3] + [origin, module, decl, rule, note, '1' if rule in SHARED_AUX else '0', file, sl, el] + p[3:]) + '\n')
out.close()

# ---------- decls / directories / keep ----------
NOCONST = {'example', 'notation', 'notation3', 'macro', 'infixl', 'infixr', 'infix', 'prefix', 'postfix', 'run_cmd', 'elab',
           'unif_hint', 'custom_cmd', 'syntax', 'macro_rules', 'elab_rules'}
dirs = collections.defaultdict(lambda: collections.Counter())
dir_files = collections.defaultdict(set)
dir_files_cone = collections.defaultdict(set)
decl_kind_total = collections.Counter()
decl_kind_cone = collections.Counter()
keep = []
dout = open(os.path.join(OUT, 'decls.tsv'), 'w')
dout.write('decl\tkind\tmodule\tfile\tstart_line\tend_line\tlines\tn_constants\tn_constants_in_cone\tin_cone\tdepth\tin_import_closure\tn_shared_aux_in_cone\n')

def dkeys(file):
    parts = file.split('/')  # src/A/B/...
    ks = [('/'.join(parts[:2]), 1)]
    if len(parts) > 3:
        ks.append(('/'.join(parts[:3]), 2))
    return ks

for rid, r in enumerate(rows):
    if top_of[rid] != rid:
        continue
    kind = C(r, 'kind')
    file = C(r, 'file')
    sl, el = int(C(r, 'start_line')), int(C(r, 'end_line'))
    nl = el - sl + 1
    hits, hc = row_hits[rid], row_hits_cone[rid]
    incone = hc > 0
    dname = C(r, 'kernel_name') or C(r, 'full_name')
    dout.write(f"{dname}\t{kind}\t{C(r, 'module')}\t{file}\t{sl}\t{el}\t{nl}\t{hits}\t{hc}\t{int(incone)}\t{row_depth.get(rid, -1)}\t{int(file in files_in_closure)}\t{row_shared_cone[rid]}\n")
    if not incone and row_shared_cone[rid]:
        stats['decls_with_only_shared_aux_in_cone'] += 1
    if kind in NOCONST:
        continue
    decl_kind_total[kind] += 1
    decl_kind_cone[kind] += incone
    for dk in dkeys(file):
        c = dirs[dk]
        c['decls'] += 1
        c['decls_in_cone'] += incone
        dir_files[dk].add(file)
        if incone:
            c['lines_in_cone'] += nl
            dir_files_cone[dk].add(file)
    if incone:
        keep.append((dname, kind, file, sl, el, hc))
dout.close()
for file, n in file_lines.items():
    for dk in dkeys(file):
        dirs[dk]['lines'] += n
        dirs[dk]['files_all'] += 1
        dirs[dk]['files_in_import_closure'] += file in files_in_closure
with open(os.path.join(OUT, 'directories.tsv'), 'w') as f:
    f.write('dir\tlevel\tfiles\tfiles_in_import_closure\tfiles_in_cone\tlines\tlines_in_cone\tdecls\tdecls_in_cone\tpct_decls_used\tpct_lines_used\n')
    for (dk, lvl), c in sorted(dirs.items()):
        pd = 100.0 * c['decls_in_cone'] / c['decls'] if c['decls'] else 0.0
        pl = 100.0 * c['lines_in_cone'] / c['lines'] if c['lines'] else 0.0
        f.write(f"{dk}\t{lvl}\t{c['files_all']}\t{c['files_in_import_closure']}\t{len(dir_files_cone[(dk, lvl)])}\t{c['lines']}\t{c['lines_in_cone']}\t{c['decls']}\t{c['decls_in_cone']}\t{pd:.2f}\t{pl:.2f}\n")
keep.sort(key=lambda x: (x[2], x[3]))
with open(os.path.join(OUT, 'keep-v0.tsv'), 'w') as f:
    f.write('decl\tkind\tfile\tstart_line\tend_line\tn_constants_in_cone\n')
    for k in keep:
        f.write('\t'.join(map(str, k)) + '\n')

files_cone = {k[2] for k in keep}
res = {
    'origin_counts_export': dict(origin_counts.most_common()),
    'origin_counts_five_theorem_closure': dict(origin_in5.most_common()),
    'kind_mismatch_vs_deps': [f'{a} ({b}) vs deps {c}' for a, b, c in kindmis],
    'project_constant_fold_rules': dict(rule_counts.most_common()),
    'notes': {k: v for k, v in stats.items()},
    'samples': {k: v for k, v in unmapped_samples.items()},
    'files_total': len(file_lines), 'lines_total': sum(file_lines.values()),
    'files_in_main_import_closure': len(files_in_closure),
    'lines_in_main_import_closure': sum(file_lines[x] for x in files_in_closure),
    'files_with_in_cone_decl': len(files_cone),
    'lines_of_files_with_in_cone_decl': sum(file_lines[x] for x in files_cone),
    'decls_total_by_kind': dict(decl_kind_total.most_common()), 'decls_in_cone_by_kind': dict(decl_kind_cone.most_common()),
    'decls_total': sum(decl_kind_total.values()), 'decls_in_cone': sum(decl_kind_cone.values()),
    'lines_in_cone_decl_spans': sum(k[4] - k[3] + 1 for k in keep),
    'mathlib_modules_in_cone': sum(1 for v in mathlib_mods.values() if v),
    'mathlib_top_modules': mathlib_mods.most_common(40),
}
json.dump(res, open(os.path.join(OUT, 'fold-stats.json'), 'w'), indent=1, ensure_ascii=False)
for k, v in res.items():
    if k not in ('samples', 'mathlib_top_modules'):
        print(k, ':', v)
