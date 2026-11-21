"""Local validation: ground truth for synthetic mutants by probing (almost) the whole suite (all tests except the
three heaviest), then measure recall of the static candidate generator (stmt+import mode).
Usage: python recall_full.py <kit> <sqlglot> <artifacts> <mutant_dir> <out_jsonl> <workers>"""
import sys, os, json, resource, multiprocessing as mp
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path
kit, src, art, mdir, outp, W = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4]), sys.argv[5], int(sys.argv[6])
HEAVY = {1490, 1106, 1224}
def work(pf):
    import eval_baselines as eb, probe
    tests = {t['number']: t for t in eb.tests}
    patch = pf.read_text(); cand, notes = eb.select(patch, 'stmt+import')
    files = {f['new']: eb.apply((Path(src)/f['new']).read_text(), f) for f in eb.parse_patch(patch)}
    allt = [n for n in tests if n not in HEAVY]
    c0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    res = probe.probe(src, files, [tests[n]['id'] for n in allt], per_test_timeout=60)
    c1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    fails = sorted(n for n in allt if res.get(tests[n]['id']) != 'pass')
    return {'mutant': pf.name, 'file': eb.parse_patch(patch)[0]['new'], 'cand': sorted(cand), 'fail': fails,
            'missed': sorted(set(fails) - set(cand)), 'cpu': (c1.ru_utime+c1.ru_stime)-(c0.ru_utime+c0.ru_stime), 'notes': str(notes)[:300]}
if __name__ == '__main__':
    sys.argv = [sys.argv[0], kit, src, art]
    import eval_baselines, probe, importtime; probe.warm()
    with mp.get_context('fork').Pool(W) as pool, open(outp, 'w') as fo:
        for r in pool.imap_unordered(work, sorted(mdir.glob('*.patch'))):
            fo.write(json.dumps(r) + '\n'); fo.flush()
