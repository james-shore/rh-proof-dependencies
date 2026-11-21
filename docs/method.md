# Method

## Kernel closure

- Input: `solution.ndjson.zst`, the `lean4export` output for `Main` (the five theorems plus 33 kernel primitives as
  export targets). It is decompressed as a stream (`zstd -dc --long=30`) and parsed by `tools/export-parse` (Rust).
- For each constant, its type and its value (for a recursor, its rules) are walked separately as DAGs. This gives the
  direct references and the DAG sizes.
- Edges are the rules `lean4export` uses: a constant named in a type or value; a natural-number literal needs `Nat`;
  a string literal needs `Char.ofNat` and `String.ofList`; the quotient constants need `Eq`. Constants in one
  inductive block (type, constructors, recursors) come together.
- The closure of the five theorems over these edges is the set of used constants. Checks: the closure from all 38
  export targets reaches every constant in the export, and no reference dangles.

## Check against the compiled environment

- `Main`'s environment was loaded in an offline container from three independently built sets of compiled `.olean`
  files: our own build (175,187 of 175,187 modules, with `Main.olean` identical to Sutherland's), the set published by
  [proofsandreasons.io](https://proofsandreasons.io/rh-oleans.html) (every file matched their `SHA256SUMS`), and Jack
  McCarthy's. All three give byte-identical constants, references and declaration ranges.
- The environment has 2,701,132 constants. The closure of the five theorems in it is the same 1,484,729 constants as in
  the export, with none on only one side.
- Its declaration ranges show that the text index misplaced 53 of the 804,911 used declarations: 49 in the wrong file
  (the name exists in two files), 3 unnamed instances swapped within a file, and 1 declaration made by an
  `attribute [reassoc]` line in another file, which the text index folded into its source. They also give the real
  names of 1,269 unnamed instances that the text index had matched correctly. A later version takes file and line from
  these compiled ranges.
- This check loads compiled proof code, so it ran in an isolated container and is not part of `tools/reproduce.sh`.

## Origin

- Lean 4.33.1 imports every `.olean` of core, Mathlib `0df444a3` and its dependencies from Mathlib's prebuilt cache, with
  private constants (`tools/origin.lean`), and lists each constant with its module: 806,352 constants. No code from the
  proof runs.
- An export constant is project when its name is not in that list. Tool executables (`Cache.*`, `LakeMain`,
  `LeanChecker`, `LeanIR`, `Leanc`) are left out of the list.

## Folding into source declarations

- `tools/decl_index.py` indexes the source text of the 175,187 files: one row per top-level declaration, with
  namespaces and sections resolved, `_private.<Module>.0.` names formed, structure fields and constructors, and anonymous
  instances given a guessed name and their signature tokens.
- `tools/fold.py` maps each project constant to a declaration, in order: exact name; the longest indexed prefix, with
  the stripped suffix classified (`_proof_N`, `_simp_N`, `_abel_N`, `eq_N`, `match_N`, `rec`/`casesOn`, `mk`,
  `congr_simp`, `_aux_N`, `_unary`, …); `@[reassoc]`/`simps`-style and `to_additive` names; anonymous instances, matched
  within their namespace by name tokens against signature tokens, one to one; aids Lean generated in the project for a
  Mathlib declaration.
- Of the 1,270,184 project constants: 852,825 by exact name, 415,808 by suffix, 1,272 by the instance heuristic,
  137 attribute-generated, 8 `to_additive`, 96 Mathlib aids, 5 copies made by a project command, 20 by prefix only,
  13 unmapped.
- A declaration is **used** when one of its own constants is in the closure.

## Shared auxiliaries

Lean reuses an auxiliary lemma or matcher (`_proof_N`, `_simp_N`, `_abel_N`, `_aux_N`, `match_N`) when another
declaration needs one with the same type, even in another file. Such a constant can be used while the declaration that
generated it is not. It does not make its generator used (`shared_aux` in `constants`). 839 declarations are in this
position. In a cut tree, every declaration that uses the lemma regenerates it.

## Lines

- Newlines (`wc -l`) per file: 17,374,215 over the 175,187 files. Counting newlines + 1 per file gives 17,549,402.
- A declaration's span starts at its attributes and `… in` prefixes and ends before the next top-level command. Spans
  never overlap, so each line is inside one used declaration, inside one unused declaration, inside a command with no
  kernel constant, or outside any command.

## Dominators

- Graph: one node per used declaration; plus each shared auxiliary lemma used by a node other than its generator
  (found as a fixed point: splitting one out can expose another), since folding it would add a false edge to the
  generator; plus project constants with no declaration row. Edge A→B when a constant of A references a constant of B.
  A virtual root has an edge to each of the five theorems.
- Lengauer–Tarjan (`tools/structure/rs`, Rust). Subtree sizes summed in numpy.
- Checks on every run: for every edge u→v, the immediate dominator of v is an ancestor of u (so each subtree is entered
  only at its root); brute force on 24 random nodes (deleting each ancestor makes the node unreachable, and nothing
  else does); an independent Python check of the 20 largest subtrees; agreement with networkx on 300 random graphs.

## Unused hypotheses

- For a project `def` or `theorem` with type `∀ x₀ … x_{m−1}, R` and value `fun x₀ … x_{k−1} => V`, position i is
  removable when its type is a proposition, `x_i` does not occur in a later binder type or in `R`, and `x_i` does not
  occur relevantly in `V`. An occurrence inside an argument that a callee receives at one of its own removable positions
  is not relevant. Eta-reduced values pass positions ≥ k to the head constant.
- Propositions are decided by a small weak-head evaluator (β, ζ, δ, universe instantiation). A binder whose sort it
  cannot decide is kept. Constructors, recursors, `opaque`s, projections, the five theorems and non-project constants
  keep every argument.
- The export lists each constant after everything it references, so one pass in export order gives the least fixed
  point.
- Then the closure is taken again following relevant references only. With relevance switched off, the same code gives
  the kernel closure exactly (1,484,729). Variant: a `let`/`have` whose variable is not used relevantly also drops its
  type and value.
- 13 hand-checked cases (`tools/structure/tests/relevance/`).

## Duplicates

- The export hash-conses expressions with Lean's expression equality, which ignores binder names and binder info. Two
  theorems with the same statement id have alpha-equivalent statements; two definitions also need the same value id
  (`tools/structure/rs/src/bin/dupes.rs`).
- Up to renaming: every constant inside the terms is replaced by its class representative, and this is repeated until
  no class changes. Inductive types, constructors, recursors, axioms and `opaque`s never merge.
- Members of a group often prove one another, so keeping one keeps every member its proof reaches. Redundant lines are a
  group's lines minus the cheapest such keep (`tools/structure/duplicates.py`).

## Statements labelled as published results

- Each labelled statement is matched to its proofs: theorems whose type is exactly the statement, plus the theorems that
  the `*_of_literature` theorems in `EveryCharacterOfLiteratureOfVWin.lean` point to (`tools/literature_map.py`).
- Owned: the dominator-graph nodes that become unreachable from the five theorems once all of a group's proofs are
  removed (`tools/structure/ownership.py`). Used: the nodes reachable from those proofs.

## Universe levels

- Starting from the five theorems, the universe arguments of every constant occurrence are evaluated under the
  instantiation of the constant that contains it, until no new (constant, level vector) pairs appear. An inductive type
  brings its constructors at the same levels (`tools/export-parse/src/bin/universes.rs`).
- For each pair, the largest `Sort` in its instantiated type and value is recorded.
- These levels include artefacts of compilation: the `below`/`brecOn` encoding of structural recursion raises the
  motive's universe by one. The kernel checks a universe-polymorphic constant once, with symbolic levels.

## Text maps

Each map is regenerated with its authors' scripts on the same source, at the commits named in the README, and its set
of names is compared with the kernel closure of the same root, at declaration level, with `_private` prefixes dropped
(`tools/text_trace_compare.py`). jae-63's published traces stop at a fixed depth, so their `trace.py` is rerun from their
root with no depth limit.

## Limits

- The closure is what the proof terms reference. Anything used only to elaborate is not in it: notation, macros,
  tactics, `simp`/`dsimp` lemmas that leave no constant, instances tried and discarded, `decide`/`rfl` unfolding. A tree
  cut to the used declarations will not compile as it is.
- File and line come from a text index: anonymous instances (1,279) can swap within a namespace; 123 constants match one
  of several declarations with the same name; 20 map by prefix only; 13 are unmapped. A later version takes them from
  declaration ranges in the compiled environment.
- Origin is assigned by name. A project constant named like a Mathlib constant from a module the project never imports
  would count as Mathlib.
- Domination is relative to the five theorems and to this folding. A declaration used by two branches is dominated by
  their common ancestor, however small either use.
- Removable hypotheses are an upper bound on safe cuts. Removing one changes that declaration's statement and every call
  site; elaboration needs are not in the proof terms.
