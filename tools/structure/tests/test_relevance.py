"""Hand-checked expectations for the relevance binary on tests/relevance/RelTest.lean.
usage: test_relevance.py RELEVANCE_BIN WORKDIR   (WORKDIR/RelTest.ndjson must exist: tests/relevance/export.sh)"""
import os, subprocess, sys

BIN, W = sys.argv[1], sys.argv[2]
out = os.path.join(W, 'rel-out')
os.makedirs(out, exist_ok=True)
roots = ','.join(f'RelTest.root{i}' for i in range(1, 7))
with open(os.path.join(W, 'RelTest.ndjson'), 'rb') as f:
    subprocess.run([BIN, out, roots, 'prefix:RelTest.'], stdin=f, check=True, env={**os.environ, 'DEADLET': '1', 'WATCH_BINDERS': 'h'})


def table(name):
    with open(os.path.join(out, name)) as f:
        hdr = next(f).rstrip('\n').split('\t')
        return {r['name']: r for r in (dict(zip(hdr, l.rstrip('\n').split('\t'))) for l in f)}


pos = table('binders-positions.tsv')
cone = table('binders-cone.tsv')


def removable(name):
    r = pos.get('RelTest.' + name)
    return sorted(int(x.split(':')[0]) for x in r['removable'].split()) if r and r['removable'] else []


want = {'lemA': [1], 'lemB': [1, 2], 'lemC': [], 'inConcl': [], 'inLater': [3], 'f': [1], 'etaA': [1],
        'viaVar': [2], 'viaDef': [2], 'univ': [], 'dataUnused': [], 'optP': [1]}
bad = [(k, removable(k), v) for k, v in want.items() if removable(k) != v]
assert not bad, bad
assert pos['RelTest.univ']['unused_unknown_sort'] == '1', pos['RelTest.univ']
assert pos['RelTest.dataUnused']['unused_binders'] == '1' and pos['RelTest.dataUnused']['unused_prop'] == '0'
r = cone['RelTest.expensive']
assert r['in_kernel_cone'] == '1' and r['in_pruned_cone'] == '0', r
# alsoExpensive feeds lemB's removable position (root1) and a dead `have` (root6): only the deadlet variant drops it
assert cone['RelTest.alsoExpensive']['in_pruned_cone'] == '1'
dl = table('deadlet-cone.tsv')
assert dl['RelTest.alsoExpensive']['in_pruned_cone'] == '0' and dl['RelTest.expensive']['in_pruned_cone'] == '0'
assert dl['RelTest.deadHave']['in_pruned_cone'] == '1' 
for k in ('lemA', 'lemB', 'f', 'etaA', 'root1', 'root4'):
    assert cone['RelTest.' + k]['in_pruned_cone'] == '1', k
assert pos['RelTest.lemB']['seeds_dropped'] != '0' and pos['RelTest.lemB']['drags_dropped'] not in ('', '0'), pos['RelTest.lemB']
stats = open(os.path.join(out, 'relevance-stats.txt')).read()
assert 'parse errors: 0' in stats
wb = {}
with open(os.path.join(out, 'binders-watched-binders.tsv')) as f:
    hdr = next(f).rstrip('\n').split('\t')
    for l in f:
        r = dict(zip(hdr, l.rstrip('\n').split('\t')))
        wb[r['name']] = r
r = wb['RelTest.lemB']
assert r['occurs_in_value'] == '1' and r['used'] == '0' and r['removable'] == '1' and r['prop'] == 'Yes', r
r = wb['RelTest.lemC']
assert r['occurs_in_value'] == '1' and r['used'] == '1' and r['removable'] == '0', r
r = wb['RelTest.inConcl']
assert r['used_in_type'] == '1' and r['removable'] == '0', r
print('relevance: hand-checked cases OK')
print(open(os.path.join(out, 'binders-positions.tsv')).read())
