/-
origin.lean: list every kernel constant declared by a set of modules, with the module that declares it.

Usage: lean --run origin.lean <modules.tsv> <outprefix>
  modules.tsv rows: <module path relative to libdir, '/'-separated, no extension> TAB <package> TAB <libdir>
Writes:
  <outprefix>.rows.tsv     name, module, package, kind, isUnsafe (no header, unsorted)
  <outprefix>.multi.tsv    name, recorded module, every module whose data contains the name (only names in >1 module)
  <outprefix>.modules.tsv  module, package, constants attributed to it
  <outprefix>.log          import passes, left-out roots and why
Imports with `level := .private`, which loads `.olean` + `.olean.server` + `.olean.private` for every module
and puts private constants in `env.constants`.
-/
import Lean
open Lean

def kindOf : ConstantInfo → String
  | .axiomInfo _ => "axiom"
  | .defnInfo _ => "defn"
  | .thmInfo _ => "thm"
  | .opaqueInfo _ => "opaque"
  | .quotInfo _ => "quot"
  | .inductInfo _ => "induct"
  | .ctorInfo _ => "ctor"
  | .recInfo _ => "rec"

def pathToName (p : String) : Name :=
  (p.splitOn "/").foldl Name.str .anonymous

def render (n : Name) : String := n.toString (escape := false)

/-- Import `roots`; on a duplicate-constant failure drop the module the error names from the roots and retry.
If that module is not a root (it is imported by other roots), drop instead every root sharing the first name
component of the other module in the conflict (e.g. all `Cache.*`). Dropped groups are imported separately. -/
partial def importDropping (roots : Array Name) (log : IO.FS.Handle) (dropped : Array (Array Name × String) := #[]) :
    IO (Environment × Array (Array Name × String)) := do
  try
    let env ← importModules (roots.map ({ module := · })) {} (loadExts := false) (level := .private) (leakEnv := true)
    return (env, dropped)
  catch e =>
    let msg := toString e
    log.putStrLn s!"import of {roots.size} roots failed: {msg}"
    -- message shape: "import <M> failed, environment already contains '<c>' from <M'>"
    let some rest := (msg.splitOn "import ")[1]? | throw e
    let some modStr := (rest.splitOn " failed")[0]? | throw e
    let some otherStr := (msg.splitOn "' from ").getLast? | throw e
    let bad := modStr.toName
    let other := otherStr.trimAscii.toString.toName
    if roots.contains bad then
      importDropping (roots.filter (· != bad)) log (dropped.push (#[bad], msg))
    else
      let top := other.getRoot
      -- undo drops made for this same conflict; fold earlier drops under `top` into the group
      let restored := (dropped.filter (·.2 == msg)).flatMap (·.1)
      let dropped := dropped.filter (·.2 != msg)
      let grp := (roots ++ dropped.flatMap (·.1)).filter (·.getRoot == top)
      if grp.isEmpty then throw e
      log.putStrLn s!"  {bad} is not a root; restoring {restored} and dropping all {grp.size} modules under {top} as one group"
      let dropped := dropped.filter (fun (g, _) => !g.any (·.getRoot == top))
      importDropping ((roots ++ restored).filter (·.getRoot != top)) log (dropped.push (grp, msg))

def main (args : List String) : IO UInt32 := do
  let [modsFile, outPrefix] := args
    | IO.eprintln "usage: origin <modules.tsv> <outprefix>"; return 2
  let log ← IO.FS.Handle.mk s!"{outPrefix}.log" .write
  let mut pkgOf : Std.HashMap Name String := {}
  let mut dirs : Array System.FilePath := #[]
  let mut roots : Array Name := #[]
  for l in (← IO.FS.lines modsFile) do
    if l.isEmpty then continue
    let [m, p, d] := l.splitOn "\t" | throw (IO.userError s!"bad line: {l}")
    let n := pathToName m
    if let some p' := pkgOf[n]? then
      log.putStrLn s!"module {n} found in both {p'} and {p}; keeping {p'}"
      continue
    pkgOf := pkgOf.insert n p
    roots := roots.push n
    unless dirs.contains d do dirs := dirs.push d
  searchPathRef.set dirs.toList
  log.putStrLn s!"roots: {roots.size}; search path: {dirs}"

  let (env, dropped) ← importDropping roots log
  log.putStrLn s!"pass 1: {env.header.moduleNames.size} modules, {env.constants.fold (fun n _ _ => n + 1) 0} constants"
  -- each dropped group gets its own import; keep only constants of modules not loaded by an earlier pass
  let mut envs : Array Environment := #[env]
  for (g, why) in dropped do
    log.putStrLn s!"left out of pass 1, imported on its own: {g} ({why})"
    let e ← importModules (g.map ({ module := · })) {} (loadExts := false) (level := .private) (leakEnv := true)
    envs := envs.push e

  let rows ← IO.FS.Handle.mk s!"{outPrefix}.rows.tsv" .write
  let multi ← IO.FS.Handle.mk s!"{outPrefix}.multi.tsv" .write
  -- a module's constants are taken from the first environment that loaded it
  let mut covered : Std.HashSet Name := {}
  let mut counts : Std.HashMap Name Nat := {}
  let mut unattributed := 0
  for e in envs do
    let modNames := e.header.moduleNames
    let fresh := modNames.filter (!covered.contains ·)
    -- names contained in more than one module's data (allowed for theorems, see `subsumesInfo`)
    let mut where_ : Std.HashMap Name (Array Name) := {}
    let mut i := 0
    for md in e.header.moduleData do
      for c in md.constNames do
        where_ := where_.alter c fun
          | none => some #[modNames[i]!]
          | some a => some (a.push modNames[i]!)
      i := i + 1
    let consts := e.constants.fold (fun acc n c => acc.push (n, c)) (#[] : Array (Name × ConstantInfo))
    let mut buf : String := ""
    for (n, c) in consts do
      let some idx := e.getModuleIdxFor? n | unattributed := unattributed + 1; continue
      let m := modNames[idx.toNat]!
      if covered.contains m then continue
      let p := pkgOf[m]?.getD "?"
      counts := counts.alter m (fun | none => some 1 | some k => some (k + 1))
      buf := buf ++ s!"{render n}\t{render m}\t{p}\t{kindOf c}\t{c.isUnsafe}\n"
      if buf.utf8ByteSize > 1000000 then rows.putStr buf; buf := ""
      if let some ms := where_[n]? then
        if ms.size > 1 then
          multi.putStrLn s!"{render n}\t{render m}\t{",".intercalate (ms.toList.map render)}"
    rows.putStr buf
    for m in fresh do covered := covered.insert m
  rows.flush; multi.flush
  let mf ← IO.FS.Handle.mk s!"{outPrefix}.modules.tsv" .write
  for (m, p) in pkgOf.toList do
    mf.putStrLn s!"{render m}\t{p}\t{counts[m]?.getD 0}"
  for (m, k) in counts.toList do
    unless pkgOf.contains m do mf.putStrLn s!"{render m}\t?\t{k}"
  log.putStrLn s!"unattributed constants: {unattributed}"
  log.flush
  return 0
