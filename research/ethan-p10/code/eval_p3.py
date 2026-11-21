"""P3: P2-style cost-aware policy on the warm-parent fastprobe engine. Whole-process run (time it with bash).
Usage: python eval_p3.py <kit> <sqlglot> <artifacts> [B] [ALPHA] [patch_dir answers.json|-]"""
import sys, os, json, time, resource
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path
B = float(sys.argv[4]) if len(sys.argv) > 4 else 2.0
ALPHA = float(sys.argv[5]) if len(sys.argv) > 5 else 1.0
OVH = 0.03
S1 = float(os.environ.get('P3_S1', '0.3'))
os.environ.setdefault('BR_ARTIFACTS', str(Path(sys.argv[3]).resolve()))
argv = sys.argv; sys.argv = sys.argv[:4]
import eval_baselines as eb
kit, src = Path(argv[1]), Path(argv[2]).resolve()
pdir = Path(argv[6]) if len(argv) > 6 else kit / 'sample/commits'
ans = eb.ans if len(argv) <= 7 else (json.load(open(argv[7]))['commits'] if argv[7] != '-' else {})
tests = {t['number']: t for t in eb.tests}
S = lambda sel: eb.schedule(sel)[0] / 1000
def cpu_est(ns): return (ALPHA * sum(tests[n]['seconds'] for n in ns) + OVH) if ns else 0.0
t0 = time.time()
# ---- phase 1: static selection for every commit (no sqlglot import) ----
jobs = []
for pf in sorted(pdir.glob('*.patch')):
    patch = pf.read_text(); cand, notes = eb.select(patch, 'stmt+import'); parsed = eb.parse_patch(patch)
    files = {f['new']: eb.apply((src / f['new']).read_text(), f) for f in parsed if f['new'] != '/dev/null'}
    jobs.append((pf.stem, cand, files, parsed))
t_static = time.time() - t0
mods = sorted({tests[n]['file'][:-3].replace('/', '.') for _, c, _, _ in jobs for n in c if tests[n]['seconds'] <= B})
import fastprobe as fp
r_w0 = resource.getrusage(resource.RUSAGE_SELF)
fp.warm(str(src), mods)
r_w1 = resource.getrusage(resource.RUSAGE_SELF)
def rp(files, ns, plan):
    if not ns: return set()
    r = fp.probe(files, [tests[n]['id'] for n in ns], plan)
    return {n for n in ns if r.get(tests[n]['id']) != 'pass'}
tot = 0; misses = 0; out = []
for cid, cand, files, parsed in jobs:
    kind = 'fixture' if all(f['new'].startswith('tests/fixtures/') for f in parsed) else 'code'
    cheap = sorted((n for n in cand if tests[n]['seconds'] <= B), key=lambda n: tests[n]['seconds']); big = set(cand) - set(cheap)
    q0 = resource.getrusage(resource.RUSAGE_CHILDREN); plan = None
    if not cand: sel = set(); how = 'empty'
    elif not cheap: sel = set(cand); how = 'only-big'
    else:
        plan = fp.prepare(files)
        if cpu_est(cheap) >= S(cand) and not big:      # probing everything can't beat including everything
            sel = set(cand); how = 'include-all'
        else:
            s1 = []; acc = 0
            for n in cheap:
                if s1 and acc + tests[n]['seconds'] > S1: break
                s1.append(n); acc += tests[n]['seconds']
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
    q1 = resource.getrusage(resource.RUSAGE_CHILDREN); pc = q1.ru_utime + q1.ru_stime - q0.ru_utime - q0.ru_stime
    c, workers = eb.schedule(sel)
    out.append({'commit': cid, 'workers': workers})
    if cid in ans:
        truth = set(ans[cid]['failing']); miss = not truth <= sel
        tot += c / 1000 + 300 * miss; misses += miss
        print(f'{cid} {kind:7s} cand={len(cand):4d} sel={len(sel):3d} truth={len(truth):3d} cost={c/1000:7.3f} probeCPU={pc:5.2f} {plan[0] if plan else "-"} {how} {"MISS" if miss else ""}', file=sys.stderr, flush=True)
for o in out: print(json.dumps(o))
ru = resource.getrusage(resource.RUSAGE_SELF); rc = resource.getrusage(resource.RUSAGE_CHILDREN)
print(f'P3 B={B} ALPHA={ALPHA} SIMULATED={tot:.3f}s misses={misses} static_wall={t_static:.2f}s warm_cpu={(r_w1.ru_utime+r_w1.ru_stime)-(r_w0.ru_utime+r_w0.ru_stime):.2f}s '
      f'self_cpu={ru.ru_utime+ru.ru_stime:.2f}s children_cpu={rc.ru_utime+rc.ru_stime:.2f}s modes={dict(fp.STATS)} wall={time.time()-t0:.1f}s', file=sys.stderr)
