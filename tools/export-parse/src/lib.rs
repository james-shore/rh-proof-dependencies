// Streaming parser for lean4export v4.33.0 NDJSON (format 3.1.0): the parse and indexing phases, shared by
// the `export-parse` binary and by other tools. See docs/export-format.md.
// KEEP_EXPR_DETAIL=1 also keeps binder names and binder info, the universe arguments of const nodes, the level
// table and each constant's level-parameter names (off by default: the default path's memory and outputs are unchanged).

use crossbeam_channel::{bounded, Receiver, Sender};
use rayon::prelude::*;
use serde::Deserialize;
use std::borrow::Cow;
use std::io::{self, Read};
use std::time::Instant;

const BLOCK: usize = 64 << 20;

// ---------- JSON line shapes ----------
#[derive(Deserialize)]
struct NameStr<'a> {
    pre: u64,
    #[serde(borrow)]
    str: Cow<'a, str>,
}
#[derive(Deserialize)]
struct NameNum {
    pre: u64,
    i: u64,
}
#[derive(Deserialize)]
struct EConst {
    name: u64,
    us: Vec<u64>,
}
#[derive(Deserialize)]
struct EApp {
    #[serde(rename = "fn")]
    f: u64,
    arg: u64,
}
#[derive(Deserialize)]
struct EBind<'a> {
    #[serde(rename = "type")]
    ty: u64,
    body: u64,
    #[serde(default)]
    name: u64,
    #[serde(rename = "binderInfo", default, borrow)]
    bi: Option<Cow<'a, str>>,
}
#[derive(Deserialize)]
struct ELet {
    #[serde(rename = "type")]
    ty: u64,
    value: u64,
    body: u64,
    #[serde(default)]
    name: u64,
}
#[derive(Deserialize)]
struct EProj {
    #[serde(rename = "typeName")]
    type_name: u64,
    #[serde(rename = "struct")]
    st: u64,
    #[serde(default)]
    idx: u64,
}
#[derive(Deserialize)]
struct EMData {
    expr: u64,
}
#[derive(Deserialize)]
struct DAxiom {
    name: u64,
    #[serde(rename = "levelParams")]
    lps: Vec<u64>,
    #[serde(rename = "type")]
    ty: u64,
    #[serde(rename = "isUnsafe", default)]
    is_unsafe: bool,
}
#[derive(Deserialize)]
struct DDef {
    name: u64,
    #[serde(rename = "levelParams")]
    lps: Vec<u64>,
    #[serde(rename = "type")]
    ty: u64,
    value: u64,
    hints: serde_json::Value,
    safety: String,
    #[serde(default)]
    all: Vec<u64>,
}
#[derive(Deserialize)]
struct DThm {
    name: u64,
    #[serde(rename = "levelParams")]
    lps: Vec<u64>,
    #[serde(rename = "type")]
    ty: u64,
    value: u64,
    #[serde(default)]
    all: Vec<u64>,
}
#[derive(Deserialize)]
struct DOpaque {
    name: u64,
    #[serde(rename = "levelParams")]
    lps: Vec<u64>,
    #[serde(rename = "type")]
    ty: u64,
    value: u64,
    #[serde(default)]
    all: Vec<u64>,
    #[serde(rename = "isUnsafe", default)]
    is_unsafe: bool,
}
#[derive(Deserialize)]
struct DQuot {
    name: u64,
    #[serde(rename = "levelParams")]
    lps: Vec<u64>,
    #[serde(rename = "type")]
    ty: u64,
    kind: String,
}
#[derive(Deserialize)]
struct IndVal {
    name: u64,
    #[serde(rename = "levelParams")]
    lps: Vec<u64>,
    #[serde(rename = "type")]
    ty: u64,
    #[serde(rename = "numParams")]
    num_params: u64,
    #[serde(rename = "numIndices")]
    num_indices: u64,
    all: Vec<u64>,
    ctors: Vec<u64>,
    #[serde(rename = "numNested", default)]
    num_nested: u64,
    #[serde(rename = "isRec", default)]
    is_rec: bool,
    #[serde(rename = "isReflexive", default)]
    is_reflexive: bool,
}
#[derive(Deserialize)]
struct CtorVal {
    name: u64,
    #[serde(rename = "levelParams")]
    lps: Vec<u64>,
    #[serde(rename = "type")]
    ty: u64,
    induct: u64,
    cidx: u64,
    #[serde(rename = "numParams")]
    num_params: u64,
    #[serde(rename = "numFields")]
    num_fields: u64,
}
#[derive(Deserialize)]
struct Rule {
    rhs: u64,
}
#[derive(Deserialize)]
struct RecVal {
    name: u64,
    #[serde(rename = "levelParams")]
    lps: Vec<u64>,
    #[serde(rename = "type")]
    ty: u64,
    #[serde(rename = "numParams")]
    num_params: u64,
    #[serde(rename = "numIndices")]
    num_indices: u64,
    #[serde(rename = "numMotives")]
    num_motives: u64,
    #[serde(rename = "numMinors")]
    num_minors: u64,
    rules: Vec<Rule>,
    k: bool,
}
#[derive(Deserialize)]
struct DInd {
    #[serde(alias = "inductiveVals")]
    types: Vec<IndVal>,
    #[serde(alias = "constructorVals")]
    ctors: Vec<CtorVal>,
    #[serde(alias = "recursorVals")]
    recs: Vec<RecVal>,
}
#[derive(Deserialize)]
struct Line<'a> {
    #[serde(rename = "in")]
    in_: Option<u64>,
    il: Option<u64>,
    ie: Option<u64>,
    #[serde(borrow)]
    str: Option<NameStr<'a>>,
    num: Option<NameNum>,
    succ: Option<u64>,
    max: Option<(u64, u64)>,
    imax: Option<(u64, u64)>,
    param: Option<u64>,
    bvar: Option<u64>,
    sort: Option<u64>,
    #[serde(rename = "const")]
    cnst: Option<EConst>,
    app: Option<EApp>,
    #[serde(borrow)]
    lam: Option<EBind<'a>>,
    #[serde(rename = "forallE", borrow)]
    pi: Option<EBind<'a>>,
    #[serde(rename = "letE")]
    lete: Option<ELet>,
    proj: Option<EProj>,
    #[serde(rename = "natVal", borrow)]
    natval: Option<Cow<'a, str>>,
    #[serde(rename = "strVal", borrow)]
    strval: Option<Cow<'a, str>>,
    mdata: Option<EMData>,
    axiom: Option<DAxiom>,
    def: Option<OneOrMany<DDef>>,
    thm: Option<OneOrMany<DThm>>,
    opaque: Option<OneOrMany<DOpaque>>,
    quot: Option<DQuot>,
    inductive: Option<DInd>,
    meta: Option<serde_json::Value>,
}

#[derive(Deserialize)]
#[serde(untagged)]
enum OneOrMany<T> {
    One(T),
    Many(Vec<T>),
}
impl<T> OneOrMany<T> {
    fn into_vec(self) -> Vec<T> {
        match self {
            OneOrMany::One(x) => vec![x],
            OneOrMany::Many(v) => v,
        }
    }
}

// ---------- in-memory tables ----------
/// One expression node. Children: app (a=fn, b=arg), lam/forallE (a=type, b=body), letE (a=type, b=value, c=body),
/// proj (a=struct, b=typeName name index), mdata (a=expr). const: a = name index. sort: a = level index.
#[derive(Clone, Copy, Default)]
#[repr(C)]
pub struct E {
    pub tag: u8,
    pub a: u32,
    pub b: u32,
    pub c: u32,
}
pub const T_BVAR: u8 = 1;
pub const T_SORT: u8 = 2;
pub const T_CONST: u8 = 3;
pub const T_APP: u8 = 4;
pub const T_LAM: u8 = 5;
pub const T_PI: u8 = 6;
pub const T_LET: u8 = 7;
pub const T_PROJ: u8 = 8;
pub const T_NAT: u8 = 9;
pub const T_STR: u8 = 10;
pub const T_MDATA: u8 = 11;

#[derive(Default, Clone)]
pub struct NameRec {
    pub pre: u32,
    pub num: Option<u64>,
    pub s: String,
    pub set: bool,
}

pub const KIND_NAMES: [&str; 8] = [
    "axiom", "def", "theorem", "opaque", "quot", "inductive", "constructor", "recursor",
];
pub const K_AXIOM: u8 = 0;
pub const K_DEF: u8 = 1;
pub const K_THM: u8 = 2;
pub const K_OPAQUE: u8 = 3;
pub const K_QUOT: u8 = 4;
pub const K_IND: u8 = 5;
pub const K_CTOR: u8 = 6;
pub const K_REC: u8 = 7;

pub struct Cst {
    pub name: u32,
    pub kind: u8,
    pub nlev: u32,
    pub ty: u32,
    pub vals: Vec<u32>,
    pub info: String,
    pub is_unsafe: bool,
    /// level-parameter names (name indices); only with KEEP_EXPR_DETAIL=1
    pub lps: Vec<u32>,
}
struct Rec {
    seq: u64,
    pos: u32,
    line_kind: u8,
    consts: Vec<Cst>,
}

/// Universe level node (index 0 = zero). tag: 0 zero, 1 succ(a), 2 max(a,b), 3 imax(a,b), 4 param(a = name index).
#[derive(Clone, Copy, Default)]
pub struct Lv {
    pub tag: u8,
    pub a: u32,
    pub b: u32,
}
pub const L_ZERO: u8 = 0;
pub const L_SUCC: u8 = 1;
pub const L_MAX: u8 = 2;
pub const L_IMAX: u8 = 3;
pub const L_PARAM: u8 = 4;

/// binder info codes in `ExprDetail::bi`
pub const BI_DEFAULT: u8 = 0;
pub const BI_IMPLICIT: u8 = 1;
pub const BI_STRICT: u8 = 2;
pub const BI_INST: u8 = 3;

/// Per-expression detail kept with KEEP_EXPR_DETAIL=1. `aux[i]`: binder name index for lam/forallE/letE, for const
/// an offset into `us` where `us[off]` is the number of universe arguments followed by their level indices, for proj
/// the field index. `bi[i]`: binder info for lam/forallE. `lits[i]`: the digits of a natVal or the string of a strVal.
#[derive(Default)]
pub struct ExprDetail {
    pub aux: Vec<u32>,
    pub bi: Vec<u8>,
    pub us: Vec<u32>,
    pub levels: Vec<Lv>,
    pub lits: std::collections::HashMap<u32, String>,
}

#[derive(Default)]
struct BlockOut {
    bytes: u64,
    lines: u64,
    names: Vec<(u32, NameRec)>,
    nlevels: u64,
    max_level: u64,
    exprs: Vec<(u32, E)>,
    decls: Vec<Rec>,
    meta: Option<String>,
    errors: Vec<String>,
    expr_kind: [u64; 12],
    univ_args: u64,
    // detail (only with KEEP_EXPR_DETAIL=1)
    levels: Vec<(u32, Lv)>,
    xaux: Vec<(u32, u32, u8)>,
    xus: Vec<(u32, Vec<u32>)>,
    xlits: Vec<(u32, String)>,
}

fn u32c(x: u64) -> u32 {
    assert!(x < u32::MAX as u64, "index overflow {x}");
    x as u32
}

fn bi_code(s: &Option<Cow<str>>) -> u8 {
    match s.as_deref() {
        Some("implicit") => BI_IMPLICIT,
        Some("strictImplicit") => BI_STRICT,
        Some("instImplicit") => BI_INST,
        _ => BI_DEFAULT,
    }
}

fn parse_line(seq: u64, line: &[u8], out: &mut BlockOut, detail: bool) -> Result<(), String> {
    let l: Line = serde_json::from_slice(line).map_err(|e| {
        format!("{e}: {}", String::from_utf8_lossy(&line[..line.len().min(300)]))
    })?;
    if let Some(i) = l.in_ {
        let r = if let Some(s) = l.str {
            NameRec { pre: u32c(s.pre), num: None, s: s.str.into_owned(), set: true }
        } else if let Some(n) = l.num {
            NameRec { pre: u32c(n.pre), num: Some(n.i), s: String::new(), set: true }
        } else {
            return Err(format!("bad name line {}", String::from_utf8_lossy(line)));
        };
        out.names.push((u32c(i), r));
        return Ok(());
    }
    if let Some(i) = l.il {
        if l.succ.is_none() && l.max.is_none() && l.imax.is_none() && l.param.is_none() {
            return Err(format!("bad level line {}", String::from_utf8_lossy(line)));
        }
        out.nlevels += 1;
        out.max_level = out.max_level.max(i);
        if detail {
            let lv = if let Some(a) = l.succ {
                Lv { tag: L_SUCC, a: u32c(a), b: 0 }
            } else if let Some((a, b)) = l.max {
                Lv { tag: L_MAX, a: u32c(a), b: u32c(b) }
            } else if let Some((a, b)) = l.imax {
                Lv { tag: L_IMAX, a: u32c(a), b: u32c(b) }
            } else {
                Lv { tag: L_PARAM, a: u32c(l.param.unwrap()), b: 0 }
            };
            out.levels.push((u32c(i), lv));
        }
        return Ok(());
    }
    if let Some(i) = l.ie {
        let e = if l.bvar.is_some() {
            E { tag: T_BVAR, a: if detail { u32c(l.bvar.unwrap()) } else { 0 }, ..Default::default() }
        } else if let Some(s) = l.sort {
            E { tag: T_SORT, a: u32c(s), ..Default::default() }
        } else if let Some(c) = l.cnst {
            out.univ_args += c.us.len() as u64;
            if detail {
                out.xus.push((u32c(i), c.us.iter().map(|&u| u32c(u)).collect()));
            }
            E { tag: T_CONST, a: u32c(c.name), ..Default::default() }
        } else if let Some(a) = l.app {
            E { tag: T_APP, a: u32c(a.f), b: u32c(a.arg), c: 0 }
        } else if let Some(b) = l.lam {
            if detail {
                out.xaux.push((u32c(i), u32c(b.name), bi_code(&b.bi)));
            }
            E { tag: T_LAM, a: u32c(b.ty), b: u32c(b.body), c: 0 }
        } else if let Some(b) = l.pi {
            if detail {
                out.xaux.push((u32c(i), u32c(b.name), bi_code(&b.bi)));
            }
            E { tag: T_PI, a: u32c(b.ty), b: u32c(b.body), c: 0 }
        } else if let Some(b) = l.lete {
            if detail {
                out.xaux.push((u32c(i), u32c(b.name), BI_DEFAULT));
            }
            E { tag: T_LET, a: u32c(b.ty), b: u32c(b.value), c: u32c(b.body) }
        } else if let Some(p) = l.proj {
            if detail {
                out.xaux.push((u32c(i), u32c(p.idx), BI_DEFAULT));
            }
            E { tag: T_PROJ, a: u32c(p.st), b: u32c(p.type_name), c: 0 }
        } else if let Some(v) = l.natval {
            if detail {
                out.xlits.push((u32c(i), v.into_owned()));
            }
            E { tag: T_NAT, ..Default::default() }
        } else if let Some(v) = l.strval {
            if detail {
                out.xlits.push((u32c(i), v.into_owned()));
            }
            E { tag: T_STR, ..Default::default() }
        } else if let Some(m) = l.mdata {
            E { tag: T_MDATA, a: u32c(m.expr), ..Default::default() }
        } else {
            return Err(format!("bad expr line {}", String::from_utf8_lossy(line)));
        };
        out.expr_kind[e.tag as usize] += 1;
        out.exprs.push((u32c(i), e));
        return Ok(());
    }
    let pos = out.decls.len() as u32;
    let mk = |line_kind: u8, consts: Vec<Cst>| Rec { seq, pos, line_kind, consts };
    let lp = |v: &Vec<u64>| -> Vec<u32> { if detail { v.iter().map(|&x| u32c(x)).collect() } else { Vec::new() } };
    if let Some(d) = l.axiom {
        out.decls.push(mk(
            K_AXIOM,
            vec![Cst { name: u32c(d.name), kind: K_AXIOM, nlev: d.lps.len() as u32, ty: u32c(d.ty), vals: vec![], info: String::new(), is_unsafe: d.is_unsafe, lps: lp(&d.lps) }],
        ));
    } else if let Some(ds) = l.def {
        let mut cs = Vec::new();
        for d in ds.into_vec() {
            let hints = match &d.hints {
                serde_json::Value::String(s) => s.clone(),
                serde_json::Value::Object(o) => match o.get("regular") {
                    Some(v) => format!("regular:{v}"),
                    None => d.hints.to_string(),
                },
                v => v.to_string(),
            };
            cs.push(Cst { name: u32c(d.name), kind: K_DEF, nlev: d.lps.len() as u32, ty: u32c(d.ty), vals: vec![u32c(d.value)], info: format!("hints={hints};safety={};all={}", d.safety, d.all.len()), is_unsafe: d.safety == "unsafe", lps: lp(&d.lps) });
        }
        out.decls.push(mk(K_DEF, cs));
    } else if let Some(ds) = l.thm {
        let cs = ds.into_vec().into_iter().map(|d| Cst { name: u32c(d.name), kind: K_THM, nlev: d.lps.len() as u32, ty: u32c(d.ty), vals: vec![u32c(d.value)], info: format!("all={}", d.all.len()), is_unsafe: false, lps: lp(&d.lps) }).collect();
        out.decls.push(mk(K_THM, cs));
    } else if let Some(ds) = l.opaque {
        let cs = ds.into_vec().into_iter().map(|d| Cst { name: u32c(d.name), kind: K_OPAQUE, nlev: d.lps.len() as u32, ty: u32c(d.ty), vals: vec![u32c(d.value)], info: format!("all={}", d.all.len()), is_unsafe: d.is_unsafe, lps: lp(&d.lps) }).collect();
        out.decls.push(mk(K_OPAQUE, cs));
    } else if let Some(d) = l.quot {
        out.decls.push(mk(
            K_QUOT,
            vec![Cst { name: u32c(d.name), kind: K_QUOT, nlev: d.lps.len() as u32, ty: u32c(d.ty), vals: vec![], info: format!("kind={}", d.kind), is_unsafe: false, lps: lp(&d.lps) }],
        ));
    } else if let Some(d) = l.inductive {
        let mut cs = Vec::new();
        for t in &d.types {
            cs.push(Cst { name: u32c(t.name), kind: K_IND, nlev: t.lps.len() as u32, ty: u32c(t.ty), vals: vec![], info: format!("params={};indices={};ctors={};all={};nested={};isRec={};isReflexive={}", t.num_params, t.num_indices, t.ctors.len(), t.all.len(), t.num_nested, t.is_rec, t.is_reflexive), is_unsafe: false, lps: lp(&t.lps) });
        }
        for c in &d.ctors {
            cs.push(Cst { name: u32c(c.name), kind: K_CTOR, nlev: c.lps.len() as u32, ty: u32c(c.ty), vals: vec![], info: format!("induct={};cidx={};params={};fields={}", c.induct, c.cidx, c.num_params, c.num_fields), is_unsafe: false, lps: lp(&c.lps) });
        }
        for r in &d.recs {
            cs.push(Cst { name: u32c(r.name), kind: K_REC, nlev: r.lps.len() as u32, ty: u32c(r.ty), vals: r.rules.iter().map(|x| u32c(x.rhs)).collect(), info: format!("params={};indices={};motives={};minors={};rules={};k={}", r.num_params, r.num_indices, r.num_motives, r.num_minors, r.rules.len(), r.k), is_unsafe: false, lps: lp(&r.lps) });
        }
        out.decls.push(mk(K_IND, cs));
    } else if let Some(m) = l.meta {
        out.meta = Some(m.to_string());
    } else {
        return Err(format!("unknown line {}", String::from_utf8_lossy(&line[..line.len().min(300)])));
    }
    Ok(())
}

fn worker(rx: Receiver<(u64, Vec<u8>)>, tx: Sender<BlockOut>, detail: bool) {
    for (seq, buf) in rx {
        let mut out = BlockOut { bytes: buf.len() as u64, ..Default::default() };
        for line in buf.split(|&b| b == b'\n') {
            if line.is_empty() {
                continue;
            }
            out.lines += 1;
            if let Err(e) = parse_line(seq, line, &mut out, detail) {
                if out.errors.len() < 5 {
                    out.errors.push(e);
                }
            }
        }
        tx.send(out).unwrap();
    }
}

fn reader(tx: Sender<(u64, Vec<u8>)>) -> io::Result<()> {
    let stdin = io::stdin();
    let mut inp = stdin.lock();
    let mut carry: Vec<u8> = Vec::new();
    let mut seq = 0u64;
    let mut eof = false;
    while !eof {
        let mut target = BLOCK;
        let mut buf = vec![0u8; target];
        let mut len = carry.len();
        buf[..len].copy_from_slice(&carry);
        carry.clear();
        loop {
            if buf.len() < target {
                buf.resize(target, 0);
            }
            while len < target {
                let n = inp.read(&mut buf[len..target])?;
                if n == 0 {
                    eof = true;
                    break;
                }
                len += n;
            }
            buf.truncate(len);
            if eof {
                break;
            }
            match buf.iter().rposition(|&b| b == b'\n') {
                Some(p) => {
                    carry.extend_from_slice(&buf[p + 1..]);
                    buf.truncate(p + 1);
                    break;
                }
                None => target *= 2,
            }
        }
        if !buf.is_empty() {
            tx.send((seq, buf)).unwrap();
            seq += 1;
        }
    }
    Ok(())
}

pub fn render(names: &[NameRec], mut i: u32) -> String {
    let mut parts: Vec<String> = Vec::new();
    while i != 0 {
        let n = &names[i as usize];
        match n.num {
            Some(k) => parts.push(k.to_string()),
            None => parts.push(n.s.clone()),
        }
        i = n.pre;
    }
    parts.reverse();
    let s = parts.join(".");
    s.replace('\t', "\\t").replace('\n', "\\n")
}

/// Timestamped progress log: each line goes to stderr and is kept for stats.txt.
pub struct Log {
    pub t0: Instant,
    pub text: String,
}
impl Log {
    pub fn new() -> Log {
        Log { t0: Instant::now(), text: String::new() }
    }
    pub fn say(&mut self, s: String) {
        eprintln!("[{:>7.1}s] {}", self.t0.elapsed().as_secs_f64(), s);
        self.text.push_str(&s);
        self.text.push('\n');
    }
}

/// Everything the parse and indexing phases produce. `consts[i]` belongs to declaration record `rec_of[i]`;
/// `cname[i]` is its rendered name; `cid_of_name[name index]` is its constant id (u32::MAX if not a constant).
pub struct Parsed {
    pub names: Vec<NameRec>,
    pub exprs: Vec<E>,
    pub consts: Vec<Cst>,
    pub rec_of: Vec<u32>,
    pub cname: Vec<String>,
    pub cid_of_name: Vec<u32>,
    pub meta: Option<String>,
    pub detail: Option<ExprDetail>,
}

pub fn keep_detail_from_env() -> bool {
    std::env::var("KEEP_EXPR_DETAIL").map(|v| v == "1").unwrap_or(false)
}

/// Parse an export from stdin with `nworkers` threads, then flatten the declaration records into constants.
pub fn parse_stdin(nworkers: usize, expr_cap: usize, detail: bool, log: &mut Log) -> io::Result<Parsed> {
    let t0 = log.t0;
    macro_rules! say {
        ($($a:tt)*) => {{ log.say(format!($($a)*)); }};
    }

    // ---- phase 1: parse ----
    let (btx, brx) = bounded::<(u64, Vec<u8>)>(nworkers * 2);
    let (otx, orx) = bounded::<BlockOut>(nworkers * 2);
    let rd = std::thread::spawn(move || reader(btx));
    let mut ws = Vec::new();
    for _ in 0..nworkers {
        let (r, t) = (brx.clone(), otx.clone());
        ws.push(std::thread::spawn(move || worker(r, t, detail)));
    }
    drop(brx);
    drop(otx);

    let mut names: Vec<NameRec> = vec![NameRec { set: true, ..Default::default() }];
    let mut exprs: Vec<E> = Vec::with_capacity(expr_cap);
    let mut decls: Vec<Rec> = Vec::new();
    let (mut bytes, mut lines, mut nlevels, mut max_level, mut nexprs, mut nnames, mut univ_args) = (0u64, 0u64, 0u64, 0u64, 0u64, 0u64, 0u64);
    let mut expr_kind = [0u64; 12];
    let mut errors: Vec<String> = Vec::new();
    let mut meta = None;
    let mut dup_expr = 0u64;
    let mut dup_name = 0u64;
    let mut nblocks = 0u64;
    let mut xd = ExprDetail::default();
    if detail {
        xd.aux = Vec::with_capacity(expr_cap);
        xd.bi = Vec::with_capacity(expr_cap);
        xd.us.push(0); // offset 0 = no universe arguments
    }
    for b in orx {
        nblocks += 1;
        bytes += b.bytes;
        lines += b.lines;
        nlevels += b.nlevels;
        max_level = max_level.max(b.max_level);
        univ_args += b.univ_args;
        for k in 0..12 {
            expr_kind[k] += b.expr_kind[k];
        }
        if b.meta.is_some() {
            meta = b.meta;
        }
        errors.extend(b.errors);
        for (i, r) in b.names {
            let i = i as usize;
            if i >= names.len() {
                names.resize(i + 1, NameRec::default());
            }
            if names[i].set {
                dup_name += 1;
            }
            names[i] = r;
            nnames += 1;
        }
        for (i, e) in b.exprs {
            let i = i as usize;
            if i >= exprs.len() {
                exprs.resize(i + 1, E::default());
            }
            if exprs[i].tag != 0 {
                dup_expr += 1;
            }
            exprs[i] = e;
            nexprs += 1;
        }
        if detail {
            for (i, lv) in b.levels {
                let i = i as usize;
                if i >= xd.levels.len() {
                    xd.levels.resize(i + 1, Lv::default());
                }
                xd.levels[i] = lv;
            }
            for (i, a, bi) in b.xaux {
                let i = i as usize;
                if i >= xd.aux.len() {
                    xd.aux.resize(i + 1, 0);
                    xd.bi.resize(i + 1, 0);
                }
                xd.aux[i] = a;
                xd.bi[i] = bi;
            }
            for (i, v) in b.xlits {
                xd.lits.insert(i, v);
            }
            for (i, us) in b.xus {
                if us.is_empty() {
                    continue;
                }
                let i = i as usize;
                if i >= xd.aux.len() {
                    xd.aux.resize(i + 1, 0);
                    xd.bi.resize(i + 1, 0);
                }
                xd.aux[i] = xd.us.len() as u32;
                xd.us.push(us.len() as u32);
                xd.us.extend(us);
            }
        }
        decls.extend(b.decls);
        if nblocks % 64 == 0 {
            eprintln!("[{:>7.1}s] {} GB parsed", t0.elapsed().as_secs_f64(), bytes / 1_000_000_000);
        }
    }
    rd.join().unwrap()?;
    for w in ws {
        w.join().unwrap();
    }
    if detail {
        xd.aux.resize(exprs.len(), 0);
        xd.bi.resize(exprs.len(), 0);
        xd.levels.resize(max_level as usize + 1, Lv::default());
    }
    say!("parse done: {bytes} bytes, {lines} lines, {nblocks} blocks");
    say!("meta: {}", meta.clone().unwrap_or_default());
    say!("names: {nnames} lines (max index {}), dup {dup_name}, unset {}", names.len() - 1, names.iter().filter(|n| !n.set).count());
    say!("levels: {nlevels} lines (max index {max_level})");
    let unset_e = exprs.iter().filter(|e| e.tag == 0).count();
    say!("exprs: {nexprs} lines (max index {}), dup {dup_expr}, unset {unset_e}; universe args in consts {univ_args}", exprs.len() as i64 - 1);
    let enames = ["", "bvar", "sort", "const", "app", "lam", "forallE", "letE", "proj", "natVal", "strVal", "mdata"];
    for k in 1..12 {
        say!("  expr {}: {}", enames[k], expr_kind[k]);
    }
    say!("parse errors: {}", errors.len());
    for e in errors.iter().take(5) {
        say!("  ERR {e}");
    }

    // ---- phase 2: constants ----
    decls.sort_by_key(|r| (r.seq, r.pos));
    let mut line_kind_count = [0u64; 8];
    for r in &decls {
        line_kind_count[r.line_kind as usize] += 1;
    }
    say!("declaration records: {}", decls.len());
    for k in 0..8 {
        say!("  records {}: {}", KIND_NAMES[k], line_kind_count[k]);
    }
    let mut consts: Vec<Cst> = Vec::new();
    let mut rec_of: Vec<u32> = Vec::new();
    for (ri, r) in decls.into_iter().enumerate() {
        for c in r.consts {
            consts.push(c);
            rec_of.push(ri as u32);
        }
    }
    let n = consts.len();
    let mut kind_count = [0u64; 8];
    for c in &consts {
        kind_count[c.kind as usize] += 1;
    }
    say!("constants: {n}");
    for k in 0..8 {
        say!("  constants {}: {}", KIND_NAMES[k], kind_count[k]);
    }
    say!("unsafe flags set: {}", consts.iter().filter(|c| c.is_unsafe).count());
    let mut cid_of_name: Vec<u32> = vec![u32::MAX; names.len()];
    let mut dup_const = 0;
    for (i, c) in consts.iter().enumerate() {
        if cid_of_name[c.name as usize] != u32::MAX {
            dup_const += 1;
        }
        cid_of_name[c.name as usize] = i as u32;
    }
    say!("duplicate constant names: {dup_const}");
    let cname: Vec<String> = consts.par_iter().map(|c| render(&names, c.name)).collect();
    Ok(Parsed { names, exprs, consts, rec_of, cname, cid_of_name, meta, detail: if detail { Some(xd) } else { None } })
}
