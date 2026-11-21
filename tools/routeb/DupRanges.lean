/-
DupRanges: for every constant name that two or more imported modules declare, the environment's copy (pointer identity,
as in Dups.lean) and that copy's declaration range, read from its own module's `declRangeExt` entries (getModuleIdxFor?
and so `declRangeExt.find?` name the first copy, which is not the environment's).

Usage: dupranges <searchpath-file> <out.tsv> <Module>...
Writes: name, winner_idx, winner_module, n_copies, start_line, end_line, sel_line, sel_col (empty when the winning module
        has no range entry for the name).
-/
import Lean
open Lean

def render (n : Name) : String :=
  ((n.toString (escape := false)).replace "\t" "\\t").replace "\n" "\\n"

unsafe def ptrSameImpl (x y : ConstantInfo) : Bool := ptrAddrUnsafe x == ptrAddrUnsafe y
@[implemented_by ptrSameImpl] opaque ptrSame (x y : ConstantInfo) : Bool

def main (args : List String) : IO UInt32 := do
  let spFile :: outPath :: mods := args
    | IO.eprintln "usage: dupranges <searchpath-file> <out.tsv> <Module>..."; return 2
  let dirs := (← IO.FS.lines spFile).filter (!·.isEmpty) |>.toList.map System.FilePath.mk
  searchPathRef.set dirs
  let t0 ← IO.monoMsNow
  let env ← importModules (mods.toArray.map fun m => { module := m.toName }) {} (loadExts := false) (level := .private)
    (leakEnv := true)
  let data := env.header.moduleData
  let mut locs : Std.HashMap Name (Array (Nat × Nat)) := {}
  for h : i in [:data.size] do
    let md := data[i]
    for h2 : j in [:md.constNames.size] do
      let n := md.constNames[j]
      locs := match locs.get? n with
        | some a => locs.insert n (a.push (i, j))
        | none => locs.insert n #[(i, j)]
  IO.eprintln s!"indexed {locs.size} names in {(← IO.monoMsNow) - t0} ms"
  let mut cache : Std.HashMap Nat (Std.HashMap Name DeclarationRanges) := {}
  let mut out : Array String := #[]
  let mut nw := 0
  for (n, ls) in locs do
    if ls.size < 2 then continue
    let some e := env.find? n | continue
    let mut win : Option Nat := none
    for (i, j) in ls do
      if ptrSame data[i]!.constants[j]! e then win := some i
    let some w := win | continue
    nw := nw + 1
    if !cache.contains w then
      let mut m : Std.HashMap Name DeclarationRanges := {}
      for (k, r) in declRangeExt.getModuleEntries (level := .server) env w do
        m := m.insert k r
      for (k, r) in declRangeExt.getModuleEntries (level := .private) env w do
        if !m.contains k then m := m.insert k r
      cache := cache.insert w m
    let rng := match cache[w]?.bind (·.get? n) with
      | some r => s!"{r.range.pos.line}\t{r.range.endPos.line}\t{r.selectionRange.pos.line}\t{r.selectionRange.pos.column}"
      | none => "\t\t\t"
    out := out.push s!"{render n}\t{w}\t{render env.header.moduleNames[w]!}\t{ls.size}\t{rng}"
  let h ← IO.FS.Handle.mk outPath .write
  h.putStrLn "name\twinner_idx\twinner_module\tn_copies\tstart_line\tend_line\tsel_line\tsel_col"
  for s in out.qsort (· < ·) do
    h.putStrLn s
  h.flush
  IO.eprintln s!"names with 2+ copies and an identified winner: {nw}; done in {(← IO.monoMsNow) - t0} ms"
  return 0
