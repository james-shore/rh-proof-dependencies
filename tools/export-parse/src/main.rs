// Streaming parser for lean4export v4.33.0 NDJSON (format 3.1.0).
// Usage: zstd -dc --long=30 solution.ndjson.zst | export-parse OUTDIR ROOTS_FILE THM1,THM2,...
// Writes OUTDIR/{constants.tsv,edges.tsv,stats.txt}. See docs/export-format.md. Parsing lives in lib.rs.

use export_parse::*;
use rayon::prelude::*;
use std::cell::RefCell;
use std::collections::{HashMap, VecDeque};
use std::fs::File;
use std::io::{self, BufWriter, Write};

// ---------- DAG walking ----------
struct Walker {
    bits: Vec<u64>,
    touched: Vec<u32>,
    stack: Vec<u32>,
}
thread_local! {
    static WALKER: RefCell<Option<Walker>> = RefCell::new(None);
}
struct WalkOut {
    dag: u64,
    refs: Vec<u32>, // name indices
    nat: bool,
    str_: bool,
}
fn walk(w: &mut Walker, ex: &[E], roots: &[u32]) -> WalkOut {
    let mut o = WalkOut { dag: 0, refs: Vec::new(), nat: false, str_: false };
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
        o.dag += 1;
        let e = ex[i as usize];
        match e.tag {
            T_CONST => o.refs.push(e.a),
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

fn main() -> io::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let outdir = args.get(1).expect("OUTDIR").clone();
    let roots_file = args.get(2).expect("ROOTS_FILE").clone();
    let thms: Vec<String> = args.get(3).expect("THMS").split(',').map(|s| s.to_string()).collect();
    let nworkers: usize = std::env::var("WORKERS").ok().and_then(|s| s.parse().ok()).unwrap_or(48);
    let expr_cap: usize = std::env::var("EXPR_CAP").ok().and_then(|s| s.parse().ok()).unwrap_or(2_000_000_000);
    let mut lg = Log::new();
    macro_rules! say {
        ($($a:tt)*) => {{ lg.say(format!($($a)*)); }};
    }
    let p = parse_stdin(nworkers, expr_cap, keep_detail_from_env(), &mut lg)?;
    let Parsed { exprs, consts, rec_of, cname, cid_of_name, .. } = p;
    let n = consts.len();
    let mut by_str: HashMap<&str, u32> = HashMap::with_capacity(n);
    for (i, s) in cname.iter().enumerate() {
        by_str.insert(s.as_str(), i as u32);
    }
    say!("distinct rendered names: {}", by_str.len());
    let lookup = |s: &str| by_str.get(s).copied();

    // tree sizes (children always have smaller indices: exporter assigns an index after its children)
    let mut tree: Vec<u64> = vec![0; exprs.len()];
    let mut order_viol = 0u64;
    for i in 0..exprs.len() {
        let e = exprs[i];
        let nch = match e.tag {
            T_APP | T_LAM | T_PI => 2,
            T_LET => 3,
            T_PROJ | T_MDATA => 1,
            _ => 0,
        };
        let chs = [e.a, e.b, e.c];
        let mut s = 1u64;
        for &c in &chs[..nch] {
            if c as usize >= i {
                order_viol += 1;
            }
            s = s.saturating_add(tree[c as usize]);
        }
        tree[i] = s;
    }
    say!("child-index order violations: {order_viol}");

    // ---- phase 3: per-constant walks ----
    let nex = exprs.len();
    let ex = &exprs[..];
    let res: Vec<(WalkOut, WalkOut, u64, u64)> = consts
        .par_iter()
        .map(|c| {
            WALKER.with(|cell| {
                let mut b = cell.borrow_mut();
                if b.is_none() {
                    *b = Some(Walker { bits: vec![0; nex / 64 + 1], touched: Vec::new(), stack: Vec::new() });
                }
                let w = b.as_mut().unwrap();
                let t = walk(w, ex, &[c.ty]);
                let v = walk(w, ex, &c.vals);
                let tt = tree[c.ty as usize];
                let vt = c.vals.iter().fold(0u64, |a, &x| a.saturating_add(tree[x as usize]));
                (t, v, tt, vt)
            })
        })
        .collect();
    say!("walks done");

    let nat = lookup("Nat");
    let char_of_nat = lookup("Char.ofNat");
    let string_of_list = lookup("String.ofList");
    let eq = lookup("Eq");
    // adjacency (type, value, lit, quot) + block siblings
    let mut dangling = 0u64;
    let mut edges: Vec<(u32, u32, u8)> = Vec::new(); // via: 0 type, 1 value, 2 lit, 3 quot
    for (i, (t, v, _, _)) in res.iter().enumerate() {
        for (via, w) in [(0u8, t), (1u8, v)] {
            for &nm in &w.refs {
                let d = cid_of_name[nm as usize];
                if d == u32::MAX {
                    dangling += 1;
                } else {
                    edges.push((i as u32, d, via));
                }
            }
        }
        let mut lits = Vec::new();
        if t.nat || v.nat {
            lits.extend(nat);
        }
        if t.str_ || v.str_ {
            lits.extend(char_of_nat);
            lits.extend(string_of_list);
        }
        for d in lits {
            edges.push((i as u32, d, 2));
        }
        if consts[i].kind == K_QUOT {
            if let Some(d) = eq {
                edges.push((i as u32, d, 3));
            }
        }
    }
    say!("edges: {} (dangling refs to names that are not exported constants: {dangling})", edges.len());
    let mut via_count = [0u64; 4];
    for e in &edges {
        via_count[e.2 as usize] += 1;
    }
    say!("  edges type {} value {} lit {} quot {}", via_count[0], via_count[1], via_count[2], via_count[3]);

    // block = declaration record; all quot constants form one block (the exporter dumps the whole package)
    let mut block: Vec<u32> = rec_of.clone();
    let quots: Vec<usize> = (0..n).filter(|&i| consts[i].kind == K_QUOT).collect();
    if let Some(&q0) = quots.first() {
        for &q in &quots {
            block[q] = rec_of[q0];
        }
    }
    let mut members: HashMap<u32, Vec<u32>> = HashMap::new();
    for i in 0..n {
        members.entry(block[i]).or_default().push(i as u32);
    }
    // CSR adjacency
    let mut deg = vec![0u32; n + 1];
    for e in &edges {
        deg[e.0 as usize + 1] += 1;
    }
    for i in 0..n {
        deg[i + 1] += deg[i];
    }
    let mut adj = vec![0u32; edges.len()];
    let mut fill = deg.clone();
    for e in &edges {
        adj[fill[e.0 as usize] as usize] = e.1;
        fill[e.0 as usize] += 1;
    }
    let bfs = |srcs: &[u32]| -> Vec<i32> {
        let mut dist = vec![-1i32; n];
        let mut q = VecDeque::new();
        for &s in srcs {
            if dist[s as usize] < 0 {
                dist[s as usize] = 0;
                q.push_back(s);
            }
        }
        while let Some(u) = q.pop_front() {
            let du = dist[u as usize];
            let it = adj[deg[u as usize] as usize..deg[u as usize + 1] as usize].iter().copied().chain(members[&block[u as usize]].iter().copied());
            for v in it {
                if dist[v as usize] < 0 {
                    dist[v as usize] = du + 1;
                    q.push_back(v);
                }
            }
        }
        dist
    };
    let roots_txt = std::fs::read_to_string(&roots_file)?;
    let mut roots = Vec::new();
    for r in roots_txt.split_whitespace() {
        match lookup(r) {
            Some(i) => roots.push(i),
            None => say!("ROOT NOT FOUND: {r}"),
        }
    }
    let all = bfs(&roots);
    let unreached: Vec<usize> = (0..n).filter(|&i| all[i] < 0).collect();
    say!("closure from {} export targets: {} of {n} constants; unreached {}", roots.len(), n - unreached.len(), unreached.len());
    for &u in unreached.iter().take(20) {
        say!("  unreached: {} ({})", cname[u], KIND_NAMES[consts[u].kind as usize]);
    }
    let mut thm_ids = Vec::new();
    for t in &thms {
        match lookup(t) {
            Some(i) => thm_ids.push(i),
            None => say!("THEOREM NOT FOUND: {t}"),
        }
    }
    let depth = bfs(&thm_ids);
    let five = depth.iter().filter(|&&d| d >= 0).count();
    say!("closure of the {} theorems: {five} constants", thm_ids.len());
    let mut mask = vec![0u8; n];
    for (k, &t) in thm_ids.iter().enumerate() {
        let d = bfs(&[t]);
        let c = d.iter().filter(|&&x| x >= 0).count();
        say!("  closure of {}: {c} constants", cname[t as usize]);
        for i in 0..n {
            if d[i] >= 0 {
                mask[i] |= 1 << k;
            }
        }
    }
    let outside: Vec<usize> = (0..n).filter(|&i| depth[i] < 0).collect();
    say!("constants not in the five theorems' closure (only reached from kernel-primitive targets): {}", outside.len());
    for &u in outside.iter().take(50) {
        say!("  only-primitive: {} ({})", cname[u], KIND_NAMES[consts[u].kind as usize]);
    }

    // ---- outputs ----
    {
        let mut f = BufWriter::with_capacity(16 << 20, File::create(format!("{outdir}/constants.tsv"))?);
        writeln!(f, "id\tname\tkind\trecord\tblock\tnlevels\ttype_dag\tvalue_dag\ttype_tree\tvalue_tree\trefs_type\trefs_value\tdepth\tthm_mask\tinfo")?;
        for i in 0..n {
            let c = &consts[i];
            let (t, v, tt, vt) = &res[i];
            let has_val = !c.vals.is_empty();
            writeln!(
                f,
                "{i}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
                cname[i], KIND_NAMES[c.kind as usize], rec_of[i], block[i], c.nlev, t.dag,
                if has_val { v.dag.to_string() } else { String::new() }, tt,
                if has_val { vt.to_string() } else { String::new() },
                t.refs.len(), v.refs.len(), depth[i], mask[i], c.info
            )?;
        }
    }
    {
        let mut f = BufWriter::with_capacity(16 << 20, File::create(format!("{outdir}/edges.tsv"))?);
        writeln!(f, "src\tdst\tvia")?;
        let vn = ["type", "value", "lit", "quot"];
        for e in &edges {
            writeln!(f, "{}\t{}\t{}", e.0, e.1, vn[e.2 as usize])?;
        }
    }
    say!("wrote outputs; total {:.1}s", lg.t0.elapsed().as_secs_f64());
    std::fs::write(format!("{outdir}/stats.txt"), &lg.text)?;
    Ok(())
}

