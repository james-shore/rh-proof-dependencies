// Concrete universe levels used by the proof terms of given root theorems.
// Usage: KEEP_EXPR_DETAIL=1 zstd -dc --long=30 solution.ndjson.zst | universes OUTDIR THM1,THM2,...
// From the roots (instantiated with all level parameters = 0), propagate concrete instantiations through every
// `const` occurrence's universe arguments in types and values (recursor value = its rules), to a fixed point over
// (constant, level vector) pairs. An inductive used at an instantiation brings its constructors at the same one.
// For each pair, the largest `Sort` level in its instantiated type and value is recorded.
use export_parse::*;
use rayon::prelude::*;
use std::cell::RefCell;
use std::collections::{HashMap, VecDeque};
use std::io::{self, Write};

struct W {
    bits: Vec<u64>,
    touched: Vec<u32>,
    stack: Vec<u32>,
}
thread_local! { static WK: RefCell<Option<W>> = RefCell::new(None); }

/// Symbolic occurrences in one constant: (destination constant, `us` offset) and Sort level indices.
fn occurrences(w: &mut W, ex: &[E], aux: &[u32], cid_of_name: &[u32], roots: &[u32]) -> (Vec<(u32, u32)>, Vec<u32>) {
    let mut occ = Vec::new();
    let mut sorts = Vec::new();
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
                let d = cid_of_name[e.a as usize];
                if d != u32::MAX {
                    occ.push((d, aux[i as usize]));
                }
            }
            T_SORT => sorts.push(e.a),
            T_APP | T_LAM | T_PI => {
                visit!(e.a);
                visit!(e.b);
            }
            T_LET => {
                visit!(e.a);
                visit!(e.b);
                visit!(e.c);
            }
            T_PROJ | T_MDATA => visit!(e.a),
            _ => {}
        }
    }
    for &t in &w.touched {
        w.bits[(t >> 6) as usize] = 0;
    }
    w.touched.clear();
    occ.sort_unstable();
    occ.dedup();
    sorts.sort_unstable();
    sorts.dedup();
    (occ, sorts)
}

fn eval(lv: &[Lv], l: u32, sigma: &HashMap<u32, u32>, bad: &mut u64) -> u32 {
    let x = lv[l as usize];
    match x.tag {
        L_ZERO => 0,
        L_SUCC => eval(lv, x.a, sigma, bad) + 1,
        L_MAX => eval(lv, x.a, sigma, bad).max(eval(lv, x.b, sigma, bad)),
        L_IMAX => {
            let b = eval(lv, x.b, sigma, bad);
            if b == 0 { 0 } else { eval(lv, x.a, sigma, bad).max(b) }
        }
        _ => match sigma.get(&x.a) {
            Some(&v) => v,
            None => {
                *bad += 1;
                0
            }
        },
    }
}

fn main() -> io::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let outdir = args.get(1).expect("OUTDIR").clone();
    let thms: Vec<String> = args.get(2).expect("THMS").split(',').map(|s| s.to_string()).collect();
    let nworkers: usize = std::env::var("WORKERS").ok().and_then(|s| s.parse().ok()).unwrap_or(8);
    let expr_cap: usize = std::env::var("EXPR_CAP").ok().and_then(|s| s.parse().ok()).unwrap_or(1_100_000_000);
    let mut log = Log::new();
    let p = parse_stdin(nworkers, expr_cap, true, &mut log)?;
    let d = p.detail.as_ref().expect("detail");
    let n = p.consts.len();
    let nex = p.exprs.len();
    let (ex, aux, cid) = (&p.exprs[..], &d.aux[..], &p.cid_of_name[..]);
    let occ: Vec<(Vec<(u32, u32)>, Vec<u32>)> = p
        .consts
        .par_iter()
        .map(|c| {
            WK.with(|cell| {
                let mut b = cell.borrow_mut();
                if b.is_none() {
                    *b = Some(W { bits: vec![0; nex / 64 + 1], touched: Vec::new(), stack: Vec::new() });
                }
                let w = b.as_mut().unwrap();
                let mut roots = vec![c.ty];
                roots.extend(&c.vals);
                occurrences(w, ex, aux, cid, &roots)
            })
        })
        .collect();
    log.say(format!("symbolic occurrences: {} const, {} sort", occ.iter().map(|o| o.0.len()).sum::<usize>(), occ.iter().map(|o| o.1.len()).sum::<usize>()));
    // constructors of each inductive (same record)
    let mut ctors: HashMap<u32, Vec<u32>> = HashMap::new();
    for i in 0..n {
        if p.consts[i].kind == K_CTOR {
            ctors.entry(p.rec_of[i]).or_default().push(i as u32);
        }
    }
    let by_name: HashMap<&str, u32> = p.cname.iter().enumerate().map(|(i, s)| (s.as_str(), i as u32)).collect();
    // worklist over (constant, instantiation)
    let mut key: HashMap<(u32, Vec<u32>), u32> = HashMap::new();
    let mut pairs: Vec<(u32, Vec<u32>, u32, u32)> = Vec::new(); // cid, inst, parent pair, max sort
    let mut q = VecDeque::new();
    for t in &thms {
        let c = by_name[t.as_str()];
        let inst = vec![0u32; p.consts[c as usize].lps.len().max(p.consts[c as usize].nlev as usize)];
        if !inst.is_empty() {
            log.say(format!("root {t} has {} universe params; instantiated with 0", inst.len()));
        }
        if !key.contains_key(&(c, inst.clone())) {
            key.insert((c, inst.clone()), pairs.len() as u32);
            pairs.push((c, inst, u32::MAX, 0));
            q.push_back(pairs.len() as u32 - 1);
        }
    }
    let mut bad = 0u64;
    while let Some(pi) = q.pop_front() {
        let (c, inst) = (pairs[pi as usize].0, pairs[pi as usize].1.clone());
        let cs = &p.consts[c as usize];
        let sigma: HashMap<u32, u32> = cs.lps.iter().copied().zip(inst.iter().copied()).collect();
        let mut ms = 0u32;
        for &l in &occ[c as usize].1 {
            ms = ms.max(eval(&d.levels, l, &sigma, &mut bad));
        }
        pairs[pi as usize].3 = ms;
        let mut next: Vec<(u32, Vec<u32>)> = Vec::new();
        for &(dst, off) in &occ[c as usize].0 {
            let k = d.us[off as usize] as usize;
            let li: Vec<u32> = d.us[off as usize + 1..off as usize + 1 + k].iter().map(|&l| eval(&d.levels, l, &sigma, &mut bad)).collect();
            next.push((dst, li));
        }
        if cs.kind == K_IND {
            if let Some(cl) = ctors.get(&p.rec_of[c as usize]) {
                for &ct in cl {
                    if p.consts[ct as usize].lps.len() == inst.len() {
                        next.push((ct, inst.clone()));
                    }
                }
            }
        }
        for kk in next {
            if !key.contains_key(&kk) {
                key.insert(kk.clone(), pairs.len() as u32);
                pairs.push((kk.0, kk.1, pi, 0));
                q.push_back(pairs.len() as u32 - 1);
            }
        }
    }
    log.say(format!("pairs (constant, instantiation): {}; unresolved level params: {bad}", pairs.len()));
    let reached: std::collections::HashSet<u32> = pairs.iter().map(|x| x.0).collect();
    log.say(format!("constants reached: {} of {n}", reached.len()));
    // per constant: max sort over its instantiations, max level argument
    let mut cmax: HashMap<u32, u32> = HashMap::new();
    let mut amax: HashMap<u32, u32> = HashMap::new();
    for x in &pairs {
        let e = cmax.entry(x.0).or_insert(0);
        *e = (*e).max(x.3);
        let a = amax.entry(x.0).or_insert(0);
        *a = (*a).max(x.1.iter().copied().max().unwrap_or(0));
    }
    let mut hist: std::collections::BTreeMap<u32, u64> = Default::default();
    for v in cmax.values() {
        *hist.entry(*v).or_default() += 1;
    }
    let mut ahist: std::collections::BTreeMap<u32, u64> = Default::default();
    for v in amax.values() {
        *ahist.entry(*v).or_default() += 1;
    }
    log.say(format!("constants by the largest Sort level in their instantiated type/value: {:?}", hist));
    log.say(format!("constants by the largest universe argument they are instantiated with: {:?}", ahist));
    let top = pairs.iter().enumerate().max_by_key(|(_, x)| x.3).map(|(i, _)| i).unwrap();
    log.say(format!("max Sort level: {}", pairs[top].3));
    let inst_s = |v: &Vec<u32>| v.iter().map(|x| x.to_string()).collect::<Vec<_>>().join(",");
    let mut chain = Vec::new();
    let mut cur = top as u32;
    while cur != u32::MAX {
        chain.push(cur);
        cur = pairs[cur as usize].2;
    }
    chain.reverse();
    let mut f = std::fs::File::create(format!("{outdir}/universes-witness.tsv"))?;
    writeln!(f, "step\tconstant\tkind\tinstantiation\tmax_sort")?;
    for (k, &pi) in chain.iter().enumerate() {
        let x = &pairs[pi as usize];
        writeln!(f, "{k}\t{}\t{}\t{}\t{}", p.cname[x.0 as usize], KIND_NAMES[p.consts[x.0 as usize].kind as usize], inst_s(&x.1), x.3)?;
    }
    let mut g = std::fs::File::create(format!("{outdir}/universes-constants.tsv"))?;
    writeln!(g, "constant\tkind\tmax_sort\tmax_level_arg\tn_instantiations")?;
    let mut ninst: HashMap<u32, u32> = HashMap::new();
    for x in &pairs {
        *ninst.entry(x.0).or_default() += 1;
    }
    let mut ids: Vec<&u32> = cmax.keys().collect();
    ids.sort();
    for &c in ids {
        writeln!(g, "{}\t{}\t{}\t{}\t{}", p.cname[c as usize], KIND_NAMES[p.consts[c as usize].kind as usize], cmax[&c], amax[&c], ninst[&c])?;
    }
    let mut h = std::fs::File::create(format!("{outdir}/universes-pairs-ge2.tsv"))?;
    writeln!(h, "constant\tinstantiation\tmax_sort\tparent")?;
    for x in &pairs {
        if x.3 >= 3 || x.1.iter().any(|&v| v >= 2) {
            let par = if x.2 == u32::MAX { String::new() } else { format!("{}[{}]", p.cname[pairs[x.2 as usize].0 as usize], inst_s(&pairs[x.2 as usize].1)) };
            writeln!(h, "{}\t{}\t{}\t{}", p.cname[x.0 as usize], inst_s(&x.1), x.3, par)?;
        }
    }
    std::fs::write(format!("{outdir}/universes-stats.txt"), &log.text)?;
    Ok(())
}
