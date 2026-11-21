"""ownership.py on the synthetic dataset of test_dominators.py (run that first with the same WORKDIR).
usage: test_ownership.py WORKDIR"""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = sys.argv[1]
ds, out = os.path.join(W, 'ds'), os.path.join(W, 'out')
# collective ownership of {chapA}: chapA, a1, a2 are used only through it; shared and a1._proof_2 are also used by chapB
with open(os.path.join(W, 'L.txt'), 'w') as f:
    f.write('chapA\n')
subprocess.run([sys.executable, os.path.join(HERE, 'ownership.py'), ds, out, os.path.join(W, 'L.txt'), os.path.join(W, 'own')], check=True)
o = json.load(open(os.path.join(W, 'own.json')))
assert o['owned_nodes'] == 3 and o['closure_nodes'] == 5 and o['owned_lines'] == 5 + 15 + 3, o
print('ownership.py synthetic test OK')
