// Duplicate statements and definitions, from the export's expression DAG.
// Usage: zstd -dc --long=30 solution.ndjson.zst | dupes OUTDIR PROJECT    (PROJECT as in relevance: names file or prefix:P)
// Writes OUTDIR/dupes-constants.tsv: one row per theorem or definition, with
//   type_id, value_id: the export's hash-consed expression ids. lean4export hash-conses with Lean's Expr equality,
//     which ignores binder names and binder info, so equal ids = alpha-equivalent terms (level-parameter names count).
//   exact_class: smallest constant id with the same key (theorem: type id; def: type id and value id).
//   twin_class: the same after iterating to a fixpoint, with every constant inside the terms replaced by its class
//     representative (definitions merge on type and value, theorems on type; inductives, constructors, recursors,
//     axioms, quot and opaque constants never merge). A non-project constant is preferred as the representative.
//   twin_round: the round in which the constant joined a larger class (0 = exact).

use export_parse::*;
use std::collections::HashMap;
use std::fs::File;
use std::io::{self, BufWriter, Write};

const NONE: u32 = u32::MAX;

#[inline]
fn mix64(mut x: u64) -> u64 {
    x ^= x >> 30;
    x = x.wrapping_mul(0xbf58476d1ce4e5b9);
    x ^= x >> 27;
    x = x.wrapping_mul(0x94d049bb133111eb);
    x ^ (x >> 31)
}
#[inline]
fn h2(tag: u64, xs: &[u64]) -> (u64, u64) {
    let (mut a, mut b) = (mix64(tag ^ 0x9e3779b97f4a7c15), mix64(tag ^ 0xc2b2ae3d27d4eb4f));
    for &x in xs {
        a = mix64(a ^ x).wrapping_add(0x165667b19e3779f9);
        b = mix64(b.rotate_left(17) ^ x ^ 0x27d4eb2f165667c5);
    }
    (a, b)
}

fn main() -> io::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 3 {
        eprintln!("usage: dupes OUTDIR PROJECT  (export on stdin)");
        std::process::exit(2);
    }
    let outdir = &args[1];
    let nworkers: usize = std::env::var("WORKERS").ok().and_then(|s| s.parse().ok()).unwrap_or(8);
    let mut lg = Log::new();
    macro_rules! say {
        ($($a:tt)*) => {{ lg.say(format!($($a)*)); }};
    }
    let p = parse_stdin(nworkers, 1_100_000_000, true, &mut lg)?;
    let xd = p.detail.as_ref().unwrap();
    let ex = &p.exprs[..];
    let n = p.consts.len();
    let nex = ex.len();
    let mut project = vec![false; n];
    if let Some(pre) = args[2].strip_prefix("prefix:") {
        for i in 0..n {
            project[i] = p.cname[i].starts_with(pre);
        }
    } else {
        let by_name: HashMap<&str, u32> = p.cname.iter().enumerate().map(|(i, s)| (s.as_str(), i as u32)).collect();
        for l in std::fs::read_to_string(&args[2])?.lines() {
            if let Some(&i) = by_name.get(l) {
                project[i as usize] = true;
            }
        }
    }
    say!("project constants: {}", project.iter().filter(|&&b| b).count());
    // which constants can merge, and on what
    let mergeable = |c: &Cst| (c.kind == K_THM || c.kind == K_DEF) && !c.vals.is_empty();
    let us_of = |i: usize| -> &[u32] {
        let off = xd.aux[i] as usize;
        if off == 0 {
            &[]
        } else {
            let k = xd.us[off] as usize;
            &xd.us[off + 1..off + 1 + k]
        }
    };

    // exact classes straight from the hash-consed ids
    let mut exact_class: Vec<u32> = vec![NONE; n];
    let exact_n: usize;
    {
        let mut rep: HashMap<(u8, u32, u32), u32> = HashMap::with_capacity(n);
        for (i, c) in p.consts.iter().enumerate() {
            if mergeable(c) {
                let key = if c.kind == K_THM { (0u8, c.ty, 0) } else { (1u8, c.ty, c.vals[0]) };
                rep.entry(key).and_modify(|r| *r = (*r).min(i as u32)).or_insert(i as u32);
            }
        }
        for (i, c) in p.consts.iter().enumerate() {
            if mergeable(c) {
                let key = if c.kind == K_THM { (0u8, c.ty, 0) } else { (1u8, c.ty, c.vals[0]) };
                exact_class[i] = rep[&key];
            }
        }
        exact_n = rep.len();
        say!("exact classes (by expression ids): {exact_n} over {} theorems and definitions", (0..n).filter(|&i| mergeable(&p.consts[i])).count());
    }
    let mut canon: Vec<u32> = (0..n as u32).collect();
    let mut round_of: Vec<i32> = vec![-1; n];
    let mut hash: Vec<(u64, u64)> = vec![(0, 0); nex];
    let mut round = 0;
    loop {
        // structural hash bottom-up (children have smaller indices)
        let mut buf: Vec<u64> = Vec::with_capacity(16);
        for i in 0..nex {
            let e = ex[i];
            buf.clear();
            let h = match e.tag {
                T_BVAR => h2(1, &[e.a as u64]),
                T_SORT => h2(2, &[e.a as u64]),
                T_CONST => {
                    let c = p.cid_of_name[e.a as usize];
                    buf.push(if c == NONE { (e.a as u64) | (1 << 40) } else { canon[c as usize] as u64 });
                    buf.extend(us_of(i).iter().map(|&u| u as u64));
                    h2(3, &buf)
                }
                T_APP | T_LAM | T_PI => {
                    let (a, b) = (hash[e.a as usize], hash[e.b as usize]);
                    h2(e.tag as u64, &[a.0, a.1, b.0, b.1])
                }
                T_LET => {
                    let (a, b, c) = (hash[e.a as usize], hash[e.b as usize], hash[e.c as usize]);
                    h2(7, &[a.0, a.1, b.0, b.1, c.0, c.1])
                }
                T_PROJ => {
                    let a = hash[e.a as usize];
                    h2(8, &[a.0, a.1, e.b as u64, xd.aux[i] as u64])
                }
                T_MDATA => hash[e.a as usize],
                _ => h2(9, &[i as u64]), // literals: hash-consed, so the node id is exact
            };
            hash[i] = h;
        }
        // classes
        let mut rep: HashMap<(u8, u64, u64, u64, u64), u32> = HashMap::with_capacity(n);
        for (i, c) in p.consts.iter().enumerate() {
            if !mergeable(c) {
                continue;
            }
            let t = hash[c.ty as usize];
            let key = if c.kind == K_THM {
                (0u8, t.0, t.1, 0, 0)
            } else {
                let v = hash[c.vals[0] as usize];
                (1u8, t.0, t.1, v.0, v.1)
            };
            let better = |a: u32, b: u32| -> u32 {
                // prefer non-project, then smaller id
                let (pa, pb) = (project[a as usize], project[b as usize]);
                if pa != pb {
                    if pa { b } else { a }
                } else {
                    a.min(b)
                }
            };
            rep.entry(key).and_modify(|r| *r = better(*r, i as u32)).or_insert(i as u32);
        }
        let mut changed = 0u64;
        let mut new = canon.clone();
        for (i, c) in p.consts.iter().enumerate() {
            if !mergeable(c) {
                continue;
            }
            let t = hash[c.ty as usize];
            let key = if c.kind == K_THM {
                (0u8, t.0, t.1, 0, 0)
            } else {
                let v = hash[c.vals[0] as usize];
                (1u8, t.0, t.1, v.0, v.1)
            };
            let r = rep[&key];
            if new[i] != r {
                new[i] = r;
                changed += 1;
                if round_of[i] < 0 {
                    round_of[i] = round;
                }
            }
        }
        let classes = rep.len();
        say!("round {round}: {classes} classes over mergeable constants; {changed} constants changed representative");
        if round == 0 && classes != exact_n {
            say!("WARNING: round-0 hash classes ({classes}) differ from id classes ({exact_n}): hash collision or bug");
        }
        canon = new;
        if changed == 0 || round >= 50 {
            break;
        }
        round += 1;
    }
    // representatives must be stable: canon[canon[i]] == canon[i]
    let unstable = (0..n).filter(|&i| canon[canon[i] as usize] != canon[i]).count();
    say!("unstable representatives: {unstable}");

    let mut f = BufWriter::with_capacity(1 << 22, File::create(format!("{outdir}/dupes-constants.tsv"))?);
    writeln!(f, "id\tname\tkind\tproject\tnlevels\ttype_id\tvalue_id\texact_class\ttwin_class\ttwin_round")?;
    for (i, c) in p.consts.iter().enumerate() {
        if !mergeable(c) {
            continue;
        }
        writeln!(
            f,
            "{i}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
            p.cname[i], KIND_NAMES[c.kind as usize], project[i] as u8, c.nlev, c.ty, c.vals[0], exact_class[i], canon[i],
            if round_of[i] < 0 { String::new() } else { round_of[i].to_string() }
        )?;
    }
    say!("done; total {:.1}s", lg.t0.elapsed().as_secs_f64());
    std::fs::write(format!("{outdir}/dupes-stats.txt"), &lg.text)?;
    Ok(())
}
