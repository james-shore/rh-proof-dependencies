"""Cross-export twins: KeysTest's export is the library, RelTest's the project (run tests/relevance/export.sh first).
usage: test_xtwins.py XTWINS_BIN WORKDIR"""
import os, subprocess, sys

BIN, W = sys.argv[1], sys.argv[2]
out = os.path.join(W, 'xtwins-out')
os.makedirs(out, exist_ok=True)
keys = os.path.join(out, 'keys.tsv')
with open(os.path.join(W, 'KeysTest.ndjson'), 'rb') as f:
    subprocess.run([BIN, 'keys', keys], stdin=f, check=True, capture_output=True)
with open(os.path.join(W, 'RelTest.ndjson'), 'rb') as f:
    subprocess.run([BIN, 'twins', keys, out, 'prefix:RelTest.'], stdin=f, check=True, capture_output=True)
rows = {}
with open(os.path.join(out, 'xtwins.tsv')) as f:
    hdr = next(f).rstrip('\n').split('\t')
    for l in f:
        r = dict(zip(hdr, l.rstrip('\n').split('\t')))
        rows[r['name']] = r
g = lambda n: rows.get('RelTest.' + n)
assert g('twin1')['twin'] == 'Keys.twinK' and g('twin1')['round'] == '0', g('twin1')
assert g('twin2')['twin'] == 'Keys.twinK'
assert g('tw1')['twin'] == 'Keys.twK' and g('tw1')['round'] == '1', g('tw1')
assert g('natTwin')['twin'] == 'Nat.succ_ne_zero' and g('natTwin')['round'] == '0'
assert g('bigLit')['twin'] == 'Keys.bigLit', g('bigLit')
assert g('bigLitOff') is None and g('lemA') is None
stats = open(os.path.join(out, 'xtwins-stats.txt')).read()
chk = [l for l in stats.splitlines() if 'non-project theorems/definitions in this export match a key' in l][0]
a, b = chk.split('check: ')[1].split(' non-project')[0].split(' of ')
assert int(a) > 0.9 * int(b), chk
print('xtwins: hand-checked cases OK;', chk.split('] ')[-1])
