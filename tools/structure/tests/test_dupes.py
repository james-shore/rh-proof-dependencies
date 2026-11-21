"""Hand-checked duplicates on tests/relevance/RelTest.lean (export it first with tests/relevance/export.sh).
usage: test_dupes.py DUPES_BIN WORKDIR"""
import os, subprocess, sys

BIN, W = sys.argv[1], sys.argv[2]
out = os.path.join(W, 'dupes-out')
os.makedirs(out, exist_ok=True)
with open(os.path.join(W, 'RelTest.ndjson'), 'rb') as f:
    subprocess.run([BIN, out, 'prefix:RelTest.'], stdin=f, check=True, capture_output=True)
rows = {}
with open(os.path.join(out, 'dupes-constants.tsv')) as f:
    hdr = next(f).rstrip('\n').split('\t')
    for l in f:
        r = dict(zip(hdr, l.rstrip('\n').split('\t')))
        rows[r['name']] = r
g = lambda n: rows['RelTest.' + n] if '.' not in n or n.startswith('RelTest') else rows[n]
assert g('dupA')['exact_class'] == g('dupB')['exact_class'], (g('dupA'), g('dupB'))
assert g('twin1')['exact_class'] == g('twin2')['exact_class']
assert g('tw1')['exact_class'] != g('tw2')['exact_class'] and g('tw1')['twin_class'] == g('tw2')['twin_class']
assert g('tw2')['twin_round'] == '1', g('tw2')
nat = rows['Nat.succ_ne_zero']
assert g('natTwin')['exact_class'] == nat['exact_class'] == nat['id'] and g('natTwin')['twin_class'] == nat['id']
assert g('lemA')['exact_class'] == g('lemA')['id']
print('dupes: hand-checked cases OK')
