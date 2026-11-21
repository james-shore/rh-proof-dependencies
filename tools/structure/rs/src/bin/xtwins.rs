// Project statements and definitions that equal a Mathlib/core constant anywhere in Mathlib, across two exports.
//   keys:  zstd -dc mathlib.ndjson.zst | xtwins keys KEYS.tsv
//          one row per theorem/definition of the export: name, kind, name hash, statement hash, value hash.
//   twins: zstd -dc solution.ndjson.zst | xtwins twins KEYS.tsv OUTDIR PROJECT
//          OUTDIR/xtwins.tsv: project theorems whose statement, and project definitions whose statement and value,
//          equal a constant in KEYS; round 0 = exact, round k = after replacing project constants found in earlier
//          rounds by their Mathlib twins.
// The hash is structural and independent of export ids: names by their components, levels by structure (parameter
// names count), literals by value, constants by name, binder names and binder info ignored (as Lean's Expr equality).

use export_parse::*;
use std::collections::HashMap;
use std::fs::File;
use std::io::{self, BufRead, BufReader, BufWriter, Write};

const NONE: u32 = u32::MAX;
type H = (u64, u64);

#[inline]
fn mix64(mut x: u64) -> u64 {
    x ^= x >> 30;
    x = x.wrapping_mul(0xbf58476d1ce4e5b9);
    x ^= x >> 27;
    x = x.wrapping_mul(0x94d049bb133111eb);
    x ^ (x >> 31)
}
#[inline]
fn h2(tag: u64, xs: &[u64]) -> H {
    let (mut a, mut b) = (mix64(tag ^ 0x9e3779b97f4a7c15), mix64(tag ^ 0xc2b2ae3d27d4eb4f));
    for &x in xs {
        a = mix64(a ^ x).wrapping_add(0x165667b19e3779f9);
        b = mix64(b.rotate_left(17) ^ x ^ 0x27d4eb2f165667c5);
    }
    (a, b)
}
fn hbytes(tag: u64, s: &[u8]) -> H {
    let mut xs: Vec<u64> = vec![s.len() as u64];
    for ch in s.chunks(8) {
        let mut w = [0u8; 8];
        w[..ch.len()].copy_from_slice(ch);
        xs.push(u64::from_le_bytes(w));
    }
    h2(tag, &xs)
}
fn hx(h: H) -> String {
    format!("{:016x}{:016x}", h.0, h.1)
}
fn unhx(s: &str) -> H {
    (u64::from_str_radix(&s[..16], 16).unwrap(), u64::from_str_radix(&s[16..32], 16).unwrap())
}

struct Hasher<'a> {
    p: &'a Parsed,
    xd: &'a ExprDetail,
    name_h: Vec<H>,
    level_h: Vec<H>,
}

impl<'a> Hasher<'a> {
    fn new(p: &'a Parsed) -> Hasher<'a> {
        let xd = p.detail.as_ref().expect("detail");
        let mut name_h = vec![(0u64, 0u64); p.names.len()];
        name_h[0] = h2(100, &[]);
        for i in 1..p.names.len() {
            let n = &p.names[i];
            let pre = name_h[n.pre as usize];
            name_h[i] = match n.num {
                Some(k) => h2(101, &[pre.0, pre.1, k]),
                None => {
                    let s = hbytes(102, n.s.as_bytes());
                    h2(103, &[pre.0, pre.1, s.0, s.1])
                }
            };
        }
        let mut level_h = vec![(0u64, 0u64); xd.levels.len().max(1)];
        level_h[0] = h2(110, &[]);
        for i in 1..xd.levels.len() {
            let l = xd.levels[i];
            level_h[i] = match l.tag {
                L_SUCC => {
                    let a = level_h[l.a as usize];
                    h2(111, &[a.0, a.1])
                }
                L_MAX | L_IMAX => {
                    let (a, b) = (level_h[l.a as usize], level_h[l.b as usize]);
                    h2(110 + l.tag as u64, &[a.0, a.1, b.0, b.1])
                }
                L_PARAM => {
                    let a = name_h[l.a as usize];
                    h2(114, &[a.0, a.1])
                }
                _ => h2(110, &[]),
            };
        }
        Hasher { p, xd, name_h, level_h }
    }

    /// Hash every expression node bottom-up; `refh[c]` is the hash used for constant c.
    fn exprs(&self, refh: &[H], out: &mut Vec<H>) {
        let ex = &self.p.exprs;
        out.resize(ex.len(), (0, 0));
        let mut buf: Vec<u64> = Vec::with_capacity(16);
        for i in 0..ex.len() {
            let e = ex[i];
            let h = match e.tag {
                T_BVAR => h2(1, &[e.a as u64]),
                T_SORT => {
                    let l = self.level_h[e.a as usize];
                    h2(2, &[l.0, l.1])
                }
                T_CONST => {
                    buf.clear();
                    let c = self.p.cid_of_name[e.a as usize];
                    let n = if c == NONE { self.name_h[e.a as usize] } else { refh[c as usize] };
                    buf.push(n.0);
                    buf.push(n.1);
                    let off = self.xd.aux[i] as usize;
                    if off != 0 {
                        let k = self.xd.us[off] as usize;
                        for &u in &self.xd.us[off + 1..off + 1 + k] {
                            let l = self.level_h[u as usize];
                            buf.push(l.0);
                            buf.push(l.1);
                        }
                    }
                    h2(3, &buf)
                }
                T_APP | T_LAM | T_PI => {
                    let (a, b) = (out[e.a as usize], out[e.b as usize]);
                    h2(e.tag as u64, &[a.0, a.1, b.0, b.1])
                }
                T_LET => {
                    let (a, b, c) = (out[e.a as usize], out[e.b as usize], out[e.c as usize]);
                    h2(7, &[a.0, a.1, b.0, b.1, c.0, c.1])
                }
                T_PROJ => {
                    let a = out[e.a as usize];
                    let t = self.name_h[e.b as usize];
                    h2(8, &[a.0, a.1, t.0, t.1, self.xd.aux[i] as u64])
                }
                T_MDATA => out[e.a as usize],
                T_NAT | T_STR => {
                    let v = self.xd.lits.get(&(i as u32)).map(|s| s.as_bytes()).unwrap_or(b"?");
                    hbytes(e.tag as u64 + 200, v)
                }
                _ => h2(99, &[i as u64]),
            };
            out[i] = h;
        }
    }
}

fn mergeable(c: &Cst) -> bool {
    (c.kind == K_THM || c.kind == K_DEF) && !c.vals.is_empty()
}

fn main() -> io::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let nworkers: usize = std::env::var("WORKERS").ok().and_then(|s| s.parse().ok()).unwrap_or(8);
    let mut lg = Log::new();
    macro_rules! say {
        ($($a:tt)*) => {{ lg.say(format!($($a)*)); }};
    }
    match args.get(1).map(|s| s.as_str()) {
        Some("keys") => {
            let p = parse_stdin(nworkers, 1_100_000_000, true, &mut lg)?;
            let hs = Hasher::new(&p);
            let refh: Vec<H> = p.consts.iter().map(|c| hs.name_h[c.name as usize]).collect();
            let mut eh = Vec::new();
            hs.exprs(&refh, &mut eh);
            let mut f = BufWriter::with_capacity(1 << 22, File::create(&args[2])?);
            writeln!(f, "name\tkind\tname_hash\ttype_hash\tvalue_hash")?;
            let mut n = 0u64;
            for (i, c) in p.consts.iter().enumerate() {
                if !mergeable(c) {
                    continue;
                }
                n += 1;
                let v = if c.kind == K_DEF { hx(eh[c.vals[0] as usize]) } else { String::new() };
                writeln!(f, "{}\t{}\t{}\t{}\t{}", p.cname[i], KIND_NAMES[c.kind as usize], hx(refh[i]), hx(eh[c.ty as usize]), v)?;
            }
            say!("wrote {n} keys; literals kept: {}", hs.xd.lits.len());
            std::fs::write(format!("{}.stats.txt", args[2]), &lg.text)?;
        }
        Some("twins") => {
            let (keys, outdir, project_arg) = (&args[2], &args[3], &args[4]);
            // Mathlib keys: statement -> (name, name hash, count); (statement, value) -> same
            let mut thm: HashMap<H, (String, H, u32)> = HashMap::new();
            let mut def: HashMap<(H, H), (String, H, u32)> = HashMap::new();
            for (k, line) in BufReader::new(File::open(keys)?).lines().enumerate() {
                let line = line?;
                if k == 0 {
                    continue;
                }
                let f: Vec<&str> = line.split('\t').collect();
                let (nh, th) = (unhx(f[2]), unhx(f[3]));
                if f[1] == "theorem" {
                    thm.entry(th).and_modify(|e| e.2 += 1).or_insert((f[0].to_string(), nh, 1));
                } else {
                    def.entry((th, unhx(f[4]))).and_modify(|e| e.2 += 1).or_insert((f[0].to_string(), nh, 1));
                }
            }
            say!("keys: {} theorem statements, {} definitions", thm.len(), def.len());
            let p = parse_stdin(nworkers, 1_100_000_000, true, &mut lg)?;
            let n = p.consts.len();
            let mut project = vec![false; n];
            if let Some(pre) = project_arg.strip_prefix("prefix:") {
                for i in 0..n {
                    project[i] = p.cname[i].starts_with(pre);
                }
            } else {
                let by_name: HashMap<&str, u32> = p.cname.iter().enumerate().map(|(i, s)| (s.as_str(), i as u32)).collect();
                for l in std::fs::read_to_string(project_arg)?.lines() {
                    if let Some(&i) = by_name.get(l) {
                        project[i as usize] = true;
                    }
                }
            }
            let hs = Hasher::new(&p);
            let mut refh: Vec<H> = p.consts.iter().map(|c| hs.name_h[c.name as usize]).collect();
            // sanity: non-project constants of this export should mostly be found in the keys (same Mathlib)
            let mut eh = Vec::new();
            let mut found: Vec<Option<(u32, String, u32)>> = vec![None; n]; // (round, twin, matches)
            let (mut nonproj, mut nonproj_found) = (0u64, 0u64);
            let mut round = 0u32;
            loop {
                hs.exprs(&refh, &mut eh);
                let mut changed = 0u64;
                for (i, c) in p.consts.iter().enumerate() {
                    if !mergeable(c) {
                        continue;
                    }
                    let t = eh[c.ty as usize];
                    let hit = if c.kind == K_THM { thm.get(&t) } else { def.get(&(t, eh[c.vals[0] as usize])) };
                    if !project[i] {
                        if round == 0 {
                            nonproj += 1;
                            if hit.is_some() {
                                nonproj_found += 1;
                            }
                        }
                        continue;
                    }
                    if let Some((name, nh, cnt)) = hit {
                        if refh[i] != *nh {
                            refh[i] = *nh;
                            changed += 1;
                            if found[i].is_none() {
                                found[i] = Some((round, name.clone(), *cnt));
                            }
                        }
                    }
                }
                if round == 0 {
                    say!("check: {nonproj_found} of {nonproj} non-project theorems/definitions in this export match a key");
                }
                say!("round {round}: {changed} project constants matched");
                if changed == 0 || round >= 50 {
                    break;
                }
                round += 1;
            }
            let mut f = BufWriter::with_capacity(1 << 22, File::create(format!("{outdir}/xtwins.tsv"))?);
            writeln!(f, "id\tname\tkind\tround\ttwin\ttwin_matches")?;
            let mut nt = 0;
            for i in 0..n {
                if let Some((r, t, cnt)) = &found[i] {
                    nt += 1;
                    writeln!(f, "{i}\t{}\t{}\t{r}\t{t}\t{cnt}", p.cname[i], KIND_NAMES[p.consts[i].kind as usize])?;
                }
            }
            say!("project constants with a twin in the keys: {nt}; literals kept: {}", hs.xd.lits.len());
            std::fs::write(format!("{outdir}/xtwins-stats.txt"), &lg.text)?;
        }
        _ => {
            eprintln!("usage: xtwins keys KEYS.tsv | xtwins twins KEYS.tsv OUTDIR PROJECT   (export on stdin)");
            std::process::exit(2);
        }
    }
    Ok(())
}
