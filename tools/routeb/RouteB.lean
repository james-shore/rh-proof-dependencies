/-
RouteB: dump every constant of an environment with its module, declaration range, kind, block, and direct references.

Usage: routeb <searchpath-file> <outprefix> <Module>...
  searchpath-file: one olean root directory per line (toolchain lib/lean, package lib dirs, the build's lib dir).
Writes:
  <outprefix>.constants.tsv  id, name, module, kind, unsafe, block, nlevels, start_line, end_line, sel_line, sel_col,
                             lit_nat, lit_str, refs_type, refs_value
  <outprefix>.edges.tsv      src_id, dst_id, via (t = type, v = value; recursor value = its rules' rhs)
  <outprefix>.modules.tsv    module index, module name
Names: `Name.toString (escape := false)`, tab/newline written as \t, \n (same as export-parse).
-/
import Lean
open Lean

def kindOf : ConstantInfo → String
  | .axiomInfo _ => "axiom"
  | .defnInfo _ => "def"
  | .thmInfo _ => "theorem"
  | .opaqueInfo _ => "opaque"
  | .quotInfo _ => "quot"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"

def render (n : Name) : String :=
  ((n.toString (escape := false)).replace "\t" "\\t").replace "\n" "\\n"

def ranges? (env : Environment) (n : Name) : Option DeclarationRanges :=
  declRangeExt.find? (level := .server) env n <|> declRangeExt.find? (level := .private) env n <|>
    declRangeExt.find? (level := .exported) env n

def hasNat (e : Expr) : Bool := (e.find? fun x => x matches .lit (.natVal _)).isSome
def hasStr (e : Expr) : Bool := (e.find? fun x => x matches .lit (.strVal _)).isSome

/-- The exporter's unit: an inductive block (all types, their constructors and recursors) or the quot package. -/
def blockKey (env : Environment) : ConstantInfo → Name
  | .inductInfo v => v.all.headD v.name
  | .ctorInfo v => match env.find? v.induct with
    | some (.inductInfo iv) => iv.all.headD v.induct
    | _ => v.induct
  | .recInfo v => v.all.headD v.name
  | .quotInfo _ => `Quot
  | c => c.name

def values : ConstantInfo → List Expr
  | .defnInfo v => [v.value]
  | .thmInfo v => [v.value]
  | .opaqueInfo v => [v.value]
  | .recInfo v => v.rules.map (·.rhs)
  | _ => []

def processChunk (env : Environment) (idx : Std.HashMap Name Nat) (cs : Array (Nat × Name × ConstantInfo)) :
    String × String := Id.run do
  let mut a := ""
  let mut b := ""
  for (i, n, c) in cs do
    let mod := match env.getModuleIdxFor? n with
      | some m => toString m.toNat
      | none => ""
    let (sl, el, ssl, ssc) := match ranges? env n with
      | some r => (toString r.range.pos.line, toString r.range.endPos.line, toString r.selectionRange.pos.line,
          toString r.selectionRange.pos.column)
      | none => ("", "", "", "")
    let vs := values c
    let tr := c.type.getUsedConstants
    let mut vset : Std.HashSet Name := {}
    for e in vs do
      for d in e.getUsedConstants do
        vset := vset.insert d
    let nat := hasNat c.type || vs.any hasNat
    let str := hasStr c.type || vs.any hasStr
    a := a ++ s!"{i}\t{render n}\t{mod}\t{kindOf c}\t{c.isUnsafe}\t{render (blockKey env c)}\t{c.levelParams.length}\t{sl}\t{el}\t{ssl}\t{ssc}\t{nat}\t{str}\t{tr.size}\t{vset.size}\n"
    for d in tr do
      b := b ++ s!"{i}\t{idx[d]?.getD 4294967295}\tt\n"
    for d in vset do
      b := b ++ s!"{i}\t{idx[d]?.getD 4294967295}\tv\n"
  return (a, b)

def main (args : List String) : IO UInt32 := do
  let spFile :: outPrefix :: mods := args
    | IO.eprintln "usage: routeb <searchpath-file> <outprefix> <Module>..."; return 2
  let dirs := (← IO.FS.lines spFile).filter (!·.isEmpty) |>.toList.map System.FilePath.mk
  searchPathRef.set dirs
  let t0 ← IO.monoMsNow
  let env ← importModules (mods.toArray.map fun m => { module := m.toName }) {} (loadExts := false) (level := .private)
    (leakEnv := true)
  let consts := env.constants.fold (fun acc n c => acc.push (n, c)) (#[] : Array (Name × ConstantInfo))
  IO.eprintln s!"imported {env.header.moduleNames.size} modules, {consts.size} constants in {(← IO.monoMsNow) - t0} ms"
  let mut idx : Std.HashMap Name Nat := Std.HashMap.emptyWithCapacity consts.size
  let mut numbered : Array (Nat × Name × ConstantInfo) := Array.mkEmpty consts.size
  for h : i in [:consts.size] do
    let (n, c) := consts[i]
    idx := idx.insert n i
    numbered := numbered.push (i, n, c)
  let chunk := 5000
  let mut tasks := #[]
  let mut s := 0
  while s < numbered.size do
    let part := numbered.extract s (s + chunk)
    tasks := tasks.push (Task.spawn fun _ => processChunk env idx part)
    s := s + chunk
  let hc ← IO.FS.Handle.mk s!"{outPrefix}.constants.tsv" .write
  let he ← IO.FS.Handle.mk s!"{outPrefix}.edges.tsv" .write
  hc.putStrLn "id\tname\tmodule_idx\tkind\tunsafe\tblock\tnlevels\tstart_line\tend_line\tsel_line\tsel_col\tlit_nat\tlit_str\trefs_type\trefs_value"
  he.putStrLn "src_id\tdst_id\tvia"
  let mut k := 0
  for t in tasks do
    let (a, b) := t.get
    hc.putStr a
    he.putStr b
    k := k + 1
    if k % 100 == 0 then IO.eprintln s!"  {k * chunk} constants written, {(← IO.monoMsNow) - t0} ms"
  hc.flush; he.flush
  let hm ← IO.FS.Handle.mk s!"{outPrefix}.modules.tsv" .write
  hm.putStrLn "module_idx\tmodule"
  for h : i in [:env.header.moduleNames.size] do
    hm.putStrLn s!"{i}\t{render env.header.moduleNames[i]}"
  hm.flush
  IO.eprintln s!"done in {(← IO.monoMsNow) - t0} ms"
  return 0
