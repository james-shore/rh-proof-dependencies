#!/usr/bin/env python3
"""Correct route B's module attribution for constants that several modules declare (dataset v0.2.1).
Lean's getModuleIdxFor? (and so declRangeExt.find?) names the FIRST module holding a copy; the environment's constant is
the copy from the module imported LAST (proved by pointer identity in routeb/Dups.lean and routeb/DupRanges.lean).
This rewrites module_idx and the range columns of every such constant to the winning copy's module and that module's
own declRangeExt entry, and copies the other route B files unchanged (stdlib only).

Usage: routeb_winners.py ROUTEB_PREFIX DUPRANGES_TSV OUT_PREFIX
  ROUTEB_PREFIX: DIR/main (main.constants.tsv, main.modules.tsv, main.edges.tsv)
  DUPRANGES_TSV: dupranges.tsv (name, winner_idx, winner_module, n_copies, start_line, end_line, sel_line, sel_col)
Writes OUT_PREFIX.constants.tsv (corrected), OUT_PREFIX.modules.tsv and OUT_PREFIX.edges.tsv (hard links or copies),
       OUT_PREFIX.winners-changes.tsv (name, old_module, new_module, old_start, new_start, old_end, new_end),
       OUT_PREFIX.winners-stats.json"""
import collections, json, os, shutil, sys

RB, DR, OUTP = sys.argv[1:4]
os.makedirs(os.path.dirname(os.path.abspath(OUTP)), exist_ok=True)
mods = {}
with open(RB + '.modules.tsv', encoding='utf-8') as f:
    f.readline()
    for line in f:
        i, m = line.rstrip('\n').split('\t')
        mods[i] = m
win = {}
with open(DR, encoding='utf-8') as f:
    h = f.readline().rstrip('\n').split('\t'); H = {x: i for i, x in enumerate(h)}
    for line in f:
        p = line.rstrip('\n').split('\t')
        win[p[H['name']]] = (p[H['winner_idx']], p[H['start_line']], p[H['end_line']], p[H['sel_line']], p[H['sel_col']])
st = collections.Counter(); st['multi_copy_names'] = len(win)
with open(RB + '.constants.tsv', encoding='utf-8') as f, open(OUTP + '.constants.tsv', 'w', encoding='utf-8') as g, \
     open(OUTP + '.winners-changes.tsv', 'w', encoding='utf-8') as ch:
    head = f.readline(); g.write(head)
    h = head.rstrip('\n').split('\t'); H = {x: i for i, x in enumerate(h)}
    ch.write('name\told_module\tnew_module\told_start\tnew_start\told_end\tnew_end\n')
    for line in f:
        p = line.rstrip('\n').split('\t')
        w = win.get(p[H['name']])
        if w is None:
            g.write(line); continue
        st['constants_seen'] += 1
        old = (p[H['module_idx']], p[H['start_line']], p[H['end_line']])
        p[H['module_idx']], p[H['start_line']], p[H['end_line']], p[H['sel_line']], p[H['sel_col']] = w
        if old[0] != w[0]:
            st['module_changed'] += 1
            st['module_changed_project' if mods[w[0]].split('.')[0] not in ('Mathlib', 'Init', 'Lean', 'Std', 'Batteries', 'Aesop', 'Qq', 'ProofWidgets', 'Plausible', 'ImportGraph', 'LeanSearchClient', 'Lake') else 'module_changed_deps'] += 1
        if (old[1], old[2]) != (w[1], w[2]):
            st['range_changed'] += 1
        if old != (w[0], w[1], w[2]):
            ch.write(f"{p[H['name']]}\t{mods.get(old[0], '')}\t{mods[w[0]]}\t{old[1]}\t{w[1]}\t{old[2]}\t{w[2]}\n")
        g.write('\t'.join(p) + '\n')
for ext in ('modules', 'edges'):
    dst = f'{OUTP}.{ext}.tsv'
    if os.path.exists(dst):
        os.remove(dst)
    try:
        os.link(f'{RB}.{ext}.tsv', dst)
    except OSError:
        shutil.copyfile(f'{RB}.{ext}.tsv', dst)
json.dump(dict(st), open(OUTP + '.winners-stats.json', 'w'), indent=1)
print(json.dumps(dict(st)))
