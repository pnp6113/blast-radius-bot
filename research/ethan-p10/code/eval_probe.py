"""Run stmt-level candidates through the runtime probe on sample commits; compare with labels; measure CPU.
Usage: python eval_probe.py <kit> <sqlglot> <artifacts> [max_probe_seconds]"""
import sys, os, json, time, resource
sys.path.insert(0, os.path.dirname(__file__))
import eval_baselines as eb
from pathlib import Path
import probe
kit, src, art = map(Path, sys.argv[1:4]); BIG = float(sys.argv[4]) if len(sys.argv) > 4 else 1e9
tests = {t['number']: t for t in eb.tests}
probe.warm()
r0 = resource.getrusage(resource.RUSAGE_SELF); c0 = resource.getrusage(resource.RUSAGE_CHILDREN)
tot = 0; misses = 0; rows = []
for cid in sorted(eb.ans):
    patch = (kit/f'sample/commits/{cid}.patch').read_text()
    cand, notes = eb.select(patch, 'stmt')
    files = {}
    for f in eb.parse_patch(patch):
        p = f['new']; files[p] = eb.apply((src/p).read_text(), f)
    to_probe = [n for n in cand if tests[n]['seconds'] <= BIG]
    unprobed = set(cand) - set(to_probe)
    t0 = time.time(); ch0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    res = probe.probe(str(src), files, [tests[n]['id'] for n in to_probe]) if to_probe else {}
    ch1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (ch1.ru_utime + ch1.ru_stime) - (ch0.ru_utime + ch0.ru_stime)
    failed = {n for n in to_probe if res.get(tests[n]['id']) != 'pass'}
    sel = failed | unprobed
    c, _ = eb.schedule(sel); truth = set(eb.ans[cid]['failing'])
    miss = not truth <= sel
    tot += c + 300000*miss; misses += miss
    rows.append((cid, eb.ans[cid]['kind'], 'cand', len(cand), 'probed', len(to_probe), 'probe_fail', sorted(failed), 'truth', sorted(truth), 'extra', sorted(failed - truth), f'cpu={cpu:.2f}s wall={time.time()-t0:.2f}s', f'cost={c/1000:.3f}', 'MISS' if miss else ''))
    print(rows[-1], flush=True)
r1 = resource.getrusage(resource.RUSAGE_SELF); c1 = resource.getrusage(resource.RUSAGE_CHILDREN)
cpu_total = (r1.ru_utime + r1.ru_stime + c1.ru_utime + c1.ru_stime) - (r0.ru_utime + r0.ru_stime + c0.ru_utime + c0.ru_stime)
print(f'SIMULATED={tot/1000:.3f}s misses={misses} probe+driver CPU={cpu_total:.2f}s  => total≈{tot/1000+cpu_total:.1f}s')
