"""Cost-aware probing policy (P2) on the sample. Stage 1 probes a cheap prefix; then decides per expected
value whether to include the rest directly or probe it. Big tests (> B s) are never probed: included iff the
mutant is shown live by a cheap failing probe (or no cheap candidate exists).
Usage: python eval_policy2.py <kit> <sqlglot> <artifacts> [B] [alpha]"""
import sys, os, json, time, resource
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path
B = float(sys.argv[4]) if len(sys.argv) > 4 else 2.0
ALPHA = float(sys.argv[5]) if len(sys.argv) > 5 else 1.3   # probe CPU ~= ALPHA*recorded + overhead (measured ~1.0-1.3 on box)
OVH = 0.15
P_PASS = {'code': 0.26, 'fixture': 0.05}  # priors: 0.26 measured on synthetic mutants; fixture prior is a HYPOTHESIS
sys.argv = sys.argv[:4]
import eval_baselines as eb, probe
kit, src = Path(sys.argv[1]), Path(sys.argv[2])
tests = {t['number']: t for t in eb.tests}
S = lambda sel: eb.schedule(sel)[0] / 1000
def cpu_est(ns): return (ALPHA * sum(tests[n]['seconds'] for n in ns) + OVH) if ns else 0.0
def run_probe(files, ns):
    return {n for n in ns if probe.probe(str(src), files, [tests[n]['id'] for n in ns]).get(tests[n]['id']) != 'pass'} if False else _rp(files, ns)
def _rp(files, ns):
    if not ns: return set()
    r = probe.probe(str(src), files, [tests[n]['id'] for n in ns])
    return {n for n in ns if r.get(tests[n]['id']) != 'pass'}
probe.warm()
r0 = resource.getrusage(resource.RUSAGE_SELF); c0 = resource.getrusage(resource.RUSAGE_CHILDREN)
tot = 0; misses = 0
for cid in sorted(eb.ans):
    patch = (kit / f'sample/commits/{cid}.patch').read_text()
    cand, notes = eb.select(patch, 'stmt+import')
    pf = eb.parse_patch(patch)
    kind = 'fixture' if all(f['new'].startswith('tests/fixtures/') for f in pf) else 'code'
    files = {f['new']: eb.apply((src / f['new']).read_text(), f) for f in pf}
    cheap = sorted((n for n in cand if tests[n]['seconds'] <= B), key=lambda n: tests[n]['seconds'])
    big = set(cand) - set(cheap)
    q0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    if not cand: sel = set(); how = 'empty'
    elif cpu_est(cheap) > P_PASS[kind] * S(cand) and not big:
        sel = set(cand); how = 'include-all(no probe worth it)'
    else:
        # stage 1: cheap prefix
        s1 = []; acc = 0
        for n in cheap:
            if s1 and acc + tests[n]['seconds'] > 0.3: break
            s1.append(n); acc += tests[n]['seconds']
        failed = _rp(files, s1); rest = [n for n in cheap if n not in s1]
        live = bool(failed) or not cheap
        must = failed | (big if live else set())
        if live and rest:
            marginal = S(must | set(rest)) - S(must)
            if marginal <= cpu_est(rest): sel = must | set(rest); how = 'live: include rest'
            else: sel = must | _rp(files, rest); how = 'live: probe rest'
        elif rest:
            f2 = _rp(files, rest)
            if f2: sel = f2 | failed | big; how = 'stage2 live'
            else: sel = set(); how = 'all pass'
        else: sel = must; how = 'stage1 only'
    q1 = resource.getrusage(resource.RUSAGE_CHILDREN); pc = q1.ru_utime + q1.ru_stime - q0.ru_utime - q0.ru_stime
    c = S(sel); truth = set(eb.ans[cid]['failing']); miss = not truth <= sel
    tot += c + 300 * miss; misses += miss
    print(f'{cid} {kind:7s} cand={len(cand):4d} sel={len(sel):3d} truth={len(truth):3d} cost={c:7.3f} probeCPU={pc:5.2f} {how} {"MISS" if miss else ""}', flush=True)
r1 = resource.getrusage(resource.RUSAGE_SELF); c1 = resource.getrusage(resource.RUSAGE_CHILDREN)
cpu = (r1.ru_utime + r1.ru_stime + c1.ru_utime + c1.ru_stime) - (r0.ru_utime + r0.ru_stime + c0.ru_utime + c0.ru_stime)
print(f'P2 B={B} SIMULATED={tot:.3f}s misses={misses} CPU(after warm imports)={cpu:.2f}s total≈{tot+cpu:.1f}s')
