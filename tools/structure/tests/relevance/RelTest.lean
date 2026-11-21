-- Hand-checkable cases for the unused-hypothesis analysis (core Lean only). Expected results: tests/relevance/expected.tsv
namespace RelTest

def P (n : Nat) : Prop := n = n
def MySet (α : Type) := α → Prop

-- position 1 (h) is unused
theorem lemA (n : Nat) (h : n > 0) : P n := rfl
-- h feeds only lemA's unused position, so it is unused too; h2 is unused
theorem lemB (n : Nat) (h : n > 0) (h2 : n ≠ 5) : P n := lemA n h
-- h is used
theorem lemC (n : Nat) (h : n > 0) : 0 < n := h
-- h occurs in the conclusion: used
theorem inConcl (n : Nat) (h : 0 < n) : (⟨0, h⟩ : Fin n).val = 0 := rfl
-- h occurs in a later binder's type: used
theorem inLater (n : Nat) (h : 0 < n) (x : Fin n) (hx : x = ⟨0, h⟩) : True := trivial
-- a def with an unused Prop argument
def f (n : Nat) (h : 0 < n) : Nat := n + 1
-- eta-reduced value: positions are forwarded to lemA, so position 1 is unused
theorem etaA : ∀ n : Nat, n > 0 → P n := lemA
-- Prop-ness through a bound variable's type and through a def that unfolds to a function type
theorem viaVar (s : Nat → Prop) (x : Nat) (h : s x) : True := trivial
theorem viaDef (s : MySet Nat) (x : Nat) (h : s x) : True := trivial
-- a binder whose sort depends on a universe parameter: not known to be Prop, so never a candidate
theorem univ.{u} {α : Sort u} (a : α) : True := trivial
-- unused non-Prop binder: not a candidate
theorem dataUnused (n m : Nat) : P n := rfl
-- auto/opt params are Props when their underlying type is
theorem optP (n : Nat) (h : n = n := rfl) : P n := rfl

-- what the roots pull in
theorem expensive : 1 > 0 := Nat.zero_lt_one
theorem alsoExpensive : (1 : Nat) ≠ 5 := by decide
theorem root1 : P 1 := lemB 1 expensive alsoExpensive
theorem root2 : f 1 Nat.one_pos = 2 := rfl
theorem root3 : 0 < 1 := lemC 1 Nat.one_pos
theorem root4 : P 2 := etaA 2 (Nat.zero_lt_succ 1)
theorem root5 : True := viaVar (fun _ => True) 0 trivial
-- a `have` whose variable is never used: its value is dead code (only the deadlet variant drops it)
set_option linter.unusedVariables false in
theorem deadHave : P 3 := by
  have hx : (1 : Nat) ≠ 5 := alsoExpensive
  exact rfl
theorem root6 : P 3 := deadHave

-- duplicates (tests/test_dupes.py): dupA and dupB state the same thing; twin1 and twin2 are the same definition, so
-- tw1 and tw2 state the same thing up to renaming twin2 to twin1; natTwin restates core's Nat.succ_ne_zero
theorem dupA : 1 + 1 = 2 := rfl
theorem dupB : 1 + 1 = 2 := by decide
def twin1 (n : Nat) : Nat := n + 1
def twin2 (m : Nat) : Nat := m + 1
theorem tw1 : twin1 3 = 4 := rfl
theorem tw2 : twin2 3 = 4 := rfl
theorem natTwin (n : Nat) : n.succ ≠ 0 := Nat.succ_ne_zero n
-- tests/test_xtwins.py: equal to Keys.bigLit (literal values must be compared); bigLitOff differs only in a digit
theorem bigLit : (123456789012345678901234567890 : Nat) + 1 = 123456789012345678901234567891 := by decide
theorem bigLitOff : (123456789012345678901234567890 : Nat) + 2 = 123456789012345678901234567892 := by decide

end RelTest
