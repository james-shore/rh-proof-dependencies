#!/usr/bin/env python3
"""Split literature.tsv (literature_dom.py output) into per-group proof-name lists for ownership.py.
Usage: literature_groups.py literature.tsv OUTDIR  -> OUTDIR/L-{published,jae-known,jae-crux}.txt"""
import collections, csv, sys
LIT, OUT = sys.argv[1:3]
g = collections.defaultdict(set)
for r in csv.DictReader(open(LIT), delimiter='\t'):
    key = 'published' if r['label'] == 'published result' else ('jae-known' if r['label'].startswith('known') else 'jae-crux')
    g[key].update(p for p in r['proofs'].split(',') if p)
for k, v in g.items():
    open(f'{OUT}/L-{k}.txt', 'w').write('\n'.join(sorted(v)) + '\n')
    print(k, len(v))
