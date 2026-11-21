// Immediate dominators of a directed graph, with checks.
// Usage: dom EDGES.tsv N ROOT OUT.tsv [SAMPLE_K] [SEED]
//   EDGES: .bin (u32 LE pairs) or .tsv ("src<TAB>dst", header optional), node ids in 0..N. OUT.tsv: "node<TAB>idom" (-1 = unreachable).
// Checks written to stderr and OUT.tsv.check:
//   edge check (all edges): for every edge u->v between reachable nodes, idom(v) is an ancestor-or-self of u in the
//     tree. This proves every tree ancestor of v really dominates v (so reported subtrees are really dominated).
//   brute check (SAMPLE_K random reachable nodes): the strict dominators found by deleting each node on a root->v
//     path and re-running BFS equal the tree ancestors of v (so no domination is missed).

use rayon::prelude::*;
use proof_structure::graph::*;
use std::fs::File;
use std::io::{self, BufWriter, Write};
use std::time::Instant;

fn main() -> io::Result<()> {
    let a: Vec<String> = std::env::args().collect();
    if a.len() < 5 {
        eprintln!("usage: dom EDGES.tsv N ROOT OUT.tsv [SAMPLE_K] [SEED]");
        std::process::exit(2);
    }
    let n: usize = a[2].parse().unwrap();
    let root: u32 = a[3].parse().unwrap();
    let sample_k: usize = a.get(5).map(|s| s.parse().unwrap()).unwrap_or(0);
    let mut seed: u64 = a.get(6).map(|s| s.parse().unwrap()).unwrap_or(1);
    let t0 = Instant::now();
    let mut log = String::new();
    macro_rules! say {
        ($($x:tt)*) => {{ let s = format!($($x)*); eprintln!("[{:>6.1}s] {}", t0.elapsed().as_secs_f64(), s); log.push_str(&s); log.push('\n'); }};
    }
    let edges = read_edges_file(&a[1])?;
    for &(u, v) in &edges {
        assert!((u as usize) < n && (v as usize) < n, "edge {u}->{v} out of range");
    }
    say!("read {} edges over {} nodes", edges.len(), n);
    let succ = Csr::from_edges(n, &edges, false);
    let pred = Csr::from_edges(n, &edges, true);
    let idom = dominators(&succ, &pred, root);
    let reach = idom.iter().filter(|&&d| d != NONE).count();
    say!("dominators done: {reach} reachable from root {root}");

    let (tin, tout) = tree_intervals(&idom, root);
    let is_anc = |x: u32, y: u32| tin[x as usize] <= tin[y as usize] && tin[y as usize] < tout[x as usize];
    let mut bad = 0u64;
    let mut checked = 0u64;
    for &(u, v) in &edges {
        if idom[u as usize] == NONE || idom[v as usize] == NONE || v == root {
            continue;
        }
        checked += 1;
        if !is_anc(idom[v as usize], u) {
            bad += 1;
            if bad <= 10 {
                say!("  EDGE CHECK FAIL: {u} -> {v}, idom(v) = {}", idom[v as usize]);
            }
        }
    }
    say!("edge check: {checked} edges, {bad} failures");

    if sample_k > 0 {
        let reachable: Vec<u32> = (0..n as u32).filter(|&v| idom[v as usize] != NONE && v != root).collect();
        let mut picks = Vec::new();
        for _ in 0..sample_k.min(reachable.len()) {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            picks.push(reachable[(seed % reachable.len() as u64) as usize]);
        }
        let res: Vec<(u32, Option<bool>)> = picks
            .par_iter()
            .map(|&v| {
                let brute = brute_dominators(&succ, root, v);
                let mut chain = Vec::new();
                let mut u = idom[v as usize];
                loop {
                    chain.push(u);
                    if u == root {
                        break;
                    }
                    u = idom[u as usize];
                }
                chain.reverse();
                if brute == vec![NONE] {
                    return (v, None);
                }
                (v, Some(brute == chain))
            })
            .collect();
        let fails: Vec<u32> = res.iter().filter(|r| r.1 == Some(false)).map(|r| r.0).collect();
        let skipped = res.iter().filter(|r| r.1.is_none()).count();
        say!("brute check: {} sampled nodes, {} failures {:?}{}", res.len() - skipped, fails.len(), &fails[..fails.len().min(10)],
             if skipped > 0 { format!(" ({skipped} skipped: root path longer than 5000)") } else { String::new() });
    }

    let mut f = BufWriter::with_capacity(1 << 24, File::create(&a[4])?);
    writeln!(f, "node\tidom")?;
    for v in 0..n {
        let d = idom[v];
        if d == NONE {
            writeln!(f, "{v}\t-1")?;
        } else {
            writeln!(f, "{v}\t{d}")?;
        }
    }
    std::fs::write(format!("{}.check", a[4]), log)?;
    Ok(())
}
