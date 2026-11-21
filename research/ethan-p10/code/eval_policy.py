"""End-to-end policy on sample commits: static candidates (stmt + import-time + fixtures) -> probe cheap
candidates (<= B s recorded) grouped by class -> include failing cheap tests; include expensive candidates only
if some probed test failed or no cheap candidate exists. Reports simulated seconds + measured CPU.
Usage: python eval_policy.py <kit> <sqlglot> <artifacts> [B]"""
import sys, os, json, time, resource
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path
B = float(sys.argv[4]) if len(sys.argv) > 4 else 2.0
sys.argv = sys.argv[:4]
import eval_baselines as eb, probe
kit, src = Path(sys.argv[1]), Path(sys.argv[2])
tests = {t['number']: t for t in eb.tests}
probe.warm()
r0 = resource.getrusage(resource.RUSAGE_SELF); c0 = resource.getrusage(resource.RUSAGE_CHILDREN); w0 = time.time()
tot = 0; misses = 0
for cid in sorted(eb.ans):
    patch = (kit / f'sample/commits/{cid}.patch').read_text()
    cand, notes = eb.select(patch, 'stmt+import')
    cheap = [n for n in cand if tests[n]['seconds'] <= B]; big = set(cand) - set(cheap)
    files = {f['new']: eb.apply((src / f['new']).read_text(), f) for f in eb.parse_patch(patch)}
    q0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    res = probe.probe(str(src), files, [tests[n]['id'] for n in cheap]) if cheap else {}
    q1 = resource.getrusage(resource.RUSAGE_CHILDREN); pc = q1.ru_utime+q1.ru_stime-q0.ru_utime-q0.ru_stime
    cheap_sec = sum(tests[n]['seconds'] for n in cheap)
    failed = {n for n in cheap if res.get(tests[n]['id']) != 'pass'}
    sel = failed | (big if (failed or not cheap) else set())
    c, workers = eb.schedule(sel); truth = set(eb.ans[cid]['failing']); miss = not truth <= sel
    tot += c + 300000 * miss; misses += miss
    print(f'{cid} {eb.ans[cid]["kind"]:8s} cand={len(cand):4d} cheap={len(cheap):4d} big={len(big)} sel={len(sel):3d} truth={len(truth):3d} cost={c/1000:7.3f} probeCPU={pc:6.2f} cheapRecSec={cheap_sec:6.2f} {"MISS" if miss else ""}', flush=True)
r1 = resource.getrusage(resource.RUSAGE_SELF); c1 = resource.getrusage(resource.RUSAGE_CHILDREN)
cpu = (r1.ru_utime + r1.ru_stime + c1.ru_utime + c1.ru_stime) - (r0.ru_utime + r0.ru_stime + c0.ru_utime + c0.ru_stime)
print(f'B={B} SIMULATED={tot/1000:.3f}s misses={misses} CPU(after warm imports)={cpu:.2f}s wall={time.time()-w0:.1f}s total≈{tot/1000+cpu:.1f}s')
