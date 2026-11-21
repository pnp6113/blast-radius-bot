"""P5 = P4 + expected-value gate before probing (priors as in P2) + AST-equal skip. P4 = P3 + import probe for import-affecting patches. P3: P2-style cost-aware policy on the warm-parent fastprobe engine. Whole-process run (time it with bash).
Usage: python eval_p3.py <kit> <sqlglot> <artifacts> [B] [ALPHA] [patch_dir answers.json|-]"""
import sys, os, json, time, resource
os.environ.setdefault('BR_IMPDERIV', '1')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path
B = float(sys.argv[4]) if len(sys.argv) > 4 else 2.0
ALPHA = float(sys.argv[5]) if len(sys.argv) > 5 else 1.0
OVH = float(os.environ.get('OVH', '0.01'))
_CF = os.environ.get('TEST_CPU', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_cpu.json'))
COST = {int(k): v for k, v in json.load(open(_CF)).items()} if os.path.exists(_CF) else None   # build-time measured probe CPU per test
P_PASS = {'code': float(os.environ.get('P_CODE', '0.26')), 'fixture': float(os.environ.get('P_FIX', '0.02'))}  # priors (P2): code measured on synthetic mutants; fixture = HYPOTHESIS
S1 = float(os.environ.get('P3_S1', '0.3'))
os.environ.setdefault('BR_ARTIFACTS', str(Path(sys.argv[3]).resolve()))
argv = sys.argv; sys.argv = sys.argv[:4]
import eval_baselines as eb
kit, src = Path(argv[1]), Path(argv[2]).resolve()
pdir = Path(argv[6]) if len(argv) > 6 else kit / 'sample/commits'
ans = eb.ans if len(argv) <= 7 else (json.load(open(argv[7]))['commits'] if argv[7] != '-' else {})
tests = {t['number']: t for t in eb.tests}
S = lambda sel: eb.schedule(sel)[0] / 1000
def tcost(n): return COST.get(n, tests[n]['seconds']) if COST else ALPHA * tests[n]['seconds']
def cpu_est(ns): return (sum(tcost(n) for n in ns) + OVH) if ns else 0.0
t0 = time.time()
# ---- phase 1: static selection for every commit (no sqlglot import) ----
jobs = []
for pf in sorted(pdir.glob('*.patch')):
    patch = pf.read_text(); cand, notes = eb.select(patch, 'stmt+import'); parsed = eb.parse_patch(patch)
    files = {f['new']: eb.apply((src / f['new']).read_text(), f) for f in parsed if f['new'] != '/dev/null'}
    jobs.append((pf.stem, cand, files, parsed))
t_static = time.time() - t0
mods = sorted({tests[n]['file'][:-3].replace('/', '.') for _, c, _, _ in jobs for n in c if tcost(n) <= B})
import fastprobe as fp, importtime as it, ast as _ast, astcache as AC
SQLGLOT_MODS = sorted(str(p.relative_to(src))[:-3].replace('/', '.').removesuffix('.__init__') for p in (src / 'sqlglot').rglob('*.py') if p.name != '__main__.py')
TEST_MODS = sorted({t['file'][:-3].replace('/', '.') for t in eb.tests})
by_file = {}
for t in eb.tests: by_file.setdefault(t['file'], []).append(t['number'])
def import_affecting(parsed):
    for f in parsed:
        p = f['new'] if f['new'] != '/dev/null' else f['old']
        if not (p.endswith('.py') and p.startswith('sqlglot/')): continue
        if f['new'] == '/dev/null' or f['old'] == '/dev/null': return True
        old = (src / p).read_text(); new = eb.apply(old, f)
        try: AC.parse(new)
        except SyntaxError: return True
        if AC.dump(new) == AC.dump(old): continue   # AST-equal .py edit
        if AC.sdump(old) != AC.sdump(new): return True
        if any(it.import_line(p, l) for l in eb.touched_lines(f)): return True
    return False
_G = json.load(open(Path(argv[3]) / 'import_graph.json'))
_RIMP = {}
for _k in ('edges', 'lazy_edges'):
    for a, bs in _G[_k].items():
        for b in bs: _RIMP.setdefault(b, set()).add(a)
ALLMODS = set(SQLGLOT_MODS + TEST_MODS)
def rclosure(mods):
    seen = set(mods); st = list(mods)
    while st:
        m = st.pop()
        for a in _RIMP.get(m, ()):
            if a not in seen: seen.add(a); st.append(a)
    return seen
STATS_CL = []
PARTIAL = os.environ.get('IMP_PARTIAL', '1') == '1'
def import_failures(files):
    changed = {rel[:-3].replace('/', '.').removesuffix('.__init__') for rel in files if rel.endswith('.py')}
    if PARTIAL and changed and changed <= ALLMODS:
        cl = rclosure(changed)
        order = [m for m in SQLGLOT_MODS + TEST_MODS if m in cl]
        if 'sqlglot' in cl or len(order) > 40: bad = fp.import_probe(files, SQLGLOT_MODS + TEST_MODS); STATS_CL.append(-len(order))
        else: bad = fp.import_probe_partial(files, order); STATS_CL.append(len(order))
    else:
        bad = fp.import_probe(files, SQLGLOT_MODS + TEST_MODS)
    if not bad: return set(), []
    if 'sqlglot' in bad: return set(tests), bad
    out = set()
    for m in bad:
        if m.startswith('tests.'): out |= set(by_file.get(m.replace('.', '/') + '.py', []))
        else:
            rel = m.replace('.', '/') + ('.py' if (src / (m.replace('.', '/') + '.py')).exists() else '/__init__.py')
            for l, ts in eb.cov.get(rel, {}).items(): out |= set(ts)
            if rel.startswith(it.DIALECT_DIRS):
                if '_IDX' not in eb.__dict__: eb._IDX = it.Index(src, eb.cov)
                for st in it.subclasses_stems(src, Path(rel).stem): out |= eb._IDX.dialect_users(st)
            if rel.startswith('sqlglot/') and m.count('.') == 1 and not rel.startswith(it.DIALECT_DIRS):
                pass
    return out, bad
r_w0 = resource.getrusage(resource.RUSAGE_SELF)
fp.warm(str(src), mods)
_PC = os.environ.get('PRESET_CACHE', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'preset_cache.pkl'))
_need = sorted({(tests[n]['file'][:-3].replace('/', '.'), tests[n]['id'].split('::')[1]) for _, c, _, _ in jobs for n in c if tcost(n) <= B})
if os.environ.get('PRESET', '1') == '1' and os.path.exists(_PC):
    fp.load_presets(_PC, _need)
elif os.environ.get('PRESET', '1') == '1':
    fp.preset(sorted({(tests[n]['file'][:-3].replace('/', '.'), tests[n]['id'].split('::')[1]) for _, c, _, _ in jobs for n in c if tests[n]['seconds'] <= B}))
r_w1 = resource.getrusage(resource.RUSAGE_SELF)
def rp(files, ns, plan):
    if not ns: return set()
    r = fp.probe(files, [tests[n]['id'] for n in ns], plan)
    return {n for n in ns if r.get(tests[n]['id']) != 'pass'}
tot = 0; misses = 0; out = []
for cid, cand, files, parsed in jobs:
    kind = 'fixture' if all(f['new'].startswith('tests/fixtures/') for f in parsed) else 'code'
    cheap = sorted((n for n in cand if tcost(n) <= B), key=tcost); big = set(cand) - set(cheap)
    q0 = resource.getrusage(resource.RUSAGE_CHILDREN); plan = None
    imp_fail = set(); imp_note = ''
    if import_affecting(parsed):
        qa = resource.getrusage(resource.RUSAGE_CHILDREN); imp_fail, bad = import_failures(files); qb = resource.getrusage(resource.RUSAGE_CHILDREN); imp_note = f' import-probe:{len(bad)}mods/{len(imp_fail)}t/{qb.ru_utime+qb.ru_stime-qa.ru_utime-qa.ru_stime:.2f}s'
    if not cand: sel = set(); how = 'empty'
    elif not cheap: sel = set(cand); how = 'only-big'
    else:
        plan = fp.prepare(files)
        p_pass = P_PASS['fixture' if kind == 'fixture' else 'code']
        # expected saving of probing all cheap candidates vs including everything: all-pass => save S(cand);
        # live => save roughly S(cand) - S(cheapest single candidate) (truth is usually much smaller than cand)
        live_save = max(0.0, S(cand) - min(S({n}) for n in cand))
        if not big and cpu_est(cheap) >= p_pass * S(cand) + (1 - p_pass) * live_save:
            sel = set(cand); how = 'include-all(EV)'
        else:
            s1 = []; acc = 0
            for n in cheap:
                if s1 and acc + tcost(n) > S1: break
                s1.append(n); acc += tcost(n)
            failed = rp(files, s1, plan); rest = [n for n in cheap if n not in s1]
            live = bool(failed)
            if live:
                must = failed | big
                marginal = S(must | set(rest)) - S(must) if rest else 0
                if marginal <= cpu_est(rest): sel = must | set(rest); how = 'live:incl-rest'
                else: sel = must | rp(files, rest, plan); how = 'live:probe-rest'
            elif rest:
                f2 = rp(files, rest, plan)
                if f2:
                    sel = f2 | big; how = 'stage2-live'
                else: sel = set(); how = 'all-pass'
            else: sel = set(); how = 'all-pass(s1)'
    sel = sel | imp_fail; how += imp_note
    q1 = resource.getrusage(resource.RUSAGE_CHILDREN); pc = q1.ru_utime + q1.ru_stime - q0.ru_utime - q0.ru_stime
    c, workers = eb.schedule(sel)
    out.append({'commit': cid, 'workers': workers})
    if cid in ans:
        truth = set(ans[cid]['failing']); miss = not truth <= sel
        tot += c / 1000 + 300 * miss; misses += miss
        print(f'{cid} {kind:7s} cand={len(cand):4d} sel={len(sel):3d} truth={len(truth):3d} cost={c/1000:7.3f} probeCPU={pc:5.2f} {plan[0] if plan else "-"} {how} {"MISS" if miss else ""}', file=sys.stderr, flush=True)
for o in out: print(json.dumps(o))
ru = resource.getrusage(resource.RUSAGE_SELF); rc = resource.getrusage(resource.RUSAGE_CHILDREN)
print(f'P5 B={B} ALPHA={ALPHA} SIMULATED={tot:.3f}s misses={misses} static_wall={t_static:.2f}s warm_cpu={(r_w1.ru_utime+r_w1.ru_stime)-(r_w0.ru_utime+r_w0.ru_stime):.2f}s '
      f'self_cpu={ru.ru_utime+ru.ru_stime:.2f}s children_cpu={rc.ru_utime+rc.ru_stime:.2f}s modes={dict(fp.STATS)} closure={STATS_CL} wall={time.time()-t0:.1f}s', file=sys.stderr)
