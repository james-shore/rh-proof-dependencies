//! Compressed adjacency, Lengauer–Tarjan dominators and brute-force checks.

use std::collections::VecDeque;
use std::io::{self, BufRead};

pub const NONE: u32 = u32::MAX;

/// Compressed sparse rows: neighbours of v are adj[off[v]..off[v+1]].
pub struct Csr {
    pub off: Vec<u64>,
    pub adj: Vec<u32>,
}

impl Csr {
    pub fn from_edges(n: usize, edges: &[(u32, u32)], reverse: bool) -> Csr {
        let mut off = vec![0u64; n + 1];
        for &(a, b) in edges {
            let s = if reverse { b } else { a };
            off[s as usize + 1] += 1;
        }
        for i in 0..n {
            off[i + 1] += off[i];
        }
        let mut fill = off.clone();
        let mut adj = vec![0u32; edges.len()];
        for &(a, b) in edges {
            let (s, d) = if reverse { (b, a) } else { (a, b) };
            adj[fill[s as usize] as usize] = d;
            fill[s as usize] += 1;
        }
        Csr { off, adj }
    }
    #[inline]
    pub fn nb(&self, v: u32) -> &[u32] {
        &self.adj[self.off[v as usize] as usize..self.off[v as usize + 1] as usize]
    }
    pub fn n(&self) -> usize {
        self.off.len() - 1
    }
}

/// Read an edge file: `.bin` = little-endian u32 pairs, otherwise "src<TAB>dst" lines.
pub fn read_edges_file(path: &str) -> io::Result<Vec<(u32, u32)>> {
    if path.ends_with(".bin") {
        let b = std::fs::read(path)?;
        if b.len() % 8 != 0 {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "edge .bin length not a multiple of 8"));
        }
        return Ok(b
            .chunks_exact(8)
            .map(|c| (u32::from_le_bytes([c[0], c[1], c[2], c[3]]), u32::from_le_bytes([c[4], c[5], c[6], c[7]])))
            .collect());
    }
    read_edges(io::BufReader::with_capacity(1 << 24, std::fs::File::open(path)?))
}

/// Read "src<TAB>dst" integer lines (an optional non-numeric header line is skipped).
pub fn read_edges<R: BufRead>(r: R) -> io::Result<Vec<(u32, u32)>> {
    let mut out = Vec::new();
    for (i, line) in r.lines().enumerate() {
        let line = line?;
        if line.is_empty() {
            continue;
        }
        let mut it = line.split('\t');
        let a = it.next().unwrap_or("");
        let b = it.next().unwrap_or("");
        match (a.parse::<u32>(), b.parse::<u32>()) {
            (Ok(a), Ok(b)) => out.push((a, b)),
            _ if i == 0 => continue,
            _ => return Err(io::Error::new(io::ErrorKind::InvalidData, format!("bad edge line {}: {line}", i + 1))),
        }
    }
    Ok(out)
}

/// Immediate dominators from `root` (Lengauer–Tarjan, simple version with path compression).
/// Returns idom[v] (idom[root] = root; NONE if v is unreachable).
pub fn dominators(succ: &Csr, pred: &Csr, root: u32) -> Vec<u32> {
    let n = succ.n();
    // iterative DFS: dfs number per vertex, vertex per number, parent (as number)
    let mut dfn = vec![NONE; n];
    let mut vertex: Vec<u32> = Vec::with_capacity(n);
    let mut parent: Vec<u32> = Vec::with_capacity(n);
    let mut stack: Vec<(u32, u64)> = vec![(root, succ.off[root as usize])];
    dfn[root as usize] = 0;
    vertex.push(root);
    parent.push(NONE);
    while let Some(&mut (v, ref mut pos)) = stack.last_mut() {
        let end = succ.off[v as usize + 1];
        let mut pushed = false;
        while *pos < end {
            let w = succ.adj[*pos as usize];
            *pos += 1;
            if dfn[w as usize] == NONE {
                dfn[w as usize] = vertex.len() as u32;
                parent.push(dfn[v as usize]);
                vertex.push(w);
                stack.push((w, succ.off[w as usize]));
                pushed = true;
                break;
            }
        }
        if !pushed {
            stack.pop();
        }
    }
    let m = vertex.len();
    // all arrays below are indexed by dfs number
    let mut semi: Vec<u32> = (0..m as u32).collect();
    let mut label: Vec<u32> = (0..m as u32).collect();
    let mut anc = vec![NONE; m];
    let mut idom = vec![NONE; m];
    let mut bucket_head = vec![NONE; m];
    let mut bucket_next = vec![NONE; m];
    let mut path: Vec<u32> = Vec::new();

    fn eval(v: u32, anc: &mut [u32], label: &mut [u32], semi: &[u32], path: &mut Vec<u32>) -> u32 {
        if anc[v as usize] == NONE {
            return v;
        }
        // compress the ancestor path of v
        path.clear();
        let mut u = v;
        while anc[anc[u as usize] as usize] != NONE {
            path.push(u);
            u = anc[u as usize];
        }
        while let Some(x) = path.pop() {
            let a = anc[x as usize];
            if semi[label[a as usize] as usize] < semi[label[x as usize] as usize] {
                label[x as usize] = label[a as usize];
            }
            anc[x as usize] = anc[a as usize];
        }
        label[v as usize]
    }

    for w in (1..m as u32).rev() {
        let wv = vertex[w as usize];
        for &pv in pred.nb(wv) {
            let p = dfn[pv as usize];
            if p == NONE {
                continue;
            }
            let u = eval(p, &mut anc, &mut label, &semi, &mut path);
            if semi[u as usize] < semi[w as usize] {
                semi[w as usize] = semi[u as usize];
            }
        }
        let s = semi[w as usize];
        bucket_next[w as usize] = bucket_head[s as usize];
        bucket_head[s as usize] = w;
        let pw = parent[w as usize];
        anc[w as usize] = pw; // link
        let mut b = bucket_head[pw as usize];
        while b != NONE {
            let u = eval(b, &mut anc, &mut label, &semi, &mut path);
            idom[b as usize] = if semi[u as usize] < semi[b as usize] { u } else { pw };
            b = bucket_next[b as usize];
        }
        bucket_head[pw as usize] = NONE;
    }
    for w in 1..m {
        if idom[w] != semi[w] {
            idom[w] = idom[idom[w] as usize];
        }
    }
    let mut out = vec![NONE; n];
    out[root as usize] = root;
    for w in 1..m {
        out[vertex[w] as usize] = vertex[idom[w] as usize];
    }
    out
}

/// Euler-tour intervals of the dominator tree: v is in subtree(a) iff tin[a] <= tin[v] < tout[a].
pub fn tree_intervals(idom: &[u32], root: u32) -> (Vec<u32>, Vec<u32>) {
    let n = idom.len();
    let mut head = vec![NONE; n];
    let mut next = vec![NONE; n];
    for v in 0..n as u32 {
        let p = idom[v as usize];
        if p != NONE && v != root {
            next[v as usize] = head[p as usize];
            head[p as usize] = v;
        }
    }
    let mut tin = vec![NONE; n];
    let mut tout = vec![NONE; n];
    let mut t = 0u32;
    let mut st: Vec<(u32, u32)> = vec![(root, head[root as usize])];
    tin[root as usize] = t;
    t += 1;
    while let Some(&mut (v, ref mut c)) = st.last_mut() {
        if *c == NONE {
            tout[v as usize] = t;
            st.pop();
        } else {
            let w = *c;
            *c = next[w as usize];
            tin[w as usize] = t;
            t += 1;
            st.push((w, head[w as usize]));
        }
    }
    (tin, tout)
}

/// BFS from `root` that never enters `skip`; returns the visited marks.
pub fn reach_avoiding(succ: &Csr, root: u32, skip: u32) -> Vec<bool> {
    let n = succ.n();
    let mut seen = vec![false; n];
    if root == skip {
        return seen;
    }
    let mut q = VecDeque::new();
    seen[root as usize] = true;
    q.push_back(root);
    while let Some(u) = q.pop_front() {
        for &w in succ.nb(u) {
            if w != skip && !seen[w as usize] {
                seen[w as usize] = true;
                q.push_back(w);
            }
        }
    }
    seen
}

/// Strict dominators of v found by brute force: candidates are the nodes on one BFS path root→v; d dominates v iff
/// v is unreachable once d is removed. Returned in path order (root first).
pub fn brute_dominators(succ: &Csr, root: u32, v: u32) -> Vec<u32> {
    let n = succ.n();
    let mut par = vec![NONE; n];
    let mut q = VecDeque::new();
    par[root as usize] = root;
    q.push_back(root);
    while let Some(u) = q.pop_front() {
        if u == v {
            break;
        }
        for &w in succ.nb(u) {
            if par[w as usize] == NONE {
                par[w as usize] = u;
                q.push_back(w);
            }
        }
    }
    if par[v as usize] == NONE {
        return vec![];
    }
    let mut path = Vec::new();
    let mut u = v;
    while u != root {
        u = par[u as usize];
        path.push(u);
    }
    path.reverse();
    if path.len() > 5000 {
        return vec![NONE]; // too deep for the quadratic check; reported as a skip, not a failure
    }
    path.into_iter().filter(|&d| d == root || !reach_avoiding(succ, root, d)[v as usize]).collect()
}
