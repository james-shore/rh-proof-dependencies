"""Project declarations equal to a constant anywhere in Mathlib/core/deps (from `xtwins twins`), folded to source
declarations, with file:line and the twin's module.

usage: mathlib_restatements.py DATASET_DIR XTWINS_DIR DEPS_CONSTANTS_TSV DATA_DIR
  DEPS_CONSTANTS_TSV: the table of every dependency constant (deps-constants.tsv from reproduce.sh) with its module and package (name, module, package, ...).
Writes DATA_DIR/mathlib-restatements.tsv and XTWINS_DIR/mathlib-restatements.json.
"""
import os, sys
from common import *


def main():
    ds, xd, deps, data = sys.argv[1:5]
    os.makedirs(data, exist_ok=True)
    con = connect()
    con.execute(f"""create table x as select * from read_csv('{xd}/xtwins.tsv', delim='\\t', header=true, quote='', escape='',
        sample_size=-1)""")
    con.execute(f"""create table dep as select name, any_value(module) as module, any_value(package) as package
        from read_csv('{deps}', delim='\\t', header=true, quote='', escape='', sample_size=-1) group by name""")
    con.execute(f"""create table c as select id, name, kind, origin, coalesce(aux_rule, '') aux, coalesce(decl, '') decl,
        coalesce(file, '') file, coalesce(start_line, 0) start_line from {rel(find_table(ds, 'constants'))}""")
    con.execute(f"""create table dl as select decl, file, start_line, lines from {rel(find_table(ds, 'decls'))}
        where origin = 'project' and decl <> ''""")
    gen_rows = not_generated(con, find_table(ds, 'decls'))
    con.execute(f"""create table dgen as select decl, file, start_line from {rel(find_table(ds, 'decls'))}
        where origin = 'project' and decl <> '' {'and coalesce(cast(generated as boolean), false)' if gen_rows else 'and false'}""")
    con.execute("""create table r as select x.name, x.kind, x.round, x.twin, x.twin_matches, c.aux, c.decl, c.file, c.start_line,
        coalesce(dl.lines, 0) lines, coalesce(dep.module, '') twin_module, coalesce(dep.package, '') twin_package,
        exists (select 1 from c c2 where c2.name = x.twin) as twin_in_cone
        from x join c on c.id = x.id left join dl on dl.decl = c.decl and dl.file = c.file and dl.start_line = c.start_line
        left join dep on dep.name = x.twin""")
    con.execute("delete from r where (decl, file, start_line) in (select decl, file, start_line from dgen)")
    rows = con.execute("""select decl, kind, file, start_line, lines, case when round = 0 then 'exact' else 'up to renaming' end,
        round, twin, twin_matches, twin_module, twin_package, twin_in_cone from r where aux = 'self'
        order by kind, round, lines desc, decl, twin""").fetchall()
    write_tsv(os.path.join(data, 'mathlib-restatements.tsv'),
              ['decl', 'kind', 'file', 'start_line', 'lines', 'match', 'round', 'twin', 'twin_matches', 'twin_module',
               'twin_package', 'twin_in_cone'], rows)
    S = {'dataset': os.environ.get('RHS_DATASET_VERSION', os.path.basename(os.path.normpath(ds)))}
    q = con.execute("""select kind, round = 0 as exact, count(*), sum(lines), count(*) filter (where not twin_in_cone),
        sum(lines) filter (where not twin_in_cone) from r where aux = 'self' group by all order by all""").fetchall()
    S['source_level'] = [dict(zip(['kind', 'exact', 'decls', 'lines', 'decls_twin_outside_cone', 'lines_twin_outside_cone'], r)) for r in q]
    S['all_constants'] = con.execute("select kind, count(*) from r group by kind").fetchall()
    S['by_area'] = con.execute("""select regexp_extract(twin_module, '^([^.]+\\.[^.]+)', 1) area, count(*) n, sum(lines)
        from r where aux = 'self' group by area order by n desc limit 25""").fetchall()
    S['top'] = con.execute("""select decl, kind, file || ':' || start_line, lines, round, twin, twin_module, twin_in_cone
        from r where aux = 'self' order by lines desc, decl, twin limit 40""").fetchall()
    stats = open(os.path.join(xd, 'xtwins-stats.txt')).read().splitlines()
    S['log'] = [l.split('] ', 1)[-1] for l in stats if any(k in l for k in ('keys:', 'check:', 'round', 'twin in the keys'))]
    write_json(os.path.join(xd, 'mathlib-restatements.json'), S)
    log(S['source_level'])


if __name__ == '__main__':
    main()
