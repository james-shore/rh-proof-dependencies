#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 (adapted from keithadler/rh-trim tools/reach.py; see NOTICE)
"""Text index of every declaration in a Lean 4 source tree (reads text, runs nothing).

Declaration matching and span/attribute handling adapted from keithadler/rh-trim tools/reach.py
(Apache-2.0, commit 73794d8f6531c856ae983b47daac500524478251); rewritten to track scopes,
modifiers, `in` prefixes, mutual blocks, fields, constructors and `where` auxiliaries.

usage: decl_index.py MIRROR_ROOT OUT.tsv.gz [--jobs N] [--summary OUT.md]
"""
import gzip, os, re, sys, time, collections
from multiprocessing import Pool

COLS = ['full_name', 'kernel_name', 'private', 'kind', 'module', 'file', 'start_line', 'decl_line',
        'end_line', 'attrs', 'parent', 'name_guessed', 'namespace', 'modifiers', 'in_prefix', 'notes', 'sig']

DECL_KW = {'theorem', 'lemma', 'def', 'abbrev', 'instance', 'structure', 'class', 'inductive',
           'opaque', 'axiom', 'example'}
SYNTAX_KW = {'macro', 'macro_rules', 'elab', 'elab_rules', 'syntax', 'notation', 'notation3', 'infix',
             'infixl', 'infixr', 'prefix', 'postfix', 'declare_syntax_cat', 'binder_predicate',
             'unif_hint', 'run_cmd', 'run_elab', 'run_meta', 'initialize', 'builtin_initialize',
             'add_decl_doc', 'deriving', 'alias', 'simproc', 'dsimproc', 'register_simp_attr'}
MODS = {'private', 'protected', 'noncomputable', 'unsafe', 'partial', 'nonrec', 'scoped', 'local',
        'public', 'meta'}
NODECL = {'import', 'open', 'end', 'namespace', 'section', 'variable', 'set_option', 'universe',
          'attribute', 'omit', 'include', 'export', 'unseal', 'seal', 'mutual', 'module', 'suppress_compilation',
          'assert_not_exists', 'library_note', 'compile_inductive'}
CONT = {'by', 'where', 'termination_by', 'decreasing_by', 'fun', 'with', 'then', 'else', 'do', 'at',
        'using', 'from', 'calc', 'show', 'have', 'in', 'λ', 'match', 'if', 'let', 'exact', 'refine'}
IN_PREFIX_KW = {'open', 'set_option', 'omit', 'include', 'variable', 'attribute', 'local', 'scoped', 'unseal'}

PART = r'(?:«[^»]*»|[^\s.:()\[\]{}⦃⦄⟨⟩⟦⟧,«»@;|"]+)'
DECLID = re.compile(r'(' + PART + r'(?:\.' + PART + r')*)(?:\.\{[^}]*\})?')
FIRSTTOK = re.compile(r'#?[^\s()\[\]{}⦃⦄⟨⟩,:;"@]+|@\[|/-|.')
WS = re.compile(r'\s+')
TOK = re.compile(r'[()\[\]{}⦃⦄⟨⟩⟦⟧⦅⦆]|:=|::|(?<!:):(?![=:])|(?<![\w\'.!?])where(?![\w\'!?])|\n[ \t]*\|(?![|])')
OPEN, CLOSE = set('([{⦃⟨⟦⦅'), set(')]}⦄⟩⟧⦆')
IDENT = re.compile(r"(?:«[^»]*»|[A-Za-z_À-ɏͰ-Ͽᴀ-ᶿ℀-⅏\U0001d400-\U0001d7ff]"
                   r"[A-Za-z0-9_'!?À-ɏͰ-Ͽᴀ-ᶿ₀-ₜ℀-⅏\U0001d400-\U0001d7ff]*)"
                   r"(?:\.(?:«[^»]*»|[A-Za-z_0-9À-ɏͰ-Ͽ℀-⅏\U0001d400-\U0001d7ff][^\s.:()\[\]{}⦃⦄⟨⟩,]*))*")
BINDER_NAMES = re.compile(r'[(\[{⦃]\s*((?:[^\s:()\[\]{}⦃⦄]+\s+)*[^\s:()\[\]{}⦃⦄]+)\s*:(?!=)')
NON_CONST = {'fun', 'λ', 'by', 'Type', 'Sort', 'Prop', 'in', 'let', 'have', 'show', 'from', 'if', 'then', 'else',
             'match', 'with', 'do', 'at', 'Type*', 'Sort*'}


def unesc(name):
    return name.replace('«', '').replace('»', '')


SPECIAL = re.compile(r"/-|--|\"|'")
CHARLIT = re.compile(r"'(?:\\.[^']*|[^'\\\n])'")


def clean_text(t):
    """Blank comments and string/char literal contents (same length, newlines kept)."""
    if '/-' not in t and '--' not in t and '"' not in t and "'" not in t:
        return t
    out = None
    i, n = 0, len(t)
    blanks = []
    while True:
        m = SPECIAL.search(t, i)
        if not m: break
        i = m.start(); s = m.group()
        if s == '/-':
            depth, j = 1, i + 2
            while j < n and depth:
                a = t.find('/-', j); b = t.find('-/', j)
                if b < 0: j = n; break
                if 0 <= a < b: depth += 1; j = a + 2
                else: depth -= 1; j = b + 2
            blanks.append((i, j)); i = j
        elif s == '--':
            j = t.find('\n', i); j = n if j < 0 else j
            blanks.append((i, j)); i = j
        elif s == '"':
            j = i + 1
            while j < n and t[j] != '"':
                j += 2 if t[j] == '\\' else 1
            blanks.append((i + 1, min(j, n))); i = j + 1
        else:
            prev = t[i - 1] if i else ' '
            cm = None if (prev.isalnum() or prev in "_'!?.") else CHARLIT.match(t, i)
            if cm:
                blanks.append((i + 1, cm.end() - 1)); i = cm.end()
            else:
                i += 1
    if not blanks: return t
    out = list(t)
    for a, b in blanks:
        for k in range(a, b):
            if out[k] != '\n': out[k] = ' '
    return ''.join(out)


def scan(text, start=0, stop=('assign', 'where', 'bar')):
    """First depth-0 token of the given kinds at/after start: (pos, kind, end) or (None, None, None)."""
    depth = 0
    for m in TOK.finditer(text, start):
        s = m.group()
        if s in OPEN or s[0] in OPEN:
            depth += 1
        elif s in CLOSE:
            depth = max(0, depth - 1)
        elif depth == 0:
            k = 'assign' if s == ':=' else 'where' if s == 'where' else 'colon' if s == ':' else \
                'dcolon' if s == '::' else 'bar'
            if k in stop:
                return m.start(), k, m.end()
    return None, None, None


def first_colon(text, end):
    """Position of the first depth-0 ':' in text[:end] (start of the type), or -1."""
    depth = 0
    for m in TOK.finditer(text, 0, end):
        s = m.group()
        if s in OPEN or s[0] in OPEN: depth += 1
        elif s in CLOSE: depth = max(0, depth - 1)
        elif depth == 0 and s == ':': return m.start()
    return -1


def parse_attrs(text, i):
    """Parse consecutive @[...] groups from i; return (list_of_attr_texts, new_i)."""
    attrs = []
    while True:
        while i < len(text) and text[i].isspace(): i += 1
        if not text.startswith('@[', i): return attrs, i
        depth, j = 0, i + 1
        while j < len(text):
            if text[j] == '[': depth += 1
            elif text[j] == ']':
                depth -= 1
                if depth == 0: break
            j += 1
        attrs.append(WS.sub(' ', text[i:j + 1]))
        i = j + 1


def top_binder_names(line):
    """Names in the depth-0 binder groups at the start of line: `(a b : T) {c : U}` -> [a, b, c]."""
    names, i = [], 0
    while i < len(line) and line[i] in '([{⦃':
        depth, j = 0, i
        while j < len(line):
            if line[j] in OPEN: depth += 1
            elif line[j] in CLOSE:
                depth -= 1
                if depth == 0: break
            j += 1
        m = re.match(r'[(\[{⦃]\s*((?:[^\s:()\[\]{}⦃⦄]+\s+)*[^\s:()\[\]{}⦃⦄]+)\s*:(?!=)', line[i:j + 1])
        if m: names += m.group(1).split()
        i = j + 1
        while i < len(line) and line[i] == ' ': i += 1
    return names


def capital(s):
    return s[:1].upper() + s[1:]


def guess_inst_name(type_text, local_names):
    """Approximation of Lean's instance-name generator: 'inst' + capitalised last components."""
    out, seen = [], set()
    for tok in re.findall(r'→|∀|Type\*?|Sort\*?|Prop|' + IDENT.pattern, type_text):
        if tok in ('→', '∀'):
            continue
        if tok in ('Type', 'Type*'): s = 'Type'
        elif tok in ('Sort', 'Sort*'): s = 'Sort'
        elif tok == 'Prop': s = 'Prop'
        else:
            tok = unesc(tok)
            if tok in NON_CONST: continue
            if tok.split('.')[0] in local_names:
                if '.' not in tok: continue
                tok = tok.split('.')[-1]          # generalized field notation `x.Foo`
            if tok in seen: continue
            seen.add(tok)
            s = capital(tok.split('.')[-1])
        out.append(s)
    return 'inst' + ''.join(out)


def body_lines(text, start):
    """Lines of text[start:] at the minimal indentation; yields (line_text_stripped, offset)."""
    seg = text[start:]
    first_nl = seg.find('\n')
    inline = seg[:first_nl] if first_nl >= 0 else seg
    res = []
    if inline.strip():
        res.append((inline.strip(), start))
    if first_nl < 0:
        return res
    lines, off = [], start + first_nl + 1
    for ln in seg[first_nl + 1:].split('\n'):
        if ln.strip():
            lines.append((len(ln) - len(ln.lstrip()), ln.strip(), off))
        off += len(ln) + 1
    if not lines:
        return res
    ind = min(l[0] for l in lines)
    res += [(s, o) for k, s, o in lines if k == ind]
    return res


def strip_mods(s):
    mods = []
    while True:
        a, j = parse_attrs(s, 0)
        if a: s = s[j:].lstrip()
        m = re.match(r'(private|protected|noncomputable|unsafe|partial|nonrec)\s+', s)
        if not m:
            return s, mods
        mods.append(m.group(1)); s = s[m.end():]


def structure_children(text, hdr_end, kind_at, base):
    """Fields, ctor and extends-projections of a structure/class. Returns list of (kind, name, notes, private)."""
    out = []
    ctor, ctor_priv = 'mk', False
    if kind_at == 'where':
        for line, _ in body_lines(text, hdr_end):
            line, mods = strip_mods(line)
            if not line: continue
            m = re.match(r'(' + PART + r')\s*::', line)
            if m:
                ctor, ctor_priv = unesc(m.group(1)), 'private' in mods
                line = line[m.end():].strip()
                if not line: continue
            if line.startswith('deriving'):
                out.append(('deriving', line, '', False)); continue
            if line[0] in '([{⦃':
                for nm in top_binder_names(line):
                    out.append(('field', unesc(nm), '', 'private' in mods))
                continue
            m = re.match(r'((?:' + PART + r'\s+)*' + PART + r')\s*(:=|:|\(|\{|\[|⦃|$)', line)
            if m:
                names = m.group(1).split()
                note = 'default-only (may override an inherited field)' if m.group(2) == ':=' else ''
                for nm in names:
                    out.append(('field', unesc(nm), note, 'private' in mods))
    return ctor, ctor_priv, out


def extends_parents(header):
    m = re.search(r'(?<![\w.])extends\s+(.*)$', header, re.S)
    if not m: return []
    parts, depth, cur = [], 0, ''
    for ch in m.group(1):
        if ch in OPEN: depth += 1
        elif ch in CLOSE: depth -= 1
        if ch == ',' and depth == 0:
            parts.append(cur); cur = ''
        else:
            cur += ch
    parts.append(cur)
    res = []
    for p in parts:
        p = p.strip()
        mm = re.match(r'(to' + PART + r')\s*:\s*(.*)', p, re.S)
        if mm:
            res.append(unesc(mm.group(1))); continue
        mm = IDENT.match(p)
        if mm:
            res.append('to' + unesc(mm.group(0)).split('.')[-1])
    return res


def inductive_ctors(text, hdr_end):
    """Constructor names: '|' at line starts (minimal indent) or inline after the header."""
    seg = text[hdr_end:]
    res = []
    nl = seg.find('\n')
    inline = seg if nl < 0 else seg[:nl]
    for m in re.finditer(r'\|\s*(?:(?:private|protected)\s+)?(' + PART + ')', inline):
        res.append(unesc(m.group(1)))
    bars = [(len(l) - len(l.lstrip()), l.strip()) for l in seg[nl + 1:].split('\n') if l.lstrip().startswith('|')] if nl >= 0 else []
    if bars:
        ind = min(b[0] for b in bars)
        for k, l in bars:
            if k != ind: continue
            for m in re.finditer(r'(?:^|\s)\|\s*(?:(?:private|protected)\s+)?(' + PART + ')', l):
                res.append(unesc(m.group(1)))
    deriv = [l.strip() for l in seg.split('\n') if l.strip().startswith('deriving')]
    return res, deriv


def where_aux(text, start):
    names = []
    for line, _ in body_lines(text, start):
        line, _m = strip_mods(line)
        if not line or line.startswith(('termination_by', 'decreasing_by', 'deriving', '|')): continue
        m = re.match(r'(?:theorem|lemma|def|abbrev|instance)?\s*(' + PART + r'(?:\.' + PART + r')*)', line)
        if m and m.group(1):
            names.append(unesc(m.group(1)))
    return names


def index_file(args):
    root, rel = args
    path = os.path.join(root, rel)
    t = open(path, encoding='utf-8').read()
    module = rel[len('src/'):-len('.lean')].replace('/', '.') if rel.startswith('src/') else rel[:-5].replace('/', '.')
    c = clean_text(t)
    lines = c.split('\n')
    orig_lines = t.split('\n')
    nlines = len(lines)
    if nlines and lines[-1] == '': nlines -= 1
    # line start offsets
    offs, o = [], 0
    for ln in lines:
        offs.append(o); o += len(ln) + 1
    stats = collections.Counter()
    warns = []
    # chunk starts: column-0 lines in code whose first token is not a continuation
    starts = []
    for i in range(nlines):
        ln = lines[i]
        o_ln = orig_lines[i]
        if o_ln.startswith('/-') and (i == 0 or lines[i - 1] == orig_lines[i - 1] or lines[i - 1].strip()):
            starts.append((i, 'doc' if o_ln.startswith('/--') else 'trivia'))     # a comment starts here
            continue
        if not ln or ln[0].isspace():
            continue
        m = FIRSTTOK.match(ln)
        tok = m.group() if m else ''
        if tok == '@[' or tok == '/-':
            starts.append((i, 'cmd'))
        elif tok in CONT or not (tok[:1].isalpha() or tok[:1] in '_#«'):
            continue
        else:
            starts.append((i, 'cmd'))
    trivia = [ (lines[i].strip() == '') for i in range(nlines) ]
    rows = []
    scopes = []        # (kind, [components])
    var_names = set()
    pending = []       # (line, kind, text) for attr/doc/in-prefix chunks
    priv_prefix = '_private.' + module + '.0.'

    def ns():
        return '.'.join(x for k, x in scopes if k == 'namespace')

    def chunk_end(idx):
        e = (starts[idx + 1][0] - 1) if idx + 1 < len(starts) else nlines - 1
        while e > starts[idx][0] and trivia[e]: e -= 1
        return e

    def emit(full, kernel, priv, kind, s_line, d_line, e_line, attrs, parent, guessed, mods, prefix, notes, sig):
        rows.append((full, kernel, priv, kind, module, rel, s_line, d_line, e_line, attrs, parent,
                     guessed, ns(), mods, prefix, notes, sig))

    for idx, (li, ck) in enumerate(starts):
        e = chunk_end(idx)
        if ck == 'trivia':
            continue
        if ck == 'doc':
            pending.append((li, 'doc', ''))
            continue
        text = c[offs[li]:offs[e] + len(lines[e])]
        attrs, p = parse_attrs(text, 0)
        rest = text[p:]
        rs = rest.strip()
        if not rs:
            pending.append((li, 'attr', ' '.join(attrs)))
            continue
        ft = FIRSTTOK.match(rs).group()
        # `open X in` / `set_option ... in` prefixes (on their own chunk)
        if ft in IN_PREFIX_KW and re.search(r'(?<![\w.])in$', rs):
            pending.append((li, 'in', WS.sub(' ', rs)))
            continue
        same_line_in = ''
        m = re.match(r'((?:open|set_option)\b[^\n]*?\s)in\s+(?=\S)', rs)
        if m:
            same_line_in = WS.sub(' ', m.group(0).strip()); rs = rs[m.end():]
            ft = FIRSTTOK.match(rs).group()
        # scope commands
        if ft in ('namespace', 'section', 'end', 'mutual') or rs.startswith('noncomputable section'):
            words = rs.split()
            if words[0] == 'noncomputable': words = words[1:]
            if words[0] in ('namespace', 'section'):
                comps = [unesc(x) for x in words[1].split('.')] if len(words) > 1 else [None]
                for x in comps:
                    scopes.append((words[0], x))
            elif words[0] == 'mutual':
                scopes.append(('mutual', None))
            elif words[0] == 'end':
                want = [unesc(x) for x in words[1].split('.')] if len(words) > 1 else [None]
                if len(scopes) < len(want):
                    stats['end_without_scope'] += 1; warns.append(f'{rel}:{li + 1}: end without open scope: {rs[:80]}')
                    scopes.clear()
                else:
                    got = scopes[len(scopes) - len(want):]
                    del scopes[len(scopes) - len(want):]
                    if [x for _k, x in got] != want:
                        stats['end_mismatch'] += 1; warns.append(f'{rel}:{li + 1}: end mismatch {want} vs {got}')
            pending = []
            continue
        if ft == 'variable':
            for g in BINDER_NAMES.findall(rs):
                var_names.update(g.split())
            pending = []
            continue
        # modifiers
        mods = []
        r2 = rs
        while True:
            mm = re.match(r'(' + '|'.join(MODS) + r')\s+', r2) or re.match(r'(scoped\[[^\]]*\])\s*', r2)
            if not mm: break
            mods.append(mm.group(1)); r2 = r2[mm.end():]
            a2, p2 = parse_attrs(r2, 0)
            if a2: attrs += a2; r2 = r2[p2:]
        kw_m = FIRSTTOK.match(r2)
        kw = kw_m.group() if kw_m else ''
        if kw == 'class' and re.match(r'class\s+inductive\b', r2):
            kw = 'class inductive'
        all_attrs = [x for (_l, k, x) in pending if k == 'attr' and x] + attrs
        attrs_s = ' '.join(all_attrs)
        prefix_s = '; '.join([x for (_l, k, x) in pending if k == 'in'] + ([same_line_in] if same_line_in else []))
        s_line = (pending[0][0] if pending else li) + 1
        d_line = li + 1
        e_line = e + 1
        has_doc = any(k == 'doc' for (_l, k, _x) in pending)
        pending = []
        priv = 1 if 'private' in mods else 0
        mods_s = ' '.join(mods)
        if ft in NODECL or kw in NODECL:
            stats['cmd_' + (kw or ft)] += 1
            continue
        text = r2
        after_kw = len(kw)
        if kw in DECL_KW or kw == 'class inductive':
            k = 'class_inductive' if kw == 'class inductive' else kw
            i2 = after_kw
            while i2 < len(text) and text[i2].isspace(): i2 += 1
            name, guessed = '', 0
            if kw == 'instance' and text.startswith('(priority', i2):
                d0, j = 0, i2
                while j < len(text):
                    if text[j] == '(': d0 += 1
                    elif text[j] == ')':
                        d0 -= 1
                        if d0 == 0: break
                    j += 1
                i2 = j + 1
                while i2 < len(text) and text[i2].isspace(): i2 += 1
            if kw != 'example':
                dm = DECLID.match(text, i2)
                if dm and dm.group(1) and not (kw == 'instance' and text[i2] in ':([{⦃'):
                    name = unesc(dm.group(1)); i2 = dm.end()
            hpos, hk, hend = scan(text, i2)
            hdr = text[i2:hpos] if hpos is not None else text[i2:]
            sig = WS.sub(' ', hdr).strip()[:200]
            nsp = ns()
            notes = []
            if has_doc: notes.append('doc')
            if kw == 'example':
                emit('', '', priv, k, s_line, d_line, e_line, attrs_s, '', 0, mods_s, prefix_s, ';'.join(notes), sig)
                continue
            if not name:
                if kw == 'instance':
                    lc = first_colon(hdr, len(hdr))
                    ty = hdr[lc + 1:] if lc >= 0 else hdr
                    local = set(var_names)
                    for g in BINDER_NAMES.findall(hdr[:lc] if lc >= 0 else ''):
                        local.update(g.split())
                    local.update(re.findall(r'[∀∃λ]\s*([^\s,:]+)', ty))
                    name = guess_inst_name(ty, local)
                    guessed = 1
                else:
                    stats['unnamed_' + k] += 1
            if name.startswith('_root_.'):
                full = name[7:]
            else:
                full = (nsp + '.' + name) if nsp and name else name
            kernel = (priv_prefix + full) if priv and full else full
            emit(full, kernel, priv, k, s_line, d_line, e_line, attrs_s, '', guessed, mods_s, prefix_s,
                 ';'.join(notes), sig if kw == 'instance' else '')
            # children
            if kw in ('structure', 'class'):
                ctor, ctor_priv, fields = structure_children(text, hend if hend else len(text), hk, full)
                cp = 1 if (priv or ctor_priv) else 0
                ck_name = full + '.' + ctor
                emit(ck_name, (priv_prefix + ck_name) if cp else ck_name, cp, 'ctor', s_line, d_line, e_line,
                     '', full, 0, '', '', '', '')
                for par in extends_parents(hdr):
                    fn = full + '.' + par
                    emit(fn, (priv_prefix + fn) if priv else fn, priv, 'field', s_line, d_line, e_line, '', full, 0,
                         '', '', 'extends', '')
                for fk, fnm, note, fpriv in fields:
                    if fk == 'deriving':
                        for cls in re.findall(IDENT.pattern, fnm[len('deriving'):]):
                            gn = (nsp + '.' if nsp else '') + 'inst' + capital(cls.split('.')[-1]) + full.split('.')[-1]
                            emit(gn, gn, 0, 'derived_instance', s_line, d_line, e_line, '', full, 1, '', '',
                                 'deriving', fnm[:200])
                        continue
                    fp = 1 if (priv or fpriv) else 0
                    fn = full + '.' + fnm
                    emit(fn, (priv_prefix + fn) if fp else fn, fp, 'field', s_line, d_line, e_line, '', full, 0,
                         '', '', note, '')
            elif kw in ('inductive', 'class inductive'):
                ctors, deriv = inductive_ctors(text, hend if hk == 'where' else (hpos if hpos is not None else len(text)))
                for cn in ctors:
                    fn = full + '.' + cn
                    emit(fn, (priv_prefix + fn) if priv else fn, priv, 'ctor', s_line, d_line, e_line, '', full, 0,
                         '', '', '', '')
                for d in deriv:
                    for cls in re.findall(IDENT.pattern, d[len('deriving'):]):
                        gn = (nsp + '.' if nsp else '') + 'inst' + capital(cls.split('.')[-1]) + full.split('.')[-1]
                        emit(gn, gn, 0, 'derived_instance', s_line, d_line, e_line, '', full, 1, '', '',
                             'deriving', d[:200])
            else:
                # `where` auxiliary definitions (after a `:=` body or equations), vs. `where` structure instances
                if hk is not None and hk != 'where' and 'where' in text[hend:]:
                    wpos, wk, wend = scan(text, hend, stop=('where',))
                    if wpos is not None:
                        for an in where_aux(text, wend):
                            fn = full + '.' + an
                            emit(fn, (priv_prefix + fn) if priv else fn, priv, 'where_aux', s_line, d_line, e_line,
                                 '', full, 0, '', '', '', '')
                if re.search(r'(?<![\w.])let\s+rec\b', text):
                    stats['let_rec'] += 1
            continue
        if kw in SYNTAX_KW or ft in SYNTAX_KW:
            kk = kw or ft
            sig = WS.sub(' ', r2[len(kk):]).strip()[:200]
            name = ''
            mn = re.search(r'\(name\s*:=\s*(' + PART + r'(?:\.' + PART + r')*)\)', r2[:300])
            if kk == 'alias':
                ma = re.match(r'alias\s+(?:⟨([^⟩]*)⟩|(' + PART + r'(?:\.' + PART + r')*))', r2)
                nsp = ns()
                if ma:
                    nms = [x.strip() for x in (ma.group(1).split(',') if ma.group(1) else [ma.group(2)])]
                    for nm in nms:
                        if not nm or nm == '_': continue
                        nm = unesc(nm)
                        full = nm[7:] if nm.startswith('_root_.') else ((nsp + '.' + nm) if nsp else nm)
                        emit(full, (priv_prefix + full) if priv else full, priv, 'alias', s_line, d_line, e_line,
                             attrs_s, '', 0, mods_s, prefix_s, '', sig)
                    continue
            if mn:
                nm = unesc(mn.group(1)); nsp = ns()
                name = nm[7:] if nm.startswith('_root_.') else ((nsp + '.' + nm) if nsp else nm)
            emit(name, name, priv, kk, s_line, d_line, e_line, attrs_s, '', 0, mods_s, prefix_s,
                 'generated names not derivable from text' if not name else '', sig)
            continue
        if ft.startswith('#'):
            stats['cmd_' + ft] += 1
            continue
        # unknown command (e.g. a project-defined elab command that may add constants)
        emit('', '', 0, 'custom_cmd', s_line, d_line, e_line, attrs_s, '', 0, mods_s, prefix_s,
             'unknown command; may generate constants', WS.sub(' ', r2)[:200])
        stats['custom_cmd'] += 1; warns.append(f'{rel}:{li + 1}: custom command: {r2[:80]}')
    if scopes:
        stats['unclosed_scopes'] += 1
    used = collections.Counter()
    for i, r in enumerate(rows):
        if r[3] == 'instance' and r[11] == 1:
            n = used[r[0]]; used[r[0]] += 1
            if n:
                nm = r[0] + '_' + str(n)
                rows[i] = (nm, nm) + r[2:]
    return rows, stats, nlines, warns


def main():
    root = sys.argv[1]
    out = sys.argv[2]
    jobs = int(sys.argv[sys.argv.index('--jobs') + 1]) if '--jobs' in sys.argv else os.cpu_count()
    t0 = time.time()
    rels = []
    for d, _, fs in os.walk(os.path.join(root, 'src')):
        for f in fs:
            if f.endswith('.lean'):
                rels.append(os.path.relpath(os.path.join(d, f), root))
    rels.sort()
    allrows, stats, total_lines, warns = [], collections.Counter(), 0, []
    with Pool(jobs) as pool:
        for rows, st, nl, w in pool.imap(index_file, [(root, r) for r in rels], chunksize=64):
            allrows.extend(rows); stats.update(st); total_lines += nl; warns.extend(w)
    with open(re.sub(r'\.tsv\.gz$', '', out) + '-warnings.txt', 'w') as h:
        h.write('\n'.join(warns) + '\n')
    with gzip.open(out, 'wt', encoding='utf-8', compresslevel=6) as h:
        h.write('\t'.join(COLS) + '\n')
        for r in allrows:
            h.write('\t'.join(str(x).replace('\t', ' ') for x in r) + '\n')
    el = time.time() - t0
    print(f'files {len(rels)} lines {total_lines} rows {len(allrows)} time {el:.1f}s', file=sys.stderr)
    if '--summary' in sys.argv:
        write_summary(sys.argv[sys.argv.index('--summary') + 1], rels, allrows, stats, total_lines, el)


def write_summary(path, rels, rows, stats, total_lines, el):
    kinds = collections.Counter(r[3] for r in rows)
    top = collections.Counter()
    topk = collections.defaultdict(collections.Counter)
    for r in rows:
        parts = r[5].split('/')
        d = parts[1] if len(parts) > 2 else parts[-1]
        top[d] += 1
        topk[d][r[3]] += 1
    files_per_top = collections.Counter((x.split('/')[1] if x.count('/') > 1 else x.split('/')[-1]) for x in rels)
    empty = sum(1 for r in rows if not r[0])
    guessed = sum(1 for r in rows if r[11] == 1)
    with open(path, 'w') as h:
        h.write('# decl-index summary (auto-generated part)\n\n')
        h.write(f'- files: {len(rels)}; lines: {total_lines}; rows: {len(rows)}; run time: {el:.1f} s\n')
        h.write(f'- rows with empty full_name: {empty}; rows with name_guessed=1: {guessed}\n')
        h.write(f'- parser stats: {dict(sorted(stats.items()))}\n\n')
        h.write('| kind | rows |\n|---|---|\n')
        for k, v in kinds.most_common():
            h.write(f'| {k} | {v} |\n')
        h.write('\n| top-level dir under src/ | files | rows | theorem+lemma | def+abbrev | instance | structure+class+inductive | ctor+field |\n|---|---|---|---|---|---|---|---|\n')
        for d, v in sorted(top.items()):
            k = topk[d]
            h.write(f"| {d} | {files_per_top[d]} | {v} | {k['theorem'] + k['lemma']} | {k['def'] + k['abbrev']} | "
                    f"{k['instance']} | {k['structure'] + k['class'] + k['inductive'] + k['class_inductive']} | "
                    f"{k['ctor'] + k['field']} |\n")


if __name__ == '__main__':
    main()
