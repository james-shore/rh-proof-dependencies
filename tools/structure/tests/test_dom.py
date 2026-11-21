"""Compare the Rust `dom` binary with networkx.immediate_dominators on random graphs (cyclic and acyclic)."""
import os, random, subprocess, sys, tempfile
import networkx as nx

DOM = sys.argv[1]


def run(n, edges, root, tmp, sample=20):
    p = os.path.join(tmp, 'e.tsv'); o = os.path.join(tmp, 'o.tsv')
    with open(p, 'w') as f:
        f.write('src\tdst\n')
        for u, v in edges:
            f.write(f'{u}\t{v}\n')
    subprocess.run([DOM, p, str(n), str(root), o, str(sample), '7'], check=True, capture_output=True)
    got = {}
    with open(o) as f:
        next(f)
        for line in f:
            v, d = map(int, line.split())
            if d >= 0:
                got[v] = d
    check = open(o + '.check').read()
    assert 'edge check:' in check and ', 0 failures' in check.split('edge check:')[1].split('\n')[0], check
    if sample:
        assert 'brute check' in check and ' 0 failures' in check.split('brute check:')[1].split('\n')[0], check
    return got


def main():
    rnd = random.Random(1)
    with tempfile.TemporaryDirectory() as tmp:
        for t in range(300):
            n = rnd.randint(2, 60)
            m = rnd.randint(1, n * 4)
            dag = t % 2 == 0
            edges = set()
            for _ in range(m):
                u, v = rnd.randrange(n), rnd.randrange(n)
                if dag:
                    u, v = min(u, v), max(u, v)
                if u != v or not dag:
                    edges.add((u, v))
            edges = sorted(edges)
            g = nx.DiGraph(); g.add_nodes_from(range(n)); g.add_edges_from(edges)
            want = nx.immediate_dominators(g, 0)
            want[0] = 0
            got = run(n, edges, 0, tmp)
            assert got == want, (t, n, edges, got, want)
        # a long chain (deep DFS) and a wide fan
        n = 200000
        edges = [(i, i + 1) for i in range(n - 1)] + [(0, n // 2)]
        got = run(n, edges, 0, tmp, sample=0)
        assert got[n // 2] == 0 and got[n // 2 + 1] == n // 2 and got[n // 2 - 1] == n // 2 - 2
    print('dom: 300 random graphs + chain OK')


main()
