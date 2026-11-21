# Query

`tools/structure/query.py` answers path questions about the proof cone at the level of source declarations: what a
declaration uses, what uses it, how one reaches another, and what it is used through. It needs only the published
tables (`constants`, `edges`, `decls`, and `dominators.tsv.gz` for the `dominator` subcommand). It makes no network
requests. Python 3.12 with numpy, pyarrow and duckdb (`tools/structure/requirements.txt`).

```
python tools/structure/query.py --data DATASET_DIR build                 # once: folds the edges, writes a cache (~6 min, a few GB RAM)
python tools/structure/query.py --data DATASET_DIR deps NAME [--ext]     # what NAME uses directly (--ext: also Mathlib/core)
python tools/structure/query.py --data DATASET_DIR users NAME            # what uses NAME directly, inside the cone
python tools/structure/query.py --data DATASET_DIR path A B              # one shortest chain "A uses ... uses B"
python tools/structure/query.py --data DATASET_DIR paths A B --max N     # up to N distinct shortest chains
python tools/structure/query.py --data DATASET_DIR meet A B              # nearest declarations that use both A and B
python tools/structure/query.py --data DATASET_DIR --dom dominators.tsv.gz dominator NAME   # used only through …, subtree size
python tools/structure/query.py --data DATASET_DIR batch < queries.txt   # many queries, cache loaded once
```

- **Names.** A full name, a short one (`omTied_retie_of_cube`, `AuxBlock8.Conclusion1`), or the name of a
  compiler-generated constant (`foo._proof_1`, which resolves to its declaration). `NAME@path-substring` picks among
  same-named declarations. An ambiguous name lists every match with its file and line.
- **Edges.** Each edge is marked by where the use occurs: `stmt` (in the statement), `proof` (in the proof or
  definition body), or `both`.
- **Nodes.** Nodes are the same as in the dominator tree: source declarations, plus shared auxiliary lemmas that more
  than one declaration uses, which stand alone.
- **Scope.** Only kernel references are counted. Something needed only while elaborating (a `simp` lemma, an
  instance tried, notation) does not appear.
- **Dataset versions.** The tool works on any dataset version that has the same tables.
- **Cache.** Next to the dataset as `query-cache-<version>.npz` plus `.ext.npz`; override with `--cache` or
  `$RHS_QUERY_CACHE`. Queries then load in a few seconds.

Example:

```
$ query.py --data v0.1.3 users omTied_retie_of_cube
GlobalTheory.Mem.Top.Bl.TrueLocal.omTied_retie_of_cube  [theorem, src/Development/AbelianVarietyTheory/Proofs/IwasawaDecomposition/ExistsGIs.lean:21, 27 lines]
  is used directly by 1 declaration in the cone:
    [proof] GlobalTheory.Mem.Top.Bl.TrueLocal.omTied_withTrue  [theorem, src/Development/AbelianVarietyTheory/Proofs/IwasawaDecomposition/ExistsGIs.lean:49, 19 lines]
```
