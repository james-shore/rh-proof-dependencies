"""query.py on the synthetic dataset of test_dominators.py (run that first with the same WORKDIR).
usage: test_query.py WORKDIR"""
import os, subprocess, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = sys.argv[1]
ds, out = os.path.join(W, 'ds'), os.path.join(W, 'out')
cache = os.path.join(W, 'q.npz')


def q(*args, code=0):
    r = subprocess.run([sys.executable, os.path.join(HERE, 'query.py'), '--data', ds, '--cache', cache,
                        '--dom', os.path.join(out, 'dominators.tsv.gz'), *args], capture_output=True, text=True)
    assert r.returncode == code, (args, r.returncode, r.stdout, r.stderr)
    return r.stdout


s = q('deps', 'chapA', '--ext')
assert 'uses 2 project declarations' in s and '] a1  [' in s and '] a2  [' in s, s
s = q('deps', 'a2', '--ext')
assert 'and 2 Mathlib/core constants' in s and 'M1' in s and 'M2' in s, s
s = q('users', 'shared')
assert 'used directly by 2' in s and '] a1  [' in s and '] chapB  [' in s, s
s = q('path', 'main', 'shared')
assert '1 shortest chain(s) of length 2' in s and 'chapB' in s and 'chapA' not in s, s
s = q('paths', 'main', 'a1._proof_2', '--max', '5')
assert '2 shortest chain(s) of length 3' in s, s
s = q('path', 'b1', 'main')
assert 'does not use' in s, s
s = q('users', 'a1.proof_1')       # a folded helper resolves to its declaration
assert '] chapA  [' in s, s
s = q('dominator', 'a1')
assert 'a1  [' in s and s.index('    a1  [') < s.index('    chapA  [') < s.index('    main  ['), s
q('deps', 'nonexistent_thing', code=2)
s = q('meet', 'a1', 'b1')
assert 'nearest at combined distance 4' in s and 'main  [' in s and '6 declarations use both' in s, s
r = subprocess.run([sys.executable, os.path.join(HERE, 'query.py'), '--data', ds, '--cache', cache, 'batch'],
                   input='users shared\npath main shared\ndeps nonexistent_thing\n', capture_output=True, text=True)
assert r.returncode == 0 and r.stdout.count('\n$ ') == 3 and 'used directly by 2' in r.stdout and 'length 2' in r.stdout, r.stdout
print('query.py synthetic test OK')
