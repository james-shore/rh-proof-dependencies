//! A small weak-head evaluator over the export's expression DAG, used only to decide whether a binder's type is a
//! proposition (its sort is `Prop`). Closures carry a de Bruijn environment and a universe substitution, so nothing
//! is ever instantiated or copied. Anything it cannot decide within its fuel is `Unknown`.

use export_parse::*;
use std::rc::Rc;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Tri {
    Yes,
    No,
    Unknown,
}

#[derive(Clone)]
pub enum Val {
    Clo(u32, Ctx, LS),
    Var(u32),
}
pub struct Cell {
    v: Val,
    next: Ctx,
}
pub type Ctx = Option<Rc<Cell>>;

/// Universe substitution: level parameter names of the constant whose body we are in, mapped to levels that are
/// themselves interpreted in an outer substitution. `None` = the analysed constant's own (free) parameters.
pub type LS = Option<Rc<LSub>>;
pub struct LSub {
    params: Vec<u32>,
    vals: Vec<(u32, LS)>,
}

pub fn push(c: &Ctx, v: Val) -> Ctx {
    Some(Rc::new(Cell { v, next: c.clone() }))
}
fn get(c: &Ctx, mut i: u32) -> Option<&Val> {
    let mut cur = c.as_ref();
    while let Some(cell) = cur {
        if i == 0 {
            return Some(&cell.v);
        }
        i -= 1;
        cur = cell.next.as_ref();
    }
    None
}

pub enum Hnf {
    Sort(u32, LS),
    Pi { body: u32, ctx: Ctx, ls: LS },
    Lam,
    Const { c: u32, us_off: u32, ls: LS, args: Vec<Val> },
    Var { k: u32, args: Vec<Val> },
    Other,
}

pub struct Eval<'a> {
    pub ex: &'a [E],
    pub aux: &'a [u32],
    pub us: &'a [u32],
    pub levels: &'a [Lv],
    pub consts: &'a [Cst],
    pub cid: &'a [u32],
    pub fuel: u32,
    pub deltas: u32,
}

impl<'a> Eval<'a> {
    fn subst(&self, c: u32, us_off: u32, ls: &LS) -> LS {
        let lps = &self.consts[c as usize].lps;
        if lps.is_empty() {
            return None;
        }
        let n = if us_off == 0 { 0 } else { self.us[us_off as usize] as usize };
        let vals: Vec<(u32, LS)> = (0..n).map(|k| (self.us[us_off as usize + 1 + k], ls.clone())).collect();
        Some(Rc::new(LSub { params: lps.clone(), vals }))
    }

    pub fn level_zero(&self, l: u32, ls: &LS, depth: u32) -> Tri {
        if depth > 200 {
            return Tri::Unknown;
        }
        let lv = self.levels.get(l as usize).copied().unwrap_or_default();
        let and = |a: Tri, b: Tri| match (a, b) {
            (Tri::No, _) | (_, Tri::No) => Tri::No,
            (Tri::Yes, Tri::Yes) => Tri::Yes,
            _ => Tri::Unknown,
        };
        match lv.tag {
            L_ZERO => Tri::Yes,
            L_SUCC => Tri::No,
            L_MAX => and(self.level_zero(lv.a, ls, depth + 1), self.level_zero(lv.b, ls, depth + 1)),
            L_IMAX => self.level_zero(lv.b, ls, depth + 1),
            _ => match ls {
                Some(s) => match s.params.iter().position(|&p| p == lv.a) {
                    Some(k) if k < s.vals.len() => {
                        let (l2, ls2) = &s.vals[k];
                        self.level_zero(*l2, ls2, depth + 1)
                    }
                    _ => Tri::Unknown,
                },
                None => Tri::Unknown,
            },
        }
    }

    pub fn whnf(&mut self, v: &Val, delta: bool) -> Hnf {
        let (mut e, mut ctx, mut ls) = match v {
            Val::Var(k) => return Hnf::Var { k: *k, args: vec![] },
            Val::Clo(e, c, l) => (*e, c.clone(), l.clone()),
        };
        let mut stack: Vec<Val> = Vec::new();
        loop {
            if self.fuel == 0 {
                return Hnf::Other;
            }
            self.fuel -= 1;
            let x = self.ex[e as usize];
            match x.tag {
                T_BVAR => match get(&ctx, x.a) {
                    Some(Val::Clo(e2, c2, l2)) => {
                        let (e2, c2, l2) = (*e2, c2.clone(), l2.clone());
                        e = e2;
                        ctx = c2;
                        ls = l2;
                    }
                    Some(Val::Var(k)) => {
                        stack.reverse();
                        return Hnf::Var { k: *k, args: stack };
                    }
                    None => return Hnf::Other,
                },
                T_APP => {
                    stack.push(Val::Clo(x.b, ctx.clone(), ls.clone()));
                    e = x.a;
                }
                T_LAM => match stack.pop() {
                    Some(arg) => {
                        ctx = push(&ctx, arg);
                        e = x.b;
                    }
                    None => return Hnf::Lam,
                },
                T_LET => {
                    ctx = push(&ctx, Val::Clo(x.b, ctx.clone(), ls.clone()));
                    e = x.c;
                }
                T_MDATA => e = x.a,
                T_CONST => {
                    let c = self.cid[x.a as usize];
                    if c == u32::MAX {
                        return Hnf::Other;
                    }
                    let k = &self.consts[c as usize];
                    if delta && k.kind == K_DEF && !k.vals.is_empty() && self.deltas > 0 {
                        self.deltas -= 1;
                        ls = self.subst(c, self.aux[e as usize], &ls);
                        ctx = None;
                        e = k.vals[0];
                    } else {
                        stack.reverse();
                        return Hnf::Const { c, us_off: self.aux[e as usize], ls, args: stack };
                    }
                }
                T_SORT => return if stack.is_empty() { Hnf::Sort(x.a, ls) } else { Hnf::Other },
                T_PI => return if stack.is_empty() { Hnf::Pi { body: x.b, ctx, ls } } else { Hnf::Other },
                _ => return Hnf::Other,
            }
        }
    }

    /// Is the type `t` a proposition? `vars[k]` is the type of free variable k.
    pub fn is_prop(&mut self, t: &Val, vars: &mut Vec<Val>) -> Tri {
        match self.whnf(t, false) {
            Hnf::Sort(..) | Hnf::Lam => Tri::No,
            Hnf::Other => Tri::Unknown,
            Hnf::Pi { body, ctx, ls } => {
                let e = match t {
                    Val::Clo(..) => self.pi_domain(t),
                    _ => None,
                };
                let dom = match e {
                    Some(d) => d,
                    None => return Tri::Unknown,
                };
                let k = vars.len() as u32;
                vars.push(dom);
                let r = self.is_prop(&Val::Clo(body, push(&ctx, Val::Var(k)), ls), vars);
                vars.pop();
                r
            }
            Hnf::Const { c, us_off, ls, args } => {
                let ty = Val::Clo(self.consts[c as usize].ty, None, self.subst(c, us_off, &ls));
                self.sort_after(ty, args)
            }
            Hnf::Var { k, args } => match vars.get(k as usize) {
                Some(ty) => {
                    let ty = ty.clone();
                    self.sort_after(ty, args)
                }
                None => Tri::Unknown,
            },
        }
    }

    /// The domain of the forall that `t` reduces to (without delta), as a closure.
    fn pi_domain(&mut self, t: &Val) -> Option<Val> {
        let (mut e, mut ctx, mut ls) = match t {
            Val::Clo(e, c, l) => (*e, c.clone(), l.clone()),
            _ => return None,
        };
        let mut stack: Vec<Val> = Vec::new();
        for _ in 0..100_000 {
            let x = self.ex[e as usize];
            match x.tag {
                T_BVAR => match get(&ctx, x.a) {
                    Some(Val::Clo(e2, c2, l2)) => {
                        let (e2, c2, l2) = (*e2, c2.clone(), l2.clone());
                        e = e2;
                        ctx = c2;
                        ls = l2;
                    }
                    _ => return None,
                },
                T_APP => {
                    stack.push(Val::Clo(x.b, ctx.clone(), ls.clone()));
                    e = x.a;
                }
                T_LAM => {
                    let a = stack.pop()?;
                    ctx = push(&ctx, a);
                    e = x.b;
                }
                T_LET => {
                    ctx = push(&ctx, Val::Clo(x.b, ctx.clone(), ls.clone()));
                    e = x.c;
                }
                T_MDATA => e = x.a,
                T_PI => return Some(Val::Clo(x.a, ctx, ls)),
                _ => return None,
            }
        }
        None
    }

    /// The sort of `f a1 … an` given `ty` = the type of `f`: peel one forall per argument, then expect a Sort.
    fn sort_after(&mut self, mut ty: Val, args: Vec<Val>) -> Tri {
        for a in args {
            match self.whnf(&ty, true) {
                Hnf::Pi { body, ctx, ls } => ty = Val::Clo(body, push(&ctx, a), ls),
                _ => return Tri::Unknown,
            }
        }
        match self.whnf(&ty, true) {
            Hnf::Sort(l, ls) => self.level_zero(l, &ls, 0),
            _ => Tri::Unknown,
        }
    }
}
