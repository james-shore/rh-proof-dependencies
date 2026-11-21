/-
Dups: for every constant name that two or more imported modules declare, which copy the environment holds.
Reads each module's own ModuleData (its constNames/constants) and compares that copy's type and value with the
environment's constant (`Expr` structural equality), and records which module `getModuleIdxFor?` attributes it to.

Usage: dups <searchpath-file> <out.tsv> <Module>...
Writes: name, module_idx, module, kind, attributed (1 = getModuleIdxFor? names this module), env_copy (1 = this module's
        object is the environment's constant), type_hash_eq, value_hash_eq (Expr.hash equal to the environment's), n_copies. Same name rendering as RouteB.
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

def b (x : Bool) : String := if x then "1" else "0"

/-- The environment's map holds the very `ConstantInfo` object of the module whose copy won: pointer identity says
exactly which copy that is. (Structural `==` on two separate copies can take exponential time on shared DAGs.) -/
unsafe def ptrSameImpl (x y : ConstantInfo) : Bool := ptrAddrUnsafe x == ptrAddrUnsafe y
@[implemented_by ptrSameImpl] opaque ptrSame (x y : ConstantInfo) : Bool

def valHash (c : ConstantInfo) : UInt64 := match c.value? (allowOpaque := true) with
  | some v => v.hash
  | none => 0

def main (args : List String) : IO UInt32 := do
  let spFile :: outPath :: mods := args
    | IO.eprintln "usage: dups <searchpath-file> <out.tsv> <Module>..."; return 2
  let dirs := (← IO.FS.lines spFile).filter (!·.isEmpty) |>.toList.map System.FilePath.mk
  searchPathRef.set dirs
  let t0 ← IO.monoMsNow
  let env ← importModules (mods.toArray.map fun m => { module := m.toName }) {} (loadExts := false) (level := .private)
    (leakEnv := true)
  let data := env.header.moduleData
  IO.eprintln s!"imported {data.size} modules in {(← IO.monoMsNow) - t0} ms"
  let mut locs : Std.HashMap Name (Array (Nat × Nat)) := {}
  let mut total := 0
  for h : i in [:data.size] do
    let md := data[i]
    for h2 : j in [:md.constNames.size] do
      let n := md.constNames[j]
      total := total + 1
      locs := match locs.get? n with
        | some a => locs.insert n (a.push (i, j))
        | none => locs.insert n #[(i, j)]
  IO.eprintln s!"{total} module constants, {locs.size} names, {(← IO.monoMsNow) - t0} ms"
  let h ← IO.FS.Handle.mk outPath .write
  h.putStrLn "name\tmodule_idx\tmodule\tkind\tattributed\tenv_copy\ttype_hash_eq\tvalue_hash_eq\tn_copies"
  let mut ndup := 0
  let mut nrows := 0
  for (n, ls) in locs do
    if ls.size < 2 then continue
    ndup := ndup + 1
    let some e := env.find? n | continue
    let attr := match env.getModuleIdxFor? n with
      | some m => m.toNat
      | none => data.size
    for (i, j) in ls do
      let c := data[i]!.constants[j]!
      let teq := c.type.hash == e.type.hash
      let veq := valHash c == valHash e
      h.putStrLn s!"{render n}\t{i}\t{render env.header.moduleNames[i]!}\t{kindOf c}\t{b (attr == i)}\t{b (ptrSame c e)}\t{b teq}\t{b veq}\t{ls.size}"
      nrows := nrows + 1
  h.flush
  IO.eprintln s!"names in 2+ modules: {ndup}; rows {nrows}; done in {(← IO.monoMsNow) - t0} ms"
  return 0
