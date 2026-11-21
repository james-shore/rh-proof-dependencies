// Unused hypotheses and the relevance-pruned cone, from the export's proof terms.
// Usage: zstd -dc --long=30 solution.ndjson.zst | relevance OUTDIR ROOT1,ROOT2,... PROJECT
//   PROJECT: a file of project constant names (one per line), or "prefix:<P>" for names starting with P.
//   Env: DEADLET=1 also runs the variant that drops `let` values whose variable is never relevantly used.
//        DRAG_TOP (default 3000): how many callees get the exact "drags in" closure.
//        WATCH_BINDERS=O,…: also list every binder with one of these names (used where, Prop or not, removable).
// Method (see docs/structure-method.md):
//   A position i of a project def/theorem c (not a root) is removable iff binder i of c's type is a proposition,
//   does not occur in the types of later binders or the conclusion, and does not occur relevantly in c's value.
//   An occurrence is irrelevant when it sits inside the argument at a removable position of an applied constant.
//   The kernel graph is acyclic and the export lists dependencies first, so one pass in export order reaches the
//   least fixed point. Constructors, recursors, opaque constants, projections, roots and non-project constants
//   use every argument.

use export_parse::*;
use rayon::prelude::*;
use proof_structure::eval::*;
use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::{self, BufWriter, Write};

const NONE: u32 = u32::MAX;

/// Loose bound-variable sets per expression node: bits 0..63 inline, wider sets on the side.
struct Sets {
    mask: Vec<u64>,
    is_wide: Vec<u64>,
    wide: HashMap<u32, Box<[u64]>>,
}
impl Sets {
    fn new(n: usize) -> Sets {
        Sets { mask: vec![0; n], is_wide: vec![0; n / 64 + 1], wide: HashMap::new() }
    }
    #[inline]
    fn view(&self, i: u32) -> &[u64] {
        if self.is_wide[(i >> 6) as usize] >> (i & 63) & 1 == 1 {
            &self.wide[&i]
        } else {
            std::slice::from_ref(&self.mask[i as usize])
        }
    }
    #[inline]
    fn has(&self, i: u32, b: u32) -> bool {
        let v = self.view(i);
        let w = (b >> 6) as usize;
        w < v.len() && v[w] >> (b & 63) & 1 == 1
    }
    fn store(&mut self, i: u32, acc: &mut Vec<u64>) {
        while acc.len() > 1 && *acc.last().unwrap() == 0 {
            acc.pop();
        }
        if acc.len() <= 1 {
            self.mask[i as usize] = acc.first().copied().unwrap_or(0);
        } else {
            self.mask[i as usize] = acc[0];
            self.is_wide[(i >> 6) as usize] |= 1 << (i & 63);
            self.wide.insert(i, acc.clone().into_boxed_slice());
        }
    }
}
#[inline]
fn or_into(acc: &mut Vec<u64>, v: &[u64]) {
    if acc.len() < v.len() {
        acc.resize(v.len(), 0);
    }
    for (a, b) in acc.iter_mut().zip(v) {
        *a |= *b;
    }
}
#[inline]
fn or_shift_into(acc: &mut Vec<u64>, v: &[u64]) {
    if acc.len() < v.len() {
        acc.resize(v.len(), 0);
    }
    for k in 0..v.len() {
        let hi = if k + 1 < v.len() { v[k + 1] << 63 } else { 0 };
        acc[k] |= (v[k] >> 1) | hi;
    }
}
fn set_bit(acc: &mut Vec<u64>, b: u32) {
    let w = (b >> 6) as usize;
    if acc.len() <= w {
        acc.resize(w + 1, 0);
    }
    acc[w] |= 1 << (b & 63);
}
fn bits(v: &[u64]) -> impl Iterator<Item = u32> + '_ {
    v.iter().enumerate().flat_map(|(k, &w)| {
        let mut w = w;
        std::iter::from_fn(move || {
            if w == 0 {
                return None;
            }
            let t = w.trailing_zeros();
            w &= w - 1;
            Some(k as u32 * 64 + t)
        })
    })
}

struct Ctx<'a> {
    ex: &'a [E],
    cid: &'a [u32],
    head: Vec<u32>,
    pos: Vec<u16>,
}

struct Walker {
    bits: Vec<u64>,
    touched: Vec<u32>,
    stack: Vec<u32>,
}
thread_local! {
    static WALKER: RefCell<Option<Walker>> = RefCell::new(None);
}

#[derive(Default, Clone)]
struct WalkOut {
    refs: Vec<u32>,
    nat: bool,
    str_: bool,
    irr: Vec<(u32, u32, u16)>, // (callee, argument node, position)
}

struct Variant<'a> {
    c: &'a Ctx<'a>,
    rl: Sets,
    done: Vec<u64>,
    unused: Vec<u64>,
    deadlet: bool,
}

impl<'a> Variant<'a> {
    #[inline]
    fn arg_relevant(&self, i: u32) -> bool {
        let h = self.c.head[i as usize];
        let p = self.c.pos[i as usize];
        !(h != NONE && p < 64 && self.unused[h as usize] >> p & 1 == 1)
    }
    #[inline]
    fn is_done(&self, i: u32) -> bool {
        self.done[(i >> 6) as usize] >> (i & 63) & 1 == 1
    }
    /// Relevant loose bvars of every node reachable from `root` along relevant edges (memoised).
    fn rl_compute(&mut self, root: u32, acc: &mut Vec<u64>, st: &mut Vec<(u32, u8)>) {
        if self.is_done(root) {
            return;
        }
        let ex = self.c.ex;
        st.clear();
        st.push((root, 0));
        while let Some((i, state)) = st.pop() {
            if self.is_done(i) {
                continue;
            }
            let e = ex[i as usize];
            if state == 0 {
                st.push((i, 1));
                match e.tag {
                    T_APP => {
                        st.push((e.a, 0));
                        if self.arg_relevant(i) {
                            st.push((e.b, 0));
                        }
                    }
                    T_LAM | T_PI => {
                        st.push((e.a, 0));
                        st.push((e.b, 0));
                    }
                    T_LET => {
                        st.push((e.c, 0));
                        if !self.deadlet {
                            st.push((e.a, 0));
                            st.push((e.b, 0));
                        }
                    }
                    T_PROJ | T_MDATA => st.push((e.a, 0)),
                    _ => {}
                }
                continue;
            }
            if state == 1 && e.tag == T_LET && self.deadlet && self.rl.has(e.c, 0) && !(self.is_done(e.a) && self.is_done(e.b)) {
                st.push((i, 2));
                st.push((e.a, 0));
                st.push((e.b, 0));
                continue;
            }
            acc.clear();
            match e.tag {
                T_BVAR => set_bit(acc, e.a),
                T_APP => {
                    or_into(acc, self.rl.view(e.a));
                    if self.arg_relevant(i) {
                        or_into(acc, self.rl.view(e.b));
                    }
                }
                T_LAM | T_PI => {
                    or_into(acc, self.rl.view(e.a));
                    or_shift_into(acc, self.rl.view(e.b));
                }
                T_LET => {
                    if !self.deadlet || self.rl.has(e.c, 0) {
                        or_into(acc, self.rl.view(e.a));
                        or_into(acc, self.rl.view(e.b));
                    }
                    or_shift_into(acc, self.rl.view(e.c));
                }
                T_PROJ | T_MDATA => or_into(acc, self.rl.view(e.a)),
                _ => {}
            }
            self.rl.store(i, acc);
            self.done[(i >> 6) as usize] |= 1 << (i & 63);
        }
    }

    fn walk(&self, w: &mut Walker, roots: &[u32], rel: bool) -> WalkOut {
        let ex = self.c.ex;
        let mut o = WalkOut::default();
        macro_rules! visit {
            ($i:expr) => {{
                let i = $i as usize;
                let (wd, bt) = (i >> 6, 1u64 << (i & 63));
                if w.bits[wd] & bt == 0 {
                    w.bits[wd] |= bt;
                    w.touched.push(i as u32);
                    w.stack.push(i as u32);
                }
            }};
        }
        for &r in roots {
            visit!(r);
        }
        while let Some(i) = w.stack.pop() {
            let e = ex[i as usize];
            match e.tag {
                T_CONST => {
                    let c = self.c.cid[e.a as usize];
                    if c != NONE {
                        o.refs.push(c);
                    }
                }
                T_APP => {
                    visit!(e.a);
                    if !rel || self.arg_relevant(i) {
                        visit!(e.b);
                    } else {
                        o.irr.push((self.c.head[i as usize], e.b, self.c.pos[i as usize]));
                    }
                }
                T_LAM | T_PI => {
                    visit!(e.a);
                    visit!(e.b);
                }
                T_LET => {
                    visit!(e.c);
                    if !(rel && self.deadlet && !self.rl.has(e.c, 0)) {
                        visit!(e.a);
                        visit!(e.b);
                    }
                }
                T_PROJ | T_MDATA => visit!(e.a),
                T_NAT => o.nat = true,
                T_STR => o.str_ = true,
                _ => {}
            }
        }
        for &t in &w.touched {
            w.bits[(t >> 6) as usize] = 0;
        }
        w.touched.clear();
        o.refs.sort_unstable();
        o.refs.dedup();
        o
    }
}

fn with_walker<R>(nex: usize, f: impl FnOnce(&mut Walker) -> R) -> R {
    WALKER.with(|cell| {
        let mut b = cell.borrow_mut();
        if b.is_none() {
            *b = Some(Walker { bits: vec![0; nex / 64 + 1], touched: Vec::new(), stack: Vec::new() });
        }
        f(b.as_mut().unwrap())
    })
}

/// Leading binders of `root` of the given tag (forallE or lam), at most `max`: (binder nodes, body).
fn telescope(ex: &[E], mut root: u32, tag: u8, max: usize) -> (Vec<u32>, u32) {
    let mut bs = Vec::new();
    while bs.len() < max && ex[root as usize].tag == tag {
        bs.push(root);
        root = ex[root as usize].b;
    }
    (bs, root)
}

struct Info {
    m: u32,
    k: u32,
    cand: u32,
    cand_prop: u32,
    cand_unknown: u32,
    names: Vec<(u32, u32)>, // (position, binder name index) of removable positions
}

fn main() -> io::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 4 {
        eprintln!("usage: relevance OUTDIR ROOTS PROJECT  (export on stdin)");
        std::process::exit(2);
    }
    let outdir = args[1].clone();
    let root_names: Vec<String> = args[2].split(',').map(|s| s.to_string()).collect();
    let project_arg = args[3].clone();
    let deadlet_too = std::env::var("DEADLET").map(|v| v == "1").unwrap_or(false);
    let drag_top: usize = std::env::var("DRAG_TOP").ok().and_then(|s| s.parse().ok()).unwrap_or(3000);
    let watch: Vec<String> = std::env::var("WATCH_BINDERS").map(|v| v.split(',').filter(|x| !x.is_empty()).map(|x| x.to_string()).collect()).unwrap_or_default();
    let nworkers: usize = std::env::var("WORKERS").ok().and_then(|s| s.parse().ok()).unwrap_or(8);
    let expr_cap: usize = std::env::var("EXPR_CAP").ok().and_then(|s| s.parse().ok()).unwrap_or(1_100_000_000);
    let mut lg = Log::new();
    macro_rules! say {
        ($($a:tt)*) => {{ lg.say(format!($($a)*)); }};
    }
    let p = parse_stdin(nworkers, expr_cap.min(4_000_000_000), true, &mut lg)?;
    let xd = p.detail.as_ref().unwrap();
    let ex = &p.exprs[..];
    let n = p.consts.len();
    let nex = ex.len();
    let by_name: HashMap<&str, u32> = p.cname.iter().enumerate().map(|(i, s)| (s.as_str(), i as u32)).collect();
    let mut project = vec![false; n];
    if let Some(pre) = project_arg.strip_prefix("prefix:") {
        for i in 0..n {
            project[i] = p.cname[i].starts_with(pre);
        }
    } else {
        let txt = std::fs::read_to_string(&project_arg)?;
        let mut miss = 0;
        for l in txt.lines() {
            match by_name.get(l.trim_end_matches('\r')) {
                Some(&i) => project[i as usize] = true,
                None => miss += 1,
            }
        }
        say!("project names not in export: {miss}");
    }
    say!("project constants: {}", project.iter().filter(|&&b| b).count());
    let mut roots = Vec::new();
    for r in &root_names {
        match by_name.get(r.as_str()) {
            Some(&i) => roots.push(i),
            None => say!("ROOT NOT FOUND: {r}"),
        }
    }
    let is_root: HashSet<u32> = roots.iter().copied().collect();

    // heads and argument positions of application nodes
    let mut head = vec![NONE; nex];
    let mut pos = vec![0u16; nex];
    for i in 0..nex {
        let e = ex[i];
        if e.tag == T_APP {
            let f = ex[e.a as usize];
            if f.tag == T_APP {
                head[i] = head[e.a as usize];
                pos[i] = pos[e.a as usize].saturating_add(1);
            } else if f.tag == T_CONST {
                head[i] = p.cid_of_name[f.a as usize];
            }
        }
    }
    let ctx = Ctx { ex, cid: &p.cid_of_name, head, pos };
    say!("application heads done");

    // plain loose bvars (any occurrence), in index order: children always have smaller indices
    let mut lb = Sets::new(nex);
    {
        let mut acc: Vec<u64> = Vec::new();
        for i in 0..nex as u32 {
            let e = ex[i as usize];
            acc.clear();
            match e.tag {
                T_BVAR => set_bit(&mut acc, e.a),
                T_APP => {
                    or_into(&mut acc, lb.view(e.a));
                    or_into(&mut acc, lb.view(e.b));
                }
                T_LAM | T_PI => {
                    or_into(&mut acc, lb.view(e.a));
                    or_shift_into(&mut acc, lb.view(e.b));
                }
                T_LET => {
                    or_into(&mut acc, lb.view(e.a));
                    or_into(&mut acc, lb.view(e.b));
                    or_shift_into(&mut acc, lb.view(e.c));
                }
                T_PROJ | T_MDATA => or_into(&mut acc, lb.view(e.a)),
                _ => {}
            }
            if !acc.is_empty() {
                lb.store(i, &mut acc);
            }
        }
    }
    say!("plain loose-bvar sets done ({} wide nodes)", lb.wide.len());

    // block members (inductive records; all quot constants form one block) for closures
    let mut block = p.rec_of.clone();
    let quots: Vec<usize> = (0..n).filter(|&i| p.consts[i].kind == K_QUOT).collect();
    for &q in &quots {
        block[q] = p.rec_of[quots[0]];
    }
    let mut members: HashMap<u32, Vec<u32>> = HashMap::new();
    for i in 0..n {
        members.entry(block[i]).or_default().push(i as u32);
    }
    let lit_nat = by_name.get("Nat").copied();
    let lit_str: Vec<u32> = ["Char.ofNat", "String.ofList"].iter().filter_map(|s| by_name.get(*s).copied()).collect();
    let eq = by_name.get("Eq").copied();

    let variants: Vec<bool> = if deadlet_too { vec![false, true] } else { vec![false] };
    let mut kernel_cone: Option<Vec<bool>> = None;
    for deadlet in variants {
        let tag = if deadlet { "deadlet" } else { "binders" };
        say!("=== variant {tag} ===");
        let mut v = Variant { c: &ctx, rl: Sets::new(nex), done: vec![0; nex / 64 + 1], unused: vec![0; n], deadlet };
        let mut info: HashMap<u32, Info> = HashMap::new();
        let mut watch_rows: Vec<String> = Vec::new();
        let (mut n_an, mut n_bind, mut n_cand, mut n_yes, mut n_unk, mut n_rm, mut n_c_rm, mut n_proj_like) = (0u64, 0u64, 0u64, 0u64, 0u64, 0u64, 0u64, 0u64);
        let mut acc: Vec<u64> = Vec::new();
        let mut st: Vec<(u32, u8)> = Vec::new();
        for d in 0..n {
            let c = &p.consts[d];
            v.rl_compute(c.ty, &mut acc, &mut st);
            for &x in &c.vals {
                v.rl_compute(x, &mut acc, &mut st);
            }
            if !project[d] || is_root.contains(&(d as u32)) || !(c.kind == K_DEF || c.kind == K_THM) || c.vals.is_empty() {
                continue;
            }
            let (tb, r) = telescope(ex, c.ty, T_PI, usize::MAX);
            let m = tb.len();
            if m == 0 {
                continue;
            }
            n_an += 1;
            n_bind += m as u64;
            let (vb, body) = telescope(ex, c.vals[0], T_LAM, m);
            let k = vb.len();
            if c.kind == K_DEF && k == m {
                let b = ex[body as usize];
                if b.tag == T_PROJ && ex[b.a as usize].tag == T_BVAR && ex[b.a as usize].a == 0 {
                    n_proj_like += 1;
                    continue;
                }
            }
            let mut used = vec![false; m];
            for j in 0..m {
                for b in bits(lb.view(ex[tb[j] as usize].a)) {
                    if (b as usize) < j {
                        used[j - 1 - b as usize] = true;
                    }
                }
            }
            for b in bits(lb.view(r)) {
                if (b as usize) < m {
                    used[m - 1 - b as usize] = true;
                }
            }
            let used_type = used.clone();
            let mut used_plain = vec![false; m];
            if !watch.is_empty() {
                for j in 0..k {
                    for b in bits(lb.view(ex[vb[j] as usize].a)) {
                        if (b as usize) < j {
                            used_plain[j - 1 - b as usize] = true;
                        }
                    }
                }
                for b in bits(lb.view(body)) {
                    if (b as usize) < k {
                        used_plain[k - 1 - b as usize] = true;
                    }
                }
                for i in k..m {
                    used_plain[i] = true;
                }
            }
            for j in 0..k {
                for b in bits(v.rl.view(ex[vb[j] as usize].a)) {
                    if (b as usize) < j {
                        used[j - 1 - b as usize] = true;
                    }
                }
            }
            for b in bits(v.rl.view(body)) {
                if (b as usize) < k {
                    used[k - 1 - b as usize] = true;
                }
            }
            if k < m {
                // eta: positions k.. are passed on to the body's head
                let be = ex[body as usize];
                let (h, na) = if be.tag == T_APP {
                    (ctx.head[body as usize], ctx.pos[body as usize] as u32 + 1)
                } else if be.tag == T_CONST {
                    (p.cid_of_name[be.a as usize], 0)
                } else {
                    (NONE, 0)
                };
                for i in k..m {
                    let q = na + (i - k) as u32;
                    if !(h != NONE && q < 64 && v.unused[h as usize] >> q & 1 == 1) {
                        used[i] = true;
                    }
                }
            }
            if !watch.is_empty() {
                let wi: Vec<usize> = (0..m).filter(|&i| watch.iter().any(|w| *w == render(&p.names, xd.aux[tb[i] as usize]))).collect();
                if let Some(&last) = wi.last() {
                    let mut ev = Eval { ex, aux: &xd.aux, us: &xd.us, levels: &xd.levels, consts: &p.consts, cid: &p.cid_of_name, fuel: 0, deltas: 0 };
                    let mut env: proof_structure::eval::Ctx = None;
                    let mut vars: Vec<Val> = Vec::new();
                    for i in 0..=last {
                        let ti = ex[tb[i] as usize].a;
                        if wi.contains(&i) {
                            ev.fuel = 20_000;
                            ev.deltas = 64;
                            let pr = ev.is_prop(&Val::Clo(ti, env.clone(), None), &mut vars);
                            watch_rows.push(format!(
                                "{}\t{}\t{i}\t{}\t{:?}\t{}\t{}\t{}\t{}",
                                p.cname[d], KIND_NAMES[c.kind as usize], render(&p.names, xd.aux[tb[i] as usize]), pr,
                                used_type[i] as u8, used_plain[i] as u8, used[i] as u8,
                                (!used[i] && pr == Tri::Yes && i < 64) as u8
                            ));
                        }
                        vars.push(Val::Clo(ti, env.clone(), None));
                        env = push(&env, Val::Var(i as u32));
                    }
                }
            }
            let cands: Vec<usize> = (0..m).filter(|&i| !used[i]).collect();
            if cands.is_empty() {
                continue;
            }
            n_cand += cands.len() as u64;
            let mut ev = Eval { ex, aux: &xd.aux, us: &xd.us, levels: &xd.levels, consts: &p.consts, cid: &p.cid_of_name, fuel: 0, deltas: 0 };
            let mut inf = Info { m: m as u32, k: k as u32, cand: cands.len() as u32, cand_prop: 0, cand_unknown: 0, names: Vec::new() };
            let mut env: proof_structure::eval::Ctx = None;
            let mut vars: Vec<Val> = Vec::new();
            let last = *cands.last().unwrap();
            for i in 0..=last {
                let ti = ex[tb[i] as usize].a;
                if !used[i] {
                    ev.fuel = 20_000;
                    ev.deltas = 64;
                    let t = Val::Clo(ti, env.clone(), None);
                    match ev.is_prop(&t, &mut vars) {
                        Tri::Yes => {
                            inf.cand_prop += 1;
                            n_yes += 1;
                            if i < 64 {
                                v.unused[d] |= 1 << i;
                                inf.names.push((i as u32, xd.aux[tb[i] as usize]));
                                n_rm += 1;
                            }
                        }
                        Tri::Unknown => {
                            inf.cand_unknown += 1;
                            n_unk += 1;
                        }
                        Tri::No => {}
                    }
                }
                vars.push(Val::Clo(ti, env.clone(), None));
                env = push(&env, Val::Var(i as u32));
            }
            if v.unused[d] != 0 {
                n_c_rm += 1;
            }
            info.insert(d as u32, inf);
        }
        say!("analysed {n_an} project defs/theorems with binders ({n_bind} binders; {n_proj_like} projection-like skipped)");
        say!("binders used nowhere: {n_cand}; of these Prop: {n_yes}, sort unknown: {n_unk}; removable positions: {n_rm} in {n_c_rm} constants");
        say!("relevant loose-bvar sets: {} wide nodes", v.rl.wide.len());

        // closures: plain (= kernel cone) and relevance-pruned, from the roots, over refs + block + lit + quot
        let unused_of = |d: usize| v.unused[d];
        let closure = |rel: bool, collect: bool| -> (Vec<i32>, Vec<(u32, u32, u16)>) {
            let mut dist = vec![-1i32; n];
            let mut frontier: Vec<u32> = Vec::new();
            for &r in &roots {
                if dist[r as usize] < 0 {
                    dist[r as usize] = 0;
                    frontier.push(r);
                }
            }
            let mut irr_all: Vec<(u32, u32, u16)> = Vec::new();
            let mut level = 0;
            while !frontier.is_empty() {
                level += 1;
                let outs: Vec<WalkOut> = frontier
                    .par_iter()
                    .map(|&d| {
                        let c = &p.consts[d as usize];
                        let um = if rel { unused_of(d as usize) } else { 0 };
                        let mut rs: Vec<u32> = Vec::new();
                        if um == 0 {
                            rs.push(c.ty);
                            rs.extend(&c.vals);
                        } else {
                            let (tb, r) = telescope(ex, c.ty, T_PI, usize::MAX);
                            for (i, &b) in tb.iter().enumerate() {
                                if !(i < 64 && um >> i & 1 == 1) {
                                    rs.push(ex[b as usize].a);
                                }
                            }
                            rs.push(r);
                            let (vb, body) = telescope(ex, c.vals[0], T_LAM, tb.len());
                            for (i, &b) in vb.iter().enumerate() {
                                if !(i < 64 && um >> i & 1 == 1) {
                                    rs.push(ex[b as usize].a);
                                }
                            }
                            rs.push(body);
                        }
                        let mut o = with_walker(nex, |w| v.walk(w, &rs, rel));
                        if !collect {
                            o.irr = Vec::new();
                        }
                        let mut extra = Vec::new();
                        if o.nat {
                            extra.extend(lit_nat);
                        }
                        if o.str_ {
                            extra.extend(&lit_str);
                        }
                        if c.kind == K_QUOT {
                            extra.extend(eq);
                        }
                        extra.extend(&members[&block[d as usize]]);
                        o.refs.extend(extra);
                        o
                    })
                    .collect();
                let mut next = Vec::new();
                for o in outs {
                    for &r in &o.refs {
                        if dist[r as usize] < 0 {
                            dist[r as usize] = level;
                            next.push(r);
                        }
                    }
                    irr_all.extend(o.irr);
                }
                frontier = next;
            }
            (dist, irr_all)
        };
        if kernel_cone.is_none() {
            let (kd, _) = closure(false, false);
            say!("kernel cone (plain closure from {} roots): {} constants", roots.len(), kd.iter().filter(|&&x| x >= 0).count());
            kernel_cone = Some(kd.iter().map(|&x| x >= 0).collect());
        }
        let kc = kernel_cone.as_ref().unwrap();
        let (pd, irr) = closure(true, true);
        let pruned: Vec<bool> = pd.iter().map(|&x| x >= 0).collect();
        let np_ = pruned.iter().filter(|&&b| b).count();
        let npp = (0..n).filter(|&i| pruned[i] && project[i]).count();
        let nkp = (0..n).filter(|&i| kc[i] && project[i]).count();
        let outside = (0..n).filter(|&i| pruned[i] && !kc[i]).count();
        say!("relevance-pruned cone: {np_} constants ({npp} project); kernel cone project constants: {nkp}; pruned-but-not-kernel: {outside}");

        // what each callee's removable positions drag in: constants in those arguments (plain), restricted to the
        // dropped set D = kernel cone - pruned cone, then their closure inside D
        let dropped: Vec<bool> = (0..n).map(|i| kc[i] && !pruned[i]).collect();
        let mut pairs: Vec<(u32, u32)> = irr.iter().map(|&(h, a, _)| (h, a)).collect();
        pairs.sort_unstable();
        pairs.dedup();
        let mut sites: HashMap<u32, u64> = HashMap::new();
        for &(h, _, _) in &irr {
            *sites.entry(h).or_default() += 1;
        }
        say!("irrelevant argument sites in the pruned cone: {}; distinct (callee, argument): {}", irr.len(), pairs.len());
        let seeds_per: Vec<(u32, Vec<u32>)> = pairs
            .par_chunks(4096)
            .flat_map_iter(|ch| {
                let mut out: Vec<(u32, Vec<u32>)> = Vec::new();
                for &(h, a) in ch {
                    let o = with_walker(nex, |w| v.walk(w, &[a], false));
                    out.push((h, o.refs.into_iter().filter(|&r| dropped[r as usize]).collect()));
                }
                out.into_iter()
            })
            .collect();
        let mut seeds: HashMap<u32, Vec<u32>> = HashMap::new();
        for (h, s) in seeds_per {
            seeds.entry(h).or_default().extend(s);
        }
        for s in seeds.values_mut() {
            s.sort_unstable();
            s.dedup();
        }
        // plain refs of dropped constants, for closures inside D
        let dl: Vec<u32> = (0..n as u32).filter(|&i| dropped[i as usize]).collect();
        let drefs: HashMap<u32, Vec<u32>> = dl
            .par_iter()
            .map(|&d| {
                let c = &p.consts[d as usize];
                let mut rs = vec![c.ty];
                rs.extend(&c.vals);
                let mut o = with_walker(nex, |w| v.walk(w, &rs, false));
                o.refs.extend(&members[&block[d as usize]]);
                o.refs.retain(|&r| dropped[r as usize]);
                (d, o.refs)
            })
            .collect();
        let mut order: Vec<(u32, usize)> = seeds.iter().map(|(&h, s)| (h, s.len())).collect();
        order.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
        let drag: HashMap<u32, usize> = order
            .iter()
            .take(drag_top)
            .collect::<Vec<_>>()
            .par_iter()
            .map(|&&(h, _)| {
                let mut seen: HashSet<u32> = HashSet::new();
                let mut q: Vec<u32> = seeds[&h].clone();
                for &x in &q {
                    seen.insert(x);
                }
                while let Some(u) = q.pop() {
                    if let Some(rs) = drefs.get(&u) {
                        for &w in rs {
                            if seen.insert(w) {
                                q.push(w);
                            }
                        }
                    }
                }
                (h, seen.len())
            })
            .collect();
        say!("dropped constants: {}; callees with seeds in the dropped set: {}; exact closures for top {}", dl.len(), seeds.len(), drag.len());

        let mut f = BufWriter::new(File::create(format!("{outdir}/{tag}-positions.tsv"))?);
        writeln!(f, "id\tname\tkind\tbinders\tlambdas\tunused_binders\tunused_prop\tunused_unknown_sort\tremovable\tin_kernel_cone\tin_pruned_cone\tirrelevant_sites\tseeds_dropped\tdrags_dropped")?;
        let mut ids: Vec<&u32> = info.keys().collect();
        ids.sort();
        for &d in ids {
            let inf = &info[&d];
            let rm: Vec<String> = inf.names.iter().map(|&(i, nm)| format!("{i}:{}", render(&p.names, nm))).collect();
            writeln!(
                f,
                "{d}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
                p.cname[d as usize],
                KIND_NAMES[p.consts[d as usize].kind as usize],
                inf.m, inf.k, inf.cand, inf.cand_prop, inf.cand_unknown,
                rm.join(" "),
                kc[d as usize] as u8,
                pruned[d as usize] as u8,
                sites.get(&d).copied().unwrap_or(0),
                seeds.get(&d).map(|s| s.len()).unwrap_or(0),
                drag.get(&d).map(|x| x.to_string()).unwrap_or_default()
            )?;
        }
        if !watch.is_empty() {
            let mut f = BufWriter::new(File::create(format!("{outdir}/{tag}-watched-binders.tsv"))?);
            writeln!(f, "name\tkind\tposition\tbinder\tprop\tused_in_type\toccurs_in_value\tused\tremovable")?;
            for r in &watch_rows {
                writeln!(f, "{r}")?;
            }
        }
        let mut f = BufWriter::new(File::create(format!("{outdir}/{tag}-cone.tsv"))?);
        writeln!(f, "id\tname\tproject\tin_kernel_cone\tin_pruned_cone\tpruned_depth\tunused_mask")?;
        for i in 0..n {
            if kc[i] || pruned[i] {
                writeln!(f, "{i}\t{}\t{}\t{}\t{}\t{}\t{}", p.cname[i], project[i] as u8, kc[i] as u8, pruned[i] as u8, pd[i], v.unused[i])?;
            }
        }
    }
    say!("done; total {:.1}s", lg.t0.elapsed().as_secs_f64());
    std::fs::write(format!("{outdir}/relevance-stats.txt"), &lg.text)?;
    Ok(())
}
